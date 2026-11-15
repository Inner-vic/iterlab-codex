"""Recoverable local deletion. Original research and native threads stay intact."""
from contextlib import ExitStack,contextmanager
from .store import now

@contextmanager
def project_lock(kernel,pid):
    store=kernel.store
    with ExitStack() as locks:
        for session in store.rows('SELECT id FROM sessions WHERE project_id=? ORDER BY id',(pid,)):
            locks.enter_context(kernel.lock_for(session['id']))
        locks.enter_context(kernel.research.lock)
        locks.enter_context(store.lock)
        if not store.one('SELECT id FROM projects WHERE id=?',(pid,)):raise ValueError('项目不存在')
        yield

def ready(kernel,pid,session_id=None):
    # Navigation is not a research strategy change. An offline CLI alone is not
    # an in-flight task; actual unknown deliveries/interrupts remain blockers.
    store=kernel.store
    for session in store.rows('SELECT * FROM sessions WHERE project_id=?'+(' AND id=?' if session_id else ''),(pid,session_id) if session_id else (pid,)):
        if session['active_turn'] or session['status'] in {'starting','running','compacting','waiting_approval','unknown'} or kernel.delivery_control.barrier(session['id']):
            raise ValueError('项目有运行中或待核对的执行，请先暂停或核对原任务；不会自动停止任务')
    if session_id:
        if store.one("SELECT id FROM research_runs WHERE session_id=? AND state IN ('requested','running','unknown')",(session_id,)):
            raise ValueError('该会话仍有未完成的计算，请先核对原任务')
        if store.one("SELECT id FROM company_training WHERE session_id=? AND state IN ('requested','unknown','submitted','pending','running','pending_approval') LIMIT 1",(session_id,)):
            raise ValueError('该会话仍有未核对终态的云任务，不能删除或归档')
    else:kernel.research.autonomy.unresolved(pid)

def pause_navigation(kernel,sid,reason):
    store=kernel.store
    kernel.research.workflow.pause(sid,reason)
    store.execute("UPDATE deliveries SET state='cancelled',updated_at=? WHERE session_id=? AND state='queued'",(now(),sid))
    store.execute('UPDATE company_watches SET auto_resume=0 WHERE session_id=?',(sid,))

def archive(kernel,pid,session_id=None):
    store=kernel.store
    with project_lock(kernel,pid):
        if store.deleted('project',pid):raise ValueError('请先恢复已移除的项目')
        ready(kernel,pid,session_id) # Check the complete batch before changing any chat.
        sessions=store.rows('SELECT id FROM sessions WHERE project_id=? AND archived=0'+(' AND id=?' if session_id else ''),(pid,session_id) if session_id else (pid,))
        for session in sessions:
            sid=session['id']
            if store.deleted('session',sid):continue
            pause_navigation(kernel,sid,'会话已归档，后台不自动唤醒')
            store.patch_session(sid,archived=1);store.event(sid,'session.archived',{})
    return {'archived':True,'count':len(sessions)}

def remove(kernel,kind,id):
    if kind not in {'project','session'}:raise ValueError('不支持的删除类型')
    store=kernel.store
    row=store.one('SELECT * FROM '+('projects' if kind=='project' else 'sessions')+' WHERE id=?',(id,))
    if not row:raise ValueError('对象不存在')
    if store.deleted(kind,id):return {'deleted':True,'recoverable':True,'files_removed':False}
    pid=id if kind=='project' else row['project_id']
    with project_lock(kernel,pid):
        if store.deleted('project',pid):return {'deleted':True,'recoverable':True,'files_removed':False}
        ready(kernel,pid,None if kind=='project' else id)
        for session in store.rows('SELECT id FROM sessions WHERE project_id=?'+(' AND id=?' if kind=='session' else '')+' ORDER BY id',(pid,id) if kind=='session' else (pid,)):
            sid=session['id']
            # Removed chats are already paused. Public session() correctly
            # rejects them, so do not re-enter its live workflow projection.
            # ready() above still checks every child's unresolved execution.
            if store.deleted('session',sid):continue
            pause_navigation(kernel,sid,'项目已移除；恢复记录不会自动恢复执行')
            store.execute("UPDATE company_watches SET auto_resume=0,wake_state='finished' WHERE session_id=?",(sid,))
            store.execute("UPDATE research_autonomy_policies SET mode='manual',reason=?,updated_at=? WHERE owner_session_id=?",('已删除，停止自动推进',now(),sid))
        store.execute('INSERT OR IGNORE INTO workbench_trash VALUES(?,?,?)',(kind,id,now()))
        store.event(None,'workbench.deleted',{'kind':kind,'id':id,'recoverable':True})
    return {'deleted':True,'recoverable':True,'files_removed':False}
