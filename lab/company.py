"""Optional company adapters over official local CLIs; no legacy app dependency.

Only identifiers and public platform state are projected to Codex. Credentials
remain owned by lpai-agent/glab/Git. No browser-cookie extraction or shell eval.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import quote, urlsplit

from .store import dumps, now

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z')
HOST = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z')
TERMINAL = {'succeeded', 'completed', 'success', 'failed', 'stopped', 'terminated', 'cancelled','canceled','error'}


def checked_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('需要有效的平台名称／UID，不接受命令或凭据')
    return value


def redact(text):
    text=re.sub(r'(https?://)[^\s/@]+@', r'\1[redacted]@', str(text))
    text=re.sub(r'\b(?:glpat|glrt|gldt|gloas)-[A-Za-z0-9_-]+','[redacted]',text)
    text=re.sub(r'\blpak_[A-Za-z0-9_-]+','[redacted]',text)
    return re.sub(r'(?im)((?:authorization|access[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*)[^\n]+', r'\1[redacted]', text)


def locate(name):
    path=shutil.which(name)
    if path and name=='glab':
        # Resolve Scoop's executable shim to avoid allocating a helper console.
        candidate=Path(path).parent.parent/'apps/glab/current/bin/glab.exe'
        if candidate.is_file():
            path=str(candidate)
    return path


def platform_item(raw, namespace, kind):
    if not isinstance(raw,dict):
        raise ValueError('官方平台对象格式未确认')
    metadata=raw.get('metadata') or {}
    status=raw.get('status') or {}
    name=checked_id(metadata.get('name') or raw.get('name'))
    uid=checked_id(metadata.get('uid') or raw.get('uid') or str(raw.get('id') or ''))
    scope=metadata.get('namespace') or raw.get('namespace') or namespace
    if scope!=namespace:
        raise ValueError('平台返回了其他工作空间的对象')
    phase=status.get('phase') if isinstance(status,dict) else status
    phase=phase or raw.get('phase') or 'unknown'
    if not isinstance(phase,str) or len(phase)>80:
        phase='unknown'
    resources=(raw.get('spec') or {}).get('resources') or {}
    safe_resources={key:value for key,value in resources.items() if key in {'cpu','memory','gpu'} and isinstance(value,(int,float,str)) and len(str(value))<30} if isinstance(resources,dict) else {}
    url=(f'https://lpai.lixiang.com/lpaiweb/xspacex_{namespace}/subApp/devspace/jupyter/detail/{quote(name)}' if kind=='space' else
         f'https://lpai.lixiang.com/lpaiweb/xspacex_{namespace}/subApp/components/detail/{quote(uid)}' if kind=='component' else
         f'https://lpai.lixiang.com/lpaiweb/xspacex_{namespace}/subApp/components/list')
    endpoint=None
    if kind=='space':
        from .devspace import command_port,HOST
        for access in (raw.get('spec') or {}).get('accesses',[]):
            if not isinstance(access,dict) or access.get('targetPort')!=22:continue
            try:port=command_port(access.get('address'))
            except ValueError:continue
            endpoint={'host':HOST,'user':'root','port':port,'source':'official_devspace_access'}
            break
    return {'name':name,'uid':uid,'namespace':scope,'phase':phase,'resources':safe_resources,'url':url,'observed_at':now(),'kind':kind,
            **({'ssh':endpoint} if endpoint else {}),
            'project_uid':raw.get('project_uid'),'created_at':raw.get('create_timestamp'),
            **({key:raw[field] for key,field in [('started_at','start_timestamp'),('finished_at','finish_timestamp')]
               if field in raw} if kind=='task' else {})}


class Company:
    def __init__(self,store,runner=subprocess.run,locator=locate):
        self.store,self.runner,self.locator=store,runner,locator
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.monitor_thread=None
        with store.lock,store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS company_config(singleton INTEGER PRIMARY KEY,revision INTEGER NOT NULL,content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_bindings(project_id TEXT NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,scope TEXT NOT NULL,
              uid TEXT NOT NULL,name TEXT NOT NULL,observation TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(project_id,kind));
            CREATE TABLE IF NOT EXISTS company_space_ssh(
                project_id TEXT NOT NULL REFERENCES projects(id), scope TEXT NOT NULL, uid TEXT NOT NULL,
                port INTEGER NOT NULL, verified_at TEXT NOT NULL, PRIMARY KEY(project_id,scope,uid));
            CREATE TABLE IF NOT EXISTS company_space_ssh_checks(
                project_id TEXT NOT NULL,scope TEXT NOT NULL,uid TEXT NOT NULL,port INTEGER NOT NULL,
                state TEXT NOT NULL,gpu_count INTEGER,verified_at TEXT,message TEXT NOT NULL,
                PRIMARY KEY(project_id,scope,uid));
            CREATE TABLE IF NOT EXISTS company_space_ssh_identities(
                project_id TEXT NOT NULL,scope TEXT NOT NULL,uid TEXT NOT NULL,identity_path TEXT NOT NULL,
                PRIMARY KEY(project_id,scope,uid));
            CREATE UNIQUE INDEX IF NOT EXISTS company_space_exclusive ON company_bindings(scope,uid) WHERE kind='lpai';
            CREATE TABLE IF NOT EXISTS company_operations(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,request_hash TEXT NOT NULL,request TEXT NOT NULL,
              state TEXT NOT NULL,result TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_watches(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,namespace TEXT NOT NULL,task_uid TEXT NOT NULL,
              task_name TEXT NOT NULL,state TEXT NOT NULL,observation TEXT NOT NULL,auto_resume INTEGER NOT NULL DEFAULT 0,
              next_check REAL NOT NULL DEFAULT 0,wake_state TEXT NOT NULL DEFAULT 'none',created_at TEXT NOT NULL,UNIQUE(session_id,task_uid));
            CREATE TABLE IF NOT EXISTS company_watch_repairs(submission_id TEXT PRIMARY KEY,state TEXT NOT NULL,
              next_check REAL NOT NULL,failures INTEGER NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_watch_health(watch_id TEXT PRIMARY KEY,last_success REAL,
              last_failure REAL,failures INTEGER NOT NULL,error_type TEXT,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_monitor_health(singleton INTEGER PRIMARY KEY,last_success REAL,
              last_failure REAL,failures INTEGER NOT NULL,error_type TEXT,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_unknown_reads(submission_id TEXT PRIMARY KEY,state TEXT NOT NULL,
              next_check REAL NOT NULL,attempts INTEGER NOT NULL,outcome TEXT NOT NULL,updated_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(2,datetime('now'));
            INSERT OR IGNORE INTO schema_versions VALUES(14,datetime('now'));
            INSERT OR IGNORE INTO schema_versions VALUES(20,datetime('now'));
            ''')
        from .lpai_training import LPAITraining
        from .execution_request import install
        install(store)
        self.training=LPAITraining(self)
        from .component_resources import ComponentResources
        self.resource_directory=ComponentResources(self)
        from .task_outputs import TaskOutputs
        self.outputs=TaskOutputs(self)
        from .platform_health import PlatformHealth
        self.health=PlatformHealth(self)
        from .platform_login import PlatformLogin
        self.login=PlatformLogin(self)
        from .space_observer import SpaceObserver
        self.space_observer=SpaceObserver(self)

    def config(self):
        row=self.store.one('SELECT * FROM company_config WHERE singleton=1')
        defaults={'revision':0,'lpai_enabled':False,'gitlab_enabled':False,'namespace':'vc-chassis',
                  'gitlab_host':'gitlab.chehejia.com','git_protocol':'https'}
        return {**defaults,**json.loads(row['content']),'revision':row['revision']} if row else defaults

    def configure(self,data):
        if set(data)-{'revision','lpai_enabled','gitlab_enabled','namespace','gitlab_host','git_protocol'}:
            raise ValueError('连接配置不接收 Token、密码或任意命令')
        with self.lock:
            old=self.config()
            if data.get('revision')!=old['revision']:
                raise ValueError('连接配置已变化，请重新读取')
            value={**old,**data}
            if type(value['lpai_enabled']) is not bool or type(value['gitlab_enabled']) is not bool:
                raise ValueError('连接开关必须明确选择')
            checked_id(value['namespace'])
            if value['git_protocol'] not in {'https','ssh'}:raise ValueError('Git传输只支持HTTPS或SSH')
            if not HOST.fullmatch(value['gitlab_host']):
                raise ValueError('GitLab只填写服务器主机名，不含账号、路径或凭据')
            for enabled,name in [('lpai_enabled','lpai-agent'),('gitlab_enabled','glab')]:
                if value[enabled] and not self.locator(name):
                    raise ValueError(f'未安装官方 {name} 命令，未用其他私有实现替代')
            if value==old:
                return self.status()
            value.pop('revision',None)
            self.store.execute('INSERT INTO company_config VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,content=excluded.content',(old['revision']+1,dumps(value)))
        self.store.event(None,'company.configured',{})
        return self.status()

    def status(self):
        config=self.config()
        return {'config':config,'lpai_installed':bool(self.locator('lpai-agent')),'glab_installed':bool(self.locator('glab')),
                'git_installed':bool(self.locator('git')),'lpai_transport':'公司官方 lpai-agent','gitlab_transport':'官方 glab 与原生 Git',
                'capabilities':self.capabilities(),'health':self.health.status(),
                'lpai_url':f"https://lpai.lixiang.com/lpaiweb/xspacex_{config['namespace']}/subApp/devspace/list",
                'lpai_create_space_url':f"https://lpai.lixiang.com/lpaiweb/xspacex_{config['namespace']}/subApp/devspace/jupyter/create",
                'gitlab_url':'https://'+config['gitlab_host'],'credential_storage':'官方CLI／Git凭据系统，本项目不保存凭据'}

    def capabilities(self):
        """One offline declaration for UI/native tools/watch; not an auth probe.

        Dependencies belong to adapters, never to an installed research Skill.
        Installed/configured is not proof of login, permissions or training.
        """
        config=self.config()
        result={}
        for name,flag,program,actions in [
            ('lpai','lpai_enabled','lpai-agent',['spaces','space','components','tasks','task','bind_space','start_bound_space','watch_task']),
            ('gitlab','gitlab_enabled','glab',['account','repositories','repository','bind_repository'])]:
            enabled=config[flag];installed=bool(self.locator(program))
            result[name]={'owner':'system_adapter','skill_required':False,'enabled':enabled,'dependency_present':installed,
                'availability':'disabled' if not enabled else 'dependency_missing' if not installed else 'configured_not_probed',
                'implemented_operations':actions,'connection_verified':False}
        result['lpai']['implemented_operations'] += ['diagnose_task','prepare_component_submission','submit_fixed_component_sdk','durable_failure_notice','archive_bound_task_directory','terminal_small_file_collection','frozen_result_contract','cloud_numeric_comparison','durable_watch_health','task_result_runtime','frozen_volume_recovery','approved_component_provisioning']
        result['lpai']['sdk_installed']=(Path(__file__).resolve().parents[1]/'.runtime/lpai'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')).is_file()
        result['lpai']['implemented_operations'] += ['account_resource_directory','versioned_execution_assets','approved_gpu_component_configuration']
        result['lpai']['implemented_operations'] += ['recover_requested_submission','bounded_output_read_recovery','cloud_assessment_index',
            'validated_devspace_ssh_training','declared_devspace_artifact_collection','manual_stop_bound_space']
        result['lpai']['operation_boundaries']={
            'component_training':{'implemented':True,'transport':'official_training_sdk',
                'requires':['enabled_connection','official_sdk','ept_session','approved_execution_scope','frozen_component_and_inputs'],
                'unknown_submission':'reconcile_original_request_without_resubmitting'},
            'artifact_collection':{'implemented':True,'mode':'bounded_small_files_and_checkpoint_index',
                'requires':['bound_original_task','matching_connection','official_asset_sdk'],
                'numeric_analysis':'requires_verified_run_receipt_and_fixed_evaluation',
                'retry':'known_transient_read_failure_only_shared_three_attempts'},
            'devspace_training':{'implemented':True,'transport':'openssh',
                'requires':['approved_devspace_route','bound_ready_space','verified_ssh_identity','matching_visible_gpu'],
                'disconnect_auto_recovery':True,'local_fallback':False,'recovery_scope':'new_durable_ssh_jobs_only; unknown reads never relaunch'},
            'devspace_stop':{'implemented':True,'entry':'human_ui_only','automatic':False,
                'requires':['explicit_confirmation','exact_bound_uid','matching_connection','no_unresolved_project_execution'],
                'success_evidence':'official_readback_of_exact_uid_in_stopped_phase'},
            'declaration_only':True,'runtime_verified':False}
        result['lpai']['not_implemented']=['arbitrary_training_transfer','metric_stream','large_checkpoint_download',
            'legacy_devspace_job_backfill','unreviewed_shared_component_mutation']
        result['git']={'owner':'system_adapter','skill_required':False,'dependency_present':bool(self.locator('git')),
            'implemented_operations':['status','diff','branches','log','clone_bound_repository','branch','switch','commit_selected','pull_ff_only','merge','push_bound_repository'],
            'write_requires_exact_approval':True}
        return result

    def _run(self,argv,cwd=None,timeout=30,raw=False):
        if not argv or not argv[0]:raise ValueError('所需官方工具未安装')
        try:
            result=self.runner(argv,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                               env={**os.environ,'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'never','GLAB_PROMPT_DISABLED':'true'})
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('官方操作超时，结果需核对，未自动重复提交') from exc
        if result.returncode:
            raise ValueError('官方工具未成功，请检查本机登录／网络／权限。'+redact(result.stderr or result.stdout)[-450:])
        if len(result.stdout.encode('utf-8'))>2*1024*1024:
            raise ValueError('响应超过读取上限，请缩小范围')
        return result.stdout if raw else json.loads(result.stdout)

    def lpai(self,operation,name=None,uid=None,asset_kind=None,version=None,page=1):
        config=self.config()
        if not config['lpai_enabled']:
            raise ValueError('LPAI连接尚未启用')
        if operation=='resources':return self.resource_directory.catalog(name)
        if operation=='assets':
            return self.training.sdk('execution_assets',kind=asset_kind,uid=uid,version=version,page=page)
        if operation=='component_sdk':
            return self.training.sdk('component',component_uid=checked_id(uid))
        if operation=='volume':
            if not name:raise ValueError('volume读取需要准确卷名name；UID可同时提供用于核对，不能代替卷名')
            result=self.outputs.inspect_volume(checked_id(name))
            if uid and result['uid']!=checked_id(uid):raise ValueError('官方卷名称和指定UID不匹配')
            return result
        if operation=='sdk_status':
            return self.training.sdk('auth_status')
        args=[self.locator('lpai-agent'),'--timeout','25']
        scope=['--namespace',config['namespace']]
        if operation in {'spaces','components','tasks'}:
            group={'spaces':'devspaces','components':'projects','tasks':'tasks'}[operation]
            args += [group,'list',*scope,'--page-size','30','--page-num','1']
            kind='space' if operation=='spaces' else 'component' if operation=='components' else 'task'
        elif operation in {'space','component'}:
            args += ['devspaces' if operation=='space' else 'projects','get',*scope,'--name',checked_id(name)]
            kind='space' if operation=='space' else 'component'
        elif operation=='task':
            args += ['tasks','get',*scope,'--uid',checked_id(uid)]
            kind='task'
        else:
            raise ValueError('未注册的LPAI只读操作')
        response=self._run(args)
        if response.get('status')!='ok':
            raise ValueError('官方LPAI未确认读取成功；没有重试写操作')
        data=response.get('data')
        if operation in {'space','component','task'}:
            item=platform_item(data,config['namespace'],kind)
            if operation in {'space','component'} and item['name']!=name or operation=='task' and item['uid']!=uid:
                raise ValueError('官方返回了其他对象，未采用')
            if operation=='space':
                self.store.execute("UPDATE company_bindings SET observation=? WHERE kind='lpai' AND scope=? AND uid=? AND name=?",
                                   (dumps(item),item['namespace'],item['uid'],item['name']))
            if operation=='space' and item['phase'].lower() in {'ready','running'}:
                self._observe_active_space(item)
            if operation=='space' and item['phase'].lower()=='stopped':
                self._observe_stopped_space(item)
            if operation=='task':
                from .task_output_files import output_hints
                item['output_hints']=output_hints(data)
            return {'item':item}
        rows=data.get('items',[]) if isinstance(data,dict) else data
        if not isinstance(rows,list):
            raise ValueError('官方列表格式未确认')
        return {'items':[platform_item(row,config['namespace'],kind) for row in rows[:30]],
                'total':data.get('total') if isinstance(data,dict) else len(rows),'scope':'授权可见的前30项，不假定全部属于本人'}

    def _glab(self,endpoint):
        config=self.config()
        if not config['gitlab_enabled']:
            raise ValueError('GitLab连接尚未启用')
        return self._run([self.locator('glab'),'api','--hostname',config['gitlab_host'],'--method','GET',endpoint])

    def gitlab(self,operation,project_id=None):
        if operation=='account':
            raw=self._glab('user')
            return {'connected':True,'username':raw.get('username'),'name':raw.get('name'),'id':raw.get('id'),'host':self.config()['gitlab_host']}
        if operation=='repositories':
            raw=self._glab('projects?membership=true&simple=true&per_page=30&order_by=last_activity_at')
            if not isinstance(raw,list):
                raise ValueError('GitLab仓库列表格式未确认')
            return {'items':[self._repository(row) for row in raw],'scope':'当前CLI账号有成员权限的前30个仓库'}
        if operation=='repository':
            return self._repository(self._glab('projects/'+quote(str(project_id),safe='')))
        raise ValueError('未注册的GitLab只读操作')

    def _repository(self,raw):
        host=self.config()['gitlab_host']
        if not isinstance(raw,dict) or not raw.get('id') or not raw.get('path_with_namespace'):
            raise ValueError('GitLab仓库身份未确认')
        return {key:raw.get(key) for key in ['id','name','path_with_namespace','web_url','default_branch','ssh_url_to_repo','http_url_to_repo']}|{'host':host}

    def context(self,session_id,brief=False):
        session=self.store.session(session_id)
        rows=self.store.rows('SELECT * FROM company_bindings WHERE project_id=? ORDER BY kind',(session['project_id'],))
        from .devspace import ssh_settings
        space = next((row for row in rows if row['kind'] == 'lpai'), None)
        from .execution_request import status
        from .rounds import approved_plan
        from .autonomy import execution_grant
        plan=approved_plan(self.store,session['project_id']) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_reviews'") else None
        try:execution=execution_grant(self.store,plan)['execution'] if plan else None
        except ValueError:execution=None
        stop=self.space_stop_status(session_id)
        operations=self._space_operations(session['project_id'],space)
        result={'component_resource_preference':self.resource_directory.preference(session_id),
                'approved_execution':execution,'execution_request':status(self.store,session['project_id']),'devspace_ssh':ssh_settings(self.store,session['project_id'],space),
                'connections':self.status(),'bindings':[{**r,'observation':json.loads(r['observation'])} for r in rows],
                'watches':self.store.rows('SELECT * FROM company_watches WHERE session_id=? ORDER BY created_at',(session_id,)),
                'authority':self.authority(session_id),'training':self.training.context(session_id,brief),
                'devspace_stop':stop,'devspace_operation':operations[0] if operations else None,
                'devspace_observation':self.space_observer.status(operations)}
        if brief:
            from .component_configuration import recipe_contract
            result['component_recipe_contract']=recipe_contract()
        return result

    def session_runs(self,session_id,scope='session'):
        from .session_runs import project,project_runs
        if scope not in {'session','project'}:raise ValueError('运行视图范围无效')
        return project_runs(self,session_id) if scope=='project' else project(self,session_id)

    def watch_preference(self,sid,watch_id,auto_resume):
        if type(auto_resume) is not bool:raise ValueError('需要明确是否继续分析')
        session=self.store.session(sid)
        if auto_resume and session['channel']!='ept':raise ValueError('公司任务分析当前仅允许企业EPT会话')
        row=self.store.one('SELECT * FROM company_watches WHERE id=? AND session_id=?',(watch_id,sid))
        if not row:raise ValueError('监控不属于当前会话')
        if row['state'].lower() in TERMINAL:raise ValueError('已结束记录不重新唤醒；可在原会话讨论结果')
        self.store.execute('UPDATE company_watches SET auto_resume=? WHERE id=?',(int(auto_resume),watch_id))
        self.store.event(sid,'company.watch_preference',{'id':watch_id,'auto_resume':auto_resume})
        return self.session_runs(sid)

    def refresh_space(self,session_id,uid):
        """Explicit human readback; never used by a native tool or monitor."""
        from .space_observer import credential_revision,process_auth_epoch
        project_id=self.store.session(session_id)['project_id']
        bound=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai' AND uid=?",(project_id,uid))
        if not bound:
            raise ValueError('本项目的开发空间绑定已变化，请刷新页面')
        config=self.config()
        if not config['lpai_enabled']:raise ValueError('LPAI连接尚未启用')
        if bound['scope']!=config['namespace']:
            raise ValueError('当前 LPAI 工作空间与项目绑定不一致')
        source=(credential_revision('lpai_cli'),process_auth_epoch())
        try:
            # Do not adopt a generic lpai() observation before this explicit
            # request has checked the original binding and exact UID together.
            response=self._run([self.locator('lpai-agent'),'--timeout','25','devspaces','get',
                                '--namespace',checked_id(bound['scope']),'--name',checked_id(bound['name'])])
            if not isinstance(response,dict) or response.get('status')!='ok':raise ValueError('官方LPAI未确认读取成功；没有重试写操作')
            item=platform_item(response.get('data'),bound['scope'],'space')
        except ValueError as exc:
            missing=(r'HTTP 404: \[NOT_FOUND\]\s+notebooks/'+re.escape(bound['name'])+r'\s+not found(?:\s|$)',
                     r'HTTP 404: \[DEVSPACE_NOT_FOUND\][ \t]+devspace not found\s*\Z')
            if not any(re.search(pattern,str(exc)) for pattern in missing):
                raise
            # Only this bound-name GET's explicit not-found response updates its
            # observation. Keep the binding, SSH settings and original requests;
            # this is not proof that the user deleted the space.
            item={**json.loads(bound['observation']),'phase':'not_found','observed_at':now()}
        if (item.get('uid'),item.get('namespace'),item.get('name'))!=(uid,bound['scope'],bound['name']):
            raise ValueError('平台返回的空间与项目绑定不一致')
        with self.store.lock:
            self.store.session(session_id) # Removed owners cannot adopt a late read.
            if self.config()['revision']!=config['revision'] or source!=(credential_revision('lpai_cli'),process_auth_epoch()):
                raise ValueError('LPAI连接已变化，请重新读回原空间')
            changed=self.store.execute("UPDATE company_bindings SET observation=? WHERE project_id=? AND kind='lpai' AND scope=? AND uid=? AND name=? AND observation=? AND created_at=?",
                (dumps(item),project_id,bound['scope'],uid,bound['name'],bound['observation'],bound['created_at']))
            if not changed:raise ValueError('开发空间绑定或观测已变化，请重新读回原空间')
            self.space_observer.observe(item,manual=True)
        return {'item':item}

    def bind_space(self,session_id,name,uid):
        session=self.store.session(session_id)
        if session['active_turn']:
            raise ValueError('请先暂停当前轮次再更换执行空间')
        item=self.lpai('space',name=name)['item']
        if item['uid']!=uid:
            raise ValueError('空间UID已变化，未绑定同名新对象')
        with self.lock:
            other=self.store.one("SELECT project_id FROM company_bindings WHERE kind='lpai' AND scope=? AND uid=?",(item['namespace'],uid))
            if other and other['project_id']!=session['project_id']:
                raise ValueError('空间已绑定本工作台的其他项目，不能跨项目复用')
            self.store.execute('INSERT INTO company_bindings VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,kind) DO UPDATE SET scope=excluded.scope,uid=excluded.uid,name=excluded.name,observation=excluded.observation',
                               (session['project_id'],'lpai',item['namespace'],uid,name,dumps(item),now()))
        self.store.event(session_id,'company.space_bound',{'project_id':session['project_id'],'name':name,'uid':uid,'training_submitted':False})
        return self.context(session_id)

    def _space_operations(self,project_id,bound):
        if not bound:return []
        operations=[]
        for row in self.store.rows('''SELECT o.* FROM company_operations o JOIN sessions s ON s.id=o.session_id
                WHERE s.project_id=? ORDER BY o.rowid DESC''',(project_id,)):
            request=json.loads(row['request'])
            if request.get('action') not in {'lpai_start','lpai_stop'}:continue
            if not any(target.get('kind')=='lpai' and (target.get('scope'),target.get('uid'),target.get('name'))==
                       (bound['scope'],bound['uid'],bound['name']) for target in request.get('bound_targets',[])):continue
            operations.append({**(json.loads(row['result']) if row['result'] else {}),'id':row['id'],
                'action':request['action'],'state':row['state'],'uid':bound['uid'],'name':bound['name'],
                'namespace':bound['scope']})
        return operations

    def _space_stop_blockers(self,session,bound,include_phase=True):
        project_id=session['project_id'];config=self.config();blockers=[]
        def block(kind,id,state,message):blockers.append({'kind':kind,'id':id,'state':state,'message':message})
        if not bound:block('binding',project_id,'missing','请先绑定本项目开发空间')
        if not config['lpai_enabled'] or not self.locator('lpai-agent'):
            block('connection',project_id,'unavailable','请先启用 LPAI 连接并安装官方工具')
        if bound and bound['scope']!=config['namespace']:
            block('connection',bound['uid'],'changed','当前 LPAI 工作空间与绑定不同，请重新核对连接')
        for row in self.store.rows('SELECT id FROM sessions WHERE project_id=? AND active_turn IS NOT NULL',(project_id,)):
            block('active_turn',row['id'],'active','项目会话仍在执行，请先暂停并核对当前执行')
        row=self.store.one('''SELECT d.id,d.state FROM deliveries d JOIN sessions s ON s.id=d.session_id
            WHERE s.project_id=? AND d.state IN ('requested','unknown') LIMIT 1''',(project_id,))
        if row:block('delivery',row['id'],row['state'],'项目有待核对的会话投递，请先核对原执行')
        row=self.store.one('''SELECT a.id,a.state FROM approvals a JOIN sessions s ON s.id=a.session_id
            WHERE s.project_id=? AND a.state='pending' LIMIT 1''',(project_id,))
        if row:block('approval',row['id'],row['state'],'项目有待处理的执行审批，请先核对原执行')
        if self.store.one("SELECT name FROM sqlite_master WHERE name='research_runs'"):
            row=self.store.one('''SELECT id,state FROM research_runs WHERE project_id=? AND lower(state) NOT IN
                ('completed','failed','timed_out','invalid_inputs','artifacts_missing','cancelled','canceled','stopped','terminated') LIMIT 1''',(project_id,))
            if row:block('research_run',row['id'],row['state'],'项目有运行中或状态未知的实验，不能停止空间')
        if self.store.one("SELECT name FROM sqlite_master WHERE name='devspace_jobs'"):
            row=self.store.one('''SELECT j.run_id AS id,j.state FROM devspace_jobs j JOIN research_runs r ON r.id=j.run_id
                WHERE r.project_id=? AND j.state<>'collected' LIMIT 1''',(project_id,))
            if row:block('devspace_job',row['id'],row['state'],'原开发空间任务或产物尚未完成核对，不能停止空间')
        from .rounds import pending_cloud,pending_platform_operation
        row=pending_cloud(self.store,project_id)
        if row:block('training',row['id'],row['state'],'项目有在途或状态未知的训练，请先核对原任务')
        row=pending_platform_operation(self.store,project_id)
        if row:block('platform_operation',row['id'],row['state'],'项目有未决平台操作，请核对原启动、停止或提交请求，不会重复发送')
        for row in self.store.rows('''SELECT w.id,w.state FROM company_watches w JOIN sessions s ON s.id=w.session_id
                WHERE s.project_id=?''',(project_id,)):
            if row['state'].lower() not in TERMINAL:
                block('task',row['id'],row['state'],'项目有运行中或状态未知的平台任务，请先核对原任务');break
        if bound and include_phase:
            phase=json.loads(bound['observation']).get('phase','unknown').lower()
            if phase not in {'running','ready'}:
                block('space_phase',bound['uid'],phase,'请读回原空间；仅运行或就绪的空间可以请求停止' if phase!='stopped' else '原空间已观测为停止')
        return blockers

    def space_stop_status(self,session_id):
        session=self.store.session(session_id)
        bound=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'",(session['project_id'],))
        operations=self._space_operations(session['project_id'],bound)
        blockers=self._space_stop_blockers(session,bound)
        return {'available':not blockers,'blockers':blockers,
                'operation':next((operation for operation in operations if operation['action']=='lpai_stop'),None)}

    def stop_space(self,session_id,uid,operation_id,confirmed,expected_authority):
        """Human UI entry only. Never register this action with company_action."""
        if confirmed is not True:raise ValueError('需要人类对停止该确切空间明确确认')
        checked_id(uid);checked_id(operation_id)
        with self.lock:
            session=self.store.session(session_id)
            bound=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'",(session['project_id'],))
            if not bound or bound['uid']!=uid:raise ValueError('开发空间绑定已变化，请重新核对确切UID')
            if expected_authority!=self.authority(session_id):raise ValueError('连接或项目绑定已变化，请重新核对')
            targets=[{key:bound[key] for key in ('kind','scope','uid','name')}]
            frozen={'action':'lpai_stop','bound_targets':targets,'approval_source':'human_ui',
                    'expected_authority':expected_authority}
            fingerprint=hashlib.sha256(dumps({'session':session_id,'request':frozen}).encode()).hexdigest()
            prior=self.store.one('SELECT * FROM company_operations WHERE id=?',(operation_id,))
            if prior:
                if prior['request_hash']!=fingerprint:raise ValueError('原操作ID不能用于新的参数／连接')
                return {**(json.loads(prior['result']) if prior['result'] else {}),'id':operation_id,
                        'action':'lpai_stop','state':prior['state'],'uid':uid,'name':bound['name'],'resubmitted':False}
            blockers=self._space_stop_blockers(session,bound,include_phase=False)
            if blockers:raise ValueError(blockers[0]['message'])
            # This read has no write intent. Unknown, changing or same-name new
            # objects never cross the non-idempotent boundary.
            item=self.lpai('space',name=bound['name'])['item']
            if (item['uid'],item['namespace'],item['name'])!=(uid,bound['scope'],bound['name']):
                raise ValueError('官方读回的确切空间UID或工作空间已变化，未停止')
            phase=item['phase'].lower()
            if phase not in {'ready','running','stopped'}:raise ValueError('原空间运行状态未知或正在变化，请先读回核对，未停止')
            if expected_authority!=self.authority(session_id):raise ValueError('连接或项目绑定已变化，请重新核对')
            # Research.run also holds store.lock while checking pending platform
            # operations and registering its run. Persist before releasing this
            # short lock; no network call is made while holding store.lock.
            with self.store.lock:
                bindings=self.store.rows('SELECT kind,scope,uid,name FROM company_bindings WHERE project_id=? ORDER BY kind',(session['project_id'],))
                if (expected_authority['revision']!=self.config()['revision'] or
                    expected_authority['bindings_hash']!=hashlib.sha256(dumps(bindings).encode()).hexdigest()):
                    raise ValueError('连接或项目绑定已变化，请重新核对')
                blockers=self._space_stop_blockers(self.store.session(session_id),bound,include_phase=False)
                if blockers:raise ValueError(blockers[0]['message'])
                stamp=now()
                self.store.execute('INSERT INTO company_operations VALUES(?,?,?,?,?,?,?,?)',
                    (operation_id,session_id,fingerprint,dumps(frozen),'requested',None,stamp,stamp))
                self.space_observer.register(operation_id,session,bindings,self.config())
            try:
                if phase=='stopped':
                    result={'state':'already_stopped','item':item,'message':'只读确认原空间已停止；未发送停止请求'}
                else:
                    try:result=self._space_write('stop',bound)
                    except TimeoutError:result={'state':'unknown','message':'原停止请求结果未知；请读回原空间，不会重复发送'}
                result.update(id=operation_id,action='lpai_stop',uid=uid,name=bound['name'],namespace=bound['scope'],
                              bound_targets=targets,approval_source='human_ui')
                with self.store.lock:
                    self.store.execute('UPDATE company_operations SET state=?,result=?,updated_at=? WHERE id=?',
                        (result['state'],dumps(result),now(),operation_id))
            finally:self.space_observer.finish(operation_id)
        self.store.event(session_id,'company.operation',{'id':operation_id,'action':'lpai_stop','result':result})
        return result

    def bind_repository(self,session_id,project_id):
        session=self.store.session(session_id)
        if session['active_turn']:
            raise ValueError('请先暂停当前轮次再修改源码仓库绑定')
        item=self.gitlab('repository',project_id)
        self.store.execute('INSERT INTO company_bindings VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,kind) DO UPDATE SET scope=excluded.scope,uid=excluded.uid,name=excluded.name,observation=excluded.observation',
                           (session['project_id'],'gitlab',item['host'],str(item['id']),item['path_with_namespace'],dumps(item),now()))
        self.store.event(session_id,'company.repository_bound',{'name':item['path_with_namespace'],'cloned':False})
        return self.context(session_id)

    def _repo(self,session_id):
        workspace=Path(self.store.session(session_id)['workspace']).resolve()
        candidate=workspace/'source'
        root=candidate if (candidate/'.git').exists() else workspace
        if not root.resolve().is_relative_to(workspace):
            raise ValueError('源码仓库目录不能通过链接逃出项目工作区')
        if not (root/'.git').exists():
            raise ValueError('当前工作区尚不是Git仓库，请先绑定并克隆，或新建时选择已有仓库目录')
        return root

    def _git(self,root,args,remote=None):
        executable=self.locator('git')
        if not executable:
            raise ValueError('本机未安装Git')
        hooks=self.store.root/'git-no-hooks'
        hooks.mkdir(exist_ok=True)
        transport=[]
        if remote:
            if remote.startswith('https://'):
                host=urlsplit(remote).hostname
                if not self.config()['gitlab_enabled'] or host!=self.config()['gitlab_host']:
                    raise ValueError('Git认证目标不匹配当前启用的GitLab')
                glab=self.locator('glab')
                if not glab:raise ValueError('未安装官方glab')
                # Only the official helper sees credentials. Per-command config;
                # no token in argv, environment, repository or global Git config.
                path=str(glab).replace('\\','/')
                if any(c in path for c in '\"`$\n\r'):raise ValueError('官方glab路径不适合作为Git认证命令')
                transport=['-c','credential.helper=','-c',f'credential.https://{host}.helper=!"{path}" auth git-credential']
            else:
                transport=['-c','core.sshCommand=ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8']
        return self._run([executable,'--no-pager','-c','core.hooksPath='+str(hooks),'-c','core.fsmonitor=false',*transport,'-C',str(root),*args],raw=True)

    def selected_remote(self,item):
        if item.get('host')!=self.config()['gitlab_host']:raise ValueError('仓库属于旧GitLab连接，请重新绑定')
        remote=item.get('http_url_to_repo' if self.config()['git_protocol']=='https' else 'ssh_url_to_repo')
        self._check_remote(remote,item)
        return remote

    def git_probe(self,item):
        remote=self.selected_remote(item)
        refs=self._git(self.store.root,['ls-remote','--',remote,'HEAD'],remote=remote)
        if refs.strip() and any(not re.fullmatch(r'[a-f0-9]{40,64}\s+[^\s]+',line) for line in refs.splitlines()):
            raise ValueError('Git引用响应格式未核实')
        return {'read_verified':True,'write_verified':False,'protocol':self.config()['git_protocol']}

    def git_read(self,session_id,operation):
        args={'status':['status','--short','--branch'],'diff':['diff','--stat'],'branches':['branch','--list'],
              'log':['log','-12','--format=%h %ad %s','--date=short']}.get(operation)
        if not args:
            raise ValueError('未注册的Git只读操作')
        return {'operation':operation,'text':redact(self._git(self._repo(session_id),args)),'observed_at':now()}

    def _branch(self,root,value):
        if not isinstance(value,str) or not value or value.startswith('-') or len(value)>160:
            raise ValueError('无效分支名称')
        self._git(root,['check-ref-format','--branch',value])
        return value

    @staticmethod
    def validate_action(request):
        if not isinstance(request,dict):raise ValueError('平台操作需要明确的结构化请求')
        action=request.get('action')
        if action not in {'lpai_start','lpai_submit','git_clone','git_branch','git_switch','git_commit','git_pull','git_merge','git_push'}:
            raise ValueError('未注册的企业写操作')
        if action=='lpai_start' and set(request)!={'action'}:
            raise ValueError('lpai_start仅启动已绑定开发空间，不接受submission_id或组件参数。提交lpai_prepare已冻结的组件任务请使用lpai_submit和原submission_id；未启动空间、未提交、未扣次数，也不是用户拒绝。')
        if action=='lpai_submit' and (set(request)!={'action','submission_id'} or not isinstance(request.get('submission_id'),str) or not request['submission_id'].strip()):
            raise ValueError('lpai_submit仅接受action与lpai_prepare返回的非空submission_id；不要用lpai_start替代组件提交。未提交、未扣次数。')

    def perform(self,session_id,request,operation_id,approved=False,approval_source=None):
        self.validate_action(request)
        if approved is not True:
            raise ValueError('需要用户对本次确切操作确认')
        action=request.get('action')
        if action not in {'lpai_start','lpai_submit','git_clone','git_branch','git_switch','git_commit','git_pull','git_merge','git_push'}:
            raise ValueError('未注册的企业写操作')
        session=self.store.session(session_id)
        bindings=self.store.rows('SELECT kind,scope,uid,name FROM company_bindings WHERE project_id=? ORDER BY kind',(session['project_id'],))
        frozen={**request,'bound_targets':bindings}
        if approval_source:frozen['approval_source']=approval_source
        fingerprint=hashlib.sha256(dumps({'session':session_id,'request':frozen,'config_revision':self.config()['revision']}).encode()).hexdigest()
        with self.lock:
            with self.store.lock:
                prior=self.store.one('SELECT * FROM company_operations WHERE id=?',(operation_id,))
                if prior:
                    if prior['request_hash']!=fingerprint:
                        raise ValueError('原操作ID不能用于新的参数／连接')
                    return json.loads(prior['result']) if prior['result'] else {'state':prior['state'],'resubmitted':False}
                if action=='lpai_start':
                    pending=self.store.rows("SELECT o.request FROM company_operations o JOIN sessions s ON s.id=o.session_id WHERE s.project_id=? AND o.state IN ('requested','submitted','unknown','pending_approval')",(session['project_id'],))
                    if any(json.loads(row['request']).get('action') in {'lpai_start','lpai_stop'} for row in pending):
                        raise ValueError('原开发空间启动请求尚未核对，不会用新操作ID重复发送；停止请求未决时也不能启动')
                stamp=now()
                self.store.execute('INSERT INTO company_operations VALUES(?,?,?,?,?,?,?,?)',(operation_id,session_id,fingerprint,dumps(frozen),'requested',None,stamp,stamp))
                if action=='lpai_start':self.space_observer.register(operation_id,session,bindings,self.config())
            try:
                try:
                    result=self._perform(session_id,request,operation_id)
                    state=result.get('state','returned')
                except TimeoutError:
                    result={'state':'unknown','message':'原操作超时，需核对原对象／分支；没有重复提交'}
                    state='unknown'
                except Exception as exc:
                    result={'state':'failed','message':str(exc)}
                    state='failed'
                result['bound_targets']=bindings
                if approval_source:result['approval_source']=approval_source
                self.store.execute('UPDATE company_operations SET state=?,result=?,updated_at=? WHERE id=?',(state,dumps(result),now(),operation_id))
            finally:self.space_observer.finish(operation_id)
        self.store.event(session_id,'company.operation',{'id':operation_id,'action':action,'result':result})
        return result

    def _perform(self,session_id,request,operation_id=None):
        self.validate_action(request)
        action=request['action']
        session=self.store.session(session_id)
        if action=='lpai_submit':
            if set(request)!={'action','submission_id'}:raise ValueError('只接受已冻结的提交预览身份')
            return self.training.submit(session_id,request['submission_id'],True,operation_id=operation_id)
        if action in {'git_clone','git_pull','git_push'} and not self.config()['gitlab_enabled']:
            raise ValueError('GitLab连接已关闭，未执行远端操作')
        if action=='lpai_start':
            binding=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'",(session['project_id'],))
            if not binding:
                raise ValueError('先选择并绑定本项目开发空间')
            item=self.lpai('space',name=binding['name'])['item']
            if item['uid']!=binding['uid'] or item['namespace']!=binding['scope']:
                raise ValueError('原空间UID不一致')
            if item['phase'].lower() in {'running','ready','pending','starting','creating'}:
                return {'state':'already_active','item':item}
            return self._space_write('start',binding)
        if action=='git_clone':
            binding=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='gitlab'",(session['project_id'],))
            if not binding:
                raise ValueError('先绑定GitLab仓库')
            item=json.loads(binding['observation'])
            remote=self.selected_remote(item)
            root=Path(session['workspace'])
            target=root/'source'
            if target.exists():
                raise ValueError('source目录已存在；不会覆盖，请核对原克隆结果')
            text=self._git(root,['clone','--',remote,'source'],remote=remote)
            return {'state':'completed','text':redact(text),'directory':'source'}
        root=self._repo(session_id);remote=None
        if action in {'git_branch','git_switch'}:
            branch=self._branch(root,request.get('branch'))
            args=['switch','-c',branch] if action=='git_branch' else ['switch',branch]
        elif action=='git_commit':
            files=request.get('files')
            if not isinstance(files,list) or not files or len(files)>100:
                raise ValueError('提交必须明确列出文件，不能自动提交全部工作区')
            paths=[]
            for item in files:
                if not isinstance(item,str) or not item or len(item)>500:
                    raise ValueError('需要明确的文件相对路径')
                path=(root/str(item)).resolve()
                if not path.is_relative_to(root) or any(part.lower() in {'.git','.ssh','.aws','credentials.json','secrets.json','id_rsa','id_ed25519'} or part.lower().startswith('.env') for part in Path(str(item)).parts):
                    raise ValueError('提交文件必须位于项目内，且不能包含凭据目录')
                if path.is_file() and path.stat().st_size<1024*1024:
                    if b'PRIVATE KEY-----' in path.read_bytes():
                        raise ValueError('不能提交私钥')
                paths.append(str(path.relative_to(root)))
            staged=self._git(root,['diff','--cached','--name-only']).splitlines()
            if any(path not in paths for path in staged):
                raise ValueError('索引中还有其他文件，请先核对，未替你提交')
            message=request.get('message')
            if not isinstance(message,str) or not message.strip() or len(message)>2000:
                raise ValueError('需要明确提交说明')
            self._git(root,['add','--',*paths])
            args=['commit','-m',message]
        elif action=='git_merge':
            args=['merge','--no-edit',self._branch(root,request.get('branch'))]
        else:
            binding=self.store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='gitlab'",(session['project_id'],))
            if not binding:
                raise ValueError('先绑定GitLab仓库再执行远端操作')
            item=json.loads(binding['observation'])
            remote=self._git(root,['remote','get-url','origin']).strip()
            self._check_remote(remote,item)
            branch=self._branch(root,request.get('branch'))
            args=['push','origin',branch] if action=='git_push' else ['pull','--ff-only','origin',branch]
        text=self._git(root,args,remote=remote)
        return {'state':'completed','text':redact(text),'action':action}

    def _check_remote(self,remote,item):
        host=item['host']
        if isinstance(remote,str) and remote.startswith('git@'+host+':'):
            path=remote.split(':',1)[1]
        elif isinstance(remote,str) and remote.startswith('https://'):
            parsed=urlsplit(remote)
            if parsed.hostname!=host or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port:
                raise ValueError('远端地址不能包含凭据，且必须匹配已绑定GitLab')
            path=parsed.path.lstrip('/')
        else:
            raise ValueError('仅支持已绑定GitLab的HTTPS／SSH地址')
        if path.removesuffix('.git')!=item['path_with_namespace']:
            raise ValueError('当前Git远端不是已绑定的仓库，未执行远端写操作')

    def _space_write(self,operation,bound):
        # The CLI can lose its response after a write. Every failure after this
        # boundary remains uncertain, including a nonzero exit or malformed JSON.
        label='启动' if operation=='start' else '停止'
        try:
            raw=self._run([self.locator('lpai-agent'),'--timeout','25','devspaces',operation,
                           '--namespace',bound['scope'],'--name',bound['name']])
        except (ValueError,TimeoutError,OSError) as exc:
            raise TimeoutError(label+'边界后未确认结果，先读回原空间；不重复发起') from exc
        status=raw.get('status') if isinstance(raw,dict) else None
        data=raw.get('data') if isinstance(raw,dict) else None
        pending=status=='pending_approval' or isinstance(data,dict) and data.get('status')=='pending_approval'
        message=raw.get('message') if isinstance(raw,dict) else None
        if not pending and isinstance(message,str) and any(word in message for word in ('需要确认','飞书')):
            raise TimeoutError(label+'响应提到确认，但未给出明确审批状态；先核对原请求')
        if status not in {'ok','pending_approval'} and not pending:
            raise TimeoutError('官方接口未确认'+label+'请求结果，请核对原空间，不要重复发送')
        return {'state':'pending_approval' if pending else 'submitted','name':bound['name'],'uid':bound['uid'],
                'message':'LPAI 返回待审批，请在官方页面核对原请求和审批入口。' if pending else
                          label+'请求已返回；需要读回确认'+('运行' if operation=='start' else '停止')+'状态'}

    def authority(self,session_id):
        session=self.store.session(session_id)
        bindings=self.store.rows('SELECT kind,scope,uid,name FROM company_bindings WHERE project_id=? ORDER BY kind',(session['project_id'],))
        head=None
        try:
            value=self._git(self._repo(session_id),['rev-parse','HEAD']).strip()
            if re.fullmatch(r'[a-f0-9]{40,64}',value):
                head=value
        except (ValueError,TimeoutError,OSError):
            pass
        return {'revision':self.config()['revision'],'bindings_hash':hashlib.sha256(dumps(bindings).encode()).hexdigest(),'git_head':head}

    def _observe_active_space(self,item):
        self.space_observer.observe(item)

    def _observe_stopped_space(self,item):
        self.space_observer.observe(item)

    def review_text(self,session_id,request):
        self.validate_action(request)
        context=self.context(session_id)
        action={'lpai_start':'启动已绑定的 LPAI 开发空间','lpai_submit':'通过官方SDK提交已审核的固定组件任务','git_clone':'克隆已绑定的 GitLab 仓库到 source 目录','git_branch':'新建并切换 Git 分支',
                'git_switch':'切换 Git 分支','git_commit':'提交明确选择的本地文件','git_pull':'拉取并仅快进合并','git_merge':'合并本地分支','git_push':'推送分支到已绑定 GitLab'}.get(request.get('action'))
        if not action:
            raise ValueError('未注册的企业操作')
        lines=['操作：'+action,'项目：'+self.store.session(session_id)['project_title']]
        if request.get('action')=='git_clone':
            binding=next((b for b in context['bindings'] if b['kind']=='gitlab'),None)
            if binding:lines.append('源码传输：'+self.selected_remote(binding['observation']))
        if request.get('action')=='lpai_submit':
            row=self.store.one('SELECT request FROM company_training WHERE id=? AND session_id=?',(request.get('submission_id'),session_id))
            if not row:raise ValueError('本次提交预览不存在')
            frozen=json.loads(row['request']);component=frozen['component']
            lines += ['组件：'+component['name'],'固定配置：'+component['digest'],'任务：'+frozen['task_name'],
                      'GPU数量：'+str(component['gpu_count']),'修复内容：'+(frozen['change_summary'] or '首次提交')]
            for patch in frozen.get('patches',[]):lines.append('仅本任务改动 '+patch['path']+'：\n'+patch['value'])
            for key,label in [('problem_statement','问题'),('solution_approach','方案'),('expected_result','预期'),('expected_duration','预计分钟'),('expected_duration_desc','时长依据')]:
                lines.append(label+'：'+str(frozen['task_information'][key]))
        for binding in context['bindings']:
            lines.append(('开发空间' if binding['kind']=='lpai' else '代码仓库')+'：'+binding['name']+' · '+binding['uid'])
        for field,label in [('branch','分支'),('message','提交说明'),('files','文件')]:
            if request.get(field):
                lines.append(label+'：'+(', '.join(request[field]) if isinstance(request[field],list) else str(request[field])))
        return '\n'.join(lines)

    def watch(self,session_id,task_uid,auto_resume=False,*,analyze_terminal=False):
        if type(auto_resume) is not bool:
            raise ValueError('需要明确选择是否在任务结束后继续分析')
        session=self.store.session(session_id)
        if auto_resume and session['channel']!='ept':
            raise ValueError('公司任务自动分析当前仅支持企业EPT会话；可使用只读监控')
        prior=self.store.one('SELECT * FROM company_watches WHERE session_id=? AND task_uid=?',(session_id,task_uid))
        if prior and prior['state'].lower() in TERMINAL:return self.context(session_id)
        item=self.lpai('task',uid=checked_id(task_uid))['item']
        item['watch_authority']=self.authority(session_id)
        watch_id=hashlib.sha256((session_id+':'+task_uid).encode()).hexdigest()[:32]
        terminal=item['phase'].lower() in TERMINAL
        continue_terminal=bool(auto_resume and (analyze_terminal or prior))
        self.store.execute('''INSERT INTO company_watches VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(session_id,task_uid) DO UPDATE SET auto_resume=excluded.auto_resume,observation=excluded.observation,state=excluded.state,next_check=excluded.next_check,wake_state=excluded.wake_state''',
          (watch_id,session_id,item['namespace'],task_uid,item['name'],item['phase'],dumps(item),int(auto_resume and (not terminal or continue_terminal)),
           time.time()+(0 if terminal and continue_terminal else min(300,self.watch_period({'session_id':session_id,'task_uid':task_uid,'state':item['phase']},item['phase']))),'already_terminal' if terminal and not continue_terminal else 'none',now()))
        self.store.event(session_id,'company.watch_bound',{'id':watch_id,'item':item,'auto_resume':auto_resume})
        return self.context(session_id)

    def repair_watches(self,stamp):
        """Repair only a known successful submission's missing observer, never resubmit."""
        rows=self.store.rows("""SELECT t.*,r.failures FROM company_training t
            LEFT JOIN company_watches w ON w.session_id=t.session_id AND w.task_uid=t.task_uid
            LEFT JOIN company_watch_repairs r ON r.submission_id=t.id
            WHERE t.state='submitted' AND t.task_uid IS NOT NULL AND w.id IS NULL
              AND (r.submission_id IS NULL OR r.next_check<=?) ORDER BY COALESCE(r.next_check,0),t.rowid LIMIT 3""",(stamp,))
        for row in rows:
            if self.stop_event.is_set():return
            failures=row['failures'] or 0
            # Reserve before the read. A crash can retry attachment later, but
            # can never cross the submission boundary again.
            self.store.execute('INSERT INTO company_watch_repairs VALUES(?,?,?,?,?) ON CONFLICT(submission_id) DO UPDATE SET state=excluded.state,next_check=excluded.next_check,updated_at=excluded.updated_at',
                (row['id'],'checking',stamp+600,failures,now()))
            try:
                session=self.store.session(row['session_id'])
                authority=json.loads(row['request']).get('authority',{})
                if session['channel']!='ept' or authority and authority.get('revision')!=self.config()['revision']:
                    raise ValueError('原监控连接范围已变化')
                self.watch(row['session_id'],row['task_uid'],True,analyze_terminal=True)
            except (ValueError,TimeoutError,OSError):
                failures+=1
                self.store.execute("UPDATE company_watch_repairs SET state='waiting',next_check=?,failures=?,updated_at=? WHERE submission_id=?",
                    (stamp+min(600,60*2**min(failures-1,4)),failures,now(),row['id']))
            else:
                self.store.execute("UPDATE company_watch_repairs SET state='attached',updated_at=? WHERE submission_id=?",(now(),row['id']))
                self.store.event(row['session_id'],'company.monitor_repaired',{'submission_id':row['id'],'task_uid':row['task_uid'],'resubmitted':False})

    def repair_unknown(self,stamp):
        # submit/perform hold this same lock across the actual SDK write. Do
        # not read back a still-live request; after restart there is no owner,
        # so a committed requested identity can be reconciled just like unknown.
        if not self.lock.acquire(blocking=False):return
        try:return self._repair_unknown(stamp)
        finally:self.lock.release()

    def _repair_unknown(self,stamp):
        """Read-back only: exact name identifies candidates, the original echo
        and immutable scope/time checks decide whether a UID may be attached.
        A failed/empty/ambiguous lookup never authorizes another submission.
        """
        rows=self.store.rows('''SELECT t.*,u.attempts FROM company_training t
            LEFT JOIN company_unknown_reads u ON u.submission_id=t.id
            WHERE t.state IN ('requested','unknown') AND t.task_uid IS NULL AND (u.submission_id IS NULL OR u.next_check<=?)
            ORDER BY COALESCE(u.next_check,0),t.rowid LIMIT 2''',(stamp,))
        for row in rows:
            if self.stop_event.is_set():return
            # Also defer unavailable old contexts. Otherwise two revoked/removed
            # oldest requests occupy the bounded batch forever and starve all
            # newer recoveries; no network lookup is authorized by this record.
            self.store.execute('INSERT INTO company_unknown_reads VALUES(?,?,?,?,?,?) ON CONFLICT(submission_id) DO UPDATE SET state=excluded.state,next_check=excluded.next_check,updated_at=excluded.updated_at',
                (row['id'],'context_pending',stamp+600,row['attempts'] or 0,dumps({'new_submission':False,'reason':'original_context_unavailable'}),now()))
            try:
                session=self.store.session(row['session_id']);frozen=json.loads(row['request']);identity=self.training._identity(row['id'])
                self.training._original_context(row['session_id'],row,frozen,identity)
            except (ValueError,OSError):continue
            if session['channel']!='ept' or not identity or not identity.get('requested_at'):continue
            attempts=(row['attempts'] or 0)+1;delay=min(600,30*2**min(attempts-1,5))
            self.store.execute('INSERT INTO company_unknown_reads VALUES(?,?,?,?,?,?) ON CONFLICT(submission_id) DO UPDATE SET state=excluded.state,next_check=excluded.next_check,attempts=excluded.attempts,updated_at=excluded.updated_at',
                (row['id'],'checking',stamp+delay,attempts,'{}',now()))
            outcome={'new_submission':False};state='waiting'
            try:
                try:
                    response=self._run([self.locator('lpai-agent'),'--timeout','25','tasks','get','--namespace',identity['namespace'],'--name',frozen['task_name']])
                    if response.get('status')!='ok':raise ValueError('原任务名称查询未确认')
                except (ValueError,TimeoutError,OSError):
                    # The platform may append an immutable UID to the supplied
                    # name. One small, namespace-scoped candidate list is allowed
                    # only after exact get failed; it never proves ownership.
                    response=self._run([self.locator('lpai-agent'),'--timeout','25','tasks','list','--namespace',identity['namespace'],
                        '--name-like',frozen['task_name'],'--page-size','5','--page-num','1'])
                    data=response.get('data');items=data.get('items') if isinstance(data,dict) else data
                    if response.get('status')!='ok' or not isinstance(items,list) or len(items)>5:raise ValueError('原任务候选列表未确认')
                    outcome['lookup_mode']='bounded_name_candidates'
                    if len(items)>3:raise ValueError('候选过多，不从截断列表猜测原任务')
                    eligible=[]
                    from .lpai_identity import task_observation,reconciliation_evidence
                    for candidate in items:
                        candidate_uid=checked_id(candidate.get('uid'))
                        full=self._run([self.locator('lpai-agent'),'--timeout','25','tasks','get','--namespace',identity['namespace'],'--uid',candidate_uid])
                        if full.get('status')!='ok':continue
                        observed=task_observation(full.get('data'))
                        if candidate_uid==frozen.get('predecessor_uid'):continue
                        try:missing,_=reconciliation_evidence(row,frozen,identity,observed,candidate_uid,False)
                        except ValueError:continue
                        if not missing:eligible.append(full)
                    if len(eligible)!=1:raise ValueError('没有唯一匹配原请求标记的候选；未绑定或重投')
                    response=eligible[0]
                from .lpai_identity import task_observation,reconciliation_evidence
                item=task_observation(response.get('data'));uid=checked_id(item.get('uid'))
                if uid==frozen.get('predecessor_uid'):raise ValueError('不能将前驱任务当作本次提交')
                missing,_=reconciliation_evidence(row,frozen,identity,item,uid,False)
                if missing:outcome['reason']=missing
                else:
                    result=self.training.reconcile(row['session_id'],row['id'],uid)
                    if result.get('state')=='submitted':state='reconciled';outcome['task_uid']=uid
                    else:outcome['reason']='original_identity_still_unconfirmed'
            except (ValueError,TimeoutError,OSError) as exc:outcome['error_type']=type(exc).__name__
            self.store.execute('UPDATE company_unknown_reads SET state=?,outcome=?,updated_at=? WHERE submission_id=?',(state,dumps(outcome),now(),row['id']))
            self.store.event(row['session_id'],'company.unknown_reconciliation',{'submission_id':row['id'],'state':state,**outcome})

    def watch_health(self,watch_id,stamp,error=None):
        prior=self.store.one('SELECT * FROM company_watch_health WHERE watch_id=?',(watch_id,)) or {}
        self.store.execute('INSERT INTO company_watch_health VALUES(?,?,?,?,?,?) ON CONFLICT(watch_id) DO UPDATE SET last_success=excluded.last_success,last_failure=excluded.last_failure,failures=excluded.failures,error_type=excluded.error_type,updated_at=excluded.updated_at',
            (watch_id,prior.get('last_success') if error else stamp,stamp if error else prior.get('last_failure'),
             prior.get('failures',0)+1 if error else 0,type(error).__name__ if error else None,now()))

    def watch_period(self,row,state):
        """Ordinary timers only. Short jobs need not wait a ten-minute queue tick."""
        owned=self.store.one('SELECT request FROM company_training WHERE session_id=? AND task_uid=?',(row['session_id'],row['task_uid']))
        minutes=json.loads(owned['request']).get('task_information',{}).get('expected_duration') if owned else None
        starting=state.lower() in {'starting','creating','image_pulling','initializing'}
        if type(minutes) is int and 0<minutes<=5:return 15 if starting else 30
        if type(minutes) is int and 5<minutes<=30:return 30 if starting else 60
        return 600 if state.lower() in {'pending','queued','scheduling','waiting'} else 60 if starting or row['state'].lower() in {'pending','queued','scheduling','waiting'} else 300

    def poll_watches(self,kernel,clock=None):
        stamp=time.time() if clock is None else clock
        config=self.config()
        if not config['lpai_enabled']:
            return
        self.repair_unknown(stamp)
        self.repair_watches(stamp)
        rows=self.store.rows("SELECT * FROM company_watches WHERE next_check<=? AND wake_state NOT IN ('already_terminal','dispatched','finished','authority_changed','research_scheduler','owner_unavailable') ORDER BY next_check LIMIT 10",(stamp,))
        for row in rows:
            self._current_watch=row['id']
            if self.stop_event.is_set():
                return
            try:session=self.store.session(row['session_id'])
            except ValueError:
                self.store.execute("UPDATE company_watches SET wake_state='owner_unavailable' WHERE id=?",(row['id'],))
                continue # An intentionally removed project must not starve other watches.
            if row['namespace']!=config['namespace']:
                self.store.execute('UPDATE company_watches SET next_check=? WHERE id=?',(stamp+600,row['id']))
                continue
            if row['state'].lower() not in TERMINAL:
                try:
                    item=self.lpai('task',uid=row['task_uid'])['item']
                    item['watch_authority']=json.loads(row['observation']).get('watch_authority')
                except (ValueError,TimeoutError,OSError) as exc:
                    self.watch_health(row['id'],stamp,exc)
                    self.store.execute('UPDATE company_watches SET next_check=? WHERE id=?',(stamp+600,row['id']))
                    continue
                if self.stop_event.is_set():return
                self.watch_health(row['id'],stamp)
                state=item['phase']
                period=self.watch_period(row,state)
                self.store.execute('UPDATE company_watches SET state=?,observation=?,next_check=? WHERE id=?',(state,dumps(item),stamp+period,row['id']))
                if state!=row['state']:
                    self.store.event(row['session_id'],'company.task_state_changed',{'id':row['id'],'item':item})
            else:
                item=json.loads(row['observation'])
                state=row['state']
            if state.lower() not in TERMINAL:
                continue
            if '_terminal_diagnostic' not in item or item['_terminal_diagnostic'].get('projection_version',0)<3:
                if state.lower() in {'failed','error','stopped','terminated','cancelled','canceled'}:
                    try:
                        diagnostic=self.training.diagnose(row['session_id'],row['task_uid'])
                        item['_terminal_diagnostic']={'id':diagnostic['id'],'path':diagnostic['path'],'signals':diagnostic['summary']['signals'][:3],'projection_version':3}
                    except (ValueError,TimeoutError,OSError):item['_terminal_diagnostic']={'unavailable':True,'reason':'失败取证暂不可用，不能认定没有异常或原样重试','projection_version':3}
                    if self.stop_event.is_set():return
                else:item['_terminal_diagnostic']={'not_requested':True,'projection_version':3}
                self.store.execute('UPDATE company_watches SET observation=? WHERE id=?',(dumps(item),row['id']))
                self.training.notice(row['session_id'],item,'terminal',f"任务 {row['task_name']} 已{state}；需核对日志、结果与产物，平台终态不等于研究通过。",item['_terminal_diagnostic'].get('id'))
                self.store.event(row['session_id'],'company.task_terminal',{'id':row['id'],'item':item})
            self.store.execute('UPDATE company_training SET state=?,updated_at=? WHERE task_uid=? AND session_id=?',(state,now(),row['task_uid'],row['session_id']))
            if not row['auto_resume']:
                self.store.execute("UPDATE company_watches SET wake_state='finished' WHERE id=?",(row['id'],))
                continue
            session=self.store.session(row['session_id'])
            if session['channel']!='ept' or item.get('watch_authority')!=self.authority(row['session_id']):
                self.store.execute("UPDATE company_watches SET wake_state='authority_changed' WHERE id=?",(row['id'],))
                self.store.event(row['session_id'],'company.wake_blocked',{'id':row['id'],'reason':'项目绑定或连接已变化，未按旧任务自动继续'})
                continue
            from .autonomy import policy,active
            p=policy(self.store,session['project_id']) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_autonomy_policies'") else None
            branch=active(self.store,session['project_id']) if p and p['mode']=='autonomous' and p['owner_session_id']==row['session_id'] else None
            owned=self.store.one('SELECT id,plan_id FROM company_training WHERE session_id=? AND task_uid=?',
                (row['session_id'],row['task_uid'])) if branch else None
            if owned and owned['plan_id']!=branch['head_plan_id']:
                # A late observer of an already reviewed former round must not
                # reopen collection/analysis or block the current plan.
                self.store.execute("UPDATE company_watches SET wake_state='research_scheduler' WHERE id=?",(row['id'],))
                continue
            collection=self.outputs.advance(row['session_id'],row['task_uid'],clock=stamp)
            if self.stop_event.is_set():return
            if collection['state'] not in {'ready','diagnostic_only'}:
                self.store.execute('UPDATE company_watches SET next_check=? WHERE id=?',(max(stamp+10,collection.get('next_check',0)),row['id']))
                continue
            item['_terminal_outputs']=collection
            self.store.execute('UPDATE company_watches SET observation=? WHERE id=?',(dumps(item),row['id']))
            paused=self.store.one('SELECT session_id FROM research_controls WHERE session_id=? AND paused=1',(row['session_id'],)) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_controls'") else None
            if session['archived'] or paused:
                self.store.execute('UPDATE company_watches SET next_check=? WHERE id=?',(stamp+60,row['id']));continue
            if owned:
                # One consumer for autonomous research: watcher only records
                # facts, scheduler/role handoff advances the original thread.
                self.store.execute("UPDATE company_watches SET wake_state='research_scheduler' WHERE id=?",(row['id'],))
                self.store.event(row['session_id'],'company.analysis_delegated',{'id':row['id'],'submission_id':owned['id']})
                continue
            if session['status']!='idle' or session['active_turn'] or session['channel']!=kernel.settings.value['channel']:
                # A human interruption/unknown state cannot be overridden by a watcher.
                self.store.execute('UPDATE company_watches SET next_check=? WHERE id=?',(stamp+60,row['id']))
                continue
            if self.store.one('SELECT session_id FROM research_controls WHERE session_id=? AND paused=1',(row['session_id'],)) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_controls'") else False:
                self.store.execute('UPDATE company_watches SET next_check=? WHERE id=?',(stamp+60,row['id']));continue
            text=(f"[LPAI后台事件 · 非用户新输入] 已绑定任务 {row['task_name']}（UID {row['task_uid']}）读回终态 {state}。"
                  "请结合原方案核对日志与产物，能读取则分析；当前通知只有平台终态，不是训练指标，不得编造结果。"
                  f"本次有限取证索引：{dumps(item.get('_terminal_diagnostic',{}))}。"
                  f"本地产物归档：{dumps(collection)}。优先读取本地报告；证据不足时只做异常/缺口分析，不能宣称模型指标通过，不循环下载。"
                  "失败时先看真正失败的训练Pod及最早异常，不把peer_abort当初始根因；成功时按固定协议核验指标和产物。保存证据和竞争解释，完成本轮评审并形成具体下一方案。"
                  "后续按research_status中的真实研究模式处理：人工模式待审核，自主模式仍需按原授权核验采纳；本事件不新增训练授权。"
                  "停止/取消只触发分析，不代表允许重投；先辨明用户主动停止、平台中断或未知原因，用户暂停不可覆盖。"
                  "不启动新训练、不修改云资源或推送代码，不把排障修复冒充模型质量提升。")
            delivery_id='lpai-terminal:'+row['id']
            kernel.send(row['session_id'],text,'queue',delivery_id,origin='background')
            self.store.execute("UPDATE company_watches SET wake_state='dispatched' WHERE id=?",(row['id'],))
            self.store.event(row['session_id'],'company.wake_queued',{'id':row['id'],'delivery_id':delivery_id,'new_training_submitted':False})

    def start_monitor(self,kernel):
        if self.monitor_thread:
            return
        def monitor():
            while not self.stop_event.wait(10):
                try:
                    self.monitor_tick(kernel);self.monitor_error_type=None
                except Exception as exc:
                    # A database outage can prevent even the durable error write.
                    # Keep the worker alive; expose only a safe in-memory type.
                    self.monitor_error_type=type(exc).__name__
        self.monitor_thread=threading.Thread(target=monitor,name='lpai-lightweight-watch',daemon=True)
        self.monitor_thread.start()

    def monitor_tick(self,kernel):
        """Safe observable failure boundary; never cancels or replaces training."""
        self._current_watch=None;stamp=time.time()
        prior=self.store.one('SELECT * FROM company_monitor_health WHERE singleton=1') or {}
        error=None
        try:self.space_observer.poll(stamp)
        except Exception as exc:error=exc
        try:self.poll_watches(kernel)
        except Exception as exc:
            error=error or exc
            if self._current_watch:
                self.watch_health(self._current_watch,stamp,exc)
                self.store.execute('UPDATE company_watches SET next_check=? WHERE id=?',(stamp+600,self._current_watch))
        self.store.execute('INSERT INTO company_monitor_health VALUES(1,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET last_success=excluded.last_success,last_failure=excluded.last_failure,failures=excluded.failures,error_type=excluded.error_type,updated_at=excluded.updated_at',
            (prior.get('last_success') if error else stamp,stamp if error else prior.get('last_failure'),
             prior.get('failures',0)+1 if error else 0,type(error).__name__ if error else None,now()))

    def close(self):
        self.stop_event.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=35)

    def unbind(self,session_id,kind,uid):
        if kind not in {'lpai','gitlab'}:
            raise ValueError('无效绑定类型')
        session=self.store.session(session_id)
        if session['active_turn']:
            raise ValueError('请先暂停当前轮次再解除绑定')
        self.store.execute('DELETE FROM company_bindings WHERE project_id=? AND kind=? AND uid=?',(session['project_id'],kind,uid))
        if kind=='lpai':
            self.store.execute('UPDATE company_watches SET auto_resume=0 WHERE session_id=?',(session_id,))
        self.store.event(session_id,'company.unbound',{'project_id':session['project_id'],'kind':kind,'uid':uid,'cloud_resource_stopped':False})
        return self.context(session_id)
