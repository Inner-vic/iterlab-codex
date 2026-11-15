"""Version a compiler-only execution binding; never extend a research grant.

The sole supported migration fixes single-node PyTorch worker -> master. All
other executable/resource fields must remain identical. Old previews, task
identities and the original approval are immutable; only the current binding
projection changes, with an append-only receipt.
"""
import copy
import hashlib
import json

from .store import dumps,now


def install(store):
    with store.lock,store.db:
        store.db.execute('''CREATE TABLE IF NOT EXISTS research_component_binding_repairs(
          plan_id TEXT PRIMARY KEY REFERENCES records(id),
          grant_id TEXT NOT NULL REFERENCES research_grants(id),
          old_preview_id TEXT NOT NULL REFERENCES research_component_previews(id),
          new_preview_id TEXT NOT NULL REFERENCES research_component_previews(id),
          critic_handoff_id TEXT NOT NULL,receipt TEXT NOT NULL,created_at TEXT NOT NULL)''')
        store.db.execute("INSERT OR IGNORE INTO schema_versions VALUES(26,datetime('now'))")


def role_only(before,after):
    """Exact compiler delta; metadata is not an excuse to change a command."""
    if before.get('schema')!=1 or after.get('schema')!=1:return False
    if before.get('workers')!=1 or before.get('gpu_count',0)<=0:return False
    old=copy.deepcopy(before);new=copy.deepcopy(after)
    for value in (old,new):
        value.pop('config_sha256',None)
        value['config'].pop('name',None)
    roles=old['config'].get('task_role_specs')
    if old['config'].get('framework')!='pytorch' or not isinstance(roles,list) or len(roles)!=1:return False
    if roles[0].get('rolename')!='worker' or str(roles[0].get('replica'))!='1':return False
    roles[0]['rolename']='master'
    return old==new


def reviewed_recipe(store,plan,recipe):
    # A corrected preview is derived from an explicitly frozen recipe, not from
    # arbitrary model prose or from a silently changed platform default.
    for ref in json.loads(plan['evidence']):
        if ref['size']>65536 or not ref['path'].endswith('.json'):continue
        path=store.safe_path(plan['project_id'],ref['path'])
        if not path.is_file() or path.is_symlink():continue
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=ref['sha256']:continue
        try:value=json.loads(data)
        except (ValueError,UnicodeError):continue
        explanatory={'rationale','compatibility'}
        if (isinstance(value,dict) and set(value)==set(recipe)
                and {k:v for k,v in value.items() if k not in explanatory}=={k:v for k,v in recipe.items() if k not in explanatory}):
            return True
    return False


def refresh(training,sid,plan,grant,provision):
    frozen=json.loads(provision['configuration']);template=frozen['template']
    config=template.get('config') or {};roles=config.get('task_role_specs') or []
    if (template.get('schema')!=1 or template.get('workers')!=1 or template.get('gpu_count',0)<=0
            or config.get('framework')!='pytorch' or len(roles)!=1 or roles[0].get('rolename')!='worker'):
        return provision
    store=training.store
    from .autonomy import active,admitted,execution_grant,policy
    from .research import plan_digest
    from .settings import Settings
    from . import rounds
    def authorized():
        branch=active(store,plan['project_id']);p=policy(store,plan['project_id'])
        if (not branch or branch['head_plan_id']!=plan['id'] or not admitted(store,plan['id'])
                or not p or p['mode']!='autonomous' or p['owner_session_id']!=sid
                or store.session(sid)['archived']
                or store.one('SELECT 1 FROM research_controls WHERE session_id=? AND paused=1',(sid,))
                or execution_grant(store,plan)['id']!=grant['id']
                or rounds.round_review(store,plan['project_id'],plan['id'])):
            raise ValueError('旧DDP配置需先由独立Critic审查并在原授权内采纳确切修复方案；未创建组件')
        stop=store.one('SELECT stopped_at FROM research_user_stops WHERE session_id=?',(sid,))
        if stop and stop['stopped_at']>=p['updated_at']:raise ValueError('用户已停止研究，未替换执行配置')
        if store.one("SELECT 1 FROM company_training WHERE project_id=? AND state IN ('requested','unknown','submitted','pending_approval','running','pending')",(plan['project_id'],)):
            raise ValueError('原任务尚未核对终态，未替换组件绑定')
        critic=store.one('''SELECT h.*,r.model FROM research_role_handoffs h
          JOIN research_role_routes r ON r.delivery_id=h.source_delivery
          WHERE h.session_id=? AND h.plan_id=? AND h.plan_digest=? AND r.role='critic'
          ORDER BY h.rowid DESC LIMIT 1''',(sid,plan['id'],plan_digest(plan)))
        configured=Settings(store.root).value.get('agent_roles')
        if (not critic or critic['verdict']!='approve' or not configured
                or critic['model']!=configured['models']['critic']):
            raise ValueError('执行配置修复缺少当前Critic对确切方案的批准')
        if not reviewed_recipe(store,plan,template['recipe']):
            raise ValueError('执行配置修复必须把原运行配置JSON纳入方案冻结证据；不得改变资源、镜像、数据或训练参数')
        return critic,p
    critic,p=authorized()
    if provision['state']!='ready':raise ValueError('旧组件结果尚未核对，不能替换或重复创建')
    old_scope=store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant['id'],))
    if not old_scope or old_scope['component_uid']!=provision['component_uid']:
        raise ValueError('旧组件绑定身份尚未核对，不能替换')
    root=store.one('SELECT * FROM records WHERE id=?',(grant['plan_id'],))
    from .devspace import compute
    preview=training._configured_preview(sid,plan,template['recipe'],approved_compute=compute(root))
    fresh=store.one('SELECT configuration FROM research_component_previews WHERE id=?',(preview['id'],))
    corrected=json.loads(fresh['configuration'])
    if frozen['identity']!=corrected['identity'] or not role_only(template,corrected['template']):
        raise ValueError('编译器修复超出单节点worker→master差异，未替换原绑定；须另行核对')
    receipt={'reason':'single_node_pytorch_master','old_provision':{k:v for k,v in provision.items() if k!='configuration'},
        'old_scope':old_scope,'old_config_sha256':template['config_sha256'],
        'new_config_sha256':corrected['template']['config_sha256'],'budget_reset':False,'task_submitted':False}
    with store.lock,store.db:
        current_critic,current_policy=authorized()
        current=store.one('SELECT * FROM research_component_provisions WHERE grant_id=?',(grant['id'],))
        if (current_critic['id']!=critic['id'] or current_policy['updated_at']!=p['updated_at']
                or any(current[k]!=provision[k] for k in current)
                or store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant['id'],))!=old_scope):
            raise ValueError('执行配置准备期间身份或授权已变化，原绑定保留')
        store.db.execute('INSERT INTO research_component_binding_repairs VALUES(?,?,?,?,?,?,?)',
            (plan['id'],grant['id'],provision['preview_id'],preview['id'],critic['id'],dumps(receipt),now()))
        store.db.execute('DELETE FROM research_cloud_scopes WHERE grant_id=?',(grant['id'],))
        store.db.execute("UPDATE research_component_provisions SET preview_id=?,state='approved',component_uid=NULL,updated_at=? WHERE grant_id=?",
            (preview['id'],now(),grant['id']))
    store.event(sid,'company.component_binding_repaired',{'plan_id':plan['id'],'grant_id':grant['id'],
        'old_preview_id':provision['preview_id'],'new_preview_id':preview['id'],'critic_handoff_id':critic['id'],**receipt})
    return store.one('''SELECT p.*,v.session_id,v.configuration FROM research_component_provisions p
      JOIN research_component_previews v ON v.id=p.preview_id WHERE p.grant_id=?''',(grant['id'],))
