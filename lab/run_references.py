"""Resolve saved execution identities in one place; never create or infer runs."""


def normalize(store,project_id,record,source):
    if record.get('kind') not in {'result','decision','review','delivery'}:return record,source
    declared=record.get('run_ids',[]);cloud=record.get('cloud_run_ids',[])
    limit=100 if record['kind']=='review' else 20
    if (not isinstance(declared,list) or len(declared)>limit or any(not isinstance(v,str) for v in declared)
            or not isinstance(cloud,list) or len(cloud)>100 or any(not isinstance(v,str) for v in cloud)):
        raise ValueError('运行引用必须是有界的明确ID列表')
    has_cloud=bool(store.one("SELECT name FROM sqlite_master WHERE name='company_training'"))
    has_local=bool(store.one("SELECT name FROM sqlite_master WHERE name='research_runs'"))
    local=[];resolved=[];receipts=[]
    for field,refs in [('run_ids',declared),('cloud_run_ids',cloud)]:
        for ref in refs:
            local_run=store.one('SELECT id FROM research_runs WHERE project_id=? AND id=?',(project_id,ref)) if field=='run_ids' and has_local else None
            matches=store.rows("SELECT id FROM company_training WHERE project_id=? AND state<>'prepared' AND (id=? OR task_uid=?)",(project_id,ref,ref)) if has_cloud else []
            if len(matches)>1 or local_run and matches:raise ValueError('运行身份有歧义，请使用明确的本地run_id或云端submission_id')
            if local_run:local.append(ref)
            elif len(matches)==1:
                id=matches[0]['id'];resolved.append(id)
                if field!='cloud_run_ids' or ref!=id:receipts.append({'field':field,'declared_id':ref,'kind':'cloud','submission_id':id})
            else:raise ValueError('关联运行必须是本项目已存在的本地run_id、已提交云端submission_id或其唯一task_uid；准备草稿和其他项目不算实际运行')
    if not receipts:return record,source
    record={**record,'run_ids':list(dict.fromkeys(local)),'cloud_run_ids':list(dict.fromkeys(resolved))}
    source={**source,'run_reference_resolution':receipts}
    return record,source
