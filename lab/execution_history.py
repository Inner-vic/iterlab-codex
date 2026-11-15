"""Read-only grouping of explicitly linked execution retries, not scientific equivalence."""
import json


def index(store, project_id, attempts=None):
    if attempts is None:
        from .lineage import cloud_attempts
        attempts=cloud_attempts(store,project_id)
    records=store.rows('SELECT id,session_id,kind,parent_id,source FROM records WHERE project_id=? ORDER BY rowid',(project_id,))
    by_id={r['id']:r for r in records};order={r['id']:n for n,r in enumerate(records)}
    trials={r['id'] for r in records if r['kind']=='trial'}
    branches={}
    if store.one("SELECT name FROM sqlite_master WHERE name='research_branches'"):
        branches={r['plan_id']:r['branch_id'] for r in store.rows('SELECT a.plan_id,a.branch_id FROM research_plan_admissions a JOIN records r ON r.id=a.plan_id WHERE r.project_id=?',(project_id,))}
        for r in store.rows('SELECT root_plan_id,id FROM research_branches WHERE project_id=?',(project_id,)):branches[r['root_plan_id']]=r['id']
    # The same UID returned more than once is ambiguous; never choose a match.
    tasks={}
    for attempt in attempts:
        if attempt.get('task_uid'):tasks.setdefault((attempt['session_id'],attempt['task_uid']),[]).append(attempt)
    parents={};conflicts=set()
    for attempt in attempts:
        candidates=tasks.get((attempt['session_id'],attempt.get('predecessor_uid')),[])
        if len(candidates)!=1:continue
        old=candidates[0];child=attempt['trial_id'];parent=old['trial_id']
        if child==parent or child not in trials or parent not in trials:continue
        if old.get('state','').lower() not in {'failed','error'} or order[parent]>=order[child]:continue
        if by_id[parent]['session_id']!=by_id[child]['session_id']:continue
        if old['plan_id']!=attempt['plan_id'] and (not branches.get(old['plan_id']) or branches.get(old['plan_id'])!=branches.get(attempt['plan_id'])):continue
        if child in parents and parents[child]!=parent:conflicts.add(child)
        else:parents[child]=parent
    for child in conflicts:parents.pop(child,None)
    def root(trial):
        while trial in parents:trial=parents[trial]
        return trial
    roots={trial:root(trial) for trial in trials};groups={}
    for attempt in attempts:
        if attempt['trial_id'] in roots:groups.setdefault(roots[attempt['trial_id']],[]).append(attempt)
    details={}
    for record in records:
        source=json.loads(record['source'])
        if source.get('origin')!='system-lpai-diagnostic':continue
        matched=tasks.get((record['session_id'],source.get('task_uid')),[])
        trial=matched[0]['trial_id'] if len(matched)==1 else None
        # An exact diagnostic parent is also a valid detail location; titles are not.
        if trial not in roots and record['parent_id'] in roots:
            parent=by_id[record['parent_id']]
            if parent['session_id']==record['session_id']:trial=parent['id']
        details[record['id']]=roots.get(trial)
    for trial,owner in roots.items():
        if trial!=owner:details[trial]=owner
    return {'roots':roots,'groups':groups,'details':details}


def diagnostics(store, session_id, task_uid):
    if not task_uid or not store.one("SELECT name FROM sqlite_master WHERE name='company_diagnostics'"):return []
    return [{'诊断文件':r['path'],'采集时间':r['created_at'],'诊断摘要':json.loads(r['summary'])}
            for r in store.rows('SELECT path,created_at,summary FROM company_diagnostics WHERE session_id=? AND task_uid=? ORDER BY rowid',(session_id,task_uid))]
