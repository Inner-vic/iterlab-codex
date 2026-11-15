"""Durable LPAI evidence and exact SDK submissions; Codex still diagnoses/decides."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

from .store import dumps, now, uid
from .lpai_evidence import scrub, inspection, failed_training_pods, failed_stages, log_lines, task_summary, apply_code_patches
from .lpai_identity import (READ_CHANNEL, MARKER_PREFIX, platform_identity,
                            reconciliation_evidence, task_observation)
from . import rounds

ROOT=Path(__file__).resolve().parents[1]
FINAL={'succeeded','success','completed','failed','stopped','terminated','cancelled','canceled','error'}
UNRESOLVED={'requested','unknown','submitted','pending_approval'}
# Existing, platform-verified CPU template and output volume for the company workspace.
CPU_PROFILES={'vc-chassis':('project-2b73bb73-5289-4681-9229-beb9ae4c4d5e','jfs-dip-vfsc-dataset-ga')}
AUTO_COMMAND_PATHS=['/lpai_task/cmd','/lpai_task/roles/0/cmd']

def command_paths(template):
    return ['/lpai_task/cmd',*['/lpai_task/roles/'+str(i)+'/cmd' for i in range(len(template['config'].get('task_role_specs') or [None]))]]


def sha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


class LPAITraining:
    def __init__(self,company):
        self.company,self.store=company,company.store
        with self.store.lock,self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS company_diagnostics(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,task_uid TEXT NOT NULL,
              task_phase TEXT NOT NULL,path TEXT NOT NULL,digest TEXT NOT NULL,summary TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_notices(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,task_uid TEXT NOT NULL,
              kind TEXT NOT NULL,text TEXT NOT NULL,evidence_id TEXT,created_at TEXT NOT NULL,UNIQUE(session_id,task_uid,kind));
            CREATE TABLE IF NOT EXISTS company_operation_reconciliations(operation_id TEXT PRIMARY KEY,submission_id TEXT NOT NULL,
              task_uid TEXT NOT NULL,receipt_sha256 TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_training(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,project_id TEXT NOT NULL,
              plan_id TEXT NOT NULL,trial_id TEXT NOT NULL,component_uid TEXT NOT NULL,task_uid TEXT,
              request TEXT NOT NULL,state TEXT NOT NULL,result TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS company_training_identity(
              submission_id TEXT PRIMARY KEY REFERENCES company_training(id) ON DELETE CASCADE,
              requested_at TEXT,namespace TEXT NOT NULL,profile TEXT NOT NULL,session_channel TEXT NOT NULL,
              read_channel TEXT NOT NULL,connection_authority TEXT NOT NULL,submission_authority TEXT,
              submission_marker TEXT NOT NULL,platform_identity TEXT,original_task_uid TEXT,
              receipt TEXT,receipt_at TEXT);
            INSERT OR IGNORE INTO schema_versions VALUES(9,datetime('now'));
            ''')
        from .component_compiler_repair import install
        install(self.store)

    def sdk(self,operation,**params):
        python=ROOT/'.runtime/lpai/Scripts/python.exe' if os.name=='nt' else ROOT/'.runtime/lpai/bin/python'
        if not python.is_file(): raise ValueError('独立官方LPAI SDK尚未安装；运行scripts/install-lpai.ps1，不切换其他提交通道')
        request={'operation':operation,'namespace':self.company.config()['namespace'],**params}
        try:
            # Measured cold Project.get took 57.5s. Only known reads get more
            # startup time; submit retains its original uncertainty boundary.
            result=self.company.runner([str(python),'-I',str(ROOT/'scripts/lpai_sdk_bridge.py')],input=dumps(request),
                capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=55 if operation=='submit' else 90,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('SDK响应超时；写入结果未知，不能重放' if operation in {'submit','provision_component','provision_configured'} else 'SDK只读检查超时；未提交任务') from exc
        if result.returncode or len(result.stdout)>800000:
            raise ValueError('SDK子进程未返回有效收据；未切换提交通道')
        response=json.loads(result.stdout)
        if not response.get('ok'):
            if response.get('error_type')=='ConfigurationError' and isinstance(response.get('validation'),str):
                raise ValueError(response['validation'][:260])
            if response.get('write_attempted'):
                raise TimeoutError('SDK写请求已经发出但结果未确认，需核对原身份')
            from .platform_health import PlatformError
            category=response.get('error_category','unconfirmed')
            raise PlatformError(category)
        return response['result']

    def _enabled(self):
        if not self.company.config()['lpai_enabled']: raise ValueError('LPAI连接未启用')

    def can_preview_component(self,sid):
        config=self.company.config();session=self.store.session(sid)
        return bool(config['lpai_enabled'] and session['channel']=='ept'
                    and self._settings_channel() in {None,'ept'})

    def validate_preview(self,frozen):
        template=frozen['template']
        if template.get('schema')!=1:return
        connection=self.company.config()
        checked=self.sdk('configured_template',recipe=template['recipe'],component_name=template['config']['name'],connection_revision=connection['revision'])
        if (checked['config_sha256']!=template['config_sha256'] or checked['account_scope']!=template['account_scope']
                or checked['gpu_count']!=template['gpu_count'] or checked['assets']!=template['assets']):
            raise ValueError('资源或资产已变化，请重新生成方案运行配置；未创建或提交')

    def validate_provisioned(self,grant,component_uid):
        row=self.store.one('''SELECT v.configuration FROM research_component_provisions p
            JOIN research_component_previews v ON v.id=p.preview_id WHERE p.grant_id=?''',(grant['id'],))
        if not row:return
        frozen=json.loads(row['configuration']);template=frozen['template']
        if template.get('schema')!=1:return
        self.validate_preview(frozen)
        checked=self.sdk('verify_configured',component_uid=component_uid,recipe=template['recipe'],
            component_name=template['config']['name'],connection_revision=self.company.config()['revision'],
            expected_config_sha256=template['config_sha256'])
        if checked['uid']!=component_uid or checked['gpu_count']!=template['gpu_count']:raise ValueError('实际组件与审核资源不符')

    def _configured_preview(self,sid,plan,recipe,*,approved_compute=None):
        from .research import plan_digest
        exact=plan_digest(plan);connection=self.company.config();namespace=connection['namespace']
        name='iterlab-'+sha([sid,plan['id'],exact,namespace,recipe])[:24]
        template=self.sdk('configured_template',recipe=recipe,component_name=name,connection_revision=connection['revision'])
        from .devspace import compute
        requested=compute(plan) or approved_compute
        if not requested or requested['workflow']!='batch' or template['gpu_count']!=requested['gpu_count']:
            raise ValueError('推荐运行配置与方案GPU数量或批量用途不一致')
        if self.company.config()!=connection:raise ValueError('连接已变化，请重新核对')
        identity={'namespace':namespace,'revision':connection['revision'],'profile':self._profile()}
        frozen={'identity':identity,'template':template,'volume':template['output_volume']['name']}
        preview_id=sha([sid,plan['id'],exact,frozen])
        self.store.execute('INSERT OR IGNORE INTO research_component_previews VALUES(?,?,?,?,?)',(preview_id,sid,plan['id'],exact,dumps(frozen)))
        self.company.resource_directory.require_template(sid,template)
        return {'id':preview_id,'plan_id':plan['id'],'plan_digest':exact,'name':name,'namespace':namespace,
            'cpu':template['cpu'],'memory_gib':template['memory_gib'],'gpu_count':template['gpu_count'],
            'gpu_type':template['package']['gpu_type'],'workers':template['workers'],'image':template['config']['image'],
            'volume':frozen['volume'],'queue':template['config']['queue_code'],'queue_name':template['queue_name'],
            'command':recipe['command'],'billing_project':template['config']['gems_project'],
            'training_parameters':recipe['training_parameters'],'rationale':recipe['rationale'],
            'compatibility':template['software_compatibility'],'mounts':template['config']['input_data_volume']+template['config']['input_dataset'],
            'account_scope':template['account_scope'],'config_sha256':template['config_sha256'],'command_paths':command_paths(template)}

    def preview_component(self,sid,plan):
        from .component_configuration import recommendation
        recipe=recommendation(self.store,plan)
        if recipe:
            if not self.can_preview_component(sid):raise ValueError('当前连接不可用')
            return self._configured_preview(sid,plan,recipe)
        proposal=json.loads(plan['source']).get('scope_proposal')
        approved=self.store.one("SELECT id FROM research_reviews WHERE plan_id=? AND decision='accept' LIMIT 1",(plan['id'],))
        if proposal and not approved:
            raise ValueError('新的组件库范围缺少scope_proposal.component_config及其冻结配置证据；请由Agent补齐compute、budget和运行配置后重新审议。未改用CPU默认模板、未创建组件或提交任务。')
        from .devspace import compute
        target=compute(plan)
        if target and target['gpu_count']:
            raise ValueError('当前自动创建配置只支持 CPU；GPU 方案需核对匹配的已配置 GPU 组件')
        from .research import plan_digest
        if not self.can_preview_component(sid) or self.company.config()['namespace'] not in CPU_PROFILES:raise ValueError('当前连接没有可用的新建组件资源配置；需先生成方案运行配置')
        exact=plan_digest(plan)
        with self.company.lock:
            connection=self.company.config();namespace=connection['namespace'];profile=self._profile()
            if not connection['lpai_enabled'] or namespace not in CPU_PROFILES:raise ValueError('连接已变化，请重新核对')
            template,volume=CPU_PROFILES[namespace]
            name='iterlab-'+sha([sid,plan['id'],exact,namespace])[:24]
            identity={'namespace':namespace,'revision':connection['revision'],'profile':profile}
            rows=self.store.rows('SELECT id,configuration FROM research_component_previews WHERE session_id=? AND plan_id=? AND plan_digest=?',(sid,plan['id'],exact))
            prior=next((row for row in reversed(rows) if json.loads(row['configuration'])['identity']==identity),None)
            if prior:
                frozen=json.loads(prior['configuration']);preview_id=prior['id']
            else:
                template_data=self.sdk('cpu_template',component_uid=template,component_name=name,volume_name=volume)
                if (template_data['namespace']!=namespace or template_data['source_uid']!=template or template_data['config']['name']!=name
                        or template_data['gpu_count']!=0 or template_data['cpu']!=1 or template_data['memory_gib']!=4):
                    raise ValueError('官方组件模板身份或资源范围未确认')
                frozen={'identity':identity,'template':template_data,'volume':volume}
                preview_id=sha([sid,plan['id'],exact,frozen])
                self.store.execute('INSERT OR IGNORE INTO research_component_previews VALUES(?,?,?,?,?)',(preview_id,sid,plan['id'],exact,dumps(frozen)))
        config=frozen['template']['config']
        self.company.resource_directory.require_template(sid,frozen['template'])
        return {'id':preview_id,'plan_id':plan['id'],'plan_digest':exact,'name':name,'namespace':namespace,
                'cpu':1,'memory_gib':4,'gpu_count':0,'image':config['image'],'volume':volume,'queue':config['queue_code']}

    def provision_component(self,sid,plan,grant,request,patches):
        from . import cloud_runtime,task_policy
        from .autonomy import finalizing,policy
        provision=self.store.one('''SELECT p.*,v.session_id,v.configuration FROM research_component_provisions p
                                    JOIN research_component_previews v ON v.id=p.preview_id WHERE p.grant_id=?''',(grant['id'],))
        if not provision:return request.get('component_uid')
        owner=policy(self.store,plan['project_id'])
        authority=self.store.one('SELECT grant_id FROM research_authorities WHERE id=?',(owner['authority_id'],)) if owner else None
        if not owner or owner['owner_session_id']!=sid or owner['mode']!='autonomous' or not authority or authority['grant_id']!=grant['id']:
            raise ValueError('原自主执行授权未启用，未创建组件')
        from .component_compiler_repair import refresh
        provision=refresh(self,sid,plan,grant,provision)
        frozen=json.loads(provision['configuration']);template=frozen['template'];config=template['config']
        configured=template.get('schema')==1
        self.validate_preview(frozen)
        self.company.resource_directory.require_template(sid,template)
        identity=frozen['identity'];connection=self.company.config();pid=plan['project_id'];grant_id=grant['id']
        if provision['session_id']!=sid or identity!={'namespace':connection['namespace'],'revision':connection['revision'],'profile':self._profile()}:
            raise ValueError('组件创建的原会话或平台连接已变化')
        uid_value=provision['component_uid']
        if request.get('component_uid') and request['component_uid']!=uid_value:raise ValueError('不能替换已审核的自动组件')
        command_policy=task_policy.from_grant(self.store,grant)
        task_policy.allowed(command_policy,patches)
        commands={patch['path']:patch['value'] for patch in patches}
        expected_paths=command_paths(template) if configured else AUTO_COMMAND_PATHS
        if sorted(commands)!=expected_paths or len(set(commands.values()))!=1 or not configured and config['cmd'] in commands.values():
            raise ValueError('自动组件需为总入口和单角色入口冻结同一实际训练命令')
        apply_code_patches({'lpai_task':{'cmd':config['cmd'],'roles':[{'cmd':config['cmd']} for _ in expected_paths[1:]]}},patches)
        output=request.get('output_contract') or {}
        runtime=cloud_runtime.authorize(command_policy,output.get('runtime'))
        source=cloud_runtime.source(output.get('source'))
        if (not output.get('required_files') or set([runtime['command_path'],*runtime.get('mirror_paths',[])])!=set(AUTO_COMMAND_PATHS)
                or source['name']!=frozen['volume'] or source['mount_root']!=(template['output_volume']['mount_path'] if configured else '/mnt/volumes/'+frozen['volume'])):
            raise ValueError('自动组件需明确必要产物、已确认卷及双入口结果执行器')
        if provision['state']=='ready':return uid_value
        if provision['state'] in {'name_conflict','preflight_failed'}:raise ValueError('原组件准备已阻塞，须核对预览及冲突，未重建组件')
        if provision['state']=='approved':
            current=rounds.approved_plan(self.store,pid)
            if (not current or current['id']!=plan['id'] or rounds.round_review(self.store,pid,plan['id'])
                    or finalizing(self.store,pid) or self.store.one('SELECT session_id FROM research_controls WHERE session_id=? AND paused=1',(sid,))
                    or rounds.pending_cloud(self.store,pid) or rounds.pending_platform_operation(self.store,pid)
                    or self.store.one("SELECT id FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown')",(pid,))
                    or self.store.one("SELECT id FROM deliveries WHERE session_id=? AND state IN ('requested','unknown')",(sid,))):
                raise ValueError('研究已暂停、未获执行或存在未决运行；未创建组件')
            rounds.check_trial_dependencies(self.store,pid,plan['id'],request['trial_id'])
            from .task_outputs import require_collected
            require_collected(self.store,pid)
            if self.store.evidence.blocking(pid,verify_ready=True):raise ValueError('原本地结果待核对，未创建组件')
            volume=self.company.outputs.inspect_volume(source['name'])
            if volume['uid']!=source['uid'] or volume['namespace']!=identity['namespace']:
                raise ValueError('结果卷UID或工作空间未核实，未创建组件')
            used=self.store.one('SELECT COUNT(*) AS n FROM research_runs WHERE project_id=?',(pid,))['n']
            used_cloud=self.store.one("SELECT COUNT(*) AS n FROM company_training WHERE project_id=? AND state<>'prepared'",(pid,))['n']
            used_fits=sum(json.loads(row['request']).get('purpose')=='fit' for row in self.store.rows('SELECT request FROM research_runs WHERE project_id=?',(pid,)))
            if used+used_cloud>=grant['max_runs'] or used_fits+used_cloud>=grant['max_fits']:
                raise ValueError('原累计运行或拟合额度已用完；未创建组件')
            claimed=self.store.execute("UPDATE research_component_provisions SET state='requested',updated_at=? WHERE grant_id=? AND state='approved'",(now(),grant_id))
            if not claimed:raise ValueError('组件创建已由原请求认领；请只读核对')
            try:
                if configured:
                    receipt=self.sdk('provision_configured',recipe=template['recipe'],component_name=config['name'],
                        connection_revision=connection['revision'],expected_config_sha256=template['config_sha256'])
                else:
                    receipt=self.sdk('provision_component',component_uid=template['source_uid'],component_name=config['name'],volume_name=frozen['volume'],
                        config=config,cluster=template['cluster'],source_digest=template['source_digest'],expected_config_sha256=template['config_sha256'])
            except Exception as exc:
                self.store.execute("UPDATE research_component_provisions SET state='unknown',updated_at=? WHERE grant_id=?",(now(),grant_id))
                self.store.event(sid,'company.component_create_unconfirmed',{'grant_id':grant_id,'preview_id':provision['preview_id'],'error_type':type(exc).__name__})
                raise ValueError('组件创建结果未知；下一次仅核对原名称，不重复创建') from exc
            state=receipt.get('state')
            if state in {'name_conflict','preflight_failed','unknown'}:
                self.store.execute('UPDATE research_component_provisions SET state=?,updated_at=? WHERE grant_id=?',(state,now(),grant_id))
                self.store.event(sid,'company.component_create_unconfirmed',{'grant_id':grant_id,'preview_id':provision['preview_id'],
                    'receipt':{k:receipt[k] for k in ('state','write_attempted','error_type','error_category','http_status','response_type','response_keys') if k in receipt}})
                raise ValueError('组件创建未确认；保留原请求，禁止重复创建。请结束本轮等待后台按原名称核对完整配置；核对后宿主将自动向原会话接续，无须搜索旧提交目录或请求新授权。')
            if state!='created':raise ValueError('组件创建收据未确认；保留原请求，禁止重复创建')
            uid_value=receipt['uid']
        elif not uid_value:
            found=self.sdk('lookup_component',component_name=config['name'])
            if found.get('state')!='observed' or found.get('name')!=config['name'] or found.get('namespace')!=identity['namespace']:
                raise ValueError('尚未查到原组件；保留未知状态，不重复创建')
            uid_value=found['uid']
        from .company import checked_id
        checked_id(uid_value)
        self.store.execute("UPDATE research_component_provisions SET state='verifying',component_uid=?,updated_at=? WHERE grant_id=?",(uid_value,now(),grant_id))
        component=self.sdk('verify_configured',component_uid=uid_value,recipe=template['recipe'],component_name=config['name'],
            connection_revision=connection['revision'],expected_config_sha256=template['config_sha256']) if configured else self.sdk('verify_provisioned_component',component_uid=uid_value,config=config,cluster=template['cluster'])
        if (component['uid']!=uid_value or component['name']!=config['name'] or component['namespace']!=identity['namespace']
                or component['gpu_count']!=template.get('gpu_count',0) or len(component['digest'])!=64):raise ValueError('创建组件的身份或资源读回未确认')
        with self.store.lock,self.store.db:
            previous=self.store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant_id,))
            expected={'grant_id':grant_id,'component_uid':uid_value,'component_digest':component['digest'],'max_gpus':template.get('gpu_count',0)}
            if previous and previous!=expected:raise ValueError('原云端范围已指向不同组件，不能覆盖')
            if not previous:self.store.db.execute('INSERT INTO research_cloud_scopes VALUES(?,?,?,?)',(grant_id,uid_value,component['digest'],template.get('gpu_count',0)))
            self.store.db.execute("UPDATE research_component_provisions SET state='ready',updated_at=? WHERE grant_id=?",(now(),grant_id))
        return uid_value

    def _settings(self):
        # App-owned settings only, never an SDK/CLI credential file or login probe.
        path=self.store.root/'settings.json'
        value=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if not isinstance(value,dict):raise ValueError('原模型设置格式未确认')
        return value

    def _profile(self):
        profile=self._settings().get('profile','default')
        if not isinstance(profile,str) or not profile.strip():raise ValueError('原模型profile未确认')
        return profile

    def _settings_channel(self):
        return self._settings().get('channel')

    def _identity(self,id):
        row=self.store.one('SELECT * FROM company_training_identity WHERE submission_id=?',(id,))
        if row:
            row['receipt_json']=row['receipt']
            for key in ('connection_authority','submission_authority','platform_identity','receipt'):
                row[key]=json.loads(row[key]) if row[key] else None
        return row

    def _original_context(self,sid,row,frozen,identity,*,offline_export=False):
        """A read-back is not a new grant; allow policy mode changes, not identity changes."""
        self._enabled();session=self.store.session(sid)
        if session['project_id']!=row['project_id']:raise ValueError('原提交的项目身份已改变')
        namespace=(identity or {}).get('namespace') or (frozen.get('component') or {}).get('namespace')
        if not namespace or self.company.config()['namespace']!=namespace:raise ValueError('namespace与原提交不同，未切换只读通道')
        if session['channel']!=(identity or {}).get('session_channel','ept'):raise ValueError('原会话通道已改变')
        if identity:
            if identity['read_channel']!=READ_CHANNEL:raise ValueError('原只读通道不受支持，未切换通道')
            if identity['profile']!=self._profile():raise ValueError('profile与原提交不同，未混用模型身份')
            if self._settings_channel() not in {None,identity['session_channel']}:raise ValueError('原模型通道已改变，未切换只读通道')
            if offline_export:
                # Export is stored public identity only, not an execution/read grant.
                # authority() also invokes Git and creates a hook directory; do not
                # invoke that helper or claim to have rechecked the current Git HEAD.
                bindings=self.store.rows('SELECT kind,scope,uid,name FROM company_bindings WHERE project_id=? ORDER BY kind',(row['project_id'],))
                original=identity['connection_authority']
                if (not isinstance(original,dict) or set(original)!={'revision','bindings_hash','git_head'}
                        or original['revision']!=self.company.config()['revision'] or original['bindings_hash']!=sha(bindings)):
                    raise ValueError('原连接/绑定authority已改变；离线导出不调用Git')
            elif identity['connection_authority']!=self.company.authority(sid):raise ValueError('原连接/绑定authority已改变')
            authority=identity.get('submission_authority')
            if isinstance(authority,dict) and authority.get('kind')=='delegated_research_scope':
                from .autonomy import policy
                p=policy(self.store,row['project_id'])
                original=self.store.one('SELECT * FROM research_authorities WHERE id=?',(authority.get('authority_id'),))
                if (not p or p['authority_id']!=authority.get('authority_id') or p['owner_session_id']!=sid
                        or not original or original['project_id']!=row['project_id']
                        or original['grant_id']!=authority.get('grant_id') or original['scope_digest']!=authority.get('scope_digest')):
                    raise ValueError('原程序提交authority已改变；只读核对不新增授权')
                scope=json.loads(original['scope'])
                if sha(scope)!=authority['scope_digest']:raise ValueError('原程序提交authority的冻结范围摘要不符')
                if scope.get('channel')!=session['channel'] or scope.get('model_profile')!=identity['profile']:
                    raise ValueError('原程序授权的profile/通道与原提交不同')
        elif frozen.get('authority')!=self.company.authority(sid):
            raise ValueError('旧提交的原连接/绑定authority未确认')

        if identity and identity.get('submission_authority'):
            authority=identity['submission_authority']
            if (not isinstance(authority,dict) or authority.get('kind') not in {'human_confirmation','delegated_research_scope'}
                    or authority.get('submission_id')!=row['id'] or authority.get('plan_id')!=row['plan_id']
                    or authority.get('kind')=='human_confirmation' and authority.get('session_id')!=sid):
                raise ValueError('原提交审批authority与原方案/会话不一致')

    def recovery_origin(self,sid,id):
        """Export only stored public identity for an explicit read-only probe.

        The export has a content checksum, not a signature or a new grant. It
        does not call a platform, infer output provenance, or open another store.
        """
        from .cloud_evidence import fingerprint,validate_origin
        from .lpai_identity import timestamp
        with self.company.lock,self.store.lock:
            row=self.store.one('SELECT * FROM company_training WHERE id=? AND session_id=?',(id,sid))
            if not row or row['state'] in {'prepared','requested','unknown'}:
                raise ValueError('须有原提交的确切UID收据，不能把准备/未知请求导出为已执行身份')
            identity=self._identity(id)
            known=self._known_uids(row,identity)
            if len(known)!=1 or row['task_uid'] not in known or not self._receipt_matches(identity,row['task_uid']):
                raise ValueError('原任务UID/提交身份收据不一致，未导出替代身份')
            if not timestamp(identity['requested_at']) or not identity.get('submission_authority'):
                raise ValueError('原实际提交时间/授权身份未确认')
            receipt_item=identity['receipt']['item']
            if (receipt_item.get('namespace') not in {None,identity['namespace']}
                    or receipt_item.get('project_uid') not in {None,row['component_uid']}):
                raise ValueError('原收据namespace/组件与锁定身份不一致')
            frozen=json.loads(row['request'])
            self._original_context(sid,row,frozen,identity,offline_export=True)
            plan=self.store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='plan'",(row['plan_id'],row['project_id']))
            trial=self.store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='trial'",(row['trial_id'],row['project_id']))
            if (not plan or not trial or json.loads(trial['source']).get('plan_id')!=plan['id']
                    or frozen.get('plan_id')!=plan['id'] or frozen.get('trial_id')!=trial['id']
                    or frozen.get('component_uid')!=row['component_uid']):
                raise ValueError('原方案/实验/组件身份不一致，未导出')
            # Compare to the original grant/admission, not a checksum calculated
            # only from today's edited record. Read-back must not relabel history.
            authority=identity['submission_authority']
            grant=self.store.one('SELECT * FROM research_grants WHERE id=? AND project_id=?',
                (authority.get('grant_id'),row['project_id']))
            if not grant or grant['execution']!='lpai':raise ValueError('原方案的云执行授权快照不存在')
            expected=grant['plan_digest'] if grant['plan_id']==plan['id'] else None
            if authority['kind']=='delegated_research_scope' and expected is None:
                admission=self.store.one('SELECT * FROM research_plan_admissions WHERE plan_id=? AND authority_id=?',
                    (plan['id'],authority['authority_id']))
                if admission and admission['scope_digest']==authority['scope_digest']:expected=admission['plan_digest']
            if expected!=sha({k:plan[k] for k in ['id','version','content','evidence']}):
                raise ValueError('原方案的冻结snapshot与当前记录不符，未导出为原执行快照')
            origin={'project_id':row['project_id'],'session_id':sid,'plan_id':row['plan_id'],
                'plan_version':plan['version'],'trial_id':row['trial_id'],'submission_id':id,
                'task_uid':row['task_uid'],'task_name':identity['receipt']['item'].get('name') or frozen['task_name'],
                'namespace':identity['namespace'],'component_uid':row['component_uid'],
                'profile':identity['profile'],'session_channel':identity['session_channel'],
                'read_channel':identity['read_channel'],'requested_at':identity['requested_at'],
                'platform_identity':identity['platform_identity'],
                'plan_snapshot_sha256':fingerprint({k:plan[k] for k in ['id','version','content','evidence']}),
                'request_sha256':fingerprint(frozen),'receipt_sha256':fingerprint(identity['receipt']),
                'submission_authority_sha256':fingerprint(identity['submission_authority']),
                'connection_authority_sha256':fingerprint(identity['connection_authority'])}
            result={'schema':'iterlab-cloud-origin/v1','origin':origin,'sha256':fingerprint(origin),
                'exported_at':now(),'platform_read_performed':False,
                'meaning':'仅原库公开身份的有界导出；内容校验不是来源签名，不授权下载/训练，不证明资产版本归属'}
            validate_origin(result)
            return result

    def _submission_authority(self,sid,row,grant):
        # Use the source already recorded by Company.perform, never infer a user click
        # from the current manual/autonomous policy mode.
        operations=self.store.rows("SELECT request FROM company_operations WHERE session_id=? AND state='requested' ORDER BY rowid DESC",(sid,))
        requests=[json.loads(operation['request']) for operation in operations]
        sources=[request.get('approval_source') for request in requests if request.get('action')=='lpai_submit' and request.get('submission_id')==row['id']]
        if len({dumps(source) for source in sources})>1:raise ValueError('同一提交有冲突的原审批来源，未猜测人工/程序授权')
        source=sources[0] if sources else None
        if source:
            if not isinstance(source,dict) or source.get('kind')!='delegated_research_scope':raise ValueError('原提交审批来源未确认')
            expected=self.delegated_submission(sid,row['id'])
            if (not expected or any(source.get(key)!=expected.get(key) for key in ('kind','authority_id','plan_id','submission_id'))):
                raise ValueError('原程序审批身份与确切提交不一致')
            original=self.store.one('SELECT * FROM research_authorities WHERE id=?',(source['authority_id'],))
            return {**expected,'grant_id':grant['id'],'scope_digest':original['scope_digest']}
        return {'kind':'human_confirmation','session_id':sid,'plan_id':row['plan_id'],'submission_id':row['id'],'grant_id':grant['id']}

    def _current_result(self,row):
        if row['result']:
            result=json.loads(row['result'])
            if ((result.get('item') or {}).get('uid') and self.store.one(
                    "SELECT id FROM company_notices WHERE session_id=? AND task_uid=? AND kind='monitor_attach_failed'",
                    (row['session_id'],result['item']['uid']))):result={**result,'monitor_attach_failed':True}
            return result
        result={'state':row['state'],'resubmitted':False}
        if row['task_uid']:result['item']={'uid':row['task_uid']}
        return result

    def _known_uids(self,row,identity):
        values=[row['task_uid'],(identity or {}).get('original_task_uid')]
        receipts=[(identity or {}).get('receipt'),json.loads(row['result']) if row['result'] else None]
        for receipt in receipts:
            if isinstance(receipt,dict) and receipt.get('state')=='submitted':values.append((receipt.get('item') or {}).get('uid'))
        return {value for value in values if value}

    def _receipt_matches(self,identity,task_uid):
        if not identity or identity.get('original_task_uid')!=task_uid:return False
        receipt=identity.get('receipt')
        return bool(isinstance(receipt,dict) and receipt.get('state')=='submitted'
                    and (receipt.get('item') or {}).get('uid')==task_uid)

    def _unknown_observation(self,row,identity,item,reason):
        result={'state':row['state'],'identity_reconciled':False,'resubmitted':False,'reason':reason,
                'message':'原提交缺少可确认的来源证据；仅只读观察，不绑定猜测的UID，不重提',
                'observation':item}
        known=self._known_uids(row,identity)
        if len(known)==1:
            result['item']=item
            result['original_task_uid']=next(iter(known))
        else:result['candidate_uid']=item.get('uid')
        return result

    def _attach_watch(self,sid,id,result):
        try:self.company.watch(sid,result['item']['uid'],True,analyze_terminal=True)
        except Exception:
            self.notice(sid,result['item'],'monitor_attach_failed','已提交，首次监控绑定暂未成功；后台将按原UID重试绑定，不会重新提交任务')
            self.store.event(sid,'company.monitor_attach_failed',{'id':id,'state':'submitted',
                'task_uid':result['item']['uid'],'resubmitted':False,'new_authorization':False})
            result={**result,'monitor_attach_failed':True}
        return result

    def diagnose(self,session_id,task_uid,refresh=False):
        from .company import checked_id
        self._enabled();checked_id(task_uid)
        self.store.session(session_id)
        with self.company.lock:
            prior=self.store.one('SELECT * FROM company_diagnostics WHERE session_id=? AND task_uid=? ORDER BY rowid DESC LIMIT 1',(session_id,task_uid))
            if prior and not refresh and json.loads(prior['summary']).get('projection_version')==4:
                return {**prior,'summary':json.loads(prior['summary']),'cached':True}
            cli=self.company.locator('lpai-agent');namespace=self.company.config()['namespace']
            response=self.company._run([cli,'--timeout','20','tasks','inspect','--namespace',namespace,'--uid',task_uid],timeout=25)
            if self.company.stop_event.is_set():raise InterruptedError('服务正在关闭，原任务仍保留')
            if response.get('status')!='ok': raise ValueError('官方诊断未成功，保留原任务，不推测失败原因')
            data=response['data'];evidence=inspection(data)
            if evidence['task']['uid']!=task_uid or evidence['task'].get('namespace')!=namespace:
                raise ValueError('诊断对象与原任务不一致')
            evidence['training_logs']={};evidence['log_errors']={}
            selected=failed_training_pods(data.get('pods'))[:2]
            for pod in selected:
                if self.company.stop_event.is_set():raise InterruptedError('服务正在关闭，未继续采集')
                name=checked_id(pod.get('name'));start=(pod.get('create_timestamp') or {}).get('time');end=(pod.get('finish_timestamp') or {}).get('time')
                # Pod finish timestamps may be rounded to seconds: the actual
                # traceback can occur in that last fractional second. Use the
                # already confirmed later task terminal timestamp, never now.
                task_end=(evidence['task'].get('update_timestamp') or {}).get('time')
                if end and task_end and end<task_end:end=task_end
                if not start or not end or start.startswith('0001') or end.startswith('0001'):
                    evidence['log_errors'][name]='缺少确认的已结束时间窗，未改用默认当前窗口'
                    continue
                args=[cli,'--timeout','20','logs','pod','--namespace',namespace,'--cluster',checked_id(evidence['task']['cluster']),
                      '--pod',name,'--start',start,'--end',end,'--limit','120','--direction','backward']
                try:
                    raw=self.company._run(args,timeout=25)
                    if raw.get('status')!='ok': raise ValueError('官方日志未成功')
                    evidence['training_logs'][name]={'pod_uid':pod.get('uid'),'start':start,'end':end,'lines':log_lines(raw)}
                except (ValueError,TimeoutError,OSError): evidence['log_errors'][name]='该训练Pod日志暂不可用；不把空日志视为无异常'
            if self.company.stop_event.is_set():raise InterruptedError('服务正在关闭，不写入不完整收据')
            signals=[]
            for name,logs in evidence['training_logs'].items():
                for entry in logs['lines']:
                    text=entry['text']
                    if any(word in text for word in ['ModuleNotFoundError:','ImportError:','bounded_container_memory_observation_unavailable','CUDA out of memory','Traceback','RuntimeError:','NORMAL_JOB_EVENT','level=error','level=fatal','Permission denied','command not found','IterLab preflight failed','IterLab result lifecycle: results_incomplete','IterLab CPU component ready','IterLab approved component ready']):
                        item={'pod':name,'text':text[:700]}
                        if item not in signals: signals.append(item)
            primary=[item for item in signals if 'ModuleNotFoundError:' in item['text'] or 'bounded_container_memory_observation_unavailable' in item['text'] or 'CUDA out of memory' in item['text']]
            summary={'projection_version':4,'task':evidence['task'],'failed_stages':failed_stages(evidence['task']),
                'failed_training_pods':len(selected),'log_pods':len(evidence['training_logs']),
                'signals':(primary+[s for s in signals if s not in primary])[:10],'partial':bool(evidence['log_errors']) or not selected or not any(v['lines'] for v in evidence['training_logs'].values()),
                'meaning':'日志事实及异常线索，不是自动根因认定；peer_abort/ChildFailedError可能是连带退出，不据此调参或原样重试'}
            id=uid();relative='platform/lpai/evidence/'+task_uid+'/'+id+'.json'
            path=self.store.safe_path(self.store.session(session_id)['project_id'],relative)
            path.parent.mkdir(parents=True,exist_ok=True)
            evidence.update(observed_at=now(),summary=summary,provenance='official lpai-agent tasks.inspect + confirmed failed training pod logs')
            path.write_text(dumps(evidence),encoding='utf-8')
            fingerprint=hashlib.sha256(path.read_bytes()).hexdigest()
            self.store.execute('INSERT INTO company_diagnostics VALUES(?,?,?,?,?,?,?,?)',(id,session_id,task_uid,evidence['task']['phase'],relative,fingerprint,dumps(summary),now()))
            # Operational evidence lives with the run. It is not a scientific
            # finding merely because a program collected it. Existing records
            # remain intact; research analysis can explicitly cite this file.
            self.store.event(session_id,'company.diagnosed',{'id':id,'task_uid':task_uid,'summary':summary,'path':relative})
            return {'id':id,'task_uid':task_uid,'path':relative,'digest':fingerprint,'summary':summary,'cached':False}

    def notice(self,sid,item,kind,text,evidence_id=None):
        id=sha([sid,item['uid'],kind])[:32]
        self.store.execute('INSERT OR IGNORE INTO company_notices VALUES(?,?,?,?,?,?,?)',(id,sid,item['uid'],kind,text,evidence_id,now()))
        return id

    @staticmethod
    def _check_duration(info,timeout_seconds):
        minutes=info['expected_duration']
        if minutes*60>timeout_seconds:
            raise ValueError(f'预计时长 expected_duration 的单位是分钟：收到 {minutes} 分钟，超过原授权 {timeout_seconds} 秒（最多 {timeout_seconds//60} 整分钟）。仅修正真实估计后重新预检，不重投、不改授权；实际命令时限仍由宿主执行。')

    def prepare(self,sid,request):
        from .company import checked_id
        self._enabled();session=self.store.session(sid)
        from .execution_request import require_route
        require_route(self.store,session['project_id'],'lpai')
        from .research_modules import ResearchModules
        methods=ResearchModules(self.store)
        methods.preflight(sid,methods.plan(sid,request.get('plan_id')))
        if session['channel']!='ept' or self._settings_channel() not in {None,session['channel']}:
            raise ValueError('公司训练只能从原企业EPT会话/模型通道准备')
        compatibility=None
        if request.get('patch_file') and 'work_package' not in request:
            # Existing native threads have pinned tools. An explicitly versioned
            # logical manifest travels through the already registered file field;
            # ordinary JSON/files/prose never become control instructions.
            name=request['patch_file']
            path=self.store.evidence_path(session['project_id'],name)
            if path.is_file() and name.endswith('.json') and path.stat().st_size<=32768:
                from .evaluation_contract import json_text
                body=path.read_bytes();value=json_text(body.decode('utf-8-sig'))
                if isinstance(value,dict) and value.get('schema')=='iterlab-experiment/v1':
                    if request.get('output_contract') is not None:
                        raise ValueError('版本化实验声明由程序生成结果合同，不与高级output_contract混用')
                    compatibility={'mode':'registered_patch_file_v1','path':name,'sha256':hashlib.sha256(body).hexdigest(),
                                   'task_information_generated':True}
                    request={**{k:v for k,v in request.items() if k not in {'patch_file','task_information','output_contract'}},
                             'work_package':{k:v for k,v in value.items() if k!='schema'}}
        simplified='work_package' in request
        if simplified:
            if 'patch_file' in request or 'output_contract' in request or 'task_information' in request:
                raise ValueError('work_package由程序生成入口补丁、结果合同和平台任务说明；不要同时填写patch_file/output_contract/task_information')
            request={**{k:'' for k in ['predecessor_uid','diagnostic_id','change_summary','validation']},**request}
        required={'plan_id','trial_id','task_name','reason','predecessor_uid','diagnostic_id','change_summary','validation'}
        if not simplified:required.add('task_information')
        missing=required-set(request);extra=set(request)-required-{'component_uid','patch_file','output_contract','work_package'}
        if missing or extra:
            def names(fields):
                return ', '.join(sorted(k if isinstance(k,str) and k.isascii() and k.isidentifier() and len(k)<=60 else '[无效字段名]' for k in fields)[:8]) or '无'
            raise ValueError('提交工作包字段不符。缺少必填字段：'+names(missing)+'；不支持字段：'+names(extra)+'。请删除多余字段或补齐必填项，task_information只放规定的五项任务说明；未提交、未扣次数。')
        from . import cloud_results
        if 'output_contract' in request:
            declared=cloud_results.declaration(request['output_contract'])
            if declared.get('runtime',{}).get('stage_work_files'):
                request={**request,'output_contract':declared}  # One identity across native schema versions.
        checked_id(request['task_name'])
        if request.get('component_uid'):checked_id(request['component_uid'])
        pid=session['project_id']
        plan=self.store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='plan'",(request['plan_id'],pid))
        trial=self.store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='trial'",(request['trial_id'],pid))
        if not plan:raise ValueError('plan_id不属于当前项目的已保存方案；请核对research_status的活动方案，不提交、未扣次数')
        trial_plan=json.loads(trial['source']).get('plan_id') if trial else None
        if not trial or trial_plan!=plan['id']:
            available=self.store.rows("SELECT id FROM records WHERE project_id=? AND kind='trial' AND json_extract(source,'$.plan_id')=? ORDER BY rowid DESC LIMIT 3",(pid,plan['id']))
            problem=('trial_id='+trial['id']+'归属方案'+str(trial_plan) if trial else 'trial_id未在当前项目中找到')
            raise ValueError(problem+'，不能用于当前plan_id='+plan['id']+
                '。当前方案已有trial_id：'+(', '.join(row['id'] for row in available) or '尚未登记')+
                '。使用本方案research_record实际返回的实验id；已有身份时不要重新登记或猜测ID，不要继续复制旧方案trial_id；未提交、未扣次数')
        compiled_package=None
        if simplified:
            from .autonomy import execution_grant
            from .work_package import compile_package
            grant=execution_grant(self.store,plan)
            if not grant or grant['execution']!='lpai':raise ValueError('简化组件工作包仍需当前方案的正式LPAI授权')
            approved=self.store.one('''SELECT v.configuration FROM research_component_provisions p
                JOIN research_component_previews v ON v.id=p.preview_id WHERE p.grant_id=?''',(grant['id'],))
            if not approved:raise ValueError('简化工作包需已审核的新建组件配置；不能猜已有组件的挂载或资源')
            compiled=compile_package(self.store,plan,grant,trial,json.loads(approved['configuration']),request['work_package'])
            compiled_package=compiled.pop('receipt')
            if compatibility:compiled_package['compatibility']=compatibility
            request={**{k:v for k,v in request.items() if k!='work_package'},**compiled}
        from . import evaluation_contract
        evaluation=evaluation_contract.load(self.store,plan)
        if evaluation and 'output_contract' not in request:
            request={**request,'output_contract':{'required_files':[evaluation['spec']['artifact']]}}
        info=request['task_information']
        names={'problem_statement','solution_approach','expected_result','expected_duration','expected_duration_desc'}
        if not isinstance(info,dict) or set(info)!=names:
            raise ValueError('task_information必须且只能包含problem_statement、solution_approach、expected_result、expected_duration、expected_duration_desc五项；未提交、未扣次数。')
        if type(info['expected_duration']) is not int or not 1<=info['expected_duration']<=10080:
            raise ValueError('task_information.expected_duration必须是1至10080的整数，单位为分钟（300秒授权填5），不能填字符串、秒数或小数；未提交、未扣次数。')
        for key in names-{'expected_duration'}:
            if not isinstance(info[key],str) or not info[key].strip() or len(info[key])>4000: raise ValueError('缺少明确任务说明：'+key)
        if not request['reason'].strip(): raise ValueError('说明该次运行需要验证什么')
        # When a grant already exists, reject unit mistakes before any SDK reads
        # or preview creation. Legacy pre-approval drafts still work.
        from .autonomy import execution_grant
        try:current_grant=execution_grant(self.store,plan)
        except ValueError:current_grant=None
        if current_grant and current_grant['execution']=='lpai_devspace':
            raise ValueError('此方案已锁定 LPAI GPU 开发空间；不能准备或提交组件库替代任务')
        if current_grant and current_grant['execution']=='lpai':
            self._check_duration(info,current_grant['timeout'])
        previous=self.store.one("SELECT id,task_uid,state FROM company_training WHERE session_id=? AND trial_id=? AND state<>'prepared' ORDER BY rowid DESC LIMIT 1",(sid,trial['id']))
        if previous and previous['state'] in {'failed','error'} and previous['task_uid'] and not request['predecessor_uid']:
            raise ValueError('同一实验已有失败任务，不能清空修复链按首次运行提交。请以predecessor_uid='+previous['task_uid']+'调用lpai_diagnose，并填写该诊断ID、实际改动change_summary与验证validation；原记录保留，未提交、未扣次数。')
        if previous and previous['state'] in {'failed','error'} and previous['task_uid'] and request['predecessor_uid'] not in {previous['id'],previous['task_uid']}:
            raise ValueError('本实验最近失败任务的官方task_uid为'+previous['task_uid']+'；不能用其他任务或更早失败记录代替本次修复依据，未提交、未扣次数。')
        if request['predecessor_uid']:
            checked_id(request['predecessor_uid'])
            predecessor=self.store.one('SELECT trial_id,plan_id FROM company_training WHERE project_id=? AND session_id=? AND task_uid=? ORDER BY rowid DESC LIMIT 1',(pid,sid,request['predecessor_uid']))
            if predecessor and predecessor['plan_id']==plan['id'] and predecessor['trial_id']!=trial['id']:
                raise ValueError('同方案失败后的修复/重跑应复用原实验trial_id='+predecessor['trial_id']+'，不新建科学实验；新方法实验不应填写失败重试前序。未提交、未扣次数。')
            local=self.store.one('SELECT task_uid FROM company_training WHERE id=? AND session_id=?',(request['predecessor_uid'],sid))
            if local and local['task_uid'] and local['task_uid']!=request['predecessor_uid']:
                raise ValueError('predecessor_uid需要失败任务的官方task_uid，不是本地submission_id；该记录的task_uid为'+local['task_uid']+'。请沿此原任务取证后修复，未提交、未扣次数。')
            old=self.company.lpai('task',uid=request['predecessor_uid'])['item']
            if old['phase'].lower() not in FINAL: raise ValueError('原任务未结束，不能用新提交替代')
            proof=self.store.one('SELECT * FROM company_diagnostics WHERE id=? AND session_id=? AND task_uid=?',(request['diagnostic_id'],sid,old['uid']))
            if not proof or not request['change_summary'].strip() or not request['validation'].strip(): raise ValueError('失败后重试需要原诊断、实际修改及验证依据；不允许盲目重投')
            path=self.store.safe_path(pid,proof['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=proof['digest']:raise ValueError('原诊断证据已改变')
        elif any(request[key] for key in ['diagnostic_id','change_summary','validation']):
            raise ValueError('首次运行或正常结果迭代没有失败前序时，predecessor_uid、diagnostic_id、change_summary、validation均填空字符串；目的填reason，实施和核对写入trial/工作包。失败重试须绑定原任务UID和原诊断，不可虚构。')
        patches=[];patch_digest=None;task_changes=None;work_package=False
        if request.get('patch_file'):
            name=request['patch_file']
            if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts or any(p.startswith('.') for p in Path(name).parts):raise ValueError('补丁须为项目内明确的普通JSON文件')
            path=self.store.safe_path(pid,name)
            if not path.is_file() or path.stat().st_size>32768:raise ValueError('修复补丁不存在或超过32KiB')
            with path.open('rb') as stream:patch_bytes=stream.read(32769)
            if len(patch_bytes)>32768:raise ValueError('修复补丁在读取时超过32KiB')
            patch_digest=hashlib.sha256(patch_bytes).hexdigest()
            patches=json.loads(patch_bytes.decode('utf-8'))
            if not any(ref['path']==name and ref['sha256']==patch_digest for ref in json.loads(plan['evidence'])):
                from .autonomy import execution_grant
                from . import task_policy
                try:grant=execution_grant(self.store,plan)
                except ValueError:grant=None
                task_changes=task_policy.from_grant(self.store,grant) if grant else None
                if not task_changes:raise ValueError('修复补丁必须已纳入具体方案的冻结证据，或在首次审核时明确授权任务变更')
                task_policy.allowed(task_changes,patches)
                work_package=True
        # Resolve reviewed source bytes before an approved auto-provision can
        # create a remote component. Missing/oversized local files must fail
        # without platform side effects; freeze rechecks these hashes later.
        work_files=cloud_results.execution_files(self.store,pid,request['output_contract'],plan) if 'output_contract' in request else []
        with self.company.lock:
            namespace=self.company.config()['namespace'];profile=self._profile()
            auto_uid=self.provision_component(sid,plan,current_grant,request,patches) if current_grant and current_grant['execution']=='lpai' else request.get('component_uid')
            if not auto_uid:raise ValueError('方案未获新建组件授权，请先完成确认或使用已审核的组件UID')
            request={**request,'component_uid':auto_uid}
            component=self.sdk('component',component_uid=request['component_uid'],patches=patches)
            self.company.resource_directory.require_component(sid,component)
            if component.get('uid')!=request['component_uid'] or component.get('namespace')!=namespace:
                raise ValueError('组件身份/namespace与原准备请求不同')
            public_identity=platform_identity(component.get('platform_identity'))
            frozen={**request,'component':component,'authority':self.company.authority(sid),'patches':patches,'patch_sha256':patch_digest}
            if compiled_package:frozen['work_package_compilation']=compiled_package
            if current_grant:
                preview_row=self.store.one('''SELECT v.configuration FROM research_component_provisions p
                    JOIN research_component_previews v ON v.id=p.preview_id WHERE p.grant_id=?''',(current_grant['id'],))
                if preview_row:
                    approved=json.loads(preview_row['configuration'])
                    if approved['template'].get('schema')==1:frozen['execution_configuration']=approved
            if work_package:
                scope=self.store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant['id'],))
                if not scope or scope['component_uid']!=component['uid'] or scope['component_digest']!=component['digest'] or component['gpu_count']>scope['max_gpus']:
                    raise ValueError('任务工作包超出原固定组件或GPU范围')
                frozen['execution_snapshot']=task_policy.freeze(task_changes,component,patches,patch_digest)
            if request['predecessor_uid']:frozen['predecessor_phase']=old['phase'].lower()
            if work_files:frozen['execution_files']=work_files
            id=sha([sid,frozen,namespace,profile,READ_CHANNEL])[:32]
            result_bundle=None
            if 'output_contract' in request:
                source=request['output_contract'].get('source')
                if source:
                    from .cloud_runtime import source as checked_source
                    source=checked_source(source)
                    if not any(v.get('name')==source['name'] and v.get('env_value')==source['mount_root'] for v in component.get('volumes',[]) if isinstance(v,dict)):
                        raise ValueError('结果卷及挂载根不在官方固定组件投影中，不能猜运行路径')
                    volume=self.company.outputs.inspect_volume(source['name'])
                    if volume['uid']!=source['uid'] or volume['namespace']!=namespace:raise ValueError('结果卷UID/工作空间与准备请求不同')
                frozen['output_contract']=cloud_results.freeze(self.store,plan,trial['id'],id,component,request['output_contract'],work_files=work_files)
                if request['output_contract'].get('runtime'):
                    from . import cloud_runtime,task_policy
                    from .autonomy import execution_grant
                    if not task_changes:
                        grant=execution_grant(self.store,plan);task_changes=task_policy.from_grant(self.store,grant)
                    grant=execution_grant(self.store,plan)
                    frozen['result_runtime']=cloud_runtime.freeze(frozen['output_contract'],task_changes,self.store,pid,timeout_seconds=grant['timeout'])
                    component=self.sdk('component',component_uid=request['component_uid'],patches=patches,result_runtime=frozen['result_runtime'])
                    if component.get('digest')!=frozen['component']['digest'] or component.get('uid')!=request['component_uid'] or component.get('namespace')!=namespace:raise ValueError('准备结果执行器时组件身份/配置发生变化')
                    frozen['component']=component
                    frozen['execution_snapshot']=task_policy.freeze(task_changes,component,patches,patch_digest,frozen['result_runtime'])
                    work_package=True
                result_bundle=cloud_results.bundle(self.store,pid,frozen['output_contract'])
                frozen['result_bundle']=result_bundle
            marker=MARKER_PREFIX+id
            # Echo a public correlation marker in a supported, visible task field.
            # It is part of the frozen preview, never injected after approval or
            # sent as a guessed SDK parameter/config/environment mutation.
            info={**info,'expected_duration_desc':info['expected_duration_desc']+'\n['+marker+']'}
            if len(info['expected_duration_desc'])>4000:raise ValueError('时长说明没有原请求来源标记的空间')
            frozen['task_information']=info
            stamp=now()
            with self.store.lock,self.store.db:
                self.store.db.execute('''INSERT OR IGNORE INTO company_training
                    (id,session_id,project_id,plan_id,trial_id,component_uid,task_uid,request,state,result,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (id,sid,pid,plan['id'],trial['id'],request['component_uid'],None,dumps(frozen),'prepared',None,stamp,stamp))
                self.store.db.execute('''INSERT OR IGNORE INTO company_training_identity
                    (submission_id,requested_at,namespace,profile,session_channel,read_channel,connection_authority,
                     submission_marker,platform_identity)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                    (id,None,namespace,profile,session['channel'],READ_CHANNEL,dumps(frozen['authority']),marker,
                     dumps(public_identity) if public_identity else None))
        return {'submission_id':id,'state':'prepared','component':component,'task_information':info,'changes':request['change_summary'],
                'compiled_work_package':compiled_package,
                'result_bundle':result_bundle,
                'execution_snapshot':frozen.get('execution_snapshot'),
                'runtime_guards':{'whole_command_timeout_seconds':frozen['result_runtime']['timeout_seconds'],
                                  'execution_files':frozen['output_contract']['expected'].get('execution_files',[])} if frozen.get('result_runtime') else None,
                'message':'本次工作包已冻结，局部变更沿原研究授权核验；准备不等于提交，也不证明修复有效。' if work_package else
                          '准备不等于提交。提交需有效云端范围；自主模式按原授权核验，人工模式确认本次任务。'}

    def submit(self,sid,id,approved,*,operation_id=None):
        from .research import plan_digest
        if approved is not True:raise ValueError('需用户确认本次确切提交')
        self._enabled();session=self.store.session(sid)
        with self.company.lock:
            row=self.store.one('SELECT * FROM company_training WHERE id=? AND session_id=?',(id,sid))
            if not row: raise ValueError('未准备本次提交')
            if row['state']!='prepared': return self._current_result(row)
            from .execution_request import require_route
            require_route(self.store,session['project_id'],'lpai')
            if row['task_uid']:raise ValueError('原任务UID已锁定，不把已知提交作为prepared再次写入')
            frozen=json.loads(row['request']);plan=self.store.one('SELECT * FROM records WHERE id=?',(row['plan_id'],))
            from .research_modules import ResearchModules
            ResearchModules(self.store).preflight(sid,plan)
            identity=self._identity(id)
            if not identity:raise ValueError('旧准备记录未冻结原提交身份，须重新准备；不补造实际请求时间')
            if identity.get('requested_at') or identity.get('original_task_uid') or identity.get('receipt'):
                raise ValueError('原实际请求身份已经锁定，不能再次提交')
            self._original_context(sid,row,frozen,identity)
            for ref in json.loads(plan['evidence'])+frozen.get('result_bundle',{}).get('files',[]):
                path=self.store.evidence_path(session['project_id'],ref['path'])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('方案冻结的修复/验证证据已改变，须重新审核')
            if session['channel']!='ept' or frozen['authority']!=self.company.authority(sid): raise ValueError('会话/连接/绑定已改变，须重新准备')
            if self.store.one("SELECT id FROM deliveries WHERE session_id=? AND state IN ('unknown','requested')",(sid,)): raise ValueError('存在未核对的原请求')
            if self.store.one('SELECT session_id FROM research_controls WHERE session_id=? AND paused=1',(sid,)):
                raise ValueError('研究已暂停')
            current=rounds.approved_plan(self.store,session['project_id'])
            from .autonomy import execution_grant,finalizing
            if finalizing(self.store,session['project_id']):
                raise ValueError('本方案已进入只读收尾，未提交新的云端计算')
            grant=execution_grant(self.store,plan)
            execution_snapshot=None
            plan_patch=any(ref['path']==frozen.get('patch_file') and ref['sha256']==frozen.get('patch_sha256') for ref in json.loads(plan['evidence']))
            if frozen.get('execution_snapshot') or frozen.get('patches') and not plan_patch:
                from . import task_policy
                execution_snapshot=task_policy.verify(task_policy.from_grant(self.store,grant),frozen)
            delegated=grant['plan_id']!=plan['id']
            review=self.store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(row['plan_id'],))
            if not current or current['id']!=row['plan_id'] or not grant or grant['execution']!='lpai' or not delegated and (grant['plan_digest']!=plan_digest(plan) or not review or review['decision']!='accept') or rounds.round_review(self.store,session['project_id'],row['plan_id']):
                raise ValueError('具体方案未获有效LPAI范围审核，或本轮已收尾；不能借本地/旧方案额度提交')
            if self.store.evidence.blocking(session['project_id'],verify_ready=True):
                raise ValueError('原本地运行证据尚未回收/校验，不能以云提交跳过证据缺口')
            if self.store.one("SELECT id FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown') LIMIT 1",(session['project_id'],)):
                raise ValueError('项目有在途/未知本地计算，未开始新的云端实验')
            # Company.perform has already registered this exact wrapper. Do not
            # exempt any other operation, session or uncertain original request.
            executing=(operation_id,sid,id) if operation_id else None
            if rounds.pending_platform_operation(self.store,session['project_id'],executing_submit=executing):
                raise ValueError('项目有未决平台操作，先核对原身份，未开始新的云端实验')
            dependencies=rounds.check_trial_dependencies(self.store,session['project_id'],plan['id'],row['trial_id'])
            scope=self.store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant['id'],))
            if not scope or scope['component_uid']!=row['component_uid'] or scope['component_digest']!=frozen['component']['digest']:
                raise ValueError('本组件配置不在确切方案批准范围内')
            active=rounds.pending_cloud(self.store,session['project_id'])
            active_watch=next((w for w in self.store.rows('SELECT state FROM company_watches w JOIN sessions s ON s.id=w.session_id WHERE s.project_id=?',(session['project_id'],)) if w['state'].lower() not in FINAL),None)
            if active or active_watch:raise ValueError('项目有在途或未知云任务，先跟踪原UID，不重复提交')
            from .task_outputs import require_collected
            require_collected(self.store,session['project_id'])
            used=self.store.rows('SELECT state FROM company_training WHERE project_id=? AND state<>?',(session['project_id'],'prepared'))
            local=self.store.rows('SELECT id FROM research_runs WHERE project_id=?',(session['project_id'],))
            if len(used)+len(local)>=grant['max_runs']:raise ValueError('已达累计批准尝试上限，失败/未知不会清零')
            self._check_duration(frozen['task_information'],grant['timeout'])
            # Freeze/revalidate before the non-idempotent boundary.
            self.validate_provisioned(grant,row['component_uid'])
            runtime_args={'result_runtime':frozen['result_runtime']} if frozen.get('result_runtime') else {}
            current_component=self.sdk('component',component_uid=row['component_uid'],**({'patches':frozen['patches']} if execution_snapshot else {}),**runtime_args)
            self.company.resource_directory.require_component(sid,current_component)
            if current_component.get('uid')!=row['component_uid'] or current_component.get('namespace')!=identity['namespace']:
                raise ValueError('提交边界组件身份/namespace与原准备不同')
            if current_component['digest']!=frozen['component']['digest']: raise ValueError('组件已变化，原提交预览失效')
            if current_component.get('submission_policy')!=frozen['component'].get('submission_policy'):
                raise ValueError('平台结果存储设置缺失或已变化，请重新准备工作包；未进入提交边界，不扣训练次数')
            if execution_snapshot and current_component.get('execution_digest')!=execution_snapshot['execution_digest']:
                raise ValueError('实际任务执行快照与原工作包不同，未提交')
            current_platform=platform_identity(current_component.get('platform_identity'))
            if identity['platform_identity'] and current_platform!=identity['platform_identity']:
                raise ValueError('提交边界的平台身份与原准备不同')
            if current_component['gpu_count']>scope['max_gpus']:raise ValueError('组件GPU数量超过本方案批准范围')
            if frozen.get('result_runtime'):
                source=frozen['output_contract']['expected']['source']
                volume=self.company.outputs.inspect_volume(source['name'])
                if volume['uid']!=source['uid'] or volume['namespace']!=identity['namespace']:raise ValueError('提交前结果卷身份已变化，未提交')
            local_fits=sum(json.loads(r['request']).get('purpose')=='fit' for r in self.store.rows('SELECT request FROM research_runs WHERE project_id=?',(session['project_id'],)))
            if len(used)+local_fits>=grant['max_fits']:raise ValueError('已达累计批准拟合尝试上限')

            self._original_context(sid,row,frozen,identity)
            authority=self._submission_authority(sid,row,grant)
            if authority['kind']=='delegated_research_scope':
                original=self.store.one('SELECT scope FROM research_authorities WHERE id=?',(authority['authority_id'],))
                original_scope=json.loads(original['scope'])
                if sha(original_scope)!=authority['scope_digest']:raise ValueError('原程序提交授权的冻结范围摘要不符')
                if original_scope.get('model_profile')!=identity['profile'] or original_scope.get('channel')!=session['channel']:
                    raise ValueError('原程序提交授权的profile/通道不符')
            # Commit actual request time and frozen source BEFORE the first SDK
            # write call. Never derive it later from prepare/update timestamps.
            requested_at=now()
            with self.store.lock,self.store.db:
                changed=self.store.db.execute("""UPDATE company_training SET state='requested',updated_at=?
                    WHERE id=? AND session_id=? AND state='prepared' AND task_uid IS NULL""",(requested_at,id,sid)).rowcount
                if not changed:return self._current_result(self.store.one('SELECT * FROM company_training WHERE id=? AND session_id=?',(id,sid)))
                saved=self.store.db.execute('''UPDATE company_training_identity
                    SET requested_at=?,submission_authority=?,platform_identity=?
                    WHERE submission_id=? AND requested_at IS NULL AND original_task_uid IS NULL''',
                    (requested_at,dumps(authority),dumps(current_platform) if current_platform else None,id)).rowcount
                if saved!=1:raise ValueError('原实际请求身份已经锁定，不能再次提交')
            try:
                result=self.sdk('submit',namespace=identity['namespace'],component_uid=row['component_uid'],expected_digest=current_component['digest'],
                                task_name=frozen['task_name'],task_information=frozen['task_information'],patches=frozen.get('patches',[]),
                                **({'expected_submission_policy':current_component['submission_policy']} if current_component.get('submission_policy') else {}),
                                **runtime_args,
                                **({'execution_configuration':frozen['execution_configuration']} if frozen.get('execution_configuration') else {}),
                                **({'expected_execution_digest':execution_snapshot['execution_digest']} if execution_snapshot else {}))
                item=result.get('item') or {}
                from .company import checked_id
                if result.get('state')!='submitted' or not item.get('uid'):
                    result={'state':'unknown','message':'提交未返回确切UID，先核对，不重试'}
                else:
                    checked_id(item['uid'])
                    if (item.get('namespace') not in {None,identity['namespace']}
                            or item.get('project_uid') not in {None,row['component_uid']}):
                        raise ValueError('SDK收据不是原提交对象')
                    result={**result,'approval_source':authority,'resubmitted':False,'verified_dependencies':dependencies}
            except TimeoutError:
                result={'state':'unknown','message':'SDK提交结果未知；保留这次尝试，不自动重发'}
            except Exception:
                result={'state':'unknown','message':'提交边界后没有完整收据，需原身份对账，不自动重试'}
            stamp=now();task_uid=(result.get('item') or {}).get('uid')
            with self.store.lock,self.store.db:
                changed=self.store.db.execute('''UPDATE company_training SET state=?,task_uid=?,result=?,updated_at=?
                    WHERE id=? AND session_id=? AND state='requested' AND task_uid IS NULL''',
                    (result['state'],task_uid,dumps(result),stamp,id,sid)).rowcount
                if not changed:return self._current_result(self.store.one('SELECT * FROM company_training WHERE id=? AND session_id=?',(id,sid)))
                if result['state']=='submitted':
                    saved=self.store.db.execute('''UPDATE company_training_identity
                        SET original_task_uid=?,receipt=?,receipt_at=? WHERE submission_id=? AND original_task_uid IS NULL''',
                        (task_uid,dumps(result),stamp,id)).rowcount
                    if saved!=1:raise ValueError('原UID收据冲突，不能切换已锁定任务')
            self.store.event(sid,'company.training_submitted',{'id':id,**result})
            return self._attach_watch(sid,id,result) if result['state']=='submitted' else result

    def delegated_submission(self,sid,id):
        """Only the exact prepared task within an explicit autonomous cloud scope.

        submit() still rechecks pause, digest, attempts, component/GPU and unknown
        work at the real write boundary. This does not approve platform prompts.
        """
        from .autonomy import policy,execution_grant
        from .research import plan_digest
        session=self.store.session(sid);p=policy(self.store,session['project_id'])
        if not p or p['mode']!='autonomous' or p['owner_session_id']!=sid:return None
        row=self.store.one('SELECT * FROM company_training WHERE id=? AND session_id=?',(id,sid))
        if not row:return None
        plan=self.store.one('SELECT * FROM records WHERE id=?',(row['plan_id'],))
        grant=execution_grant(self.store,plan)
        authority=self.store.one('SELECT * FROM research_authorities WHERE id=?',(p['authority_id'],))
        if not grant or grant['id']!=authority['grant_id'] or grant['execution']!='lpai':return None
        scope=self.store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant['id'],))
        frozen=json.loads(row['request'])
        if frozen.get('predecessor_uid') and frozen.get('predecessor_phase') not in {'failed','error'}:
            return None # A stopped/cancelled/unknown predecessor is not automatic retry authority.
        if frozen.get('execution_snapshot'):
            from . import task_policy
            task_policy.verify(task_policy.from_grant(self.store,grant),frozen)
        if not scope or scope['component_uid']!=row['component_uid'] or scope['component_digest']!=frozen['component']['digest'] or frozen['component']['gpu_count']>scope['max_gpus']:return None
        return {'kind':'delegated_research_scope','authority_id':authority['id'],'plan_id':plan['id'],'submission_id':id,
                'meaning':'程序核对用户已授权研究范围；不是用户逐次点击，不处理平台强制审批'}

    def context(self,sid,brief=False):
        from .task_outputs import status
        outputs=[{'task_uid':row['task_uid'],**status(self.store,sid,row['task_uid'])} for row in
            self.store.rows('SELECT task_uid FROM company_output_jobs WHERE session_id=? ORDER BY rowid DESC LIMIT 6',(sid,))]
        runtime_guards={'timeout':'使用runtime的正式工作包自动从授权冻结完整命令时限；含安装、下载、训练。lpai_prepare回显实际秒数，不另改冻结代码。','work_files':'runtime.work_files最多8个.py/.json/.txt，绑定实际项目源码字节；research/子目录包必须显式源路径=>implementation/云端目标，根目标只允许JSON配置，类型不变。旧线程用该数组字符串映射，不混用stage_work_files；只传实际运行文件，说明文档保留原项目，不占工作文件名额。全部输入仍最多16个/256KiB，单文件64KiB。',
            'declared_npz':'明确声明的NPZ按至多2MiB原始字节回收，不加载或反序列化；未声明或其他大型权重仍不下载。'}
        runtime_guards['work_package']='已审核单节点自动组件优先lpai_prepare.work_package(entrypoint/files/args/required_files)，不填patch_file/output_contract/task_information。程序生成双入口补丁、项目路径布局（可直接传已审CSV）、已审核结果卷及分钟数，并在创建前做实际字节和命令长度校验。高级runtime字段不用于该模式。'
        runtime_guards['pinned_thread_compatibility']={'instruction':'旧线程工具固定，看不到work_package时，用research_write保存schema=iterlab-experiment/v1的JSON声明，再用已注册lpai_prepare的patch_file指向它。仍提供原plan_id/trial_id/task_name/reason及旧schema所需的空重试字段；task_information可按旧schema传入，但实际平台说明与分钟数由程序按原授权生成。不要同时提供output_contract。普通补丁数组仍按旧高级方式处理。',
            'file_shape':{'schema':'iterlab-experiment/v1','entrypoint':'<实际项目Python入口>','files':['<实际已审数据/源码路径>'],
                          'args':['<实际本轮参数>'],'required_files':['<实际必要结果>']}}
        if brief:
            return {'outputs':outputs,'runtime_guards':runtime_guards,
                'diagnostics':[{'id':row['id'],'task_uid':row['task_uid'],'task_phase':row['task_phase'],'path':row['path'],'digest':row['digest']}
                    for row in self.store.rows('SELECT id,task_uid,task_phase,path,digest FROM company_diagnostics WHERE session_id=? ORDER BY rowid DESC LIMIT 6',(sid,))],
                'notices':self.store.rows('SELECT task_uid,kind,substr(text,1,300) AS text,evidence_id FROM company_notices WHERE session_id=? ORDER BY rowid DESC LIMIT 6',(sid,)),
                'submissions':self.store.rows('SELECT id,plan_id,trial_id,component_uid,task_uid,state FROM company_training WHERE session_id=? ORDER BY rowid DESC LIMIT 6',(sid,)),
                'evidence_policy':'这里只给索引；按原UID调用lpai_diagnose取已有诊断，或读取冻结文件，不重复轮询整份日志'}
        return {'outputs':outputs,'runtime_guards':runtime_guards,'diagnostics':[{**row,'summary':json.loads(row['summary'])} for row in self.store.rows('SELECT * FROM company_diagnostics WHERE session_id=? ORDER BY rowid DESC LIMIT 8',(sid,))],
                'notices':self.store.rows('SELECT * FROM company_notices WHERE session_id=? ORDER BY rowid DESC LIMIT 12',(sid,)),
                'submissions':[{**{key:value for key,value in row.items() if key!='request'},'preview':json.loads(row['request'])} for row in self.store.rows('SELECT id,plan_id,trial_id,component_uid,task_uid,state,created_at,request FROM company_training WHERE session_id=? ORDER BY rowid DESC LIMIT 12',(sid,))]}

    def reconcile(self,sid,id,task_uid):
        """Original requested/unknown lifecycle only; no guessed UID or new authority."""
        from .company import checked_id
        checked_id(task_uid)
        with self.company.lock:
            row=self.store.one('SELECT * FROM company_training WHERE id=? AND session_id=?',(id,sid))
            if not row:raise ValueError('只对原在途/未知提交核对身份')
            identity=self._identity(id)
            if identity:
                public_identity=platform_identity(identity.get('platform_identity'))
                if public_identity:
                    identity['platform_identity']=public_identity
            # Both current state and the first confirmed immutable UID constrain
            # every reconcile, including repeated calls after terminal polling.
            known=self._known_uids(row,identity)
            if len(known)>1 or known and task_uid not in known:
                raise ValueError('原任务UID已锁定，不能用其他UID切换原提交身份')
            if row['state'] not in {'requested','unknown'}:
                if self._receipt_matches(identity,task_uid):return self._current_result(row)
                raise ValueError('只对原requested/unknown生命周期核对；已知任务不是新的待提交请求')
            frozen=json.loads(row['request'])
            self._original_context(sid,row,frozen,identity)
            response=self.company._run([self.company.locator(READ_CHANNEL),'--timeout','25','tasks','get',
                '--namespace',(identity or {}).get('namespace') or frozen['component']['namespace'],'--uid',task_uid])
            if not isinstance(response,dict) or response.get('status')!='ok':raise ValueError('原只读通道未确认任务读取成功')
            item=task_observation(response.get('data'))
            missing,origin=reconciliation_evidence(row,frozen,identity,item,task_uid,bool(known))
            if not missing and not identity.get('submission_authority'):missing='missing_original_submission_authority'
            if missing:return self._unknown_observation(row,identity,item,missing)
            self._original_context(sid,row,frozen,identity)
            result={'state':'submitted','item':item,'identity_reconciled':True,'origin_evidence':origin,
                    'resubmitted':False,'approval_source':identity.get('submission_authority'),
                    'new_authorization':False}
            stamp=now()
            with self.store.lock,self.store.db:
                # CAS also handles a separate Company/store connection winning
                # while this thread was reading the platform. Never overwrite a
                # newer state/UID or attach a second watch.
                changed=self.store.db.execute('''UPDATE company_training SET task_uid=?,state='submitted',result=?,updated_at=?
                    WHERE id=? AND session_id=? AND state=? AND task_uid IS ? AND result IS ?
                    AND EXISTS(SELECT 1 FROM company_training_identity i WHERE i.submission_id=?
                        AND i.requested_at=? AND i.original_task_uid IS ? AND i.receipt IS ?)''',
                    (task_uid,dumps(result),stamp,id,sid,row['state'],row['task_uid'],row['result'],id,identity['requested_at'],identity['original_task_uid'],
                     identity['receipt_json'])).rowcount
                if not changed:
                    current=self.store.one('SELECT * FROM company_training WHERE id=? AND session_id=?',(id,sid))
                    locked=self._identity(id)
                    current_uids=self._known_uids(current,locked)
                    if len(current_uids)>1 or current_uids and task_uid not in current_uids:
                        raise ValueError('并发核对已锁定其他原UID，未覆盖当前状态')
                    if self._receipt_matches(locked,task_uid):return self._current_result(current)
                    raise ValueError('原提交状态已并发改变，保留当前状态；未绑定猜测UID')
                saved=self.store.db.execute('''UPDATE company_training_identity SET original_task_uid=?,receipt=?,receipt_at=?
                    WHERE submission_id=? AND requested_at=? AND original_task_uid IS ?''',
                    (task_uid,dumps(result),stamp,id,identity['requested_at'],identity['original_task_uid'])).rowcount
                if saved!=1:raise ValueError('原UID收据并发冲突；未覆盖其他任务')
            # A monitor is not a submit receipt. Success is durable before its
            # attach, and failures preserve submitted + the exact original UID.
            self.store.event(sid,'company.training_reconciled',{'id':id,**result})
            # Preserve the original unknown wrapper/result. Link the newly
            # verified immutable receipt instead of overwriting write history.
            for operation in self.store.rows("SELECT * FROM company_operations WHERE session_id=? AND state IN ('unknown','requested')",(sid,)):
                request=json.loads(operation['request'])
                if request.get('action')!='lpai_submit' or request.get('submission_id')!=id:continue
                linked=self.store.one('SELECT * FROM company_operation_reconciliations WHERE operation_id=?',(operation['id'],))
                digest=sha(result)
                if linked and (linked['submission_id'],linked['task_uid'],linked['receipt_sha256'])!=(id,task_uid,digest):raise ValueError('原操作核对关联发生冲突，不覆盖')
                self.store.execute('INSERT OR IGNORE INTO company_operation_reconciliations VALUES(?,?,?,?,?)',(operation['id'],id,task_uid,digest,now()))
                self.store.event(sid,'company.operation_reconciled',{'operation_id':operation['id'],'submission_id':id,'task_uid':task_uid,'original_history_preserved':True})
            return self._attach_watch(sid,id,result)
