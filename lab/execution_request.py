"""Explicit project runtime choice, separate from historical execution grants."""
from .store import now


def install(store):
    store.execute('''CREATE TABLE IF NOT EXISTS project_execution_requests(
        project_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,execution TEXT NOT NULL,
        space_uid TEXT,reason TEXT NOT NULL,source TEXT NOT NULL,updated_at TEXT NOT NULL)''')


def status(store, project_id):
    if not store.one("SELECT name FROM sqlite_master WHERE name='project_execution_requests'"):
        return None
    row=store.one('SELECT * FROM project_execution_requests WHERE project_id=?',(project_id,))
    if not row:return None
    state='selected';reason='后续方案须使用已选择的执行位置；历史任务与累计保持。'
    if row['execution']=='lpai_devspace':
        from .devspace import binding,ssh_profile
        bound=binding(store,project_id)
        if not bound or row['space_uid'] and bound['uid']!=row['space_uid']:
            state='needs_binding';reason='请绑定本次选择的开发空间，不改用组件库或本地。'
        elif not ssh_profile(store,project_id,bound):
            state='needs_ssh';reason='在项目环境页保存并验证 SSH 后才能上传数据和训练；尚未启动计算。'
    return {**row,'state':state,'message':reason,
            'data_transfer_policy':'允许将原数据的同字节副本上传到选定空间并逐文件校验哈希；存储位置不等于数据来源，不改变标签、划分或评价规则。',
            'authorization':'这是执行位置选择，不新增GPU授权、不扩大预算、不启动或停止任务。'}


def select(store, sid, execution, reason, source='用户界面选择'):
    if execution not in {'lpai_devspace','lpai','local'}:raise ValueError('请选择开发空间、组件库或本地')
    if not isinstance(reason,str) or not reason.strip():raise ValueError('需说明本次执行位置选择')
    session=store.session(sid);pid=session['project_id'];space_uid=None
    if execution=='lpai_devspace':
        from .devspace import binding
        bound=binding(store,pid)
        space_uid=bound['uid'] if bound else None
    with store.lock,store.db:
        old=store.one('SELECT * FROM project_execution_requests WHERE project_id=?',(pid,))
        if old and (old['execution'],old['space_uid'],old['reason'])==(execution,space_uid,reason.strip()):
            return status(store,pid)
        store.db.execute('INSERT INTO project_execution_requests VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET session_id=excluded.session_id,execution=excluded.execution,space_uid=excluded.space_uid,reason=excluded.reason,source=excluded.source,updated_at=excluded.updated_at',
            (pid,sid,execution,space_uid,reason.strip(),source,now()))
        store.event(sid,'research.execution_location_selected',{'project_id':pid,'previous':old,'execution':execution,'space_uid':space_uid,'reason':reason.strip(),'source':source,'grant_changed':False})
    return status(store,pid)


def require_route(store, project_id, execution):
    request=status(store,project_id)
    if request and request['execution']!=execution:
        name={'lpai_devspace':'LPAI 开发空间','lpai':'LPAI 组件库','local':'本地'}[request['execution']]
        raise ValueError('用户已指定后续在'+name+'执行，不能沿用旧方案换到其他地点；先完成该地点的环境与方案核对。')
    if request and request['state'] in {'needs_ssh','needs_binding'}:
        raise ValueError(request['message'])
