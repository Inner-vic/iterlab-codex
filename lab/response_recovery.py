"""One bounded continuation after a known output-limit failure in prepare.

Never replays an unknown turn, resumes execute, or changes model / authorization.
"""
import hashlib
import json
from .store import now


def install(store):
    store.execute('''CREATE TABLE IF NOT EXISTS research_output_recoveries(
        id TEXT PRIMARY KEY,session_id TEXT NOT NULL,failed_turn TEXT NOT NULL UNIQUE,
        source_delivery TEXT NOT NULL,plan_id TEXT NOT NULL,handoff_id TEXT NOT NULL,
        root_input TEXT NOT NULL,delivery_id TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,
        UNIQUE(session_id,plan_id,root_input))''')
    store.execute("INSERT OR IGNORE INTO schema_versions VALUES(22,datetime('now'))")


def original_route(store,sid,delivery_id):
    if not store.one("SELECT name FROM sqlite_master WHERE name='research_output_recoveries'"):return None
    row=store.one('SELECT * FROM research_output_recoveries WHERE session_id=? AND delivery_id=?',(sid,delivery_id))
    if not row:return None
    return store.one('SELECT * FROM research_role_routes WHERE session_id=? AND delivery_id=?',(sid,row['source_delivery']))


def dispatch(continuation,sid):
    kernel=continuation.kernel;store=continuation.store;session=store.session(sid)
    if session['active_turn'] or session['status']!='failed' or kernel.research.workflow.paused(sid):return False
    row=store.one("SELECT payload FROM events WHERE session_id=? AND kind='turn/completed' ORDER BY seq DESC LIMIT 1",(sid,))
    turn=json.loads(row['payload']).get('turn',{}) if row else {}
    error=turn.get('error') or {}
    if not isinstance(error,dict):return False
    if turn.get('status')!='failed' or 'max_output_tokens' not in str(error.get('message','')):return False
    route=store.one('''SELECT r.* FROM research_role_routes r JOIN deliveries d ON d.id=r.delivery_id
        WHERE r.session_id=? AND d.turn_id=? AND d.state='accepted' ''',(sid,turn.get('id')))
    if not route or route['role']!='executor' or not route['handoff_id'] or kernel.roles.execution_stage(route['handoff_id'])!='prepare':return False
    from .recovery import proposal_discussion
    discussion=proposal_discussion(store,sid,route['delivery_id'])
    if not discussion:return False
    # Only the current role/profile and definitively ended original work may
    # continue. A failure label alone never authorizes replay of other writes.
    if route['policy']!=json.dumps(kernel.settings.value.get('agent_roles'),ensure_ascii=False,separators=(',',':')):return False
    if kernel.delivery_control.barrier(sid):return False
    try:kernel.research.autonomy.unresolved(session['project_id'])
    except ValueError:return False
    if store.one("SELECT id FROM messages WHERE session_id=? AND turn_id=? AND kind='command' AND status IN ('running','inProgress','in_progress') LIMIT 1",(sid,turn['id'])):return False
    prior=store.one('SELECT * FROM research_output_recoveries WHERE session_id=? AND plan_id=? AND root_input=?',
        (sid,discussion['plan_id'],discussion['root_delivery_id']))
    if prior and prior['failed_turn']!=turn['id']:
        continuation.note(sid,'blocked','代码准备再次达到模型输出上限，已停止同类自动恢复；原文件/失败证据保持，未提交训练。');return True
    id='response-limit:'+hashlib.sha256((sid+':'+turn['id']).encode()).hexdigest()
    if not prior:
        store.execute('INSERT INTO research_output_recoveries VALUES(?,?,?,?,?,?,?,?,?)',
            (id,sid,turn['id'],route['delivery_id'],discussion['plan_id'],route['handoff_id'],discussion['root_delivery_id'],id,now()))
    text=('[代码准备接续 · 系统，非新授权] 上一Executor prepare轮已明确因max_output_tokens终止，不是未知提交。'
          '保留原方案、原角色/模型、已写文件与失败记录；不重放原消息，不重置预算。'
          '本次仅一次有界接续：先检查已落盘文件，再分小步完成缺少的实现。'
          '不要重新规划整个方案或在一次响应中构造全部代码；先用research_write保存一个小而完整的模块，再继续其他文件，单个文件尽量控制在150行内，必要时按职责拆模块。'
          '使用已有审计与明确路径，不重复读取完整历史或整份原始数据。'
          '仍仅prepare：不拟合、不research_run、不平台提交、不远端安装、不改变科学方案。完成实际无拟合检查后交回Planner并引用具体文件。'
          '若再次达输出上限，本类自动接续停止，报告已完成和剩余项。')
    existing=store.one('SELECT id,state FROM deliveries WHERE id=?',(id,))
    if not existing:kernel.send(sid,text,'queue',id,origin='background')
    elif existing['state']=='queued':kernel.pool.submit(kernel.drain,sid)
    continuation.note(sid,'preparing','原代码准备因输出上限失败；已保留身份并接续一次小步实现，不重复原请求或计算。')
    return True
