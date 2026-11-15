"""Keep initial human confirmation ahead of optional inquiry scheduling.

This is not an execution grant, a review result, or a way past unknown writes.
The primary role chain and every approval/submission guard remain unchanged.
"""
import json


def initial_confirmation(store,sid,settings=None):
    pid=store.session(sid)['project_id']
    if store.one('SELECT id FROM research_grants WHERE project_id=? LIMIT 1',(pid,)):return None
    plan=store.one("SELECT * FROM records WHERE project_id=? AND kind='plan' ORDER BY rowid DESC LIMIT 1",(pid,))
    if not plan or not plan['version'] or not isinstance(json.loads(plan['source']).get('scope_proposal'),dict):return None
    from .research import plan_digest
    exact=plan_digest(plan)
    proof=store.one('''SELECT h.record_id,h.created_at,r.model FROM research_role_handoffs h
        JOIN research_role_routes r ON r.delivery_id=h.source_delivery
        WHERE h.session_id=? AND h.plan_id=? AND h.plan_digest=? AND h.verdict='approve'
        AND h.target='planner' AND r.role='critic' ORDER BY h.rowid DESC LIMIT 1''',(sid,plan['id'],exact))
    if not proof:return None
    critic=((settings or {}).get('agent_roles') or {}).get('models',{}).get('critic')
    if critic and proof['model']!=critic:return None
    latest=store.one('SELECT decision,created_at FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan['id'],))
    if latest and latest['created_at']>=proof['created_at']:return None
    return {'state':'awaiting_initial_confirmation','plan_id':plan['id'],'plan_digest':exact,
            'critic_record_id':proof['record_id'],'execution_authorized':False}
