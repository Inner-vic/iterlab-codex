"""Durable lifecycle projection from facts, not a scientific decision loop."""
import json
from .store import dumps,now
from . import rounds


class Workflow:
    def __init__(self,store):
        self.store=store
        self.role_progress=None
        with store.lock,store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS research_controls(session_id TEXT PRIMARY KEY REFERENCES sessions(id),
              paused INTEGER NOT NULL,reason TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_user_stops(session_id TEXT PRIMARY KEY,stopped_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_lifecycle(session_id TEXT PRIMARY KEY REFERENCES sessions(id),
              projection TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_input_origins(delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),
              origin TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_workflow_meta(singleton INTEGER PRIMARY KEY,started_at TEXT NOT NULL);
            ''')
            store.db.execute('INSERT OR IGNORE INTO research_workflow_meta VALUES(1,?)',(now(),))

    def paused(self,sid):
        row=self.store.one('SELECT paused FROM research_controls WHERE session_id=?',(sid,))
        return bool(row and row['paused'])

    def external_pending(self,sid):
        """Known waiting facts, even when a native connection needs restoration."""
        pid=self.store.session(sid)['project_id']
        if self.store.one("SELECT name FROM sqlite_master WHERE name='devspace_jobs'") and self.store.one(
            "SELECT j.run_id FROM devspace_jobs j JOIN research_runs r ON r.id=j.run_id WHERE r.session_id=? AND j.state<>'collected' LIMIT 1",(sid,)):
            return True
        if rounds.pending_cloud(self.store,pid) or rounds.pending_platform_operation(self.store,pid):return True
        from .task_outputs import pending
        if pending(self.store,sid):return True
        if self.store.one("SELECT name FROM sqlite_master WHERE name='company_watches'"):
            return any(w['state'].lower() not in {'succeeded','completed','success','failed','error','stopped','terminated','cancelled','canceled'}
                       for w in self.store.rows("SELECT state FROM company_watches WHERE session_id=? AND wake_state='none'",(sid,)))
        return False

    def pause(self,sid,reason='用户暂停Codex推进，已提交任务继续运行',actor='user'):
        self.store.session(sid)
        self.store.execute('INSERT INTO research_controls VALUES(?,1,?,?) ON CONFLICT(session_id) DO UPDATE SET paused=1,reason=excluded.reason,updated_at=excluded.updated_at',
                           (sid,reason,now()))
        if actor=='user':
            self.store.execute('INSERT INTO research_user_stops VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET stopped_at=excluded.stopped_at',(sid,now()))
        return self.publish(sid)

    def input(self,sid,delivery_id,origin):
        if origin not in {'user','review','background'}:
            raise ValueError('未知消息来源')
        if not self.store.one('SELECT id FROM deliveries WHERE id=? AND session_id=?',(delivery_id,sid)):
            raise ValueError('消息来源必须对应本会话真实投递')
        old=self.store.one('SELECT origin FROM research_input_origins WHERE delivery_id=?',(delivery_id,))
        if old and old['origin']!=origin:
            raise ValueError('原消息来源不能改写')
        self.store.execute('INSERT OR IGNORE INTO research_input_origins VALUES(?,?,?)',(delivery_id,origin,now()))
        # A fresh human message permits dialogue again, not a fresh training
        # budget or automatic re-enabling of company watchers stopped by pause.
        if origin in {'user','review'}:
            self.store.execute('UPDATE research_controls SET paused=0,reason=?,updated_at=? WHERE session_id=?',
                               ('新的用户输入；具体执行仍须原授权',now(),sid))
        return self.publish(sid)

    def project(self,sid):
        session=self.store.session(sid);pid=session['project_id']
        focus=self.store.one('SELECT phase FROM research_focus WHERE project_id=?',(pid,))
        def state(name,label,kind=None,id=None,reason='',actions=('discuss','inspect')):
            return {'state':name,'label':label,'subject_kind':kind,'subject_id':id,'reason':reason,
                    'phase':focus['phase'] if focus else None,'allowed_actions':list(actions),
                    'allowed_actions_meaning':'界面导航动作，不是模型的训练工具权限；working表示原生轮次正在执行，不撤销已有方案授权。',
                    'meaning':'运行生命周期；不是科学质量通过或五阶段必经顺序'}
        active=self.store.one("SELECT id,state,session_id FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown') ORDER BY rowid LIMIT 1",(pid,))
        if self.paused(sid):
            if active and active['state']=='unknown':
                return state('blocked','研究已暂停，原运行待核对','run',active['id'],'暂停不会核销未知任务，也不能重新提交')
            return state('paused','Codex已暂停，已提交计算继续运行' if active else 'Codex已暂停',
                         'run' if active else None,active['id'] if active else None,'只暂停对话及后继调度，不取消已提交任务；后台仍保存运行结果')
        unknown=self.store.one("SELECT id FROM deliveries WHERE session_id=? AND state IN ('requested','unknown') ORDER BY rowid LIMIT 1",(sid,))
        if unknown or session['status'] in {'unknown','disconnected'} or active and active['state']=='unknown':
            return state('blocked','原状态待核对','run' if active else 'delivery',active['id'] if active else unknown['id'] if unknown else None,
                         '未知不等于失败，不重放请求')
        if self.store.one("SELECT name FROM sqlite_master WHERE name='delivery_interrupt_intents'"):
            interrupt=self.store.one("SELECT id,state FROM delivery_interrupt_intents WHERE session_id=? AND state<>'completed' ORDER BY rowid LIMIT 1",(sid,))
            if interrupt:
                return state('blocked','原中断结果待核对','interrupt',interrupt['id'],
                             '已保存定向中断意图；按原thread/turn核对，不发起替代轮次或重放未知中断')
        approval=self.store.one("SELECT id FROM approvals WHERE session_id=? AND state='pending' ORDER BY rowid LIMIT 1",(sid,))
        if approval:
            return state('waiting_approval','等待确切操作审批','approval',approval['id'])
        operation=rounds.pending_platform_operation(self.store,pid)
        if operation:return state('blocked','原平台操作待核对','operation',operation['id'],
                                  '仅凭提交返回不能核销原身份；确切任务终态可作为原提交的只读处置依据')
        cloud=rounds.pending_cloud(self.store,pid)
        if cloud:return state('blocked' if cloud['state'].lower() in {'unknown','pending_approval','requested'} else 'waiting_external',
            '原云端提交结果／平台确认待核对' if cloud['state'].lower() in {'unknown','pending_approval','requested'} else '等待原云端任务',
            'plan',cloud['plan_id'],'按原任务身份处置；未识别阶段保持在途，不另开替代计算')
        if active:
            return state('running' if active['state']=='running' else 'preparing','计算运行中' if active['state']=='running' else '正在冻结输入',
                         'run',active['id'],'当前对象来自运行身份，不来自最后一个图谱节点')
        from .task_outputs import pending
        output_pending=pending(self.store,sid)
        if output_pending:
            return state('collecting_outputs','正在回收云任务结果','cloud_task',output_pending['task_uid'],
                         '后台有界回收并保存原件；暂不唤醒模型、不重训。空目录仅限量等待上传，未知读取不重放。')
        collecting=self.store.evidence.blocking(pid)
        if self.store.one("SELECT name FROM sqlite_master WHERE type='table' AND name='company_watches'"):
            for watch in self.store.rows("SELECT id,state FROM company_watches WHERE session_id=? AND wake_state='none' ORDER BY rowid",(sid,)):
                if watch['state'].lower() not in {'succeeded','completed','success','failed','error','stopped','terminated','cancelled','canceled'}:
                    return state('waiting_external','等待已绑定外部任务','watch',watch['id'],'由后台观察，不保持模型轮询')
        round_state=rounds.state(self.store,pid)
        limited_stop=bool(round_state and round_state['state'] in {'assessment_complete','waiting_stop_review','settled'}
                          and round_state.get('stop_reason') in {'budget','no_progress','blocked','user_stop'})
        from .autonomy import finalizing
        reporting=finalizing(self.store,pid)
        stopping=bool(round_state and round_state['state'] in {'assessment_complete','waiting_stop_review','settled'})
        if reporting and not stopping:
            delivery=self.store.one('SELECT state,turn_id FROM deliveries WHERE id=?',(reporting['id'],))
            delivery_state=delivery['state'] if delivery else 'reserved'
            running=bool(delivery and delivery['turn_id'] and delivery['turn_id']==session['active_turn'])
            pending=delivery_state in {'reserved','queued','requested'} or running
            label='正在只读收尾，等待结束评审和报告' if pending else '原只读收尾尚未形成结束评审，需核对原报告'
            return {**state('finalizing' if pending else 'blocked',label,'plan',reporting['plan_id'],
                           '仅核对已有证据并报告；禁止新增计算和自动交接，原收尾不重放'),
                    'finalization':{'delivery_id':reporting['id'],'plan_id':reporting['plan_id'],
                                    'reason':reporting['reason'],'delivery_state':delivery_state},
                    'evidence_pending':collecting}
        if collecting and not limited_stop:
            return state('blocked','待回收/校验原运行证据','run',collecting['run_id'],
                         '计算状态与证据状态独立；保全原收据和结果，不以退出0代替完成，不重复训练')
        from .execution_request import status as execution_request
        requested=execution_request(self.store,pid)
        if requested and requested['state'] in {'needs_ssh','needs_binding'} and not session['active_turn']:
            return state('blocked','等待开发空间 SSH 配置' if requested['state']=='needs_ssh' else '等待开发空间绑定',
                         reason=requested['message'],actions=('discuss','inspect','configure_environment'))
        if round_state and round_state['state']!='active':
            from .autonomy import policy
            p=policy(self.store,pid);autonomous=bool(p and p['mode']=='autonomous')
            kind=round_state['state']
            if kind=='assessment_complete':
                reason=round_state['stop_reason']
                label={'target_achieved':'研究已自主收束：目标评审完成','budget':'研究已自主收束：预算受限',
                       'no_progress':'研究已自主收束：无有效进展','blocked':'研究已自主收束：证据或权限受限',
                       'user_stop':'研究已按用户要求收束','legacy_unspecified':'历史研究评审已收束，结束原因未记录'}[reason]
                return {**state(kind,label,'review',round_state['review_record_id'],
                    '原证据、额度与未知屏障保留；不再自动交接或新增实验，不取消已提交任务'),
                    'stop_reason':reason,'target_verified':round_state['target_verified'],
                    'closure_source':round_state['closure_source'],'requires_human_confirmation':False,
                    'evidence_pending':collecting}
            labels={'awaiting_next_plan':'等待形成下一轮具体方案','waiting_plan_review':'下一轮方案待用户审核',
                    'next_plan_rejected':'下一轮方案已退回，等待修订','waiting_stop_review':'本轮结束建议待用户审核',
                    'waiting_plan_adoption':'下一方案待核对研究授权后自主采纳','assessment_complete':'研究评审已收束，可查看结论或继续讨论',
                    'stop_rejected':'结束建议已退回，等待修订评审','settled':'用户已确认本轮结束'}
            return {**state(kind,labels[kind],'plan' if 'next_plan_id' in round_state else 'review',
                         round_state.get('next_plan_id') or round_state.get('review_record_id'),
                         '每轮保留证据评审；自主模式按研究授权采纳，否则等待具体方案审核' if autonomous else '方案内可多实验；跨轮必须具体方案审核，不能沿用旧批准'),
                    'evidence_pending':collecting}
        # Only new runs with explicit lifecycle ownership participate. Historical
        # completed runs are not silently converted into pending analysis work.
        latest=self.store.one('SELECT r.id,r.state FROM research_runs r JOIN research_run_context c ON c.run_id=r.id WHERE r.session_id=?'+(' AND r.plan_id=?' if round_state else '')+' ORDER BY r.rowid DESC LIMIT 1',(sid,round_state['plan_id']) if round_state else (sid,))
        if latest:
            reviewed=self.store.one('SELECT record_id FROM research_record_runs WHERE run_id=? LIMIT 1',(latest['id'],))
            if not reviewed:
                if latest['state']=='artifacts_missing':
                    return state('blocked','待补齐声明产物','run',latest['id'],'进程退出0，但声明的输出未回收，不能报告实验完成')
                return state('awaiting_assessment','等待结果分析','run',latest['id'],'根据真实收据分析；失败也需保留，不自动换模型')
        if round_state and self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
            # A platform terminal state is new evidence, not permission to start
            # the next experiment. Keep it actionable until explicitly assessed.
            assessed=set()
            for record in self.store.rows("SELECT source FROM records WHERE project_id=? AND kind IN ('result','decision','review','delivery')",(pid,)):
                assessed.update(json.loads(record['source']).get('cloud_run_ids',[]))
            for run in self.store.rows("SELECT id,state FROM company_training WHERE plan_id=? AND state<>'prepared' ORDER BY rowid",(round_state['plan_id'],)):
                if run['id'] not in assessed:
                    return state('awaiting_assessment','等待云任务结果分析','cloud_run',run['id'],
                                 '先核对原任务日志、指标和产物；失败/中断也需分析，不能以平台终态替代研究结论')
        if session['active_turn'] or session['status'] in {'running','starting','compacting'}:
            return state('working','Codex正在推进研究','turn',session['active_turn'])
        if self.store.one("SELECT name FROM sqlite_master WHERE name='research_role_progress'"):
            from .role_progress import RoleProgress
            if self.role_progress is None:
                self.role_progress=RoleProgress(self.store)
            blocked=self.role_progress.lifecycle_block(sid)
            if blocked:
                return {**state('blocked',blocked['label'],'role_handoff',blocked['subject_id'],blocked['reason']),
                        'reason_code':blocked['reason_code'],'guard':blocked['guard'],'meaning':blocked['meaning']}
        if latest and round_state:
            return state('awaiting_round_review','等待本轮方案评审与下一方案','plan',round_state['plan_id'],
                         '结果记录不代表方案轮次完成')
        from .autonomy import policy
        current_policy=policy(self.store,pid)
        if round_state and round_state['state']=='active' and current_policy and current_policy['mode']=='autonomous':
            return state('ready','当前方案可按授权推进','plan',round_state['plan_id'],'执行仍核对固定证据和共享累计，范围不足时明确报告')
        if focus and focus['phase']=='deliver':
            return state('idle','可讨论结果或安排后续研究',reason='交付焦点不等于所有科学主张已通过')
        pending=self.store.one("SELECT r.id FROM records r WHERE r.project_id=? AND r.kind='plan' AND r.created_at>=(SELECT started_at FROM research_workflow_meta WHERE singleton=1) AND NOT EXISTS(SELECT 1 FROM research_reviews v WHERE v.plan_id=r.id) ORDER BY r.rowid DESC LIMIT 1",(pid,))
        if pending:
            return state('waiting_review','有具体方案待审核','plan',pending['id'],actions=('discuss','inspect','review_exact_plan'))
        accepted=self.store.one('SELECT plan_id,decision FROM research_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 1',(pid,))
        if accepted and accepted['decision']=='accept':
            return state('ready','已有明确方案范围','plan',accepted['plan_id'],'是否还有执行额度以实时累计核验为准')
        return state('idle','等待研究推进')

    def publish(self,sid):
        with self.store.lock:
            value=self.project(sid);serialized=dumps(value)
            old=self.store.one('SELECT projection FROM research_lifecycle WHERE session_id=?',(sid,))
            if not old or old['projection']!=serialized:
                self.store.execute('INSERT INTO research_lifecycle VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET projection=excluded.projection,updated_at=excluded.updated_at',
                                   (sid,serialized,now()))
                self.store.event(sid,'research.lifecycle_changed',value)
        return value

    def location(self,sid,graph):
        value=self.publish(sid);known={node['id'] for node in graph['nodes']}
        focus=value['subject_id'] if value['subject_kind'] in {'plan','review'} and value['subject_id'] in known else None
        if value['subject_kind']=='run':
            pid=self.store.session(sid)['project_id']
            run=self.store.one('SELECT r.plan_id,c.trial_id FROM research_runs r LEFT JOIN research_run_context c ON c.run_id=r.id WHERE r.id=? AND r.project_id=?',(value['subject_id'],pid))
            if run:focus=run['trial_id'] if run['trial_id'] in known else run['plan_id'] if run['plan_id'] in known else None
        if value['subject_kind']=='cloud_run':
            pid=self.store.session(sid)['project_id']
            run=self.store.one('SELECT plan_id,trial_id FROM company_training WHERE id=? AND project_id=?',(value['subject_id'],pid))
            if run:focus=run['trial_id'] if run['trial_id'] in known else run['plan_id'] if run['plan_id'] in known else None
        state='running' if value['state'] in {'running','preparing','working'} else 'paused' if value['state'] in {'paused','pausing'} else 'unknown' if value['state']=='blocked' else 'idle' if value['state'] in {'idle','settled','assessment_complete'} else 'waiting'
        return {'state':state,'focus_node_id':focus,'active_node_ids':[focus] if focus and state=='running' else [],
                'retained_node_ids':[],'summary':value['label'],'reason':value['reason'],
                'next_action':'核对研究授权后由AI自主推进下一方案' if value['state']=='waiting_plan_adoption' else '下一方案未经审核不开始新一轮' if value['state'] in {'waiting_plan_review','next_plan_rejected','awaiting_next_plan','awaiting_round_review'} else '',
                'phase_label':{'understand':'需求理解','plan':'方案设计','conduct':'实施实验','assess':'结果评审','deliver':'交付与决策'}.get(value['phase'],''),
                'source':'explicit_workflow_subject; no last-node inference'}
