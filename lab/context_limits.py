"""Conservative native compaction calibration from an exact provider rejection.

No fixed per-project context size, model switch, history rewriting or LLM loop.
Only an existing native failure with explicit input/output/window counts can
lower the auto-compaction threshold for the same thread and single role model.
"""
import json
import re

_COUNTS=re.compile(r"maximum context length of ([\d,]{1,14}) tokens.*?([\d,]{1,14}) tokens from the input messages and ([\d,]{1,14}) tokens for the completion",re.I|re.S)


def rejection(value):
    if not isinstance(value,str):return None
    match=_COUNTS.search(value)
    if not match:return None
    try:limit,inputs,reserved=(int(part.replace(',','')) for part in match.groups())
    except ValueError:return None
    if not 4096<=limit<=10_000_000 or not 0<reserved<limit or not 0<inputs<=10_000_000 or inputs+reserved<=limit:return None
    # Reserve room for the advertised output and a further 10% for the next
    # tool result. This changes when an observed provider limit changes.
    threshold=(limit-reserved)*9//10
    if threshold<4096:return None
    return {'provider_window':limit,'output_reservation':reserved,'auto_compact_tokens':threshold}


def for_thread(store,session,settings):
    policy=settings.get('agent_roles')
    models=set(policy.get('models',{}).values()) if isinstance(policy,dict) else set()
    if len(models)!=1 or session['channel']!=settings['channel'] or not session['thread_id']:return None
    model=next(iter(models))
    for event in store.rows("SELECT seq,payload FROM events WHERE session_id=? AND kind='turn/completed' ORDER BY seq DESC LIMIT 40",(session['id'],)):
        payload=json.loads(event['payload'])
        if not isinstance(payload,dict) or not isinstance(payload.get('turn'),dict):continue
        turn=payload['turn']
        if payload.get('threadId')!=session['thread_id'] or turn.get('status')!='failed':continue
        error=turn.get('error')
        observed=rejection(error.get('message')) if isinstance(error,dict) else None
        if not observed:continue
        route=store.one('''SELECT r.model,r.policy FROM research_role_routes r JOIN deliveries d ON d.id=r.delivery_id
            WHERE r.session_id=? AND d.turn_id=? AND d.state='accepted' ORDER BY d.rowid DESC LIMIT 1''',(session['id'],turn.get('id')))
        if not route or route['model']!=model or json.loads(route['policy'])!=policy:continue
        return {**observed,'model':model,'source_event':event['seq'],
                'config':{'model_auto_compact_token_limit':observed['auto_compact_tokens']}}
    return None
