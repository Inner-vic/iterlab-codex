"""Small deterministic research controls. Codex chooses hypotheses and experiments."""
from __future__ import annotations

import hashlib
from contextlib import ExitStack
from functools import lru_cache
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time

from .store import dumps,now,uid
from .owned_process import OwnedJob
from .workflow import Workflow
from . import devspace,rounds

PHASES={'understand','plan','conduct','assess','deliver'}
PHASE_LABELS={'understand':'需求理解','plan':'方案设计','conduct':'实施实验','assess':'结果评审','deliver':'交付与决策'}

def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):
            value.update(part)
    return value.hexdigest()

def plan_digest(plan):
    return hashlib.sha256(dumps({k:plan[k] for k in ['id','version','content','evidence']}).encode()).hexdigest()


@lru_cache(maxsize=1)
def _local_device(_minute):
    executable=shutil.which('nvidia-smi')
    if executable and os.environ.get('CUDA_VISIBLE_DEVICES') not in {'','-1'}:
        try:
            result=subprocess.run([executable,'--query-gpu=name','--format=csv,noheader'],capture_output=True,text=True,
                errors='replace',timeout=2,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode==0 and result.stdout.strip():
                return {'kind':'gpu','name':result.stdout.strip().splitlines()[0][:120]}
        except (OSError,subprocess.TimeoutExpired):pass
    return {'kind':'cpu','name':'CPU'}


def local_device():
    return _local_device(int(time.monotonic()//60))

class Research:
    def __init__(self,store,company=None):
        self.company=company
        self.store=store
        self.lock=threading.RLock()
        self.processes={}
        self.jobs={}
        self.cancelled=set()
        self.closing=False
        with store.lock,store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS research_focus(project_id TEXT PRIMARY KEY REFERENCES projects(id),
              phase TEXT NOT NULL,reason TEXT NOT NULL,record_id TEXT,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_reviews(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,plan_id TEXT NOT NULL,
              plan_digest TEXT NOT NULL,decision TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,authority TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_grants(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,plan_id TEXT NOT NULL,
              plan_digest TEXT NOT NULL,review_id TEXT NOT NULL,python TEXT NOT NULL,execution TEXT NOT NULL,max_runs INTEGER NOT NULL,
              max_fits INTEGER NOT NULL,timeout INTEGER NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_runs(id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,project_id TEXT NOT NULL,
              session_id TEXT NOT NULL,plan_id TEXT NOT NULL,grant_id TEXT NOT NULL,request_hash TEXT NOT NULL,request TEXT NOT NULL,
              state TEXT NOT NULL,directory TEXT NOT NULL,receipt TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_run_context(run_id TEXT PRIMARY KEY REFERENCES research_runs(id),
              trial_id TEXT REFERENCES records(id),created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_cloud_scopes(grant_id TEXT PRIMARY KEY REFERENCES research_grants(id),
              component_uid TEXT NOT NULL,component_digest TEXT NOT NULL,max_gpus INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS research_component_previews(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,
              plan_id TEXT NOT NULL,plan_digest TEXT NOT NULL,configuration TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_component_provisions(grant_id TEXT PRIMARY KEY REFERENCES research_grants(id),
              preview_id TEXT NOT NULL REFERENCES research_component_previews(id),state TEXT NOT NULL,component_uid TEXT,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_budget_changes(grant_id TEXT NOT NULL REFERENCES research_grants(id),
              revision INTEGER NOT NULL,max_runs INTEGER NOT NULL,max_fits INTEGER NOT NULL,auto INTEGER NOT NULL,
              hard_runs INTEGER,hard_fits INTEGER,source TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(grant_id,revision));
            CREATE TABLE IF NOT EXISTS research_record_runs(record_id TEXT NOT NULL REFERENCES records(id),
              run_id TEXT NOT NULL REFERENCES research_runs(id),PRIMARY KEY(record_id,run_id));
            CREATE TABLE IF NOT EXISTS research_round_decisions(id TEXT PRIMARY KEY,review_record_id TEXT NOT NULL REFERENCES records(id),
              decision TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(4,datetime('now'));
            INSERT OR IGNORE INTO schema_versions VALUES(6,datetime('now'));
            INSERT OR IGNORE INTO schema_versions VALUES(7,datetime('now'));
            INSERT OR IGNORE INTO schema_versions VALUES(18,datetime('now'));
            INSERT OR IGNORE INTO schema_versions VALUES(19,datetime('now'));
            ''')
            # A service restart cannot prove a previously started computation failed.
            if store.one("SELECT name FROM sqlite_master WHERE name='devspace_jobs'"):
                store.db.execute("UPDATE research_runs SET state='unknown',updated_at=? WHERE state IN ('requested','running') AND id NOT IN (SELECT run_id FROM devspace_jobs)",(now(),))
            else:store.db.execute("UPDATE research_runs SET state='unknown',updated_at=? WHERE state IN ('requested','running')",(now(),))
        self.workflow=Workflow(store)
        from .autonomy import Autonomy
        self.autonomy=Autonomy(self)
        from .comparison import Comparison
        self.comparison=Comparison(self)
        from .devspace_jobs import DevspaceJobs
        self.remote=DevspaceJobs(self)

    def status(self,session_id,view='full',evidence_offset=0):
        result=self._status(session_id,view,evidence_offset)
        from .execution_request import status
        result['execution_request']=status(self.store,self.store.session(session_id)['project_id'])
        from .autonomy import finalizing
        plan=rounds.approved_plan(self.store,self.store.session(session_id)['project_id'])
        project=self.store.one('SELECT title FROM projects WHERE id=?',(self.store.session(session_id)['project_id'],))
        scope=(result.get('autonomy') or {}).get('scope') or {}
        last_review=rounds.round_review(self.store,self.store.session(session_id)['project_id'],plan['id']) if plan else None
        result['project_context']={
            'project_title':project['title'],'confirmed_goal':scope.get('objective'),
            'data_boundary':scope.get('data_boundary'),'evaluation':scope.get('evaluation'),
            'current_plan':{'id':plan['id'],'title':plan['title'],'version':plan['version'],'summary':plan['summary']} if plan else None,
            'last_round_review':{'id':last_review['id'],'title':last_review['title'],'summary':last_review['summary']} if last_review else None,
            'source':'确切项目、当前授权及已保存方案/评审；不是程序新作的科学判断',
            'meaning':'背景供理解与查证，不要求每条输出；承接已知共识，自主探索主动解读重要变化，以真实可比指标支撑判断，不强制汇报流程。未知目标/类别/原因保持未知，技术阻塞不代表模型无效'}
        pid=self.store.session(session_id)['project_id']
        result['project_context']['study_background']=self.store.one("SELECT id,title,summary FROM records WHERE project_id=? AND kind IN ('alignment','question') ORDER BY CASE WHEN kind='alignment' THEN 0 ELSE 1 END,rowid LIMIT 1",(pid,))
        result['project_context']['original_plan']=self.store.one("SELECT r.id,r.title,r.summary FROM records r JOIN research_reviews v ON v.plan_id=r.id WHERE r.project_id=? AND v.decision='accept' ORDER BY v.rowid LIMIT 1",(pid,))
        result['project_context']['background_notice']='背景/原方案是历史来源，必要时按ID读取；当前目标与执行约束以最新确切授权为准，不把旧方法/预算当现状'
        try:
            grant=self.execution_grant(plan) if plan else None
            closed=bool(plan and (finalizing(self.store,plan['project_id']) or rounds.round_review(self.store,plan['project_id'],plan['id'])))
            result['execution_authorization']={'plan_id':plan['id'] if plan else None,
                'grant_valid':bool(grant),'plan_closed':closed,'execution':grant['execution'] if grant else None,
                'can_request_preflight':bool(grant and not closed and not self.workflow.paused(session_id)),
                'meaning':'此处是方案授权，不是角色授权。Executor的execute阶段可在原范围请求正式预检/提交；仍须实时核验角色、未知任务、冻结证据、资源和累计预算。界面的working状态本身不阻止提交。'}
            requested=result['execution_request']
            if requested and (requested['execution']!=(grant['execution'] if grant else None) or requested['state']!='selected'):
                result['execution_authorization'].update(can_request_preflight=False,reason=requested['message'] if requested['state']!='selected' else '后续执行位置已改变，先核对新位置的方案和资源；不修改历史授权。')
        except ValueError as exc:
            result['execution_authorization']={'grant_valid':False,'can_request_preflight':False,'reason':str(exc)}
        from .research_modules import ResearchModules
        result['research_modules']=ResearchModules(self.store).context(session_id)
        from .inquiry import InquiryReview
        result['inquiry_coverage']=InquiryReview(self.store).context(session_id)
        from .study_integrity import context as study_context
        result['study_integrity']=study_context(self.store,session_id)
        return result

    def _status(self,session_id,view='full',evidence_offset=0):
        if view not in {'full','brief'}:raise ValueError('未知研究读取视图')
        session=self.store.session(session_id);pid=session['project_id']
        from .round_evidence import packet
        evidence=packet(self.store,pid,evidence_offset,selection='actionable' if view=='brief' else 'all')
        current=rounds.approved_plan(self.store,pid)
        try:resources=self.resources(current) if current else None
        except ValueError:resources={'available':False,'reason':'原具体方案授权已失效，未重新授权'}
        cloud=self.store.rows("SELECT id,plan_id,trial_id,task_uid,state FROM company_training WHERE project_id=? AND state<>'prepared' ORDER BY rowid",(pid,)) if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else []
        if view=='brief':
            runs=self.store.rows('SELECT r.id,r.plan_id,r.state,r.directory,c.trial_id FROM research_runs r LEFT JOIN research_run_context c ON c.run_id=r.id WHERE r.project_id=? ORDER BY r.rowid DESC LIMIT 6',(pid,))
            requests=self.store.rows('SELECT request FROM research_runs WHERE project_id=?',(pid,))
            return {'default_python':sys.executable,'resources':resources,'round_evidence':evidence,'recent_comparisons':self.comparison.index(pid),'autonomy':self.autonomy.status(session_id),'lifecycle':self.workflow.publish(session_id),'round':rounds.state(self.store,pid),'recent_records':self.snapshot(session_id,limit=6),
                    'recent_runs':runs,'cloud_runs':cloud[-6:],'attempts':{'runs':len(requests)+len(cloud),'fits':sum(json.loads(r['request']).get('purpose')=='fit' for r in requests)+len(cloud)},
                    'latest_user_delivery':self.store.one("SELECT d.id,substr(d.text,1,500) AS text,d.turn_id FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id WHERE d.session_id=? AND o.origin='user' ORDER BY d.rowid DESC LIMIT 1",(session_id,)),
                    'latest_alignment_record':self.store.one("SELECT id,title,summary FROM records WHERE project_id=? AND kind='alignment' ORDER BY rowid DESC LIMIT 1",(pid,)),
                    'grants':self.store.rows('SELECT plan_id,execution,python,max_runs,max_fits,timeout FROM research_grants WHERE project_id=? ORDER BY rowid DESC LIMIT 3',(pid,)),
                    'reviews':self.store.rows('SELECT plan_id,decision,substr(reason,1,240) AS reason FROM research_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 3',(pid,)),
                    'detail':'按需research_snapshot(record_id)读取全文；运行原件在directory，不重复注入完整清单'}
        used=self.store.rows('SELECT request FROM research_runs WHERE project_id=?',(pid,))
        return {'task_policy_schema':1,'resources':resources,'round_evidence':evidence,'recent_comparisons':self.comparison.index(pid),'autonomy':self.autonomy.status(session_id),'focus':self.store.one('SELECT * FROM research_focus WHERE project_id=?',(pid,)),
            'plan_lineage':self.store.rows('SELECT plan_id,series_id,series_number,minor_number,previous_plan_id,predecessor_series_id,revision_scope,reason,trigger_record_ids,basis FROM research_plan_lineage WHERE project_id=? ORDER BY series_number,minor_number',(pid,)),
            'attempts':{'runs':len(used)+len(cloud),'fits':sum(json.loads(r['request']).get('purpose')=='fit' for r in used)+len(cloud)},'cloud_runs':cloud,
            'cloud_scopes':self.cloud_scopes(pid),
            'plans':self.store.rows("SELECT id,title,version,status FROM records WHERE project_id=? AND kind='plan' ORDER BY rowid DESC LIMIT 12",(pid,)),
            'reviews':self.store.rows('SELECT id,plan_id,decision,actor,reason,created_at FROM research_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 12',(pid,)),
            'grants':self.store.rows('SELECT id,plan_id,python,execution,max_runs,max_fits,timeout FROM research_grants WHERE project_id=? ORDER BY rowid DESC LIMIT 12',(pid,)),
            'runs':self.store.rows('SELECT id,plan_id,state,directory,receipt FROM research_runs WHERE project_id=? ORDER BY rowid DESC LIMIT 16',(pid,)),
            'default_python':sys.executable,'lifecycle':self.workflow.publish(session_id),'round':rounds.state(self.store,pid),'phase_meaning':'语义焦点，不是必经节点或质量通过状态'}

    def snapshot(self,session_id,record_id=None,limit=20):
        pid=self.store.session(session_id)['project_id']
        if record_id:
            record=self.store.one('SELECT id,kind,title,content,evidence,status,version,parent_id,source FROM records WHERE project_id=? AND id=?',(pid,record_id))
            if not record:raise ValueError('研究记录不属于当前项目或不存在')
            source=json.loads(record.pop('source'))
            from .study_integrity import describe as describe_study
            record['study_integrity']=describe_study(dict(record,source=dumps(source)))
            record['relationships']={key:source[key] for key in ['plan_id','basis_record_id','depends_on_trial_ids','trigger_record_ids','previous_plan_id','review_record_id'] if key in source}
            record['research_methods']={key:source[key] for key in ['research_protocol','method_reports','research_module_engine'] if key in source}
            record['inquiry_coverage']={key:source[key] for key in ['review_of','inquiry_outcomes','inquiry_checks'] if key in source}
            record['recorded_status']=record['status']
            current=self.store.record_statuses(pid).get(record['id'],{})
            record.update(current_status=current.get('status'),status_source=current.get('source','historical_record'))
            record['plan_lineage']=self.store.one('SELECT * FROM research_plan_lineage WHERE plan_id=?',(record_id,))
            if record['kind']=='plan':
                record['record_id']=record['id'];record['plan_id']=record['id']
                record['identity_note']='本候选plan_id就是本记录id；source或relationships中的历史plan_id不代表候选身份，前序仅由previous_plan_id表示。'
                record['plan_digest']=plan_digest(record)
                # Reviewers must inspect what was actually saved, not infer
                # resource/scope fields from prose or a prior tool receipt.
                record['scope_proposal']=source.get('scope_proposal')
                record['evaluation_contract']=source.get('evaluation_contract')
                record['authorization_context']={
                    'authority_id':source.get('authority_id'),'scope_digest':source.get('scope_digest'),
                    'meaning':'方案声明与关联身份，不代表本方案已获执行授权；实际权限仍以research_status和正式审核为准'}
                # Explicit plan inspection only: do not hash inputs on UI polls.
                # The native thread may have an older snapshot tool schema;
                # return the same program-owned byte check without a new flag.
                record['frozen_evidence']=self.frozen_evidence(session_id,record)
            return record
        records=self.store.rows('SELECT id,kind,title,summary,status,version,parent_id FROM records WHERE project_id=? ORDER BY rowid DESC LIMIT ?',(pid,limit))
        projected=self.store.record_statuses(pid)
        for record in records:
            record['recorded_status']=record['status'];record['current_status']=projected.get(record['id'],{}).get('status')
        return records

    def pause(self,session_id):
        # Dialogue control only. A submitted computation is an independent
        # resource with its own identity/budget; stopping Codex never cancels it.
        with self.lock:
            self.workflow.pause(session_id)
        return self.workflow.publish(session_id)

    def focus(self,session_id,phase,reason,record_id=None):
        session=self.store.session(session_id)
        if phase not in PHASES or not isinstance(reason,str) or not reason.strip():
            raise ValueError('研究焦点需要有效阶段与具体理由')
        if record_id and not self.store.one('SELECT id FROM records WHERE id=? AND project_id=?',(record_id,session['project_id'])):
            raise ValueError('焦点记录必须属于当前研究项目')
        self.store.execute('INSERT INTO research_focus VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET phase=excluded.phase,reason=excluded.reason,record_id=excluded.record_id,updated_at=excluded.updated_at',
            (session['project_id'],phase,reason[:1500],record_id,now()))
        self.store.event(session_id,'research.focus',{'phase':phase,'label':PHASE_LABELS[phase],'reason':reason,'record_id':record_id})
        self.workflow.publish(session_id)
        return {'phase':phase,'label':PHASE_LABELS[phase],'saved':True}

    def plan(self,session_id,plan_id):
        session=self.store.session(session_id)
        plan=self.store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='plan'",(plan_id,session['project_id']))
        if not plan:
            raise ValueError('必须引用当前项目已经保存的具体方案版本')
        return plan

    def check_plan_evidence(self,plan):
        for evidence in json.loads(plan['evidence']):
            path=self.store.evidence_path(plan['project_id'],evidence['path'])
            if not path.is_file() or digest(path)!=evidence['sha256']:
                raise ValueError('方案引用的冻结证据已变化；保存新版本并重新审核，不能沿用旧批准')

    def evidence_integrity(self,record):
        """Explicit record inspection, not a hash scan on every UI poll."""
        try:
            self.check_plan_evidence(record)
        except (OSError,ValueError) as exc:
            return {'valid':False,'message':str(exc),'meaning':'记录原文保留；引用原件已变化或不可读，不能批准执行'}
        return {'valid':True,'checked_files':len(json.loads(record['evidence'])),
                'meaning':'仅核对当前引用字节；不认证科学结论或已获批准'}

    def review(self,session_id,data):
        plan=self.plan(session_id,data['plan_id']);decision=data['decision']
        if decision == 'accept' and not plan['version']:
            raise ValueError('候选方案须先通过 Critic 发版审查')
        if decision not in {'accept','reject'} or not str(data.get('reason','')).strip():
            raise ValueError('方案审核需要具体决定与理由')
        if self.store.session(session_id)['active_turn']:
            raise ValueError('先等待当前方案生成完成或暂停再审核')
        exact=plan_digest(plan)
        if decision=='accept':self.check_plan_evidence(plan)
        if data.get('expected_digest')!=exact:
            raise ValueError('待审核方案正文／版本已变化，请重新核对')
        actor=data.get('actor','用户')
        if actor not in {'用户','用户委托开发助手审批'}:
            raise ValueError('审核来源需明确，不能由研究模型自批')
        if decision=='accept' and data.get('autonomous') is True:
            self.autonomy.quiet(plan['project_id'])
            self.autonomy.normalize_scope(session_id,plan,data.get('research_scope'))
        grant=None;component_preview=None;dev_scope=None
        execution=data.get('execution','local')
        if decision=='accept':
            from .execution_request import require_route
            require_route(self.store,plan['project_id'],execution)
        from . import task_policy
        task_changes=task_policy.normalize(data.get('task_policy')) if decision=='accept' else None
        if task_changes and execution!='lpai':raise ValueError('任务变更授权当前仅支持LPAI固定组件，不能用于其他执行位置')
        if decision=='accept':
            compute=devspace.compute(plan)
            gpus=compute['gpu_count'] if compute else 0
            if execution=='local':
                if data.get('allow_local') is not True: raise ValueError('须明确允许本项目本次本地执行，不能静默替代LPAI')
                python=Path(data.get('python') or sys.executable).resolve()
                if not python.is_file() or 'python' not in python.name.lower(): raise ValueError('请选择已安装的真实Python解释器')
                device=data.get('local_device')
                if device is not None and (device not in {'cpu','gpu'} or device=='gpu' and local_device()['kind']!='gpu'):
                    raise ValueError('本地设备状态已变化，请重新核对')
                if gpus:raise ValueError('此方案要求 LPAI GPU；请选择 GPU 开发空间或组件库，不能在本地替代')
            elif execution=='lpai_devspace':
                if data.get('allow_cloud') is not True or self.store.session(session_id)['channel']!='ept' or not self.company:
                    raise ValueError('须明确批准当前企业会话使用绑定的 LPAI GPU 开发空间')
                if compute is None:
                    # Legacy native tool schemas cannot always express compute.
                    # Only the explicit formal approval may supply this limit;
                    # never infer authorization from prose or visible GPU count.
                    gpus=data.get('max_gpus')
                    if type(gpus) is not int or not 1<=gpus<=128:
                        raise ValueError('开发空间方案缺少GPU数量；需scope_proposal.compute或正式审核明确max_gpus，不能默认成0或全部可见卡')
                dev_scope=devspace.approval(self.company,plan['project_id'],data.get('space_uid'),data.get('ssh_port'),gpus)
                python=''
            elif execution=='lpai':
                if data.get('allow_cloud') is not True or self.store.session(session_id)['channel']!='ept': raise ValueError('需明确批准公司EPT会话的LPAI执行范围')
                if self.company and self.company.resource_directory.preference(session_id) and not data.get('component_preview_id'):
                    selected_component=self.company.training.sdk('component',component_uid=data.get('component_uid'))
                    self.company.resource_directory.require_component(session_id,selected_component)
                from .company import checked_id
                if data.get('component_preview_id'):
                    component_preview=self.store.one('SELECT * FROM research_component_previews WHERE id=? AND session_id=? AND plan_id=? AND plan_digest=?',
                        (data['component_preview_id'],session_id,plan['id'],exact))
                    if not component_preview:raise ValueError('当前自动创建模板只有 CPU 或缺少当前方案GPU配置预览；请重新核对')
                    frozen_preview=json.loads(component_preview['configuration']);template=frozen_preview['template']
                    if (data.get('component_uid') or data.get('component_digest') or data.get('max_gpus')!=template.get('gpu_count',0)
                            or gpus!=template.get('gpu_count',0)):
                        raise ValueError('新建组件需要当前方案的确切GPU配置预览，请重新核对')
                    if self.company:self.company.training.validate_preview(frozen_preview)
                    from .lpai_training import AUTO_COMMAND_PATHS,command_paths
                    paths=command_paths(template) if template.get('schema')==1 else AUTO_COMMAND_PATHS
                    if task_changes!={'schema':1,'allowed_patch_paths':paths}:
                        raise ValueError('新建组件需授权确切训练入口；不授权镜像、资源或挂载变更')
                    if data.get('autonomous') is not True:raise ValueError('自动创建并提交需要明确授权自主探索范围')
                else:
                    checked_id(data.get('component_uid'))
                if type(data.get('max_gpus')) is not int or not 0<=data['max_gpus']<=128 or not component_preview and (
                    not isinstance(data.get('component_digest'),str) or len(data['component_digest'])!=64):raise ValueError('云端审核需固定组件摘要及GPU上限')
                if gpus and not component_preview:
                    if not self.company or data['max_gpus']<gpus:raise ValueError('需核实所选 GPU 组件与批准的资源范围')
                    component=self.company.training.sdk('component',component_uid=data['component_uid'])
                    if (component.get('uid')!=data['component_uid'] or component.get('digest')!=data['component_digest']
                        or component.get('namespace')!=self.company.config()['namespace'] or component.get('gpu_count')!=gpus):
                        raise ValueError('组件实际 GPU 数量或固定配置与方案不符')
                python=''
            else:raise ValueError('未知执行位置，不回落本地')
            # Ordinary approvals have one task-attempt allowance. Explicit legacy
            # max_fits (including zero-fit approvals) remains backward compatible.
            runs=data.get('max_runs',100)
            fits,timeout=data.get('max_fits',runs),data.get('timeout',120)
            if any(type(x) is not int for x in [runs,fits,timeout]) or not 1<=runs<=1000 or not 0<=fits<=runs or not 1<=timeout<=(604800 if execution=='lpai' else 86400):
                raise ValueError('请设置明确运行/拟合次数和单次超时，拟合不能超过运行次数')
        review_id=uid()
        authority={'source':data.get('authority','界面具体方案审核'),'exact_version':plan['version'],'local_exception':decision=='accept' and execution=='local','execution':execution}
        if decision=='accept' and execution=='local' and data.get('local_device'):authority['local_device']=data['local_device']
        if dev_scope:authority['devspace']=dev_scope
        if component_preview:authority['component_preview_id']=component_preview['id']
        if task_changes:
            authority.update(task_policy=task_changes,task_policy_sha256=task_policy.fingerprint(task_changes))
        with self.lock,self.store.lock,self.store.db:
            if decision=='accept':rounds.check_approval(self.store,plan,new_scope_review=data.get('autonomous') is True and bool(data.get('research_scope')))
            if decision=='accept':
                prior=self.store.rows('SELECT request FROM research_runs WHERE project_id=?',(plan['project_id'],))
                used_fits=sum(json.loads(row['request']).get('purpose')=='fit' for row in prior)
                cloud=self.store.one("SELECT COUNT(*) AS n FROM company_training WHERE project_id=? AND state<>'prepared'",(plan['project_id'],)) if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else {'n':0}
                if runs<=len(prior)+cloud['n'] or fits<used_fits+cloud['n']:
                    raise ValueError('累计上限不能低于已用尝试；请明确本轮新增范围，旧消耗不会清零')
            if decision=='accept' and self.store.one("SELECT id FROM research_grants WHERE project_id=? AND plan_id=?",(plan['project_id'],plan['id'])):
                raise ValueError('此版本已有执行额度；重复审核不能重置或追加原预算')
            self.store.db.execute('INSERT INTO research_reviews VALUES(?,?,?,?,?,?,?,?,?)',
                (review_id,plan['project_id'],plan['id'],exact,decision,actor,data['reason'],dumps(authority),now()))
            if decision=='accept':
                grant=uid()
                self.store.db.execute('INSERT INTO research_grants VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (grant,plan['project_id'],plan['id'],exact,review_id,str(python),execution,runs,fits,timeout,now()))
                if component_preview:
                    self.store.db.execute('INSERT INTO research_component_provisions VALUES(?,?,?,?,?)',(grant,component_preview['id'],'approved',None,now()))
                elif execution=='lpai': self.store.db.execute('INSERT INTO research_cloud_scopes VALUES(?,?,?,?)',(grant,data['component_uid'],data['component_digest'],data['max_gpus']))
        self.store.event(session_id,'research.plan_reviewed',{'plan_id':plan['id'],'version':plan['version'],'decision':decision,'actor':actor,'grant':grant})
        if decision=='accept' and data.get('autonomous') is True:
            self.autonomy.enable(session_id,plan['id'],exact,data.get('research_scope'),data['reason'])
        elif decision=='accept':
            from .autonomy import policy,active
            if policy(self.store,plan['project_id']):
                branch=active(self.store,plan['project_id'])
                self.store.execute('UPDATE research_branches SET head_plan_id=? WHERE id=?',(plan['id'],branch['id']))
                self.store.execute("UPDATE research_autonomy_policies SET mode='manual',reason=?,updated_at=? WHERE project_id=?",('用户单版审核，未授权后继自主范围',now(),plan['project_id']))
        self.workflow.publish(session_id)
        return {'reviewed':True,'review_id':review_id,'plan_id':plan['id'],'version':plan['version'],'decision':decision,'grant_id':grant}

    def review_stop(self,session_id,review_id,decision,reason,actor='用户'):
        session=self.store.session(session_id)
        if decision not in {'accept_stop','reject_stop'} or not str(reason).strip() or actor not in {'用户','用户委托开发助手审批'}:
            raise ValueError('结束审核需要明确的人类决定、来源和理由')
        with self.lock,self.store.lock,self.store.db:
            review=self.store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='review'",(review_id,session['project_id']))
            if not review or json.loads(review['source']).get('round_outcome')!='stop_requested':
                raise ValueError('没有该项目的确切结束建议评审')
            closure=rounds.closure_details(self.store,review)
            if self.store.evidence.blocking(session['project_id'],verify_ready=True) and closure['stop_reason'] not in {'budget','no_progress','blocked','user_stop'}:
                raise ValueError('原运行证据尚未回收/校验，只能确认受限退出，不认证目标结论')
            if closure and not closure['requires_human_confirmation']:
                raise ValueError('本轮已自主收束，无需人工确认；新研究范围请另行明确审核')
            self.autonomy.unresolved(session['project_id'])
            plan_id=json.loads(review['source'])['reviewed_plan_id']
            latest=rounds.round_review(self.store,session['project_id'],plan_id)
            current=rounds.approved_plan(self.store,session['project_id'])
            if not latest or latest['id']!=review_id or not current or current['id']!=plan_id or self.store.one("SELECT id FROM sessions WHERE project_id=? AND active_turn IS NOT NULL",(session['project_id'],)) or self.store.one("SELECT id FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown')",(session['project_id'],)):
                raise ValueError('先等待当前执行结束并核对最新评审')
            previous=self.store.one('SELECT * FROM research_round_decisions WHERE review_record_id=? ORDER BY rowid DESC LIMIT 1',(review_id,))
            if previous and previous['decision']==decision and previous['reason']==reason and previous['actor']==actor:
                return {'review_id':review_id,'decision':decision,'decision_id':previous['id'],'recorded':True,'changed':False}
            id=uid();self.store.db.execute('INSERT INTO research_round_decisions VALUES(?,?,?,?,?,?)',(id,review_id,decision,actor,reason,now()))
        self.workflow.publish(session_id)
        return {'review_id':review_id,'decision':decision,'decision_id':id,'recorded':True,'changed':True}

    def verify_frozen_files(self,session_id):
        scope=self.autonomy.status(session_id).get('scope',{})
        results=[]
        for ref in scope.get('frozen_files',[]):
            try:
                actual=self._paths(session_id,[ref['path']])
                observed=next(iter(actual.values()))['sha256']
                state='matched' if observed==ref['sha256'] else 'changed'
            except (ValueError,OSError):
                observed=None
                state='unavailable'
            results.append({'path':ref['path'],'state':state,'expected_sha256':ref['sha256'],'observed_sha256':observed})
        return results

    def frozen_evidence(self,session_id,record):
        authority=self.autonomy.status(session_id)
        if not authority.get('authority_id'):return None
        refs={ref['path']:ref for ref in json.loads(record['evidence'])}
        expected={ref['path']:ref for ref in authority.get('scope',{}).get('frozen_files',[])}
        files=self.verify_frozen_files(session_id)
        for item in files:
            ref=refs.get(item['path'])
            item['candidate_reference']='missing' if ref is None else 'matched' if ref==expected[item['path']] else 'changed'
        return {'authority_id':authority['authority_id'],'scope_digest':authority['scope_digest'],
                'files':files,'bytes_match_authority':all(item['state']=='matched' for item in files),
                'candidate_covers_authority':all(item['candidate_reference']=='matched' for item in files),
                'meaning':'程序只读核验实际文件字节与原授权、候选引用；不改正文、授权或审批。以逐项状态核对，不手工转录哈希。文件一致不表示科学方案已通过。'}

    def _paths(self,session_id,files,max_bytes=512*1024*1024):
        session=self.store.session(session_id)
        if not isinstance(files,list) or not files or len(files)>150:
            raise ValueError('列出运行所需的实际文件，最多150个；目录不会隐式整包复制')
        output={};total=0
        for name in files:
            if not isinstance(name,str) or Path(name).is_absolute() or not name or '..' in Path(name).parts:
                raise ValueError('运行输入须为项目内明确的相对文件路径')
            if any(p.lower() in {'.git','.ssh','.aws','.codex','credentials.json','secrets.json','id_rsa','id_ed25519'} or p.lower().startswith('.env') for p in Path(name).parts):
                raise ValueError('运行输入不能包含凭据目录')
            path=self.store.safe_path(session['project_id'],name)
            if not path.is_file():
                raise ValueError('运行输入不存在：'+name)
            total+=path.stat().st_size
            if total>max_bytes:
                raise ValueError(f'本次明确输入超过执行器传输范围（{max_bytes//1024//1024}MiB）；请使用独立数据传输或已核实的远端数据，不改训练位置')
            output[Path(name).as_posix()]={'path':path,'sha256':digest(path),'size':path.stat().st_size}
        return output

    def write(self,session_id,path,text):
        session=self.store.session(session_id)
        if not isinstance(text,str) or len(text.encode('utf-8'))>512*1024:
            raise ValueError('单次文本写入上限512KiB')
        if not isinstance(path,str) or Path(path).is_absolute() or not path or '..' in Path(path).parts:
            raise ValueError('需要当前项目内的相对文件路径')
        if any(p.lower() in {'runs','research-evidence','.git','.ssh','.aws','.codex','credentials.json','secrets.json','id_rsa','id_ed25519'} or p.lower().startswith('.env') for p in Path(path).parts):
            raise ValueError('不能覆盖运行证据或凭据目录')
        target=self.store.safe_path(session['project_id'],path)
        if any(p.lower() in {'research-contracts','research-comparisons'} for p in Path(path).parts):raise ValueError('不能覆盖程序管理的评价合同或核验报告')
        relative=target.relative_to(Path(session['workspace']).resolve()).as_posix().lower()
        if relative.startswith(('research/lpai-outputs/','research/cloud-results/','platform/lpai/evidence/','research-evidence/','runs/','research-contracts/','research-comparisons/')):
            raise ValueError('不能覆盖程序管理的运行原件、证据或合同')
        with self.lock:
            for row in self.store.rows('''SELECT id,version,evidence FROM records r WHERE project_id=? AND kind='plan' AND
                (EXISTS(SELECT 1 FROM research_grants g WHERE g.plan_id=r.id) OR
                 EXISTS(SELECT 1 FROM research_plan_admissions a WHERE a.plan_id=r.id))''',(session['project_id'],)):
                for ref in json.loads(row['evidence']):
                    if self.store.safe_path(session['project_id'],ref['path'])==target:
                        raise ValueError(f"{ref['path']} 已由方案{row['version']}冻结。保留原件，改动另存新的工作文件；若需要改冻结方案内容，先明确修订。")
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.'+uid()+'.tmp')
            temporary.write_text(text,encoding='utf-8');temporary.replace(target)
            result={'path':path,'sha256':digest(target),'bytes':target.stat().st_size,'encoding':'utf-8'}
            from .preparation_checks import check_text
            checked=check_text(path,text)
            if checked is not None:result['preparation_check']=checked
        self.store.event(session_id,'research.file_written',result)
        return result

    def run(self,session_id,request,operation_id):
        session=self.store.session(session_id)
        if {'execution','local_device','python','space_uid','ssh_port','host','component_uid'} & request.keys():
            raise ValueError('运行地点、设备和解释器由已确认的方案授权固定，执行请求不能覆盖')
        fingerprint=hashlib.sha256(dumps(request).encode()).hexdigest()
        with self.lock,self.store.lock:
            prior=self.store.one('SELECT * FROM research_runs WHERE operation_id=?',(operation_id,))
            if prior:
                if prior['request_hash']!=fingerprint or prior['session_id']!=session_id:
                    raise ValueError('原运行身份不能改参数或转给其他会话')
                return json.loads(prior['receipt']) if prior['receipt'] else {'run_id':prior['id'],'state':prior['state'],'resubmitted':False}
            if self.store.evidence.blocking(session['project_id'],verify_ready=True):
                raise ValueError('原运行的证据尚未回收/校验，先核对research_status；不能跳过证据另起实验')
            from .autonomy import finalizing
            if finalizing(self.store,session['project_id']):
                raise ValueError('本方案已进入只读收尾，普通讨论不授权重新计算；新范围须明确审核')
            if self.closing:
                raise ValueError('服务正在关闭，没有开始新运行')
            if self.workflow.paused(session_id):
                raise ValueError('用户已暂停；先等待新的用户输入，不由后台重开研究')
            plan=self.plan(session_id,request['plan_id'])
            grant=self.execution_grant(plan)
            execution=grant['execution']
            from .execution_request import require_route
            require_route(self.store,session['project_id'],execution)
            if execution not in {'local','lpai_devspace'}:
                raise ValueError('本方案批准的是 LPAI 组件库云端训练。请使用 lpai_prepare 和 company_action，不能用本地工具代跑。')
            review=self.store.one('SELECT authority FROM research_reviews WHERE id=?',(grant['review_id'],))
            authority=json.loads(review['authority']) if review else {}
            dev_scope=authority.get('devspace') if execution=='lpai_devspace' else None
            if execution=='lpai_devspace' and (not dev_scope or not self.company or session['channel']!='ept'):
                raise ValueError('GPU 开发空间授权缺失，未启动计算，不回落本地')
            if dev_scope:
                bound = devspace.binding(self.store, session['project_id'])
                if not bound or (bound['uid'], bound['scope']) != (dev_scope['uid'], dev_scope['namespace']):
                    raise ValueError('项目绑定已变化，请核对并重新审核开发空间执行范围')
                profile = devspace.ssh_profile(self.store, session['project_id'], bound)
                if not profile:
                    raise ValueError('请先在项目执行页配置并验证开发空间 SSH')
            if rounds.round_review(self.store,session['project_id'],plan['id']):
                raise ValueError('该方案轮次已进入收尾评审，不能借旧批准继续运行；下一方案须用户审核')
            self.check_plan_evidence(plan)
            from .research_modules import ResearchModules
            ResearchModules(self.store).preflight(session_id,plan,request.get('purpose','diagnostic'))
            latest_review=self.store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan['id'],))
            from .autonomy import admitted
            delegated=grant['plan_id']!=plan['id']
            if not grant or not delegated and (not latest_review or latest_review['decision']!='accept' or grant['plan_digest']!=plan_digest(plan)):
                raise ValueError('该具体方案尚未通过有效审核；请用户在方案审核入口确认，不能自批或绕过')
            if self.store.one("SELECT id FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown')",(session['project_id'],)):
                raise ValueError('当前项目有运行中或结果未知的原实验，先核对，不另起替代任务')
            if rounds.pending_platform_operation(self.store,session['project_id']):
                raise ValueError('当前项目有未决平台操作，先核对原身份，不另起本地替代计算')
            if rounds.pending_cloud(self.store,session['project_id']):
                raise ValueError('当前项目有在途/未知云任务，先核对原身份，不另起本地替代计算')
            from .task_outputs import require_collected
            require_collected(self.store,session['project_id'])
            used=self.store.rows('SELECT request FROM research_runs WHERE project_id=?',(session['project_id'],))
            from .evaluation_contract import load as load_evaluation_contract
            evaluation=load_evaluation_contract(self.store,plan)
            execution_files=request.get('files',[])
            if evaluation:
                if not isinstance(execution_files,list):raise ValueError('运行输入需为明确文件列表')
                execution_files=list(dict.fromkeys([*execution_files,evaluation['reference']['path'],*[p['path'] for p in evaluation['protocols']]]))
            # Earlier plans' consumption remains visible and counts in this project grant boundary.
            cloud_count=self.store.one("SELECT COUNT(*) AS n FROM company_training WHERE project_id=? AND state<>'prepared'",(session['project_id'],))['n'] if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else 0
            fits=sum(json.loads(r['request']).get('purpose')=='fit' for r in used)+cloud_count
            purpose=request.get('purpose')
            if purpose not in {'fit','evaluation','diagnostic'}:
                raise ValueError('明确声明拟合、评估或无拟合诊断；不得以诊断名义绕过拟合次数')
            if len(used)+cloud_count>=grant['max_runs'] or purpose=='fit' and fits>=grant['max_fits']:
                raise ValueError('本项目已达本次批准的累计运行／拟合边界；未清零旧消耗')
            if delegated:
                scope=self.autonomy.status(session_id)['scope']
                if any(ref['path'] not in execution_files for ref in scope['frozen_files']):raise ValueError('执行快照需包含研究授权固定的评价/数据边界文件')
            if not str(request.get('question','')).strip() or not str(request.get('expected_information','')).strip():
                raise ValueError('运行应说明本轮问题及预期区分什么解释')
            timeout=request.get('timeout',grant['timeout'])
            if type(timeout) is not int or not 1<=timeout<=grant['timeout']:
                raise ValueError('运行超时不能超出批准范围')
            files=self._paths(session_id,execution_files,devspace.MAX_INPUT_BYTES if dev_scope else 512*1024*1024);script=Path(request['script']).as_posix()
            if script not in files or not script.endswith('.py'):
                raise ValueError('Python训练入口须包含在确切输入快照内')
            args=request.get('args',[])
            if not isinstance(args,list) or len(args)>80 or any(not isinstance(x,str) or len(x)>1500 for x in args):
                raise ValueError('脚本参数需为明确字符串数组')
            outputs=request.get('outputs',[])
            if not isinstance(outputs,list) or len(outputs)>50 or any(not isinstance(x,str) or Path(x).is_absolute() or '..' in Path(x).parts for x in outputs):
                raise ValueError('输出应为运行目录内明确的相对文件路径')
            if purpose=='fit' and not outputs:
                raise ValueError('拟合需声明实际待回收的结果产物，不能只报告退出码')
            from .run_evidence import relative as evidence_name
            if any(evidence_name(name) in {'receipt.json','input-manifest.json','stdout.log','stderr.log'} for name in outputs):
                raise ValueError('输出不能占用程序管理的收据、输入清单或进程日志文件名')
            if set(outputs)&set(files):
                raise ValueError('声明输出不能覆盖冻结输入')
            if dev_scope and (any(evidence_name(x)!=x or x.split('/')[0] in devspace.RESERVED or any(p.startswith('.iterlab-') for p in Path(x).parts) for x in [*files,*outputs])
                  or sum(item['size'] for item in files.values())>devspace.MAX_INPUT_BYTES):
                raise ValueError('开发空间输入需使用普通相对路径，合计不超过 2 GiB；更大数据单独传输，不改训练位置')
            trial_id=request.get('trial_id')
            basis_record_id=None
            if trial_id and not self.store.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind='trial'",(trial_id,session['project_id'])):
                raise ValueError('实验关联必须是当前项目的明确trial记录')
            if trial_id:
                linked=self.store.one('SELECT r.plan_id FROM research_runs r JOIN research_run_context c ON c.run_id=r.id WHERE c.trial_id=? AND r.plan_id<>? LIMIT 1',(trial_id,plan['id']))
                trial=self.store.one('SELECT source FROM records WHERE id=?',(trial_id,))
                declared=json.loads(trial['source']).get('plan_id')
                basis_record_id=json.loads(trial['source']).get('basis_record_id')
                if linked or declared and declared!=plan['id']:
                    raise ValueError('实验已归属其他具体方案，不能借新方案执行；请保存本轮实验并引用旧证据')
            dependencies=rounds.check_trial_dependencies(self.store,session['project_id'],plan['id'],trial_id)
            if dev_scope:
                target = {**dev_scope, 'port': profile['port']}
                ssh = devspace.connection(self.company, session['project_id'], target)
                devspace.check_peer(ssh, target)
                if purpose=='fit':
                    try: readiness=devspace.check_compute(ssh,target)
                    except ValueError as error:
                        self.store.event(session_id,'research.environment_blocked',{'plan_id':plan['id'],
                            'trial_id':trial_id,'execution':'lpai_devspace','reason':'cuda_preflight_failed',
                            'readiness':getattr(error,'report',{'ready':False,'reason':'unconfirmed'}),'training_submitted':False})
                        raise
                    self.store.event(session_id,'research.environment_checked',{'plan_id':plan['id'],
                        'trial_id':trial_id,'execution':'lpai_devspace','readiness':readiness})
            run_id=uid();relative='runs/'+run_id;directory=self.store.safe_path(session['project_id'],relative)
            stamp=now()
            self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (run_id,operation_id,session['project_id'],session_id,plan['id'],grant['id'],fingerprint,dumps(request),'requested',relative,None,stamp,stamp))
            self.store.execute('INSERT INTO research_run_context VALUES(?,?,?)',(run_id,trial_id,stamp))
            self.store.evidence.register_local(self.store.one('SELECT * FROM research_runs WHERE id=?',(run_id,)))
            self.workflow.publish(session_id)
        start=time.monotonic();process=None;job=None;state='failed';exit_code=None;error=None
        process_started=None;process_seconds=None;timeout_observed=False;ownership_errors=[]
        phases={}
        def mark(name,started):
            phases[name]=round(time.monotonic()-started,6)
        def release_owned():
            nonlocal job,state,error
            if job is None:return
            started=time.monotonic()
            try:job.close()
            except (OSError,ValueError,subprocess.TimeoutExpired):
                ownership_errors.append('原自有进程树的清理未确认，不启动替代实验')
                state='unknown';error=ownership_errors[-1]
            finally:
                job=None;mark('process_cleanup_seconds',started)
        phase_start=start
        manifest={name:{k:v for k,v in item.items() if k!='path'} for name,item in files.items()}
        try:
            directory.mkdir(parents=True,exist_ok=False)
            for name,item in files.items():
                target=directory/name;target.parent.mkdir(parents=True,exist_ok=True)
                with item['path'].open('rb') as inp,target.open('xb') as out:
                    for part in iter(lambda:inp.read(1024*1024),b''):
                        out.write(part)
                if digest(target)!=item['sha256']:
                    raise ValueError('输入在快照时发生变化，未启动计算')
            (directory/'input-manifest.json').write_text(dumps(manifest),encoding='utf-8')
            mark('input_snapshot_seconds',phase_start)
            if dev_scope:
                # Reserve once, return after a short SSH launch acknowledgement.
                # The persistent monitor owns status/collection independently of
                # the native turn, and never launches this run a second time.
                with self.lock:
                    if self.workflow.paused(session_id):raise InterruptedError('用户暂停，尚未启动计算')
                    run=self.store.one('SELECT * FROM research_runs WHERE id=?',(run_id,))
                return self.remote.launch(run,{**dev_scope,'port':profile['port']},manifest,
                    {'timeout':timeout,'inputs':manifest,'plan_version':plan['version'],'trial_id':trial_id,
                     'basis_record_id':basis_record_id,'dependencies':dependencies},ssh)
            env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'}
            if authority.get('local_device')=='cpu':env['CUDA_VISIBLE_DEVICES']=''
            with ExitStack() as stack:
                stderr=stack.enter_context((directory/'stderr.log').open('wb'))
                options={}
                if dev_scope:
                    command=[*ssh,devspace.stage(directory,run_id,dev_scope,manifest,script,args,outputs,timeout)]
                    options['stdin']=stack.enter_context((directory/'.iterlab-input.tar').open('rb'))
                    stdout=stack.enter_context((directory/'.iterlab-response.json').open('xb'))
                else:
                    command=[grant['python'],'-B',script,*args]
                    stdout=stack.enter_context((directory/'stdout.log').open('wb'))
                with self.lock:
                    if self.workflow.paused(session_id):raise InterruptedError('用户暂停，尚未启动计算')
                    process_started=time.monotonic()
                    process=subprocess.Popen(command,cwd=directory,env=env,stdout=stdout,stderr=stderr,**options,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
                    mark('process_spawn_seconds',process_started)
                    ownership_start=time.monotonic()
                    job=OwnedJob(process)
                    mark('process_ownership_seconds',ownership_start)
                    self.processes[run_id]=process
                    self.jobs[process.pid]=job
                self.store.execute("UPDATE research_runs SET state='running',updated_at=? WHERE id=?",(now(),run_id))
                self.store.event(session_id,'research.run_started',{'run_id':run_id,'plan_id':plan['id'],'purpose':purpose,'timeout':timeout})
                self.workflow.publish(session_id)
                wait_started=time.monotonic()
                try:
                    exit_code=process.wait(timeout=timeout+120 if dev_scope else timeout)
                    state='completed' if exit_code==0 else 'failed'
                except subprocess.TimeoutExpired:
                    timeout_observed=True
                    mark('wait_seconds',wait_started)
                    termination_started=time.monotonic()
                    self._stop(process);exit_code=process.returncode;state='timed_out'
                    mark('termination_seconds',termination_started)
                    error=f'计算等待超过批准的{timeout}秒上限，本次尝试已计入累计；即使随后退出码为0，也未按合格成功处理。'
                finally:
                    if 'wait_seconds' not in phases:mark('wait_seconds',wait_started)
                process_seconds=round(time.monotonic()-process_started,3)
                if run_id in self.cancelled:state='cancelled'
            # End the owned tree before hashing inputs/results: a parent exit
            # alone does not stop a surviving writer or background computation.
            release_owned()
            if dev_scope and process is not None:
                if state=='completed':
                    state,exit_code=devspace.collect(directory,run_id,dev_scope['uid'],outputs)
                    timeout_observed=state=='timed_out'
                else:
                    state='unknown';exit_code=None
                    error='开发空间终态未确认，保留原运行身份；不得本地代跑或重复提交'
            changed=[]
            for name,item in manifest.items():
                try:
                    path=directory/name
                    if path.is_symlink() or path.resolve()!=path or not path.is_file() or digest(path)!=item['sha256']:changed.append(name)
                except (OSError,ValueError):changed.append(name)
            if changed and state!='unknown':
                state='invalid_inputs';error='运行改动了冻结输入或无法核对原输入：'+', '.join(changed[:5])
        except Exception as exc:
            if dev_scope and self.store.one('SELECT run_id FROM devspace_jobs WHERE run_id=?',(run_id,)):
                # A launch intent may have crossed the network even if later
                # local collection failed. Never publish a false failed launch.
                self.remote._defer(run_id,'原远端运行待后台核对；没有重放或本地代跑')
                return {'run_id':run_id,'state':'unknown','execution':'lpai_devspace','resubmitted':False}
            error=str(exc)
            if isinstance(exc,InterruptedError):state='cancelled'
            if process and process.poll() is None:
                try:self._stop(process)
                except (OSError,subprocess.TimeoutExpired):
                    state='unknown';error='原计算进程的终止未确认，保留原运行身份，不启动替代计算'
            if process is not None and process_started is not None and process_seconds is None:
                process_seconds=round(time.monotonic()-process_started,3)
            if dev_scope and process is not None:
                state='unknown';exit_code=None
                error='开发空间终态或产物回执未确认，保留原运行身份；不得本地代跑或重复提交'
        finally:
            release_owned()
            with self.lock:
                self.processes.pop(run_id,None)
                if process:self.jobs.pop(process.pid,None)
        artifact_started=time.monotonic()
        checked=[];artifact_errors=[]
        missing=[];empty=[]
        for name in outputs:
            try:
                original=directory/name;path=original.resolve()
                if original.is_symlink() or path!=original or not path.is_relative_to(directory) or not path.is_file():
                    missing.append(name);continue
                size=path.stat().st_size
                checked.append({'path':relative+'/'+name,'sha256':digest(path),'size':size})
                if size==0:empty.append(name)
            except (OSError,ValueError):
                missing.append(name)
                artifact_errors.append({'path':name,'reason':'声明产物无法读取或校验，原运行不重放'})
        if state=='completed' and (missing or empty):
            state='artifacts_missing';error='进程退出0，但声明产物缺失或为空：'+', '.join((missing+empty)[:5])
        mark('artifact_verification_seconds',artifact_started)
        receipt={'run_id':run_id,'plan_id':plan['id'],'plan_version':plan['version'],'state':state,'exit_code':exit_code,
            'seconds':round(time.monotonic()-start,3),'directory':relative,'purpose':purpose,'outputs':checked,
            'inputs':manifest,'python':grant['python'],'threads':1,'error':error,'missing_outputs':missing,'empty_outputs':empty,'trial_id':trial_id,
            'timing':{'total_wall_seconds':round(time.monotonic()-start,3),'process_wall_seconds':process_seconds,'wait_limit_seconds':timeout,'timeout_observed':timeout_observed,
                      'limit_scope':('remote_training_only; upload and receipt transfer are separate' if dev_scope else
                          'process_wait; spawn/ownership/collection reported separately, not a total-wall guarantee'),'phases':phases},
            'artifact_errors':artifact_errors,'ownership_errors':ownership_errors,'verified_dependencies':dependencies,
            'research_quality':'未判定；退出0不等于研究目标通过'}
        if basis_record_id:receipt['basis_record_id']=basis_record_id
        if dev_scope:
            receipt.update(execution='lpai_devspace',devspace={key:dev_scope[key] for key in ('uid','name','namespace','gpus')},
                           remote_directory='/lpai/iterlab-runs/'+run_id)
        # The process is terminal even when the workspace cannot accept a copy.
        # Persist the original result first; disk failures must not resurrect an
        # in-flight run or permit a second fit with the same operation ID.
        self.store.execute('UPDATE research_runs SET state=?,receipt=?,updated_at=? WHERE id=?',(state,dumps(receipt),now(),run_id))
        try:
            directory.mkdir(parents=True,exist_ok=True)
            (directory/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:
            self.store.event(session_id,'research.receipt_copy_failed',{'run_id':run_id,'state':state,'reason':'本地收据副本写入失败；原数据库收据保持，分析阻塞，不重放运行'})
        try:self.store.evidence.collect_local(session_id,run_id)
        except (ValueError,OSError):
            # Computation and its immutable receipt remain terminal. Only result
            # collection is blocked; the next background pass retries no training.
            self.store.execute("UPDATE run_evidence SET state='failed',analysis_ready=0,next_check=0,updated_at=? WHERE location='local' AND run_id=? AND state<>'corrupt'",(now(),run_id))
        self.store.event(session_id,'research.run_finished',{'run_id':run_id,'state':state,'receipt':relative+'/receipt.json'})
        if state!='unknown' and not self.workflow.paused(session_id):
            self.focus(session_id,'assess','本次运行已有终态，需根据实际日志与产物分析；工程终态不代表研究结论',trial_id or plan['id'])
        self.workflow.publish(session_id)
        self.cancelled.discard(run_id)
        return receipt

    def cloud_scopes(self,project_id):
        from .task_policy import from_grant
        result=self.store.rows('SELECT s.* FROM research_cloud_scopes s JOIN research_grants g ON g.id=s.grant_id WHERE g.project_id=?',(project_id,))
        for row in result:
            row['task_policy']=from_grant(self.store,self.store.one('SELECT * FROM research_grants WHERE id=?',(row['grant_id'],)))
        return result

    def resources(self,plan):
        """One cumulative resource view, shared by scheduler and execution."""
        grant=self.execution_grant(plan)
        requests=self.store.rows('SELECT request FROM research_runs WHERE project_id=?',(plan['project_id'],))
        cloud=self.store.one("SELECT COUNT(*) AS n FROM company_training WHERE project_id=? AND state<>'prepared'",(plan['project_id'],))['n'] if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else 0
        used_runs=len(requests)+cloud
        used_fits=sum(json.loads(r['request']).get('purpose')=='fit' for r in requests)+cloud
        from .task_policy import from_grant
        cloud_scope=self.store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant['id'],))
        execution_scope={'execution':grant['execution'],'max_runs':grant['max_runs'],'max_fits':grant['max_fits'],
                         'single_attempt_seconds':grant['timeout'],'task_policy':from_grant(self.store,grant),
                         'cloud':cloud_scope,'meaning':'原累计范围；云预计时长不是远端强制停机或精确费用上限'}
        provision=self.store.one('''SELECT p.state,p.component_uid,v.configuration FROM research_component_provisions p
                                    JOIN research_component_previews v ON v.id=p.preview_id WHERE p.grant_id=?''',(grant['id'],))
        if provision:
            frozen=json.loads(provision.pop('configuration'))
            execution_scope['component_provision']={**provision,'namespace':frozen['identity']['namespace'],
                'name':frozen['template']['config']['name'],'volume':frozen['volume'],'max_gpus':frozen['template'].get('gpu_count',0)}
        review=self.store.one('SELECT authority FROM research_reviews WHERE id=?',(grant['review_id'],))
        authority=json.loads(review['authority']) if review else {}
        device=authority.get('local_device')
        if device:execution_scope['local_device']=device
        if authority.get('devspace'):execution_scope['devspace']=authority['devspace']
        from . import budgets
        return {'execution_scope':execution_scope,'budget':{**budgets.status(self.store,grant),'plan_id':plan['id'],'grant_id':grant['id']},
                'used_runs':used_runs,'used_fits':used_fits,'remaining_runs':max(0,grant['max_runs']-used_runs),
                'remaining_fits':max(0,grant['max_fits']-used_fits),'computation_exhausted':used_runs>=grant['max_runs'],
                'fit_exhausted':used_fits>=grant['max_fits'],'budget_reset':False}

    def change_budget(self,sid,plan_id,grant_id,revision,add_runs=0,add_fits=None,auto=False,hard_runs=None,hard_fits=None):
        from . import budgets
        from .autonomy import finalizing,policy
        with self.lock,self.store.lock:
            session=self.store.session(sid);pid=session['project_id'];owner=policy(self.store,pid)
            if session['archived'] or self.store.one("SELECT 1 FROM workbench_trash WHERE kind='project' AND object_id=?",(pid,)) or owner and owner['owner_session_id']!=sid:
                raise ValueError('请在拥有当前研究授权的有效会话中调整预算')
            plan=rounds.approved_plan(self.store,pid)
            if not plan or plan['id']!=plan_id:raise ValueError('活动方案已变化，请重新读取预算')
            review=rounds.round_review(self.store,pid,plan_id)
            if finalizing(self.store,pid) or review and json.loads(review['source']).get('round_outcome','continue')!='continue':
                raise ValueError('该轮已有收尾评审，请在后继方案上确认新额度，历史累计保留')
            grant=self.execution_grant(plan)
            if grant['id']!=grant_id:raise ValueError('原研究授权已变化，请重新读取预算')
            if add_fits is None:
                old=budgets.status(self.store,grant)
                # One visible control for submissions. Retain internal fit facts
                # and an explicit zero-fit boundary, never reset either counter.
                if type(add_runs) is not int or not 0<=add_runs<=budgets.LIMIT:raise ValueError('增加任务次数须为有效整数')
                original=self.store.one('SELECT source FROM records WHERE id=?',(grant['plan_id'],))
                proposal=json.loads(original['source']).get('scope_proposal') or {}
                diagnostic=(proposal.get('budget') or {}).get('additional_fits')==0
                can_fit=old['max_fits']>0 and not diagnostic
                add_fits=old['max_runs']+add_runs-old['max_fits'] if can_fit else 0
                hard_fits=hard_runs if can_fit else (old['max_fits'] if hard_runs is not None else None)
            saved=budgets.change(self.store,grant,revision,add_runs,add_fits,auto,hard_runs,hard_fits,'user_budget_update')
            if saved['revision']!=revision:
                self.store.execute('UPDATE research_scheduler_status SET next_check=0 WHERE session_id=?',(sid,))
                self.store.event(sid,'research.budget_changed',{'plan_id':plan_id,**saved})
            return {'saved':True,'budget':saved,'paused':self.workflow.paused(sid)}

    def execution_grant(self,plan):
        from .autonomy import execution_grant
        return execution_grant(self.store,plan)

    def _stop(self,process):
        if process.poll() is not None:
            return
        if os.name=='nt':
            job=self.jobs.get(process.pid)
            if job:job.terminate()
            else:process.kill()
        else:
            os.killpg(process.pid,signal.SIGKILL)
        process.wait(timeout=10)

    def close(self):
        with self.lock:
            self.closing=True;processes=list(self.processes.values())
        for process in processes:
            self._stop(process)
