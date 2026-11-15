"""Read-only reconciliation of an original component creation; never create.

The approved preview and current authority are rechecked after network reads.
Only an exact official object/configuration can clear the original barrier.
"""
import json
from .store import now
from .autonomy import policy,active,execution_grant,finalizing
from . import rounds


def context(training,sid,grant_id=None):
    store=training.store;session=store.session(sid);p=policy(store,session['project_id'])
    if session['channel']!='ept' or training._settings_channel()!=session['channel']:return None
    if (session['archived'] or not p or p['mode']!='autonomous' or p['owner_session_id']!=sid
            or store.one('SELECT session_id FROM research_controls WHERE session_id=? AND paused=1',(sid,))):return None
    authority=store.one('SELECT grant_id FROM research_authorities WHERE id=?',(p['authority_id'],))
    if not authority or grant_id and grant_id!=authority['grant_id']:return None
    grant=store.one('SELECT * FROM research_grants WHERE id=?',(authority['grant_id'],))
    row=store.one('''SELECT q.*,v.configuration FROM research_component_provisions q
        JOIN research_component_previews v ON v.id=q.preview_id WHERE q.grant_id=?''',(grant['id'],)) if grant else None
    branch=active(store,session['project_id'])
    if (not row or grant['execution']!='lpai' or not branch
            or finalizing(store,session['project_id']) or rounds.round_review(store,session['project_id'],branch['head_plan_id'])):return None
    plan=store.one('SELECT * FROM records WHERE id=?',(branch['head_plan_id'],))
    try:effective=execution_grant(store,plan)
    except ValueError:return None
    if effective['id']!=grant['id']:return None
    frozen=json.loads(row['configuration']);connection=training.company.config()
    if not connection['lpai_enabled']:return None
    if frozen['identity']!={'namespace':connection['namespace'],'revision':connection['revision'],'profile':training._profile()}:return None
    return {'row':row,'grant':grant,'plan':plan,'frozen':frozen,'policy_generation':p['updated_at'],'authority_id':p['authority_id']}


def pending(training,sid):
    value=context(training,sid)
    return value if value and value['row']['state'] in {'requested','unknown','verifying'} else None


def recover(training,sid,grant_id,cancelled=lambda:False):
    if cancelled():return {'state':'stale'}
    before=context(training,sid,grant_id)
    if not before:return {'state':'stale'}
    row=before['row'];frozen=before['frozen'];template=frozen['template'];config=template['config']
    if row['state']=='ready':return {'state':'ready','changed':False}
    if row['state'] not in {'requested','unknown','verifying'}:return {'state':'stale'}
    training.validate_preview(frozen)
    uid=row['component_uid']
    if not uid:
        found=training.sdk('lookup_component',component_name=config['name'])
        if found.get('state')!='observed':return {'state':'not_observed'}
        if found.get('name')!=config['name'] or found.get('namespace')!=frozen['identity']['namespace']:
            raise ValueError('原组件名称或namespace不一致，未解除未知状态')
        uid=found.get('uid')
    from .company import checked_id
    checked_id(uid)
    configured=template.get('schema')==1
    if configured:
        component=training.sdk('verify_configured',component_uid=uid,recipe=template['recipe'],component_name=config['name'],
            connection_revision=frozen['identity']['revision'],expected_config_sha256=template['config_sha256'])
    else:
        component=training.sdk('verify_provisioned_component',component_uid=uid,config=config,cluster=template['cluster'])
    if (component.get('uid')!=uid or component.get('name')!=config['name']
            or component.get('namespace')!=frozen['identity']['namespace']
            or component.get('gpu_count')!=template.get('gpu_count',0)
            or not isinstance(component.get('digest'),str) or len(component['digest'])!=64):
        raise ValueError('原组件实际身份、配置或GPU读回未通过，未解除未知状态')
    store=training.store
    with store.lock,store.db:
        if cancelled():return {'state':'stale'}
        current=context(training,sid,grant_id)
        if (not current or current['policy_generation']!=before['policy_generation']
                or current['authority_id']!=before['authority_id'] or current['row']['preview_id']!=row['preview_id']):return {'state':'stale'}
        existing=store.one('SELECT * FROM research_cloud_scopes WHERE grant_id=?',(grant_id,))
        expected={'grant_id':grant_id,'component_uid':uid,'component_digest':component['digest'],'max_gpus':component['gpu_count']}
        if existing and existing!=expected:raise ValueError('原云端范围已经指向不同组件，未覆盖')
        if current['row']['state']=='ready':return {'state':'ready','changed':False}
        if current['row']['state'] not in {'requested','unknown','verifying'}:return {'state':'stale'}
        if not existing:store.db.execute('INSERT INTO research_cloud_scopes VALUES(?,?,?,?)',(grant_id,uid,component['digest'],component['gpu_count']))
        store.db.execute("UPDATE research_component_provisions SET state='ready',component_uid=?,updated_at=? WHERE grant_id=?",(uid,now(),grant_id))
        original=store.one("""SELECT payload FROM events WHERE session_id=? AND kind='item/tool/call'
            AND json_extract(payload,'$.tool')='lpai_prepare' AND json_extract(payload,'$.arguments.plan_id')=? ORDER BY seq DESC LIMIT 1""",(sid,before['plan']['id']))
        source=json.loads(original['payload']) if original else {}
        trial=source.get('arguments',{}).get('trial_id')
        if trial and not store.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind='trial'",(trial,before['plan']['project_id'])):trial=None
        fact={'grant_id':grant_id,'plan_id':before['plan']['id'],'preview_id':row['preview_id'],'component_uid':uid,
            'component_digest':component['digest'],'gpu_count':component['gpu_count'],'trial_id':trial,
            'source_call_id':source.get('callId'),'previous_state':row['state'],'created_again':False,'task_submitted':False}
        store.event(sid,'company.component_reconciled',fact)
    return {'state':'ready','changed':True,**fact}
