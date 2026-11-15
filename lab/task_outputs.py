"""Session-scoped, bounded LPAI output archiving; never submits or cancels work.

An archive is readable evidence, not a scientific result certificate. Unknown
reads retain their identity and are reconciled locally instead of replayed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import threading

from .store import dumps,now
from .task_output_files import MAX_TOTAL,verify_archive,local_path

ROOT=Path(__file__).resolve().parents[1]
MAX_ATTEMPTS=3
RETRY_DELAYS=(60,180)
FINAL_JOBS={'ready','diagnostic_only'}


class OutputReadFailure(ValueError):
    """A finished, owned read child reported a redacted, known failure."""
    def __init__(self,response):
        self.category=response.get('error')
        self.retryable=(self.category in {'network_timeout','network_unavailable'}
                        or type(response.get('http_status')) is int and response['http_status'] in {429,500,502,503,504})
        # Authentication/boundary errors take precedence over status hints.
        if self.category in {'auth_unavailable','access_denied','resource_unavailable','boundary_rejected'}:
            self.retryable=False
        super().__init__('官方输出读取未确认；未回显原始异常')


def status(store,sid,uid):
    """Small read model; byte verification still occurs before archive use."""
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_output_jobs'"):return None
    row=store.one('SELECT * FROM company_output_jobs WHERE session_id=? AND task_uid=?',(sid,uid))
    if not row:return None
    return {'state':row['state'],'next_check':row['next_check'],**json.loads(row['outcome'] or '{}')}


def pending(store,sid,plan_id=None):
    """Block native wakeup until bounded collection finishes, not until success.

    Legacy dispatched watches are not retroactively reopened. A new terminal
    observation with auto-resume enabled opts into the persistent collector.
    """
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_watches'"):return None
    sql='''SELECT w.* FROM company_watches w JOIN company_training t
           ON t.session_id=w.session_id AND t.task_uid=w.task_uid
           WHERE w.session_id=? AND w.auto_resume=1'''
    args=[sid]
    if plan_id:sql+=' AND t.plan_id=?';args.append(plan_id)
    for watch in store.rows(sql,args):
        if watch['state'].lower() not in {'succeeded','success','completed','failed','error','stopped','terminated','cancelled','canceled'}:continue
        value=status(store,sid,watch['task_uid'])
        if value and value['state'] not in FINAL_JOBS:return {'task_uid':watch['task_uid'],**value}
        if value is None and watch['wake_state']=='none':return {'task_uid':watch['task_uid'],'state':'pending','next_check':0}
    return None


def require_collected(store,project_id,registered_only=False):
    """Execution/adoption boundary, not just a scheduler presentation rule."""
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):return
    for row in store.rows('SELECT DISTINCT session_id FROM company_training WHERE project_id=?',(project_id,)):
        value=pending(store,row['session_id'])
        if value and (not registered_only or status(store,row['session_id'],value['task_uid']) is not None):
            raise ValueError('原云任务结果仍在有界回收中，先保全原证据；不能跳过回收另起实验或采纳下一方案')


class TaskOutputs:
    def __init__(self,company,runner=None):
        self.company,self.store=company,company.store
        self.runner=runner
        self.locks={};self.lock=threading.RLock()
        with self.store.lock,self.store.db:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS company_output_reads(
              session_id TEXT NOT NULL REFERENCES sessions(id),task_uid TEXT NOT NULL,attempt INTEGER NOT NULL,
              namespace TEXT NOT NULL,path TEXT NOT NULL,state TEXT NOT NULL,result TEXT,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(session_id,task_uid,attempt))''')
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS company_output_jobs(
              session_id TEXT NOT NULL REFERENCES sessions(id),task_uid TEXT NOT NULL,state TEXT NOT NULL,
              next_check REAL NOT NULL,outcome TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              PRIMARY KEY(session_id,task_uid))''')

    def _task_lock(self,sid,uid):
        with self.lock:return self.locks.setdefault((sid,uid),threading.RLock())

    def inspect_volume(self,name):
        from .cloud_evidence import identifier
        identifier(name);config=self.company.config()
        if not config['lpai_enabled']:raise ValueError('LPAI未启用')
        python=ROOT/'.runtime/lpai'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.is_file():raise ValueError('官方资产SDK运行环境未就绪')
        from scripts.probe_cloud_outputs import run_child
        response,stats=run_child([str(python),str(ROOT/'scripts/lpai_task_files.py'),'--stdio'],deadline=time.monotonic()+65,
            input_data=dumps({'operation':'inspect_volume','name':name,'namespace':config['namespace']}).encode(),
            max_output=4096,allow_safe_error=True,cancel_event=self.company.stop_event)
        if not response.get('ok') or not stats.get('owned_cleanup_confirmed'):raise ValueError('原卷元数据读取未确认；未列目录、登录或创建资源')
        if response.get('name')!=name or response.get('namespace')!=config['namespace']:raise ValueError('原卷身份不符')
        return response

    def advance(self,session_id,task_uid,clock=None):
        lock=self._task_lock(session_id,task_uid)
        if not lock.acquire(blocking=False):return {'state':'collecting','task_uid':task_uid,'next_check':0}
        try:return self._advance(session_id,task_uid,clock)
        finally:lock.release()

    def _advance(self,session_id,task_uid,clock=None):
        """One bounded background step; no model, training or polling loop.

        Empty/index-only directories may be late uploads: wait persistently and
        refresh twice, under the SAME three-attempt counter used by the tool.
        Unknown reads never refresh; they permit only diagnostic analysis.
        """
        self._bound(session_id,task_uid)
        stamp=time.time() if clock is None else clock
        with self.store.lock,self.store.db:
            prior=status(self.store,session_id,task_uid)
            latest=self.store.one('SELECT attempt FROM company_output_reads WHERE session_id=? AND task_uid=? ORDER BY attempt DESC LIMIT 1',(session_id,task_uid))
            newer=bool(prior and latest and latest['attempt']>prior.get('attempt',0))
            if prior and not newer and (prior['state'] in FINAL_JOBS or prior['next_check']>stamp):return prior
            if not prior:
                self.store.db.execute('INSERT INTO company_output_jobs VALUES(?,?,?,?,?,?,?)',
                    (session_id,task_uid,'collecting',stamp,'{}',now(),now()))
            else:
                self.store.db.execute("UPDATE company_output_jobs SET state='collecting',updated_at=? WHERE session_id=? AND task_uid=?",(now(),session_id,task_uid))
        try:result=self.collect(session_id,task_uid,refresh=bool(prior and prior['state']=='waiting_outputs' and not newer))
        except (ValueError,OSError) as exc:
            result={'state':'read_failed','task_uid':task_uid,'message':'本地归档未就绪；保留原尝试，不重训补证。','error_type':type(exc).__name__}
        return self._record_result(session_id,task_uid,result,stamp)

    def _record_result(self,session_id,task_uid,result,stamp):
        attempts=self.store.one('SELECT COUNT(*) AS n FROM company_output_reads WHERE session_id=? AND task_uid=?',(session_id,task_uid))['n']
        state=result['state']
        waiting=(state in {'directory_empty','index_only','required_missing'} or state=='read_failed' and result.get('retryable') is True) and attempts<MAX_ATTEMPTS
        job_state='waiting_outputs' if waiting else 'ready' if state=='files_archived' else 'diagnostic_only'
        next_check=stamp+RETRY_DELAYS[min(max(attempts-1,0),len(RETRY_DELAYS)-1)] if waiting else 0
        outcome={key:result[key] for key in ('task_uid','namespace','attempt','report_path','manifest_path','manifest_sha256','evidence_digest','report_sha256','files_saved','bytes_saved','inventory_truncated','message','result_check','retryable') if key in result}
        outcome.update(archive_state=state,analysis_scope='archived_files_unvalidated' if job_state=='ready' else 'failure_or_evidence_gap_only',
                       scientific_result_validated=False,attempts=attempts)
        self.store.execute('UPDATE company_output_jobs SET state=?,next_check=?,outcome=?,updated_at=? WHERE session_id=? AND task_uid=?',
            (job_state,next_check,dumps(outcome),now(),session_id,task_uid))
        self.store.event(session_id,'company.output_collection_state',{'task_uid':task_uid,'state':job_state,'archive_state':state,'next_check':next_check})
        return {'state':job_state,'next_check':next_check,**outcome}

    def _bound(self,sid,uid):
        from .company import checked_id
        checked_id(uid)
        session=self.store.session(sid);config=self.company.config()
        if not config['lpai_enabled']:raise ValueError('LPAI连接尚未启用')
        if session['channel']!='ept':raise ValueError('公司产物只向企业EPT会话提供')
        watch=self.store.one('SELECT * FROM company_watches WHERE session_id=? AND task_uid=?',(sid,uid))
        if not watch or watch['namespace']!=config['namespace']:raise ValueError('只能回收当前会话明确提交或绑定的原任务')
        observation=json.loads(watch['observation'])
        authority=observation.get('watch_authority') or {}
        if authority and authority.get('revision')!=config['revision']:raise ValueError('原任务连接已变化，请先核对原绑定')
        return session,config

    def _contract(self,sid,uid):
        rows=self.store.rows('SELECT request FROM company_training WHERE session_id=? AND task_uid=?',(sid,uid))
        if len(rows)>1:raise ValueError('原任务对应多个提交身份，不能猜测结果归属')
        return json.loads(rows[0]['request']).get('output_contract') if rows else None

    def _finish(self,row,manifest):
        root=local_path(self.store.safe_path(self.store.session(row['session_id'])['project_id'],row['path']))
        state=('files_archived' if manifest['files'] else
               'directory_empty' if manifest['listed_objects']==manifest['directory_markers'] and not manifest['inventory_truncated'] else 'index_only')
        frozen=self._contract(row['session_id'],row['task_uid']);check=None
        if frozen:
            from .cloud_results import validate_archive
            try:check=validate_archive(root/'files',manifest,frozen)
            except (ValueError,KeyError,TypeError):check={'state':'identity_mismatch','identity_verified':False,'message':'运行身份、结果字节或评价协议不符；不得据此认证指标。'}
            if check['state']=='missing_required':state='required_missing'
            elif not check['identity_verified']:state='identity_mismatch'
        lines=['# LPAI任务产物归档','',f"任务：`{row['task_uid']}`",f"授权空间：`{row['namespace']}`",'',
               f"本次列出 {manifest['listed_objects']} 项，目录标记 {manifest['directory_markers']} 项；保存 {len(manifest['files'])} 个文件、{manifest['bytes_saved']} 字节。",'']
        if state=='directory_empty':lines+=['官方任务输出目录为空；不代表挂载盘没有检查点。需结合任务配置和已有日志继续定位，不能据此判定研究结论。','']
        if manifest['inventory_truncated']:lines+=['目录清单已达读取上限，当前不是完整目录。','']
        if check:
            if check['state']=='missing_required':lines+=['必要结果尚未齐全：'+ '、'.join(check['missing_files'])+'。只能分析证据缺口。','']
            elif not check['identity_verified']:lines+=[check['message'],'']
            else:lines+=['运行归属、结果文件摘要和评价协议收据已匹配。后续仍须按固定数值规则核验，不等于目标已达成。','']
        for item in manifest['files']:
            label=item['name'].replace('`','').replace('\n',' ')
            lines.append(f"- `{label}`：{item['size']} 字节，SHA-256 `{item['sha256']}`"+('，仅日志尾部' if item.get('partial') else ''))
        lines+=['','权重仅建索引；文件字节已在本地复核，但尚未验证指标、数据划分、模型身份与实验协议，不构成科学结论。未启动、重投或取消云任务。']
        report='\n'.join(lines)+'\n';report_path=root/'REPORT.md'
        if report_path.exists():
            if report_path.read_text(encoding='utf-8')!=report:raise ValueError('历史归档报告已变化，不覆盖')
        else:
            with report_path.open('x',encoding='utf-8',newline='\n') as stream:stream.write(report)
        manifest_sha=hashlib.sha256((root/'files/manifest.json').read_bytes()).hexdigest()
        # Stable evidence identity excludes collection clocks, attempts, local
        # object paths and report prose. A fresh copy is not new information.
        evidence_digest=hashlib.sha256(dumps({'task_uid':row['task_uid'],'namespace':row['namespace'],
            'source':manifest.get('result_source'),
            'files':sorted([{k:item.get(k) for k in ('name','sha256','size','partial')} for item in manifest['files']],key=lambda item:item['name'])}).encode()).hexdigest()
        result={'state':state,'task_uid':row['task_uid'],'namespace':row['namespace'],'attempt':row['attempt'],
                'report_path':row['path']+'/REPORT.md','manifest_path':row['path']+'/files/manifest.json',
                'manifest_sha256':manifest_sha,'evidence_digest':evidence_digest,'report_sha256':hashlib.sha256(report.encode()).hexdigest(),
                'files_saved':len(manifest['files']),'bytes_saved':manifest['bytes_saved'],'inventory_truncated':manifest['inventory_truncated'],
                'scientific_result_validated':False,'platform_writes':0,'maximum_attempts':MAX_ATTEMPTS,
                'maximum_total_bytes':MAX_ATTEMPTS*MAX_TOTAL,'report':report}
        if check:result['result_check']=check
        self.store.execute('UPDATE company_output_reads SET state=?,result=?,updated_at=? WHERE session_id=? AND task_uid=? AND attempt=?',
                           (state,dumps(result),now(),row['session_id'],row['task_uid'],row['attempt']))
        self.store.event(row['session_id'],'company.outputs_archived',{k:v for k,v in result.items() if k!='report'})
        return result

    def collect(self,session_id,task_uid,refresh=False):
        lock=self._task_lock(session_id,task_uid)
        if not lock.acquire(blocking=False):return {'state':'collecting','task_uid':task_uid,'message':'原读取正在进行；没有启动第二次读取。'}
        try:
            result=self._collect(session_id,task_uid,refresh)
            job=status(self.store,session_id,task_uid)
            if job and job['state'] in FINAL_JOBS and (result.get('manifest_sha256')!=job.get('manifest_sha256') or result['state']!=job.get('archive_state')):
                self._record_result(session_id,task_uid,result,time.time())
            return result
        finally:lock.release()

    def _collect(self,session_id,task_uid,refresh=False):
        if type(refresh) is not bool:raise ValueError('需要明确是否重新读取原目录')
        session,config=self._bound(session_id,task_uid)
        with self.store.lock,self.store.db:
            rows=self.store.rows('SELECT * FROM company_output_reads WHERE session_id=? AND task_uid=? ORDER BY attempt',(session_id,task_uid))
            previous=rows[-1] if rows else None
            if previous:
                root=local_path(self.store.safe_path(session['project_id'],previous['path']))
                if previous['state'] in {'requested','unconfirmed'}:
                    if (root/'files/manifest.json').is_file():
                        result=self._finish(previous,verify_archive(root/'files',task_uid,config['namespace']))
                        return {**result,'cached':True,'reconciled_locally':True}
                    return {'state':'unconfirmed','task_uid':task_uid,'attempt':previous['attempt'],'cached':True,
                            'message':'原读取未确认；保留原尝试，不重放、不追加读取，不影响云训练。'}
                if not refresh:
                    result=json.loads(previous['result'])
                    if previous['state']!='read_failed':
                        manifest=verify_archive(root/'files',task_uid,config['namespace'])
                        if hashlib.sha256((root/'files/manifest.json').read_bytes()).hexdigest()!=result['manifest_sha256'] or hashlib.sha256((root/'REPORT.md').read_bytes()).hexdigest()!=result['report_sha256']:
                            raise ValueError('历史归档清单或报告已变化')
                        frozen=self._contract(session_id,task_uid)
                        if frozen and result.get('result_check',{}).get('identity_verified'):
                            from .cloud_results import validate_archive
                            validate_archive(root/'files',manifest,frozen)
                    return {**result,'cached':True}
            if len(rows)>=MAX_ATTEMPTS:raise ValueError('原任务已达三次有界回收上限，保留全部尝试；不换身份重置额度')
            if self.company.stop_event.is_set():raise InterruptedError('服务正在关闭，未开始读取')
            attempt=len(rows)+1
            relative=f'research/lpai-outputs/{session_id}/{task_uid}/attempt-{attempt}'
            root=local_path(self.store.safe_path(session['project_id'],relative))
            # Exclusive mkdir plus the DB key reserves the exact attempt before I/O.
            root.mkdir(parents=True,exist_ok=False);(root/'files').mkdir()
            request={'task_uid':task_uid,'namespace':config['namespace'],'output_dir':str(root/'files')}
            frozen=self._contract(session_id,task_uid)
            if frozen:
                from .cloud_results import requirements
                request['required_files']=requirements(frozen)
                if frozen['expected'].get('source'):request['result_source']={**frozen['expected']['source'],'run_id':frozen['expected']['run_id']}
            with (root/'request.json').open('x',encoding='utf-8') as stream:json.dump(request,stream,ensure_ascii=False)
            stamp=now()
            self.store.db.execute('INSERT INTO company_output_reads VALUES(?,?,?,?,?,?,?,?,?)',
                (session_id,task_uid,attempt,config['namespace'],relative,'requested',None,stamp,stamp))
        row={'session_id':session_id,'task_uid':task_uid,'attempt':attempt,'namespace':config['namespace'],'path':relative}
        transfer_started=False
        try:
            item=self.company.lpai('task',uid=task_uid)['item']
            from .company import TERMINAL
            if item['uid']!=task_uid or item['namespace']!=config['namespace']:raise ValueError('任务身份与绑定不一致')
            if item['phase'].lower() not in TERMINAL:raise ValueError('任务仍在排队或运行，后台继续监控；暂不归档最终结果')
            python=ROOT/'.runtime/lpai'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            if not python.is_file():raise ValueError('官方资产SDK独立运行环境未就绪')
            from scripts.probe_cloud_outputs import run_child
            runner=self.runner or run_child
            transfer_started=True
            response,stats=runner([str(python),str(ROOT/'scripts/lpai_task_files.py'),'--stdio','--archive-root',str(root)],
                deadline=time.monotonic()+160,input_data=dumps(request).encode(),max_output=256*1024,allow_safe_error=True,cancel_event=self.company.stop_event)
            if not stats.get('owned_cleanup_confirmed'):raise TimeoutError('读取进程清理未确认，不能重放读取')
            if not response.get('ok'):raise OutputReadFailure(response)
            manifest=verify_archive(root/'files',task_uid,config['namespace'])
            if request.get('required_files') and manifest.get('required_files')!=request['required_files']:raise ValueError('回收子进程未按原必要结果清单执行')
            if request.get('result_source') and manifest.get('result_source')!=request['result_source']:raise ValueError('回收来源与冻结运行卷不一致')
            if response.get('manifest')!=manifest:raise ValueError('读取收据与实际归档不一致')
            return self._finish(row,manifest)
        except Exception as exc:
            # A child timeout/crash may leave a complete archive; reconcile it
            # next time, never silently repeat the network transfer.
            from scripts.probe_cloud_outputs import ProbeError
            state='unconfirmed' if (transfer_started and isinstance(exc,(ProbeError,TimeoutError,InterruptedError))) or (root/'files/manifest.json').exists() else 'read_failed'
            result={'state':state,'task_uid':task_uid,'attempt':attempt,'platform_writes':0,
                    'message':'产物读取未完成，保留原收据；不代表云任务失败，不重投训练。','error_type':type(exc).__name__}
            if isinstance(exc,OutputReadFailure) and state=='read_failed':result['retryable']=exc.retryable
            elif not transfer_started and state=='read_failed':
                from .platform_health import error_category
                result['retryable']=not isinstance(exc,InterruptedError) and error_category(exc) in {'timeout','network'}
            self.store.execute('UPDATE company_output_reads SET state=?,result=?,updated_at=? WHERE session_id=? AND task_uid=? AND attempt=?',
                (state,dumps(result),now(),session_id,task_uid,attempt))
            return result
