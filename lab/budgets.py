"""Append-only compute allowances. The original approval remains the grant."""
from .store import now

LIMIT=1000
BATCH=6


def cloud_assessment_ready(store,project_id,plan_id):
    """Local verified evidence permits analysis, never another computation.

    Do not treat a ready flag, a failed task or an old plan's archive as proof.
    The ordinary collector, native role loop and compute guards stay in charge.
    """
    # The legacy name is retained for stored/older host contracts. New SSH jobs
    # share the local immutable evidence archive, so analysis must not deadlock
    # at the compute ceiling merely because their backend is not Task.submit.
    local=store.rows('SELECT id,state FROM research_runs WHERE project_id=? AND plan_id=?',(project_id,plan_id))
    if local:
        if any(r['state'] in {'requested','running','unknown'} for r in local):return False
        for run in local:
            proof=store.evidence.summary('local',run['id'])
            if run['state']!='completed' or not proof or not proof['analysis_ready'] or proof['analysis_scope']!='result_analysis':continue
            try:store.evidence.verify('local',run['id'])
            except (ValueError,OSError,KeyError,TypeError):continue
            return True
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_output_jobs'"):return False
    runs=store.rows("SELECT id,session_id,task_uid,state FROM company_training WHERE project_id=? AND plan_id=? AND state<>'prepared'",(project_id,plan_id))
    terminal={'success','succeeded','completed','failed','error','stopped','terminated','cancelled','canceled'}
    if not runs or any(r['state'].lower() not in terminal for r in runs):return False
    jobs={r['id']:store.one("SELECT state FROM company_output_jobs WHERE session_id=? AND task_uid=?",(r['session_id'],r['task_uid'])) for r in runs}
    if any(not job or job['state'] not in {'ready','diagnostic_only'} for job in jobs.values()):return False
    from .cloud_results import read
    for run in runs:
        if run['state'].lower() not in {'success','succeeded','completed'}:continue
        job=jobs[run['id']]
        if not job or job['state']!='ready':continue
        try:read(store,project_id,run['id'])
        except (ValueError,OSError,KeyError,TypeError):continue
        return True
    return False


def exhausted(resources):
    fit_needed=resources['fit_exhausted'] and (resources['execution_scope']['execution']=='lpai' or resources['budget']['max_fits']>0)
    return resources['computation_exhausted'],fit_needed


def latest(store,grant_id):
    return store.one('SELECT * FROM research_budget_changes WHERE grant_id=? ORDER BY revision DESC LIMIT 1',(grant_id,))


def effective(store,grant):
    change=latest(store,grant['id'])
    if change:
        return {**grant,'max_runs':change['max_runs'],'max_fits':change['max_fits']}
    return grant


def status(store,grant):
    row=latest(store,grant['id'])
    if row:return {key:row[key] for key in ('revision','max_runs','max_fits','auto','hard_runs','hard_fits','source')}
    return {'revision':0,'max_runs':grant['max_runs'],'max_fits':grant['max_fits'],
            'auto':False,'hard_runs':None,'hard_fits':None,'source':'initial_approval'}


def change(store,grant,revision,add_runs,add_fits,auto,hard_runs,hard_fits,source):
    with store.lock,store.db:
        old=status(store,grant)
        if type(revision) is not int or revision!=old['revision']:raise ValueError('预算已更新，请重新读取原累计值')
        if any(type(value) is not int or not 0<=value<=LIMIT for value in (add_runs,add_fits)) or type(auto) is not bool:
            raise ValueError('追加次数必须为非负整数，并明确选择自动续额')
        runs=old['max_runs']+add_runs;fits=old['max_fits']+add_fits
        if not 0<=fits<=runs<=LIMIT:raise ValueError('累计任务上限最多1000；兼容拟合边界不能超过任务上限')
        if hard_runs is not None or hard_fits is not None or auto:
            if (type(hard_runs) is not int or type(hard_fits) is not int or not 0<=hard_fits<=hard_runs<=LIMIT
                    or runs>hard_runs or fits>hard_fits):raise ValueError('硬上限需覆盖已批准额度，且拟合硬上限不能超过运行硬上限')
        if not add_runs and not add_fits and (auto,hard_runs,hard_fits)==(bool(old['auto']),old['hard_runs'],old['hard_fits']):return old
        store.db.execute('INSERT INTO research_budget_changes VALUES(?,?,?,?,?,?,?,?,?)',
            (grant['id'],revision+1,runs,fits,int(auto),hard_runs,hard_fits,source,now()))
        return status(store,grant)


def renew(store,grant,*,run_needed,fit_needed):
    old=status(store,grant)
    if not old['auto']:return None
    runs=min(old['hard_runs'],old['max_runs']+BATCH)
    fits=min(old['hard_fits'],runs,old['max_fits']+BATCH)
    if (run_needed and runs==old['max_runs']) or (fit_needed and fits==old['max_fits']):return None
    return change(store,grant,old['revision'],runs-old['max_runs'],fits-old['max_fits'],True,
                  old['hard_runs'],old['hard_fits'],'automatic_preapproved')
