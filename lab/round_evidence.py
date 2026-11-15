"""A paged, deterministic evidence index; never infers scientific outcomes."""
import json
import hashlib
from collections import Counter
from . import rounds


def observations(store, project_id):
    """Persisted status/content identities only; no file reads or platform calls."""
    facts={}
    for row in store.rows('''SELECT r.id,r.state,e.state AS collection_state,e.analysis_scope,e.analysis_ready,e.manifest_digest
        FROM research_runs r LEFT JOIN run_evidence e ON e.run_id=r.id AND e.location='local'
        WHERE r.project_id=?''',(project_id,)):
        facts['local:'+row.pop('id')]=row
    if store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
        jobs={}
        if store.one("SELECT name FROM sqlite_master WHERE name='company_output_jobs'"):
            jobs={(r['session_id'],r['task_uid']):r for r in store.rows('''SELECT j.* FROM company_output_jobs j
                JOIN sessions s ON s.id=j.session_id WHERE s.project_id=?''',(project_id,))}
        for row in store.rows("SELECT id,state,session_id,task_uid FROM company_training WHERE project_id=? AND state<>'prepared'",(project_id,)):
            job=jobs.get((row['session_id'],row['task_uid']),{});outcome=json.loads(job.get('outcome') or '{}')
            facts['cloud:'+row['id']]={'state':row['state'],'collection_state':job.get('state'),
                'analysis_scope':outcome.get('analysis_scope'),'manifest_digest':outcome.get('manifest_sha256'),
                'analysis_ready':job.get('state')=='ready' and outcome.get('result_check',{}).get('identity_verified') is True}
    return {key:{**value,'digest':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()} for key,value in facts.items()}


def analysis_action(fact, prior=None):
    state=str(fact.get('state','unknown')).lower()
    if state=='unknown':return 'reconcile'
    if state in {'requested','running','submitted','pending','queued','preparing'}:return 'waiting'
    unchanged=prior and prior.get('digest')==fact.get('digest')
    if fact.get('analysis_scope')=='failure_diagnosis':return 'recorded_unchanged' if unchanged else 'failure_diagnosis'
    if state in {'failed','error','timed_out','invalid_inputs','launch_failed'}:
        return ('recorded_unchanged' if unchanged else 'failure_diagnosis') if fact.get('collection_state') in {'diagnostic_only','ready'} else 'needs_evidence'
    if fact.get('analysis_ready') and fact.get('analysis_scope')=='result_analysis':return 'recorded_unchanged' if unchanged else 'result_analysis'
    if state in {'cancelled','canceled','stopped','terminated'}:return 'stopped_without_result'
    return 'needs_evidence'


def packet(store, project_id, offset=0, page_size=12, selection='all'):
    if type(offset) is not int or offset < 0:
        raise ValueError('证据页偏移应为非负整数')
    if selection not in {'all','actionable'}:raise ValueError('未知证据选择范围')
    plan=rounds.approved_plan(store,project_id)
    if not plan:return None
    local=store.rows('SELECT r.id,r.state,c.trial_id FROM research_runs r LEFT JOIN research_run_context c ON c.run_id=r.id WHERE r.plan_id=? ORDER BY r.rowid',(plan['id'],))
    cloud=store.rows("SELECT id,trial_id,task_uid,state,session_id FROM company_training WHERE plan_id=? AND state<>'prepared' ORDER BY rowid",(plan['id'],)) if store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else []
    # Stable local/cloud partition; no timestamp sorting that can reshuffle old pages.
    index=[('local',r) for r in local]+[('cloud',r) for r in cloud]
    facts=observations(store,project_id);prior={}
    for record in store.rows("SELECT source FROM records WHERE project_id=? AND json_extract(source,'$.evidence_observations') IS NOT NULL ORDER BY rowid",(project_id,)):
        prior.update(json.loads(record['source'])['evidence_observations'])
    actions={location+':'+r['id']:analysis_action(facts.get(location+':'+r['id'],{}),prior.get(location+':'+r['id'])) for location,r in index}
    selected=[(location,r) for location,r in index if selection=='all' or actions[location+':'+r['id']] not in {'waiting','recorded_unchanged','stopped_without_result'}]
    cloud_assessments={}
    if cloud:
        for record in store.rows("SELECT id,title,kind,source FROM records WHERE project_id=? AND kind IN ('result','decision','review','delivery') ORDER BY rowid",(project_id,)):
            for run_id in json.loads(record['source']).get('cloud_run_ids',[]):
                cloud_assessments.setdefault(run_id,[]).append({key:record[key] for key in ('id','title','kind')})
    entries=[]
    for location,row in selected[offset:offset+page_size]:
        entry={'run_id':row['id'],'location':location,'state':row['state'],'trial_id':row['trial_id']}
        entry['analysis_action']=actions[location+':'+row['id']]
        entry['collection']=store.evidence.summary(location,row['id'])
        if location=='local':
            row={**row,**store.one('SELECT request,receipt,directory FROM research_runs WHERE id=?',(row['id'],))}
            request=json.loads(row['request']);receipt=json.loads(row['receipt']) if row['receipt'] else {}
            entry.update(purpose=request.get('purpose'),question=request.get('question','')[:600],
                         expected_information=request.get('expected_information','')[:600],
                         directory=row['directory'],receipt_path=row['directory']+'/receipt.json' if row['receipt'] else None,
                         outputs=receipt.get('outputs',[]),missing_outputs=receipt.get('missing_outputs',[]),
                         exit_code=receipt.get('exit_code'),seconds=receipt.get('seconds'))
            entry['result_records']=store.rows("SELECT r.id,r.title,r.kind FROM research_record_runs l JOIN records r ON r.id=l.record_id WHERE l.run_id=? ORDER BY r.rowid",(row['id'],))
            if row['trial_id']:
                trial=store.one('SELECT source FROM records WHERE id=?',(row['trial_id'],))
                basis=json.loads(trial['source']).get('basis_record_id') if trial else None
                if basis:entry['basis']=store.one('SELECT id,title,kind FROM records WHERE id=?',(basis,))
        else:
            from .task_outputs import status as output_status
            entry['task_uid']=row['task_uid']
            archive=output_status(store,row['session_id'],row['task_uid']) if row['task_uid'] else None
            if archive:
                entry['collection']={k:archive[k] for k in ['state','manifest_path','manifest_sha256','report_path','analysis_scope','result_check'] if k in archive}
            # Use exact persisted run links, never titles or textual UID mentions.
            # A record link exposes prior analysis; it does not certify its claims.
            entry['result_records']=cloud_assessments.get(row['id'],[])
            entry['assessment_pending']=not bool(entry['result_records'])
            if row['trial_id']:
                trial=store.one('SELECT source FROM records WHERE id=? AND project_id=?',(row['trial_id'],project_id))
                basis=json.loads(trial['source']).get('basis_record_id') if trial else None
                if basis:entry['basis']=store.one('SELECT id,title,kind FROM records WHERE id=? AND project_id=?',(basis,project_id))
            entry['detail']='按此确切云任务读取已回收产物/诊断；平台终态不代表研究目标达成'
        entries.append(entry)
    source=json.loads(plan['source'])
    return {'plan':{'id':plan['id'],'title':plan['title'],'version':plan['version'],'evaluation_contract':source.get('evaluation_contract'),
                    'previous_plan_id':source.get('previous_plan_id'),'trigger_record_ids':source.get('trigger_record_ids',[])},
            'attempt_count':len(index),'states':dict(Counter(row['state'] for _,row in index)),
            'all_run_ids':[r['id'] for r in local],'all_cloud_run_ids':[r['id'] for r in cloud],
            'selection':selection,'selected_count':len(selected),'analysis_states':dict(Counter(actions.values())),
            'omitted_count':len(index)-len(selected),
            'offset':offset,'next_offset':offset+page_size if offset+page_size<len(selected) else None,'attempts':entries,
            'meaning':'本轮完整尝试索引，包括失败/取消；未读取产物，未判定比较公平性或科学结论。对照可来自旧方案，需按确切记录另行核对。'}
