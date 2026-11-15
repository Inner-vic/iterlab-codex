"""One durable repair after system stall/proven initialization failure, no new grant.

The old reporting turn and old plan stay closed. Only a different, Critic-reviewed
successor may be admitted against the same cumulative grant.
"""
import hashlib
import json

from .store import dumps, now


def install(store):
    with store.lock, store.db:
        store.db.execute('''CREATE TABLE IF NOT EXISTS research_recovery_authorities(
            authority_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS research_recoveries(
            id TEXT PRIMARY KEY,session_id TEXT NOT NULL,finalization_id TEXT NOT NULL UNIQUE,
            authority_id TEXT NOT NULL,branch_id TEXT NOT NULL,plan_id TEXT NOT NULL,
            review_id TEXT NOT NULL,policy_generation TEXT NOT NULL,delivery_id TEXT NOT NULL UNIQUE,
            admitted_plan_id TEXT,critic_handoff_id TEXT,created_at TEXT NOT NULL)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS research_recovery_inputs(
            delivery_id TEXT PRIMARY KEY,recovery_id TEXT NOT NULL,created_at TEXT NOT NULL,
            FOREIGN KEY(recovery_id) REFERENCES research_recoveries(id))''')


def initialization_pause(store,pid,p):
    """A proven pre-training platform failure, not a scientific or human stop.

    Only immutable official diagnostic bytes can establish this passage. An
    empty archive alone, a model's failure label, or a failed training process
    is insufficient. This returns context, never an execution grant.
    """
    from .autonomy import active
    from . import rounds
    branch=active(store,pid)
    if not branch or branch['authority_id']!=p['authority_id']:return None
    plan_id=branch['head_plan_id'];review=rounds.round_review(store,pid,plan_id)
    if not review:return None
    source=json.loads(review['source'])
    if (source.get('stop_reason')!='blocked' or source.get('round_outcome')!='stop_requested'
            or source.get('origin')!='codex-native-tool' or source.get('closure_mode')!='autonomous'):return None
    grant=store.one('SELECT g.execution FROM research_authorities a JOIN research_grants g ON g.id=a.grant_id WHERE a.id=?',(p['authority_id'],))
    if not grant or grant['execution']!='lpai':return None
    if len(store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('company_training','company_diagnostics')"))!=2:return None
    if store.one('SELECT 1 FROM research_runs WHERE project_id=? AND plan_id=?',(pid,plan_id)):return None
    runs=store.rows("SELECT * FROM company_training WHERE project_id=? AND plan_id=? AND state<>'prepared'",(pid,plan_id))
    declared=source.get('cloud_run_ids',[])
    if not isinstance(declared,list) or any(not isinstance(value,str) for value in declared):return None
    if not runs or len(runs)>1000 or set(declared)!={r['id'] for r in runs}:return None
    for run in runs:
        if run['state']!='failed' or not run['task_uid']:return None
        diagnostic=store.one('SELECT * FROM company_diagnostics WHERE session_id=? AND task_uid=? ORDER BY rowid DESC LIMIT 1',(run['session_id'],run['task_uid']))
        if not diagnostic:return None
        try:
            path=store.safe_path(pid,diagnostic['path'])
            if not path.is_file() or path.stat().st_size>2*1024*1024:return None
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()!=diagnostic['digest']:return None
            evidence=json.loads(data);task=evidence.get('task') or {};flows=task.get('flows') or []
            if task.get('uid')!=run['task_uid'] or task.get('phase')!='failed' or evidence.get('training_logs'):return None
            if not any(f.get('name')=='dataPreparation' and f.get('phase')=='failed' for f in flows):return None
            training=[f for f in flows if f.get('name')=='task']
            if len(training)!=1 or training[0].get('phase') not in (None,''):return None
            if any(training[0].get(key) not in (None,0) for key in ('startedat','scheduledat','jobinitat')):return None
        except (OSError,ValueError,TypeError,AttributeError):return None
    return {'id':'research-init-repair:'+hashlib.sha256(dumps([p['authority_id'],review['id']]).encode()).hexdigest(),
        'session_id':p['owner_session_id'],'authority_id':p['authority_id'],'branch_id':branch['id'],
        'plan_id':plan_id,'reason':'blocked','created_at':review['created_at']}


def context(store, pid):
    from .autonomy import finalizing, policy
    from . import rounds
    final = finalizing(store, pid)
    p = policy(store, pid)
    if not p or p['mode'] != 'autonomous':
        return None
    if not final or final['reason']!='no_progress':
        initialization=initialization_pause(store,pid,p)
        if not initialization:return None
        if final and (final['reason']!='blocked' or final['plan_id']!=initialization['plan_id']):return None
        final=final or initialization
    if final['session_id'] != p['owner_session_id']:
        return None
    if not store.one('SELECT 1 FROM research_recovery_authorities WHERE authority_id=? AND enabled=1',(p['authority_id'],)):
        return None # Historical read-only acceptances are not silently reopened.
    control = store.one('SELECT paused FROM research_controls WHERE session_id=?', (final['session_id'],))
    stopped=store.one('SELECT stopped_at FROM research_user_stops WHERE session_id=?',(final['session_id'],))
    if stopped and stopped['stopped_at']>=p['updated_at']:
        return None # Ordinary conversation may unpause dialogue, not this stop.
    session = store.session(final['session_id'])
    if session['archived'] or control and control['paused']:
        return None
    review = rounds.round_review(store, pid, final['plan_id'])
    if not review:
        return None  # Reporting must finish first; do not steer its read-only turn.
    source = json.loads(review['source'])
    if (source.get('round_outcome') != 'stop_requested' or source.get('stop_reason') != final['reason']
            or source.get('closure_mode') != 'autonomous' or source.get('origin') != 'codex-native-tool'):
        return None
    if store.one('SELECT 1 FROM research_round_decisions WHERE review_record_id=?', (review['id'],)):
        return None  # A human end decision always wins.
    return {**final, 'review_id': review['id'], 'current_generation': p['updated_at']}


def enable(store,sid,authority_id,reason):
    from .autonomy import policy
    p=policy(store,store.session(sid)['project_id'])
    if not p or p['owner_session_id']!=sid or p['authority_id']!=authority_id or p['mode']!='autonomous' or not str(reason).strip():
        raise ValueError('恢复策略须对应当前会话的原自主授权及明确来源，不产生新授权')
    store.execute('INSERT INTO research_recovery_authorities VALUES(?,1,?,?) ON CONFLICT(authority_id) DO UPDATE SET enabled=1,reason=excluded.reason',
        (authority_id,reason,now()))
    store.event(sid,'research.system_recovery_enabled',{'authority_id':authority_id,'reason':reason,'budget_reset':False})
    return {'enabled':True,'authority_id':authority_id,'budget_reset':False}


def candidate_link(store, pid, previous_id, review_id):
    value = context(store, pid)
    return bool(value and value['plan_id'] == previous_id and value['review_id'] == review_id)


def reserve_discussion(store,sid,delivery_id,plan,target,stage):
    """Register an already accepted repair discussion, not a new grant/turn.

    A human input may queue during reporting and start after it. Its accepted
    timestamp, not queue creation, establishes that the reporting boundary has
    passed. All original scope and exact Critic admission gates still apply.
    """
    if target=='executor' and stage!='prepare':return None
    if target not in {'planner','generator','critic','executor'}:return None
    pid=store.session(sid)['project_id'];value=context(store,pid)
    if not value or value['session_id']!=sid:return None
    from . import rounds
    if (rounds.pending_cloud(store,pid) or rounds.pending_platform_operation(store,pid)
            or store.one("SELECT 1 FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown')",(pid,))
            or store.one("SELECT 1 FROM deliveries d JOIN sessions s ON s.id=d.session_id WHERE s.project_id=? AND d.state IN ('requested','unknown')",(pid,))
            or store.one("SELECT 1 FROM approvals a JOIN sessions s ON s.id=a.session_id WHERE s.project_id=? AND a.state IN ('pending','responding','unknown')",(pid,))):return None
    source=json.loads(plan['source'])
    if (source.get('origin')!='codex-native-tool' or source.get('previous_plan_id')!=value['plan_id']
            or source.get('review_record_id')!=value['review_id']):return None
    authority=store.one('SELECT * FROM research_authorities WHERE id=?',(value['authority_id'],))
    scope=json.loads(authority['scope']);proposal=source.get('scope_proposal')
    if proposal and any(proposal.get(k)!=scope[k] for k in ('objective','evaluation','data_boundary','allowed_changes')):return None
    if source.get('authority_id') not in (None,authority['id']) or source.get('scope_digest') not in (None,authority['scope_digest']):return None
    previous=store.one('SELECT source FROM records WHERE id=?',(value['plan_id'],))
    if source.get('evaluation_contract')!=json.loads(previous['source']).get('evaluation_contract'):return None
    # A proposed repair may still need evidence-reference corrections by the
    # Critic. Registering deliberation must not demand execution-ready inputs.
    # Adoption and execution independently validate every frozen reference.
    from .devspace import compute
    root=store.one('SELECT r.* FROM records r JOIN research_grants g ON g.plan_id=r.id WHERE g.id=?',(authority['grant_id'],))
    if compute(plan) is not None and compute(plan)!=compute(root):return None
    delivery=store.one('''SELECT d.rowid,d.*,o.origin FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id
        WHERE d.id=? AND d.session_id=?''',(delivery_id,sid))
    if (not delivery or delivery['origin'] not in {'user','review'} or delivery['state']!='accepted'
            or not delivery['turn_id'] or delivery['updated_at']<=value['created_at']):return None
    session=store.session(sid)
    request=store.one('SELECT * FROM delivery_requests WHERE delivery_id=?',(delivery_id,))
    if (not request or request['method']!='turn/start' or request['thread_id']!=session['thread_id']
            or session['active_turn']!=delivery['turn_id']):return None
    if store.one("SELECT d.id FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id WHERE d.session_id=? AND d.rowid>? AND o.origin IN ('user','review')",(sid,delivery['rowid'])):return None
    key='research-recovery:'+hashlib.sha256(value['id'].encode()).hexdigest()
    with store.lock,store.db:
        if context(store,pid)!=value:return None
        existing=store.one('SELECT * FROM research_recoveries WHERE finalization_id=?',(value['id'],))
        if existing:
            if existing['policy_generation']!=value['current_generation'] or existing['admitted_plan_id']:return None
            if existing['delivery_id']==delivery_id or store.one('SELECT 1 FROM research_recovery_inputs WHERE delivery_id=?',(delivery_id,)):return None
            store.db.execute('INSERT INTO research_recovery_inputs VALUES(?,?,?)',(delivery_id,existing['id'],now()))
            key=existing['id']
        else:
            store.db.execute('INSERT INTO research_recoveries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (key,sid,value['id'],value['authority_id'],value['branch_id'],value['plan_id'],
                 value['review_id'],value['current_generation'],delivery_id,None,None,now()))
        store.event(sid,'research.recovery_discussion_registered',{'id':key,'delivery_id':delivery_id,
            'origin':delivery['origin'],'authority_id':authority['id'],'new_turn_sent':False,
            'new_authorization':False,'critic_still_required':True,'budget_reset':False})
    return store.one('SELECT * FROM research_recoveries WHERE id=?',(key,))


def chain(store, sid, delivery_id):
    """Only the reserved recovery delivery and its exact descendants get passage."""
    if not store.one("SELECT name FROM sqlite_master WHERE name='research_recoveries'"):
        return None
    seen = set()
    for _ in range(32):
        if delivery_id in seen:
            return None
        seen.add(delivery_id)
        row = store.one('SELECT * FROM research_recoveries WHERE session_id=? AND delivery_id=?', (sid, delivery_id))
        if not row and store.one("SELECT name FROM sqlite_master WHERE name='research_recovery_inputs'"):
            row=store.one('''SELECT r.* FROM research_recoveries r JOIN research_recovery_inputs i ON i.recovery_id=r.id
                WHERE r.session_id=? AND i.delivery_id=?''',(sid,delivery_id))
        if row:
            value = context(store, store.session(sid)['project_id'])
            if (value and value['id'] == row['finalization_id'] and row['admitted_plan_id'] is None
                    and value['current_generation'] == row['policy_generation']):
                return row
            return None
        handoff = store.one('SELECT source_delivery FROM research_role_handoffs WHERE session_id=? AND delivery_id=?', (sid, delivery_id))
        if not handoff:
            return None
        delivery_id = handoff['source_delivery']
    return None


def consumed_turn_steer(store,sid,source_id,before):
    """Exact accepted inputs to the source turn, never a new execution grant.

    Only nonexecuting handoff callers use this. Queued/unknown inputs, another
    turn/thread, and input arriving after the handoff keep stale-work barriers.
    """
    session=store.session(sid)
    source=store.one('SELECT rowid,* FROM deliveries WHERE id=? AND session_id=?',(source_id,sid))
    if not source or source['state']!='accepted' or not source['turn_id'] or not session['thread_id']:return None
    if not store.one("SELECT name FROM sqlite_master WHERE name='delivery_requests'"):return None
    inputs=store.rows('''SELECT d.*,r.method,r.thread_id,r.expected_turn_id,r.observed_turn_id
        FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id
        LEFT JOIN delivery_requests r ON r.delivery_id=d.id
        WHERE d.session_id=? AND d.rowid>? AND o.origin IN ('user','review') ORDER BY d.rowid LIMIT 65''',
        (sid,source['rowid']))
    if not inputs or len(inputs)>64:return None
    for item in inputs:
        if (item['mode']!='steer' or item['state']!='accepted' or item['method']!='turn/steer'
                or item['turn_id']!=source['turn_id'] or item['expected_turn_id']!=source['turn_id']
                or item['observed_turn_id'] not in {None,source['turn_id']}
                or item['thread_id']!=session['thread_id'] or item['updated_at']>before):return None
    return inputs[-1]


def proposal_discussion(store,sid,delivery_id,*,proposed=None):
    """Fresh human-led proposal discussion is not revival of a closed grant.

    Only deliberation / offline prepare descendants of a new accepted input
    may pass. A proposal remains unapproved; execute/adopt/platform guards stay.
    """
    from .autonomy import finalizing
    from . import rounds
    session=store.session(sid);final=finalizing(store,session['project_id'])
    current=rounds.approved_plan(store,session['project_id'])
    review=rounds.round_review(store,session['project_id'],current['id']) if current else None
    closed=bool(review and json.loads(review['source']).get('round_outcome')=='stop_requested')
    if (not final and not closed) or session['archived']:return None
    closed_id=current['id'] if closed else final['plan_id']
    closed_at=review['created_at'] if closed else final['created_at']
    control=store.one('SELECT paused FROM research_controls WHERE session_id=?',(sid,))
    if control and control['paused']:return None
    h=proposed or store.one('SELECT * FROM research_role_handoffs WHERE session_id=? AND delivery_id=?',(sid,delivery_id))
    if not h:
        from .response_recovery import original_route
        recovered=original_route(store,sid,delivery_id)
        if recovered and recovered['delivery_id']!=delivery_id:
            return proposal_discussion(store,sid,recovered['delivery_id'])
        return None
    stage=({'stage':h['execution_stage']} if proposed else
        store.one('SELECT stage FROM research_role_execution_stages WHERE handoff_id=?',(h['id'],)))
    if h['target']=='executor' and (not stage or stage['stage']!='prepare'):return None
    plan=store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='plan'",(h['plan_id'],session['project_id']))
    if not plan or plan['id']==closed_id or not json.loads(plan['source']).get('scope_proposal'):return None
    if store.one('SELECT id FROM research_grants WHERE plan_id=?',(plan['id'],)):return None
    source=h['source_delivery'];seen=set()
    steer=consumed_turn_steer(store,sid,source,h.get('created_at',now()))
    if steer:source=steer['id']
    for _ in range(32):
        if source in seen:return None
        seen.add(source)
        origin=store.one('''SELECT d.*,o.origin FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id
            WHERE d.id=? AND d.session_id=?''',(source,sid))
        if not origin or origin['state']!='accepted':return None
        if origin['origin'] in {'user','review'}:
            if origin['created_at']<=closed_at:return None
            if plan['created_at']<origin['created_at']:
                # A new explicit input may resume an existing unapproved
                # proposal. No duplicate plan/version is needed just for time.
                provenance=json.loads(plan['source'])
                if (provenance.get('previous_plan_id')!=closed_id or not review
                        or provenance.get('review_record_id')!=review['id']):return None
            stop=store.one('SELECT stopped_at FROM research_user_stops WHERE session_id=?',(sid,))
            if stop and stop['stopped_at']>=origin['created_at']:return None
            newer=store.one('''SELECT d.id FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id
                WHERE d.session_id=? AND o.origin IN ('user','review') AND d.created_at>? LIMIT 1''',(sid,origin['created_at']))
            if newer:return None
            return {'root_delivery_id':source,'plan_id':plan['id'],'closed_plan_id':closed_id,'execution_authorized':False}
        parent=store.one('SELECT source_delivery FROM research_role_handoffs WHERE session_id=? AND delivery_id=?',(sid,source))
        if not parent:
            from .response_recovery import original_route
            recovered=original_route(store,sid,source)
            if recovered and recovered['delivery_id']!=source:
                source=recovered['delivery_id'];continue
            return None
        source=parent['source_delivery']
    return None


def require_critic(research, sid, plan):
    from .research import plan_digest
    from .settings import Settings
    store = research.store
    final = context(store, plan['project_id'])
    source = json.loads(plan['source'])
    if not final or source.get('previous_plan_id') != final['plan_id'] or plan['id'] == final['plan_id']:
        raise ValueError('原收尾仍受保护：仅系统无进展或已核验训练前初始化失败可作确切修复；用户停止/撤销仍需明确处理')
    if source.get('review_record_id') not in {None, final['review_id']}:
        raise ValueError('修复方案必须承接最新系统暂停评审')
    if source.get('review_record_id') is None and source.get('origin') != 'human_intervention':
        raise ValueError('修复方案缺少原评审关联')
    previous = research.plan(sid, final['plan_id'])
    if (plan['content'], plan['evidence']) == (previous['content'], previous['evidence']):
        raise ValueError('修复方案须有实际变化，不能仅换标题/编号重开旧方案')
    request=store.one('SELECT * FROM research_recoveries WHERE finalization_id=?', (final['id'],))
    if not request or request['policy_generation']!=final['current_generation']:
        raise ValueError('先由宿主登记原系统暂停的修复审议，不重放旧请求')
    approval = store.one('''SELECT h.*,r.model,r.policy FROM research_role_handoffs h
        JOIN research_role_routes r ON r.delivery_id=h.source_delivery
        WHERE h.session_id=? AND h.plan_id=? AND h.plan_digest=? AND r.role='critic'
        ORDER BY h.rowid DESC LIMIT 1''', (sid, plan['id'], plan_digest(plan)))
    configured = Settings(store.root).value.get('agent_roles')
    if (not approval or approval['verdict'] != 'approve' or not configured
            or approval['model'] != configured['models']['critic'] or approval['created_at'] < final['created_at']):
        raise ValueError('系统暂停恢复须经当前 Critic 对确切修复方案独立批准，不能用旧审议或自批替代')
    return final, approval


def dispatch(continuation, sid):
    kernel = continuation.kernel
    store = continuation.store
    value = context(store, store.session(sid)['project_id'])
    if not value:
        return False
    if not kernel.settings.value.get('agent_roles'):
        continuation.note(sid, 'blocked', '系统暂停后的修复需要启用独立 Critic；未新增计算')
        return True
    kernel.research.autonomy.unresolved(store.session(sid)['project_id'])
    if store.evidence.blocking(store.session(sid)['project_id'], verify_ready=True):
        continuation.note(sid, 'waiting_evidence', '原证据尚未核验，暂不自动恢复修复方案')
        return True
    key = 'research-recovery:' + hashlib.sha256(value['id'].encode()).hexdigest()
    with store.lock, store.db:
        store.db.execute('INSERT OR IGNORE INTO research_recoveries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (key, sid, value['id'], value['authority_id'], value['branch_id'], value['plan_id'],
             value['review_id'], value['current_generation'], key, None, None, now()))
    row = store.one('SELECT * FROM research_recoveries WHERE id=?', (key,))
    if row['policy_generation'] != value['current_generation']:
        continuation.note(sid, 'blocked', '旧恢复请求的研究策略已变化；未重放')
        return True
    delivery = store.one('SELECT * FROM deliveries WHERE id=?', (row['delivery_id'],))
    if delivery:
        pending = store.one("SELECT delivery_id FROM research_role_handoffs WHERE session_id=? AND state='pending' ORDER BY rowid LIMIT 1", (sid,))
        if pending and chain(store, sid, pending['delivery_id']):
            kernel.resume_roles(sid)
        else:
            continuation.note(sid, 'repair_review', '原修复审议已投递，保留原身份；无新证据时不重复唤醒')
        return True
    if row['delivery_id']!=key:
        continuation.note(sid,'blocked','原修复讨论输入缺失；保留登记，不伪造后台投递或重发')
        return True
    cause='已核验的训练前平台初始化失败' if value['reason']=='blocked' else '系统无进展保护'
    text = ('[系统恢复审议 · 非人类新授权]\n'
            f'原方案因{cause}暂停，用户没有要求结束研究。原报告和失败尝试保留，旧方案不可重跑。\n'
            '请核对 research_status、原收尾评审和已有后继提案，找出具体阻塞及已有证据。'
            '有必要时保存有实际修改的修复后继，引用原方案和原评审；无需为恢复另起大方案或换编号。'
            '将确切修复方案交 Critic 独立审议，批准后由 research_adopt 核对原授权采纳，再交 Executor 执行。'
            '本消息不批准任何模型方案，不扩大预算/GPU数，不解除用户暂停或未知提交屏障。'
            '无有据可行修复时说明限制并停下，不重复改写无进展结论。\n'
            + dumps({'plan_id': value['plan_id'], 'review_id': value['review_id'], 'authority_id': value['authority_id']}))
    kernel.send(sid, text, 'queue', key, origin='background')
    continuation.note(sid, 'repair_review', cause+'：已向原会话投递一次修复审议，执行前仍须 Critic 和原授权核验')
    return True
