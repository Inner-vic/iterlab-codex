"""Read-only, session-owned runtime projection. Never enumerates a platform."""
import json
import re
from datetime import datetime
from urllib.parse import quote

FINAL={'completed','succeeded','success','failed','error','stopped','terminated','cancelled','canceled','timeout','timed_out','artifacts_missing','invalid_inputs'}
WAITING={'pending','queued','scheduling','waiting','submitted','starting','creating','initializing','image_pulling'}

def timing(observation,state):
    def timestamp(field):
        value=observation.get(field)
        if isinstance(value,dict):value=value.get('time')
        if not isinstance(value,str):return None
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            return parsed if parsed.tzinfo else None
        except ValueError:return None

    def elapsed(start,end):
        return int((end-start).total_seconds()) if start and end and end>=start else None

    phase=state.lower();created=timestamp('created_at');started=timestamp('started_at')
    finished=timestamp('finished_at');observed=timestamp('observed_at')
    return {'queue_seconds':elapsed(created,started or (observed if phase in WAITING else None)),
            'execution_seconds':elapsed(started,finished or (observed if phase=='running' else None))}

def task_url(observation,uid,namespace):
    component=observation.get('project_uid')
    if all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}',v) for v in [component,uid,namespace]):
        return f'https://lpai.lixiang.com/lpaiweb/xspacex_{quote(namespace)}/subApp/components/detail/{quote(component)}/record/{quote(uid)}'
    return observation.get('url')


def project(company,sid):
    store=company.store;store.session(sid);items=[];cloud_uids=set()
    local=store.rows('SELECT r.*,g.execution FROM research_runs r JOIN research_grants g ON g.id=r.grant_id WHERE r.session_id=? ORDER BY r.rowid DESC',(sid,)) if store.one("SELECT name FROM sqlite_master WHERE name='research_runs'") else []
    for row in local:
        request=json.loads(row['request']);receipt=json.loads(row['receipt']) if row['receipt'] else {}
        items.append({'id':row['id'],'kind':row['execution'],'name':request.get('question') or request.get('script') or '研究实验',
            'state':row['state'],'active':row['state'].lower() not in FINAL,'plan_id':row['plan_id'],
            'created_at':row['created_at'],'updated_at':row['updated_at'],'seconds':receipt.get('seconds'),
            'directory':row['directory'],'watch_id':None,'auto_resume':True,'url':None})
    watches={w['task_uid']:w for w in store.rows('SELECT * FROM company_watches WHERE session_id=?',(sid,))}
    for row in store.rows("SELECT * FROM company_training WHERE session_id=? AND state<>'prepared' ORDER BY rowid DESC",(sid,)):
        request=json.loads(row['request']);watch=watches.get(row['task_uid']);observation=json.loads(watch['observation']) if watch else {}
        if row['task_uid']:cloud_uids.add(row['task_uid'])
        state=watch['state'] if watch else row['state']
        items.append({'id':row['id'],'kind':'lpai','name':request.get('task_name') or row['task_uid'] or '云端提交待核对',
            'state':state,'active':state.lower() not in FINAL,'plan_id':row['plan_id'],'task_uid':row['task_uid'],
            'watch_id':watch['id'] if watch else None,'auto_resume':bool(watch and watch['auto_resume']),
            'wake_state':watch['wake_state'] if watch else 'not_attached','created_at':row['created_at'],
            'updated_at':observation.get('observed_at') or row['updated_at'],'url':task_url(observation,row['task_uid'],watch['namespace']) if watch else None,
            **timing(observation,state)})
    for uid,watch in watches.items():
        if uid in cloud_uids:continue
        observation=json.loads(watch['observation'])
        items.append({'id':watch['id'],'kind':'lpai','name':watch['task_name'],'state':watch['state'],
            'active':watch['state'].lower() not in FINAL,'task_uid':uid,'watch_id':watch['id'],
            'auto_resume':bool(watch['auto_resume']),'wake_state':watch['wake_state'],
            'created_at':watch['created_at'],'updated_at':observation.get('observed_at') or watch['created_at'],
            'url':task_url(observation,uid,watch['namespace']),**timing(observation,watch['state'])})
    from .task_outputs import status
    scheduler=store.one('SELECT * FROM research_scheduler_status WHERE session_id=?',(sid,)) if store.one("SELECT name FROM sqlite_master WHERE name='research_scheduler_status'") else None
    for item in items:
        if item['kind']!='lpai':continue
        item['collection']=status(store,sid,item.get('task_uid'))
        watch=watches.get(item.get('task_uid'))
        item['monitor']=store.one('SELECT last_success,last_failure,failures,error_type FROM company_watch_health WHERE watch_id=?',(watch['id'],)) if watch else None
        item['next_check']=watch['next_check'] if watch else None
        item['continuation']={k:scheduler[k] for k in ('state','reason','next_check','failures','updated_at')} if scheduler else None
    items.sort(key=lambda r:r['created_at'],reverse=True)
    return {'session_id':sid,'active':[r for r in items if r['active']],
        'history':[r for r in items if not r['active']],'scope':'submitted_or_explicitly_bound_to_this_session',
        'monitor_health':store.one('SELECT last_success,last_failure,failures,error_type FROM company_monitor_health WHERE singleton=1'),
        'monitor_runtime_error':getattr(company,'monitor_error_type',None)}


def project_runs(company,sid):
    store=company.store;session=store.session(sid);by_identity={}
    for owner in store.rows('SELECT id,title FROM sessions WHERE project_id=?',(session['project_id'],)):
        view=project(company,owner['id'])
        for item in [*view['active'],*view['history']]:
            row={**item,'session_id':owner['id'],'session_title':owner['title']}
            key=(row['kind'],row.get('task_uid') or row['id'])
            # A task submitted in one chat and watched in another remains one
            # actual run. Prefer its submission identity, without changing links.
            previous=by_identity.get(key)
            if previous is None or row.get('plan_id') and not previous.get('plan_id'):
                by_identity[key]=row
    items=sorted(by_identity.values(),key=lambda r:r['created_at'],reverse=True)
    return {'project_id':session['project_id'],'session_id':sid,'scope':'submitted_or_bound_to_this_project',
            'active':[r for r in items if r['active']],'history':[r for r in items if not r['active']]}
