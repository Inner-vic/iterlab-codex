"""Durable business-event delivery into the existing native Codex thread."""
import hashlib
import json
import threading
import time

from .store import dumps,now
from .autonomy import policy,active
from . import budgets

ACTIONABLE={'ready','awaiting_assessment','awaiting_round_review','awaiting_next_plan','waiting_plan_adoption','next_plan_rejected'}


class Continuation:
    def __init__(self,kernel):
        self.kernel=kernel;self.store=kernel.store
        self.stop_event=threading.Event();self.thread=None
        self.component_jobs=set()
        self.monitor_error_type=None
        with self.store.lock,self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS research_finalizations(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,
              authority_id TEXT NOT NULL,branch_id TEXT NOT NULL,plan_id TEXT NOT NULL,policy_generation TEXT NOT NULL,
              reason TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(17,datetime('now'));
            CREATE TABLE IF NOT EXISTS research_monitor_health(singleton INTEGER PRIMARY KEY,
              last_success REAL,last_failure REAL,failures INTEGER NOT NULL,error_stage TEXT,
              error_type TEXT,updated_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(25,datetime('now'));
            ''')
        from .recovery import install
        install(self.store)
        from .response_recovery import install as install_output_recovery
        install_output_recovery(self.store)

    def finalization(self,delivery_id):
        return self.store.one('SELECT * FROM research_finalizations WHERE id=?',(delivery_id,))

    def component_probe(self,sid):
        """Wait without a native process/model; network reads run outside its lock."""
        session=self.store.session(sid)
        if session['active_turn'] or session['status'] in {'starting','running','compacting','waiting_approval'}:return False
        from .component_recovery import pending,recover
        value=pending(self.kernel.company.training,sid)
        if not value:return False
        grant_id=value['grant']['id']
        if grant_id not in self.component_jobs:
            self.component_jobs.add(grant_id)
            self.note(sid,'waiting_component','正在只读核对原组件创建结果，不重复创建、不占用模型',time.time()+300)
            def read_original():
                try:
                    result=recover(self.kernel.company.training,sid,grant_id,
                        cancelled=lambda:self.stop_event.is_set() or self.kernel.stopping)
                    if result['state']=='ready':self.note(sid,'component_ready','原组件身份和配置已核对，等待一次原会话续接')
                    else:self.note(sid,'waiting_component','原组件尚未核对完成；保留原请求，稍后只读检查',time.time()+300)
                except Exception as exc:
                    self.store.event(sid,'company.component_recovery_failed',{'grant_id':grant_id,'error_type':type(exc).__name__})
                    self.note(sid,'waiting_component','原组件只读核对暂未成功；保留原请求，不重新创建',time.time()+300)
                finally:self.component_jobs.discard(grant_id)
            try:self.kernel.pool.submit(read_original)
            except Exception:
                self.component_jobs.discard(grant_id);raise
        return True

    def component_resume(self,sid,p,branch):
        """One durable readiness event, including recovery of an unsent reservation."""
        from .component_recovery import context
        value=context(self.kernel.company.training,sid)
        if not value or value['row']['state']!='ready':return False
        grant_id=value['grant']['id'];store=self.store
        if store.one("SELECT id FROM company_training WHERE project_id=? AND plan_id=? AND state<>'prepared' LIMIT 1",(branch['project_id'],branch['head_plan_id'])):return False
        event=store.one("SELECT seq,payload FROM events WHERE session_id=? AND kind='company.component_reconciled' AND json_extract(payload,'$.grant_id')=? ORDER BY seq DESC LIMIT 1",(sid,grant_id))
        if not event:return False
        fact=json.loads(event['payload'])
        if fact['component_uid']!=value['row']['component_uid'] or fact['plan_id']!=branch['head_plan_id']:return False
        signature=hashlib.sha256(dumps(['component_ready',grant_id,fact['component_uid'],fact['plan_id']]).encode()).hexdigest()
        id='research-component-ready:'+signature
        existing=store.one('SELECT * FROM research_continuations WHERE id=?',(id,))
        if existing:
            delivery=store.one('SELECT * FROM deliveries WHERE id=?',(id,))
            if delivery:
                if delivery['state']=='accepted':return False
                self.note(sid,'blocked','原组件恢复投递尚未确认或已拒绝，保留原身份，不重放')
                return True
            if existing['state']!='reserved':return True
        else:store.execute('INSERT INTO research_continuations VALUES(?,?,?,?,?,?)',(id,sid,signature,id,'reserved',now()))
        text=('[研究续接事件 · 系统，不是人类新指令] 原组件创建已通过官方只读核对，继续准备原训练任务。'
            '没有重复创建组件，也尚未提交训练。核对research_status和原trial，沿现有lpai_prepare/正式提交继续；'
            '不新建实验身份，不重放未知请求，不改变执行地点、GPU或研究范围。'
            '当前组件已绑定；提交前仍需程序验证原工作包。此事件只报告准备状态恢复，不是研究结果或新授权。\n'
            +'原身份：'+dumps({k:fact[k] for k in ('grant_id','plan_id','trial_id','component_uid','source_call_id')}))
        with self.kernel.research.lock:
            latest=context(self.kernel.company.training,sid)
            current=policy(store,branch['project_id']);current_branch=active(store,branch['project_id'])
            if (self.stop_event.is_set() or self.kernel.stopping or not latest or not current
                    or current['updated_at']!=p['updated_at'] or current_branch['id']!=branch['id']
                    or current_branch['head_plan_id']!=branch['head_plan_id']):return True
            self.kernel.send(sid,text,'queue',id,origin='background')
            store.execute("UPDATE research_continuations SET state='dispatched' WHERE id=?",(id,))
        self.note(sid,'dispatched','原组件核对完成，已向原会话投递一次准备恢复事件')
        return True

    def for_turn(self,sid,turn_id):
        """The turn's start identity, not the most recent (possibly steered) input."""
        if turn_id:
            row=self.store.one('''SELECT f.* FROM research_finalizations f JOIN deliveries d ON d.id=f.id
                LEFT JOIN delivery_requests q ON q.delivery_id=d.id
                WHERE f.session_id=? AND d.session_id=? AND d.mode='queue'
                  AND (d.turn_id=? OR q.method='turn/start' AND q.observed_turn_id=?)
                ORDER BY d.rowid LIMIT 1''',(sid,sid,turn_id,turn_id))
            if row:return row
        # Early or late callbacks can lack a confirmed turn ID. An uncertain
        # original start is still read-only; it cannot authorize a replacement.
        return self.store.one('''SELECT f.* FROM research_finalizations f JOIN deliveries d ON d.id=f.id
            JOIN delivery_requests q ON q.delivery_id=d.id
            WHERE f.session_id=? AND d.session_id=? AND d.state IN ('requested','unknown')
              AND q.method='turn/start'
            ORDER BY d.rowid LIMIT 1''',(sid,sid))

    def reporting_gap(self,pid,gap):
        """Report terminal limitations once, not every transient collection retry."""
        if not gap:return None
        from .run_evidence import LOCAL_TERMINAL,MAX_AUTOMATIC_ATTEMPTS
        row=self.store.one('''SELECT e.state,e.attempts,r.state AS execution_state,r.receipt
            FROM run_evidence e JOIN research_runs r ON r.id=e.run_id
            WHERE e.project_id=? AND e.location='local' AND e.run_id=? AND e.analysis_ready=0''',
            (pid,gap['run_id']))
        if not row or row['execution_state'] not in LOCAL_TERMINAL:return None
        if row['state']=='corrupt' or row['execution_state']=='artifacts_missing' or not row['receipt'] or row['attempts']>=MAX_AUTOMATIC_ATTEMPTS:
            return {**gap,'attempts':row['attempts'],'execution_state':row['execution_state'],
                    'meaning':'仅报告原证据缺口；不分析缺失结果、不覆盖原件或重放运行'}
        return None

    def check_finalization(self,sid,delivery_id):
        row=self.finalization(delivery_id)
        return self._check_finalization(sid,row) if row else None

    def _check_finalization(self,sid,row):
        session=self.store.session(sid);pid=session['project_id']
        branch=self.kernel.research.autonomy.check_continuation(sid,self.kernel.settings.value,row['authority_id'],reporting=row['reason']=='blocked')
        p=policy(self.store,pid)
        if row['session_id']!=sid or p['updated_at']!=row['policy_generation'] or branch['id']!=row['branch_id'] or branch['head_plan_id']!=row['plan_id']:
            raise ValueError('原只读收尾的授权或方案已变化，保留原身份，不重放')
        if row['reason']=='budget_pause':
            resources=self.kernel.research.resources(self.kernel.research.plan(sid,row['plan_id']))
            if not any(budgets.exhausted(resources)):raise ValueError('预算已追加，原只读预算盘点不再启动')
        self.kernel.research.autonomy.unresolved(pid)
        if session['archived'] or session['status'] in {'unknown','disconnected'} or self.kernel.delivery_control.barrier(sid):
            raise ValueError('原投递或定向中断待核对，不能用收尾解除屏障')
        if self.kernel.research.workflow.external_pending(sid):raise ValueError('原任务尚未就绪，不能以收尾掩盖')
        gap=self.store.evidence.blocking(pid,verify_ready=True)
        if gap and not (row['reason'] in {'budget','no_progress','blocked'} and self.reporting_gap(pid,gap)):
            raise ValueError('原证据仍在回收，不能借收尾分析未就绪结果')
        return row

    def finalize_once(self,sid,p,branch,reason,attempts=None):
        """One bounded, read-only reporting identity per authorized plan."""
        if reason not in {'budget','budget_pause','no_progress','blocked'}:raise ValueError('无效的只读收尾原因')
        identity={'authority_id':p['authority_id'],'branch_id':branch['id'],'plan_id':branch['head_plan_id']}
        signature={**identity,'budget_pause_attempts':attempts} if reason=='budget_pause' else identity
        id='research-finalize:'+hashlib.sha256(dumps(signature).encode()).hexdigest()
        row=self.finalization(id)
        if not row:
            lead={'budget':'累计计算预算已到。','budget_pause':'累计计算预算已到。','no_progress':'自动续接未产生有效进展，自动实验链已停止。',
                  'blocked':'原运行已终止，但证据缺失、损坏或有界回收仍未完成。'}[reason]
            text=('[研究只读收尾事件 · 系统，非新授权] '+lead
                  +'仅核对原方案及全部已有尝试，保存有证据支持的分析、结束评审及用户报告。'
                  '缺失或损坏证据只说明限制，不能报告其指标、比较或目标成功。'
                  '不得新增计算、写实验代码、采纳后继方案、角色交接、平台提交或重置额度。'
                  '结束原因使用'+reason+'；只有本方案事先数值合同的目标已完整核验通过且无证据缺口，才可写target_achieved。'
                  '收束不删除缺口、不覆盖原证据、不取消已提交任务。\n原身份：'+dumps(identity))
            if reason=='budget_pause':
                text=('[预算暂停 · 系统] 累计计算预算已到。仅分析并记录已有运行结果和可行的下一步。'
                      '不得新增计算、修改实验代码、做轮次结束评审、采纳后继或交接。原方案保持可续接，等待预算追加。')
            row={'id':id,'session_id':sid,**identity,'policy_generation':p['updated_at'],
                 'reason':reason,'text':text,'created_at':now()}
            # Validate before reservation: unresolved work must not acquire a
            # reporting identity that silently changes the original lifecycle.
            self._check_finalization(sid,row)
            self.store.execute('INSERT INTO research_finalizations VALUES(?,?,?,?,?,?,?,?,?)',
                tuple(row[key] for key in ['id','session_id','authority_id','branch_id','plan_id','policy_generation','reason','text','created_at']))
        delivery=self.store.one('SELECT * FROM deliveries WHERE id=?',(id,))
        if delivery:
            if reason=='budget_pause' and delivery['state'] not in {'queued','requested','unknown'}:
                self.note(sid,'budget_paused','已有结果盘点投递已处理，等待追加额度。原方案和累计保留。');return
            self.note(sid,'finalizing' if delivery['state'] in {'queued','requested'} else 'blocked',
                      '原只读收尾已登记；不重复唤醒，按原投递核对结果')
            return
        self.check_finalization(sid,id)
        self.kernel.send(sid,row['text'],'queue',id,origin='background')
        self.note(sid,'finalizing','已登记一次只读收尾；禁止后继计算，等待实际报告')


    def note(self,sid,state,reason,next_check=0,failures=0):
        prior=self.store.one('SELECT * FROM research_scheduler_status WHERE session_id=?',(sid,))
        if prior and (prior['state'],prior['reason'],prior['next_check'],prior['failures'])==(state,reason,next_check,failures):return
        self.store.execute('INSERT INTO research_scheduler_status VALUES(?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,next_check=excluded.next_check,failures=excluded.failures,updated_at=excluded.updated_at',(sid,state,reason,next_check,failures,now()))
        self.store.event(sid,'research.scheduler_state',{'state':state,'reason':reason})

    def report_failed_turn(self,sid):
        row=self.store.one("SELECT payload FROM events WHERE session_id=? AND kind='turn/completed' ORDER BY seq DESC LIMIT 1",(sid,))
        failure=None
        if row:
            turn=json.loads(row['payload']).get('turn',{})
            if turn.get('status')=='failed':failure=turn.get('error')
        if isinstance(failure,dict) and isinstance(failure.get('message'),str) and '当前套餐额度已用尽' in failure['message']:
            reason='当前模型套餐额度已用尽；已保留任务和结果。额度恢复后在原会话继续，不自动重试或换模型。'
        else:
            reason='原 Codex 轮次执行失败；已保留研究和任务证据。核对原因后在原会话继续，不自动重放。'
        self.note(sid,'blocked',reason)

    def tick(self,sid):
        kernel=self.kernel;store=self.store
        with kernel.lock_for(sid):
            session=store.session(sid);p=policy(store,session['project_id'])
            if kernel.stopping or not p or p['mode']!='autonomous' or p['owner_session_id']!=sid or session['archived']:return
            from .handoff_recovery import pending as shutdown_handoff
            if session['status']=='interrupted' and shutdown_handoff(store,sid):
                kernel.restore(sid) # Fresh native terminal identity before releasing only this committed handoff.
                return
            if kernel.research.workflow.paused(sid):return
            store.evidence.recover_local(sid)
            gap=store.evidence.blocking(session['project_id'],verify_ready=not session['active_turn'])
            authority=store.one('SELECT * FROM research_authorities WHERE id=?',(p['authority_id'],));scope=json.loads(authority['scope'])
            runs=store.rows('SELECT id,state FROM research_runs WHERE project_id=? ORDER BY rowid',(session['project_id'],))
            cloud=store.rows("SELECT id,state FROM company_training WHERE project_id=? AND state<>'prepared' ORDER BY rowid",(session['project_id'],))
            guard=kernel.roles.progress.status(sid,kernel.settings.value)
            from .execution_request import status as execution_request
            requested=execution_request(store,session['project_id'])
            if requested and requested['state'] in {'needs_ssh','needs_binding'}:
                self.note(sid,'waiting_environment',requested['message']);return
            branch=active(store,session['project_id'])
            try:
                resources=kernel.research.resources(store.one('SELECT * FROM records WHERE id=?',(branch['head_plan_id'],)))
                run_needed,fit_needed=budgets.exhausted(resources)
                budget_exhausted=run_needed or fit_needed
            except ValueError:
                self.note(sid,'blocked','当前具体方案的研究授权已失效或被退回，未调用模型或计算');return
            if session['channel']!=kernel.settings.value['channel'] or scope['model_profile']!=kernel.settings.value['profile']:
                self.note(sid,'blocked','模型通道／profile与研究授权不同，未自动切换或调用模型');return
            schedule=store.one('SELECT * FROM research_scheduler_status WHERE session_id=?',(sid,))
            if schedule and schedule['next_check']>time.time():return
            human_queue=store.one("SELECT d.id FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id WHERE d.session_id=? AND d.state='queued' AND o.origin IN ('user','review') LIMIT 1",(sid,))
            if not human_queue and self.component_probe(sid):return
            if kernel.research.workflow.external_pending(sid) and not human_queue:
                self.note(sid,'waiting','等待原云端任务，后台观察；未唤醒模型或恢复原生进程');return
            if session['thread_id'] and (session['status'] in {'disconnected','unknown'} or kernel.delivery_control.has_unknown(sid)):
                # Read the native thread before considering a new delivery. Never
                # recreate a thread or replay the previous unknown request.
                kernel.restore(sid);session=store.session(sid)
            pending=store.one("SELECT id,state FROM deliveries WHERE session_id=? AND state IN ('requested','unknown','queued') ORDER BY rowid LIMIT 1",(sid,))
            if pending:
                if pending['state']=='queued':kernel.pool.submit(kernel.drain,sid)
                return # Only a known unsent local queue may drain; unknown writes never replay.
            if session['status']=='failed' and not session['active_turn']:
                from .response_recovery import dispatch as recover_output
                if recover_output(self,sid):return
                self.report_failed_turn(sid);return
            if session['active_turn'] or session['status'] not in {'idle','completed','interrupted'}:return
            if kernel.delivery_control.barrier(sid):return
            try:kernel.research.autonomy.unresolved(session['project_id'])
            except ValueError as exc:
                self.note(sid,'waiting',str(exc));return
            if not budget_exhausted and self.component_resume(sid,p,branch):return
            lifecycle=kernel.research.workflow.publish(sid)
            from . import rounds
            current_round=rounds.state(store,session['project_id'])
            if current_round and current_round['state'] in {'assessment_complete','settled','waiting_stop_review'}:
                from .recovery import proposal_discussion
                h=store.one("SELECT delivery_id FROM research_role_handoffs WHERE session_id=? AND state='pending' ORDER BY rowid LIMIT 1",(sid,))
                if h and proposal_discussion(store,sid,h['delivery_id']):
                    kernel.resume_roles(sid);return
                from .recovery import dispatch
                if current_round.get('stop_reason') in {'no_progress','blocked'} and dispatch(self,sid):return
                self.note(sid,'settled',lifecycle['label']);return
            from .autonomy import finalizing
            existing_finalization=finalizing(store,session['project_id'])
            if existing_finalization:
                if existing_finalization['session_id']!=sid:
                    self.note(sid,'blocked','原只读收尾归属原会话，策略接管不创建替代身份');return
                self.finalize_once(sid,p,branch,existing_finalization['reason']);return
            try:kernel.research.check_plan_evidence(store.one('SELECT * FROM records WHERE id=?',(branch['head_plan_id'],)))
            except (ValueError,OSError):
                self.finalize_once(sid,p,branch,'blocked');return
            if gap:
                if self.reporting_gap(session['project_id'],gap):
                    self.finalize_once(sid,p,branch,'blocked')
                else:self.note(sid,'waiting_evidence','等待原运行证据有界回收/校验；未唤醒模型或重复训练')
                return
            if lifecycle['state'] in {'waiting_external','collecting_outputs','running','preparing','waiting_approval'} or any(r['state'] in {'requested','running','unknown'} for r in runs):
                self.note(sid,'waiting',lifecycle['label']);return
            if guard['blocked']:
                self.finalize_once(sid,p,branch,'no_progress');return
            pending_role=store.one("SELECT target FROM research_role_handoffs WHERE session_id=? AND state='pending' ORDER BY rowid LIMIT 1",(sid,))
            assessable=bool(budget_exhausted and kernel.settings.value.get('agent_roles')
                and budgets.cloud_assessment_ready(store,session['project_id'],branch['head_plan_id']))
            if assessable and pending_role and pending_role['target'] in {'planner','generator','critic'}:
                kernel.resume_roles(sid);return
            analysis_due=assessable and lifecycle['state'] in {'awaiting_assessment','awaiting_round_review','awaiting_next_plan'}
            if budget_exhausted and not analysis_due:
                grant=kernel.research.execution_grant(rounds.approved_plan(store,session['project_id']))
                added=budgets.renew(store,grant,run_needed=run_needed,fit_needed=fit_needed)
                if not added:
                    if lifecycle['state']=='awaiting_assessment':
                        self.finalize_once(sid,p,branch,'budget_pause',resources['used_runs']);return
                    self.note(sid,'budget_paused','当前额度已用完。可在研究推进面板追加，原方案和已用次数保留。');return
                store.event(sid,'research.budget_changed',{'plan_id':branch['head_plan_id'],**added})
                resources=kernel.research.resources(rounds.approved_plan(store,session['project_id']))
                budget_exhausted=False
            if kernel.settings.value.get('agent_roles') and store.one("SELECT id FROM research_role_handoffs WHERE session_id=? AND state='pending' LIMIT 1",(sid,)):
                kernel.resume_roles(sid)
                return
            if lifecycle['state'] not in ACTIONABLE:
                self.note(sid,'waiting',lifecycle['label']);return
            branch=active(store,session['project_id'])
            user=store.one("SELECT d.id FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id WHERE d.session_id=? AND o.origin IN ('user','review') ORDER BY d.rowid DESC LIMIT 1",(sid,))
            snapshot={'authority_id':authority['id'],'policy_generation':p['updated_at'],'branch_id':branch['id'],'plan_id':branch['head_plan_id'],
                'lifecycle':lifecycle['state'],'subject_id':lifecycle['subject_id'],'runs':runs,'cloud':cloud,'budget_exhausted':budget_exhausted,'budget_revision':resources['budget']['revision'],'user_input':user['id'] if user else None}
            # New recovered bytes are evidence, not merely another unchanged poll.
            # Do not include timestamps/failure counters that would create turns.
            if store.one("SELECT name FROM sqlite_master WHERE name='company_output_jobs'"):
                archives=[]
                for row in store.rows('''SELECT j.task_uid,j.state,j.outcome FROM company_output_jobs j
                    JOIN company_training t ON t.session_id=j.session_id AND t.task_uid=j.task_uid
                    WHERE t.session_id=? AND t.plan_id=? ORDER BY j.task_uid''',(sid,branch['head_plan_id'])):
                    data=json.loads(row['outcome']);archives.append({'task_uid':row['task_uid'],'state':row['state'],
                        'manifest_sha256':data.get('manifest_sha256'),'archive_state':data.get('archive_state')})
                if archives:snapshot['cloud_archives']=archives
            signature=hashlib.sha256(dumps(snapshot).encode()).hexdigest();id='research-continue:'+signature
            existing=store.one('SELECT * FROM research_continuations WHERE session_id=? AND signature=?',(sid,signature))
            if existing:
                delivery=store.one('SELECT * FROM deliveries WHERE id=?',(id,))
                if delivery:
                    if existing['state'] in {'dispatched','no_progress'} and delivery['state']=='accepted':
                        if existing['state']=='dispatched':
                            store.execute("UPDATE research_continuations SET state='no_progress' WHERE id=?",(id,))
                            store.event(sid,'research.continuation_stalled',{'reason':'同一研究状态续接后未发生实质推进，停止普通续接；仅一次只读收尾','delivery_id':id})
                        self.finalize_once(sid,p,branch,'no_progress')
                    elif delivery['state'] in {'rejected','cancelled'}:
                        self.note(sid,'blocked','原研究续接被拒绝或取消，保留原身份，未自动重发')
                    return
                if existing['state']!='reserved':
                    self.note(sid,'blocked','已投递事件缺少原消息，保留身份等待核对，未创建替代事件');return
                # Reservation alone cannot have reached Codex: Kernel.send
                # persists the delivery BEFORE any native request. Recover the
                # missing local enqueue with this exact original event identity.
            else:
                store.execute('INSERT INTO research_continuations VALUES(?,?,?,?,?,?)',(id,sid,signature,id,'reserved',now()))
            text=('[研究续接事件 · 系统，不是人类新指令] 当前：'+lifecycle['label']+'。\n'
                  '在原会话核对research_status，按已授权目标推进。必要时分析真实结果、完成本轮评审、保存并采纳有证据的下一方案；'
                  '选择哪项实验、是否修订方案由你根据实际证据判断，不预设答案。范围外先说明并等待授权。'
                  '云端结果先读company_context.training.outputs本地归档索引，用lpai_collect复核已有字节，不重复远端轮询；diagnostic_only仅支持异常/证据缺口分析，不是性能结果。'
                  '达到目标或确无可支持的下一步时保存结束评审与局限，不为续接虚构实验。暂停、未知任务、缺产物与原生安全审批优先。\n'
                  +'研究位置：'+dumps({k:snapshot[k] for k in ['authority_id','branch_id','plan_id','lifecycle','subject_id']}))
            with kernel.research.lock:
                latest_policy=policy(store,session['project_id']);latest_branch=active(store,session['project_id'])
                if self.stop_event.is_set() or kernel.stopping or not latest_policy or latest_policy['mode']!='autonomous' or latest_policy['updated_at']!=p['updated_at'] or latest_branch['id']!=branch['id'] or latest_branch['head_plan_id']!=branch['head_plan_id']:
                    store.execute("UPDATE research_continuations SET state='cancelled' WHERE id=?",(id,));return
                kernel.send(sid,text,'queue',id,origin='background')
                store.execute("UPDATE research_continuations SET state='dispatched' WHERE id=?",(id,))
            self.note(sid,'dispatched','已向原生会话投递一次研究续接；等待实际研究推进')

    def status(self):
        health=self.store.one('SELECT last_success,last_failure,failures,error_stage,error_type,updated_at FROM research_monitor_health WHERE singleton=1') or {}
        return {**health,'running':bool(self.thread and self.thread.is_alive()),
                'runtime_error_type':self.monitor_error_type}

    def monitor_tick(self):
        """Isolate deterministic stages; only tick() may dispatch original events.

        A transient collector/SQLite failure must not silently retire the sole
        continuation timer. Health stores types, never credential-bearing errors.
        """
        stamp=time.time();failure=None
        stages=(('remote_observation',self.kernel.research.remote.poll),
                ('local_evidence',self.store.evidence.recover_local))
        for stage,operation in stages:
            if self.stop_event.is_set() or self.kernel.stopping:return
            try:operation()
            except Exception as exc:
                if failure is None:failure=(stage,type(exc).__name__)
        try:owners=self.store.rows("SELECT owner_session_id FROM research_autonomy_policies WHERE mode='autonomous'")
        except Exception as exc:
            owners=[]
            if failure is None:failure=('policy_read',type(exc).__name__)
        for owner in owners:
            if self.stop_event.is_set() or self.kernel.stopping:return
            sid=owner['owner_session_id']
            try:self.tick(sid)
            except Exception as exc:
                if failure is None:failure=('session_continuation',type(exc).__name__)
                # Failure reporting can itself hit an unavailable database.
                # Do not let that starve other sessions or kill the next pass.
                try:
                    prior=self.store.one('SELECT failures FROM research_scheduler_status WHERE session_id=?',(sid,))
                    failures=(prior['failures'] if prior else 0)+1
                    self.note(sid,'blocked','原会话／服务核对未完成，保留原身份并延后检查；未重放未知请求',
                              stamp+min(300,30*2**min(failures-1,4)),failures)
                except Exception:pass
        prior=self.store.one('SELECT * FROM research_monitor_health WHERE singleton=1') or {}
        self.store.execute('''INSERT INTO research_monitor_health VALUES(1,?,?,?,?,?,?)
            ON CONFLICT(singleton) DO UPDATE SET last_success=excluded.last_success,
            last_failure=excluded.last_failure,failures=excluded.failures,
            error_stage=excluded.error_stage,error_type=excluded.error_type,updated_at=excluded.updated_at''',
            (prior.get('last_success') if failure else stamp,stamp if failure else prior.get('last_failure'),
             prior.get('failures',0)+1 if failure else 0,
             failure[0] if failure else None,failure[1] if failure else None,now()))

    def start(self):
        if self.thread:return
        def monitor():
            while not self.stop_event.wait(10):
                if self.kernel.stopping:return
                try:
                    self.monitor_tick();self.monitor_error_type=None
                except Exception as exc:
                    # If even the health write fails, retain a safe in-memory
                    # signal and retry observation on the next ordinary timer.
                    self.monitor_error_type=type(exc).__name__
        self.thread=threading.Thread(target=monitor,name='research-continuation',daemon=True);self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(75)
