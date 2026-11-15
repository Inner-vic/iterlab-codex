"""Erase recycled local records transactionally; never delete files or cloud jobs."""
from .deletion import project_lock,ready

def q(name):return '"'+name.replace('"','""')+'"'

def purge(kernel,kind,id,confirmation,*,direct=False):
    store=kernel.store
    if kind not in {'project','session'}:raise ValueError('不支持的删除类型')
    table='projects' if kind=='project' else 'sessions'
    row=store.one('SELECT * FROM '+table+' WHERE id=?',(id,))
    if not row or not direct and not store.deleted(kind,id):raise ValueError('请先将对象移入回收站')
    if not isinstance(confirmation,str) or confirmation.strip() not in {'确认','确定'}:
        raise ValueError('请输入“确认”或“确定”以永久删除')
    pid=id if kind=='project' else row['project_id']
    with project_lock(kernel,pid):
        ready(kernel,pid,None if kind=='project' else id)
        # Session deletion cannot discard an unresolved remote job either.
        if kind=='session':
            for name in ('company_training',):
                if store.one("SELECT name FROM sqlite_master WHERE name=?",(name,)) and store.one(
                    'SELECT 1 FROM '+name+" WHERE session_id=? AND state IN ('requested','unknown','submitted','pending','running','pending_approval') LIMIT 1",(id,)):
                    raise ValueError('原云任务尚未核对终态，不能永久删除')
        db=store.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('PRAGMA defer_foreign_keys=ON')
            names=[r['name'] for r in store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            cols={t:{r['name'] for r in store.rows('PRAGMA table_info('+q(t)+')')} for t in names}
            for t in names:db.execute('CREATE TEMP TABLE '+q('purge_'+t)+'(rid INTEGER PRIMARY KEY)')
            try:
                sids=[r['id'] for r in store.rows('SELECT id FROM sessions WHERE '+('project_id' if kind=='project' else 'id')+'=?',(id,))]
                args=','.join('?' for _ in sids) or 'NULL'
                def add(t,where,values=()):
                    return db.execute('INSERT OR IGNORE INTO '+q('purge_'+t)+' SELECT rowid FROM '+q(t)+' WHERE '+where,values).rowcount
                for t in names:
                    if kind=='project' and 'project_id' in cols[t]:add(t,'project_id=?',(id,))
                    for col in ('session_id','owner_session_id'):
                        if col in cols[t] and sids:add(t,q(col)+' IN ('+args+')',sids)
                add(table,'id=?',(id,))
                # Follow actual foreign keys plus legacy untyped ownership links.
                links={'record_id':['records'],'plan_id':['records'],'trial_id':['records'],
                    'delivery_id':['deliveries'],'source_delivery':['deliveries'],'source_delivery_id':['deliveries'],
                    'submission_id':['company_training'],'watch_id':['company_watches'],
                    'handoff_id':['research_role_handoffs'],'authority_id':['research_authorities'],
                    'branch_id':['research_branches'],'run_id':['research_runs','company_training','devspace_jobs'],
                    'finalization_id':['research_finalizations'],'operation_id':['company_operations']}
                changed=True
                while changed:
                    changed=False
                    for t in names:
                        refs={(r['from'],r['table'],r['to'] or 'id') for r in store.rows('PRAGMA foreign_key_list('+q(t)+')')}
                        refs|={(c,p,'id') for c,parents in links.items() if c in cols[t] for p in parents if p in cols and 'id' in cols[p]}
                        for c,p,target in refs:
                            if p not in cols:continue
                            changed=bool(add(t,q(c)+' IN (SELECT '+q(target)+' FROM '+q(p)+' WHERE rowid IN (SELECT rid FROM '+q('purge_'+p)+'))')) or changed
                for t in names:
                    selected='rowid IN (SELECT rid FROM '+q('purge_'+t)+')'
                    for col,allowed in [('project_id',[pid]),('session_id',sids),('owner_session_id',sids)]:
                        if col in cols[t] and store.one('SELECT 1 FROM '+q(t)+' WHERE '+selected+' AND '+q(col)+' IS NOT NULL AND '+q(col)+' NOT IN ('+(','.join('?' for _ in allowed) or 'NULL')+') LIMIT 1',allowed):
                            raise ValueError('其他会话仍引用此研究记录，暂不永久删除；请按项目处理')
                for t in names:
                    db.execute('DELETE FROM '+q(t)+' WHERE rowid IN (SELECT rid FROM '+q('purge_'+t)+')')
                db.execute('DELETE FROM workbench_trash WHERE kind=? AND object_id=?',(kind,id))
                if kind=='project' and sids:db.execute("DELETE FROM workbench_trash WHERE kind='session' AND object_id IN ("+args+')',sids)
                if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('关联完整性核对未通过，已回滚删除')
            finally:
                for t in names:db.execute('DROP TABLE '+q('purge_'+t))
    store.event(None,'workbench.purged',{'kind':kind,'id':id,'files_removed':False,'cloud_operations':False})
    return {'purged':True,'recoverable':False,'files_removed':False,'native_history_removed':False}
