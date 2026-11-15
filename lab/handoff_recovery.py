"""Resume a committed handoff after a proven service shutdown, never a user stop.

Native turn history remains interrupted. Only its idle session projection may
be restored, after a fresh native read and all original dispatch guards.
"""
import json
from .store import now
from .delivery_control import SERVICE_SHUTDOWN_REASON as SHUTDOWN_REASON


def pending(store,sid):
    session=store.session(sid)
    if session['archived'] or session['active_turn'] or session['status'] not in {'interrupted','idle'} or not session['thread_id']:return None
    from .autonomy import policy
    p=policy(store,session['project_id'])
    if not p or p['mode']!='autonomous' or p['owner_session_id']!=sid:return None
    control=store.one('SELECT paused,reason,updated_at FROM research_controls WHERE session_id=?',(sid,))
    if control and control['paused'] and control['reason']!=SHUTDOWN_REASON:return None
    stopped=store.one('SELECT stopped_at FROM research_user_stops WHERE session_id=?',(sid,))
    if stopped and stopped['stopped_at']>=p['updated_at']:return None
    if store.one("SELECT 1 FROM deliveries WHERE session_id=? AND state IN ('requested','unknown','queued')",(sid,)):return None
    from . import rounds
    if rounds.pending_cloud(store,session['project_id']) or rounds.pending_platform_operation(store,session['project_id']):return None
    if store.one("SELECT 1 FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown')",(session['project_id'],)):return None
    h=store.one("SELECT * FROM research_role_handoffs WHERE session_id=? AND state='pending' ORDER BY rowid LIMIT 1",(sid,))
    if not h:return None
    source=store.one('SELECT rowid,* FROM deliveries WHERE id=? AND session_id=?',(h['source_delivery'],sid))
    if not source or source['state']!='accepted' or not source['turn_id']:return None
    request=store.one('SELECT * FROM delivery_requests WHERE delivery_id=?',(source['id'],))
    if not request or request['thread_id']!=session['thread_id'] or request['method']!='turn/start':return None
    # A later human instruction or another actual turn cannot be erased by a
    # maintenance receipt. Steers in a different contract still get precedence.
    if store.one('''SELECT 1 FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id
        WHERE d.session_id=? AND d.rowid>? AND o.origin IN ('user','review')''',(sid,source['rowid'])):return None
    if store.one("SELECT 1 FROM deliveries WHERE session_id=? AND rowid>? AND state='accepted' AND turn_id IS NOT NULL AND turn_id<>?",(sid,source['rowid'],source['turn_id'])):return None
    intents=store.rows('SELECT * FROM delivery_interrupt_intents WHERE session_id=? AND thread_id=? AND turn_id=?',
        (sid,session['thread_id'],source['turn_id']))
    if (not intents or any(i['reason']!=SHUTDOWN_REASON or i['state'] not in {'acknowledged','completed'}
            or i['created_at']<h['created_at'] for i in intents)):return None
    audit=store.one("SELECT payload FROM events WHERE session_id=? AND kind='turn.interrupt_requested' AND created_at>=? ORDER BY seq DESC LIMIT 1",(sid,h['created_at']))
    if not audit:return None
    audit=json.loads(audit['payload'])
    legacy='origin' not in audit
    if audit.get('reason')!=SHUTDOWN_REASON or audit.get('origin') not in {None,'service_shutdown'}:return None
    if not legacy and (audit.get('thread_id')!=session['thread_id'] or source['turn_id'] not in audit.get('turn_ids',[])):return None
    legacy_pause=bool(control and control['paused'])
    if legacy_pause and (not legacy or control['updated_at'] not in {i['created_at'] for i in intents}):return None
    return {'handoff':h,'source_turn':source['turn_id'],'thread_id':session['thread_id'],
        'intent_id':intents[-1]['id'],'authority_id':p['authority_id'],'legacy_pause':control if legacy_pause else None}


def release(store,sid,turns):
    value=pending(store,sid)
    if not value or not any(t.get('id')==value['source_turn'] and t.get('status')=='interrupted' for t in turns):return False
    with store.lock,store.db:
        current=pending(store,sid)
        if current!=value:return False
        if value['legacy_pause']:
            store.db.execute('UPDATE research_controls SET paused=0,reason=?,updated_at=? WHERE session_id=?',
                ('已核验原服务维护中断；恢复既有交接，不扩展研究授权',now(),sid))
        store.db.execute("UPDATE sessions SET status='idle' WHERE id=? AND status IN ('interrupted','idle') AND active_turn IS NULL",(sid,))
        if not store.one("SELECT 1 FROM events WHERE session_id=? AND kind='research.handoff_recovered_after_shutdown' AND json_extract(payload,'$.handoff_id')=?",(sid,value['handoff']['id'])):
            store.event(sid,'research.handoff_recovered_after_shutdown',{'handoff_id':value['handoff']['id'],
                'delivery_id':value['handoff']['delivery_id'],'source_turn':value['source_turn'],
                'thread_id':value['thread_id'],'intent_id':value['intent_id'],'authority_id':value['authority_id'],
                'source_turn_status_preserved':'interrupted','budget_reset':False,'cloud_tasks_cancelled':False})
    return True
