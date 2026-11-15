"""Named identities on native tool receipts, derived only from saved records."""
import json


def receipt(record):
    result={**record,'record_id':record['id']}
    source=json.loads(record['source']) if isinstance(record.get('source'),str) else record.get('source',{})
    if record['kind']=='plan':
        from .research import plan_digest
        result.update(plan_id=record['id'],plan_digest=plan_digest(record),
            identity_note='plan_id即本记录id；Critic发布编号后，请重新读取research_snapshot.plan_digest，不手工重算。')
    elif record['kind']=='trial':
        result.update(trial_id=record['id'],plan_id=source.get('plan_id'),
            identity_note='trial_id与record_id/id是同一个已保存身份，没有另一个实验ID。后续准备直接使用本次返回的trial_id与plan_id；不要猜测或重复登记。')
    elif source.get('plan_id'):
        result['plan_id']=source['plan_id']
    return result
