"""Plan rounds close through explicit review; experiments remain autonomous inside one plan."""
import json


CLOUD_TERMINAL={'succeeded','completed','success','failed','error','stopped','terminated','cancelled','canceled'}


def _source(record):
    return json.loads(record['source'])


def pending_cloud(store,project_id):
    """Fail closed on every nonterminal platform phase, not just known queue labels."""
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):return None
    return store.one("""SELECT id,state,plan_id FROM company_training WHERE project_id=?
        AND lower(state) NOT IN ('prepared','succeeded','completed','success','failed','error',
                                 'stopped','terminated','cancelled','canceled') ORDER BY rowid LIMIT 1""",(project_id,))


def pending_component(store,project_id):
    if store.one("SELECT name FROM sqlite_master WHERE name='research_component_provisions'"):
        return store.one('''SELECT p.grant_id AS id,p.state FROM research_component_provisions p
            JOIN research_grants g ON g.id=p.grant_id WHERE g.project_id=? AND p.state IN ('requested','unknown','verifying') LIMIT 1''',(project_id,))


def pending_platform_operation(store,project_id,*,executing_submit=None):
    """Resolve exact submit receipts; pending_cloud independently blocks new work."""
    component=pending_component(store,project_id)
    if component:return component
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_operations'"):return None
    operations=store.rows('''SELECT o.*,s.project_id FROM company_operations o JOIN sessions s ON s.id=o.session_id
        WHERE s.project_id=? AND o.state IN ('requested','unknown','submitted','pending_approval') ORDER BY o.rowid''',(project_id,))
    for operation in operations:
        if executing_submit and operation['state']=='requested' and (operation['id'],operation['session_id'])==executing_submit[:2]:
            try:request=json.loads(operation['request'])
            except (ValueError,TypeError):request=None
            if isinstance(request,dict) and request.get('action')=='lpai_submit' and request.get('submission_id')==executing_submit[2]:continue
        if operation['state'] in {'submitted','unknown','requested'} and _confirmed_submission_matches(store,operation):continue
        return operation
    return None


def _confirmed_submission_matches(store,operation):
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_training_identity'"):return False
    try:
        request=json.loads(operation['request']);receipt=json.loads(operation['result'] or '{}')
        if request.get('action')!='lpai_submit':return False
        run=store.one('''SELECT * FROM company_training WHERE id=? AND session_id=? AND project_id=?''',
            (request.get('submission_id'),operation['session_id'],operation['project_id']))
        if not run or run['state'].lower() not in CLOUD_TERMINAL|{'submitted'}:return False
        identity=store.one('SELECT * FROM company_training_identity WHERE submission_id=?',(run['id'],))
        task_uid=run['task_uid']
        locked=json.loads(identity['receipt']) if identity and identity['receipt'] else {}
        if operation['state']!='submitted' or receipt.get('state')!='submitted':
            if not store.one("SELECT name FROM sqlite_master WHERE name='company_operation_reconciliations'"):return False
            link=store.one('SELECT * FROM company_operation_reconciliations WHERE operation_id=?',(operation['id'],))
            from .lpai_training import sha
            if not link or link['submission_id']!=run['id'] or link['task_uid']!=task_uid or link['receipt_sha256']!=sha(locked):return False
            receipt=locked
        return bool(task_uid and identity and identity['requested_at'] and identity['original_task_uid']==task_uid
                    and locked.get('state')=='submitted' and (locked.get('item') or {}).get('uid')==task_uid
                    and (receipt.get('item') or {}).get('uid')==task_uid)
    except (ValueError,TypeError,AttributeError,KeyError):return False


def approved_plan(store,project_id):
    from .autonomy import active
    branch=active(store,project_id)
    if branch:return store.one('SELECT * FROM records WHERE id=?',(branch['head_plan_id'],))
    # Lineage, not an execution grant. Execution still checks the latest review
    # of this exact plan; a later rejection is never treated as permission.
    return store.one("SELECT r.* FROM research_reviews v JOIN records r ON r.id=v.plan_id WHERE v.project_id=? AND v.decision='accept' ORDER BY v.rowid DESC LIMIT 1",(project_id,))


def round_review(store,project_id,plan_id):
    for record in store.rows("SELECT * FROM records WHERE project_id=? AND kind='review' ORDER BY rowid DESC",(project_id,)):
        if _source(record).get('reviewed_plan_id')==plan_id:
            return record


def fresh_scope_request(store, session_id, review, *, include_queued=None):
    """A later human input permits deliberation, never reuse of a closed grant."""
    session=store.session(session_id)
    if session['archived']:return None
    control=store.one('SELECT paused FROM research_controls WHERE session_id=?',(session_id,))
    if control and control['paused']:return None
    request=store.one('''SELECT d.id,d.created_at FROM deliveries d
        JOIN research_input_origins o ON o.delivery_id=d.id
        WHERE d.session_id=? AND (d.state='accepted' OR (d.id=? AND d.state='queued')) AND o.origin IN ('user','review')
        AND d.created_at>? ORDER BY d.rowid DESC LIMIT 1''',(session_id,include_queued,review['created_at']))
    if not request:return None
    stop=store.one('SELECT stopped_at FROM research_user_stops WHERE session_id=?',(session_id,))
    if stop and stop['stopped_at']>=request['created_at']:return None
    return request


def successors(store,project_id,plan_id,review_id=None):
    return [record for record in store.rows("SELECT * FROM records WHERE project_id=? AND kind='plan' ORDER BY rowid DESC",(project_id,))
            if _source(record).get('previous_plan_id')==plan_id and
            (not review_id or _source(record).get('review_record_id')==review_id)]


def verify_target_achieved(store,project_id,plan_id,comparison_ids,require_numeric=False):
    """A frozen numeric target is distinct from a model's qualitative assessment."""
    if store.evidence.blocking(project_id,verify_ready=True):
        raise ValueError('原运行证据存在缺口，不能据此声明目标达成')
    plan=store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='plan'",(plan_id,project_id))
    if not plan:raise ValueError('目标核验必须对应当前项目的确切方案')
    from .research import digest
    for ref in json.loads(plan['evidence']):
        try:
            path=store.evidence_path(project_id,ref['path'])
            if not path.is_file() or digest(path)!=ref['sha256']:raise ValueError('目标依据的冻结证据已改变')
        except (ValueError,OSError) as exc:
            raise ValueError('目标依据的方案证据未通过核对，不能声明达标') from exc
    configured=json.loads(plan['source']).get('evaluation_contract')
    if not configured:
        if require_numeric:raise ValueError('尚无本方案事前数值合同，不能把受限收尾改成程序认证达标')
        return False
    if not isinstance(comparison_ids,list) or len(comparison_ids)>8 or any(not isinstance(id,str) for id in comparison_ids):
        raise ValueError('目标核验需要有界的确切报告ID')
    from .comparison import read_report
    reports=[read_report(store,project_id,id) for id in comparison_ids]
    if not any(r['plan_id']==plan_id and r['state']=='verified' and r['rules_passed'] and r['targets_met'] is True and r['prospective'] for r in reports):
        raise ValueError('尚无按本方案事先固定条件通过的目标核验；不能把涨分、事后复算或证据不足写成达标')
    return True


def prepare_record(store,session,record,source,run_ids):
    """Validate declared links; don't infer scientific causation from chronology."""
    source=dict(source);pid=session['project_id'];kind=record['kind']
    previous_id=record.get('previous_plan_id');review_id=record.get('review_record_id')
    if (previous_id or review_id) and kind!='plan':
        raise ValueError('前轮方案/评审引用只能用于下一轮plan')
    if kind=='review':
        prior=round_review(store,pid,record.get('plan_id'))
        prior_closure=closure_details(store,prior) if prior else None
        if prior_closure and not prior_closure['requires_human_confirmation'] and record.get('round_outcome','continue')=='continue':
            raise ValueError('原轮次已自主收束，普通讨论不能重开旧方案；新的执行须显式审核新研究范围')
        gap=store.evidence.blocking(pid,verify_ready=True)
        from .autonomy import finalizing
        if record.get('round_outcome','continue')=='continue' and finalizing(store,pid):
            raise ValueError('原方案已进入只读收尾，不能用继续评审重开自动实验链')
        if record.get('round_outcome','continue')=='continue' and gap:
            raise ValueError('原运行证据尚未回收/校验，不能用继续评审掩盖；可明确记录证据受限的结束建议')
        plan_id=record.get('plan_id')
        plan=store.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind='plan'",(plan_id,pid))
        from .autonomy import admitted
        if not plan or not (admitted(store,plan_id) or store.one("SELECT id FROM research_reviews WHERE plan_id=? AND decision='accept' LIMIT 1",(plan_id,))):
            raise ValueError('轮次评审必须引用当前项目曾正式获批的具体方案。尚未首次批准的草案不能做实验后轮次评审；初始准备反馈用result/research/decision保存，补齐真实证据的初始候选后交Critic，不虚构previous_plan_id或已执行前轮。')
        current=approved_plan(store,pid)
        if not current or current['id']!=plan_id:
            raise ValueError('收尾评审必须针对当前方案轮次；历史复盘用research/decision记录')
        active=store.one("SELECT id FROM research_runs WHERE plan_id=? AND state IN ('requested','running','unknown') LIMIT 1",(plan_id,))
        if active:
            raise ValueError('该轮仍有运行中/未知任务，先处置原身份，不能用评审掩盖')
        runs=store.rows('SELECT id,state FROM research_runs WHERE plan_id=? ORDER BY rowid',(plan_id,))
        cloud=store.rows("SELECT id,state FROM company_training WHERE plan_id=? AND state<>'prepared' ORDER BY rowid",(plan_id,)) if store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else []
        if any(r['state'].lower() not in CLOUD_TERMINAL for r in cloud):raise ValueError('该轮有在途/未知云任务，不能用评审掩盖')
        claimed=source.get('cloud_run_ids',[])
        if cloud and not claimed:raise ValueError('轮次评审需明确引用实际云端运行')
        if any(id not in {r['id'] for r in cloud} for id in claimed):raise ValueError('评审的云端运行不属于当前具体方案')
        if runs and not run_ids:
            raise ValueError('轮次评审需明确引用实际分析过的运行，不只写一段无依据总结')
        if any(id not in {r['id'] for r in runs} for id in run_ids):
            raise ValueError('轮次评审的运行证据必须属于被评方案')
        missing_local=[r['id'] for r in runs if r['id'] not in run_ids]
        missing_cloud=[r['id'] for r in cloud if r['id'] not in claimed]
        if missing_local or missing_cloud:
            raise ValueError('轮次评审不能遗漏本方案实际尝试（包括失败、取消和负结果）；'
                             '先核对research_status.round_evidence。未引用：'+
                             json.dumps({'run_ids':missing_local,'cloud_run_ids':missing_cloud},ensure_ascii=False))
        outcome=record.get('round_outcome','continue')
        if outcome not in {'continue','stop_requested'}:
            raise ValueError('轮次结论只能建议继续或请求用户确认结束')
        stop_reason=record.get('stop_reason')
        if stop_reason is not None and (outcome!='stop_requested' or stop_reason not in {'target_achieved','budget','no_progress','blocked','user_stop'}):raise ValueError('结束原因与轮次决定不符')
        evaluated=store.one('SELECT source FROM records WHERE id=?',(plan_id,))
        configured=json.loads(evaluated['source']).get('evaluation_contract')
        from .autonomy import policy
        p=policy(store,pid)
        if outcome=='stop_requested' and not stop_reason and (configured or p and p['mode']=='autonomous'):
            raise ValueError('自主收束或数值评价合同的评审需明确结束原因：达标、预算、无进展、阻塞或用户停止')
        if gap and outcome=='stop_requested' and stop_reason not in {'budget','no_progress','blocked','user_stop'}:
            raise ValueError('原证据存在缺口，只能明确记录受限结束，不能声明目标达成')
        if stop_reason=='target_achieved':
            source['target_numeric_verified']=verify_target_achieved(store,pid,plan_id,record.get('comparison_ids',[]))
        if gap and outcome=='stop_requested':source['evidence_pending']=gap
        if stop_reason:source['stop_reason']=stop_reason
        if outcome=='stop_requested':
            source['closure_mode']='autonomous' if p and p['mode']=='autonomous' else 'manual'
        source.update(reviewed_plan_id=plan_id,round_outcome=outcome,
                      indexed_runs=runs,indexed_cloud_runs=cloud,claimed_evidence_run_ids=list(run_ids),
                      evidence_coverage={'version':1,'all_attempts_referenced':True,
                                         'meaning':'仅校验引用完整性，不认证已经读懂证据或科学结论正确'})
    if kind=='plan' and (previous_id or review_id):
        previous=store.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind='plan'",(previous_id,pid))
        if source.get('origin')=='human_intervention':
            if not previous or not source.get('intervention_reason'):raise ValueError('人工分叉需有确切原方案及改动理由')
            source.update(previous_plan_id=previous_id,fork_from_plan_id=previous_id)
            return source
        review=store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='review'",(review_id,pid))
        latest=round_review(store,pid,previous_id)
        current=approved_plan(store,pid)
        current_review=round_review(store,pid,current['id']) if current else None
        hint=(f"；当前previous_plan_id={current['id']}，review_record_id={current_review['id'] if current_review else '尚无轮次评审'}。"
              '未采纳候选不是已执行前轮；Critic的decision不是轮次review。保留原候选，修订仍承接同一已执行轮次。') if current else ''
        if not current or current['id']!=previous_id:
            raise ValueError('下一方案必须承接当前获批轮次，不从旧分支暗中跳过当前轮次'+hint)
        if not previous or not review or _source(review).get('reviewed_plan_id')!=previous_id:
            raise ValueError('下一轮方案需引用匹配的前轮方案及明确轮次评审'+hint)
        from .recovery import candidate_link
        new_scope=bool(source.get('scope_proposal') and latest and latest['id']==review_id
                       and fresh_scope_request(store,session['id'],review))
        if not latest or latest['id']!=review_id or (_source(review).get('round_outcome')!='continue' and not candidate_link(store,pid,previous_id,review_id) and not new_scope):
            missing_scope=(latest and latest['id']==review_id and not source.get('scope_proposal')
                and fresh_scope_request(store,session['id'],review))
            if missing_scope:
                raise ValueError('原轮次已建议结束；当前已有新的非执行讨论输入，但这个候选缺少scope_proposal。'
                    '请保留当前previous_plan_id/review_record_id，明确登记待审核的新范围；有既有GPU候选时核对并沿用其实际范围，不新增执行授权。'
                    '组件库候选须在范围中明确component_config、compute和budget，并把实际配置/实现文件列入evidence；不能借旧授权、major编号或人工分支替代新范围。')
            raise ValueError('评审已更新或建议结束，请先核对最新评审再给下一方案')
        if new_scope:source['new_scope_request_id']=fresh_scope_request(store,session['id'],review)['id']
        source.update(previous_plan_id=previous_id,review_record_id=review_id)
        if source.get('origin')=='codex-native-tool':
            from .autonomy import policy
            p=policy(store,pid)
            if p and p['mode']=='autonomous' and p['owner_session_id']==session['id']:
                authority=store.one('SELECT scope_digest FROM research_authorities WHERE id=?',(p['authority_id'],))
                source.setdefault('authority_id',p['authority_id'])
                source.setdefault('scope_digest',authority['scope_digest'])
    return source


def check_approval(store,plan,*,new_scope_review=False):
    if pending_component(store,plan['project_id']):
        raise ValueError('原组件创建待核对，不能以新方案掩盖原请求')
    previous=approved_plan(store,plan['project_id'])
    if not previous or previous['id']==plan['id']:
        return
    source=_source(plan);review=round_review(store,plan['project_id'],previous['id'])
    if source.get('origin')=='human_intervention':return
    if not review or source.get('previous_plan_id')!=previous['id'] or source.get('review_record_id')!=review['id']:
        raise ValueError('开启下一方案轮次前，先保存本轮评审和有明确关系的下一方案，再由用户审核')
    if _source(review).get('round_outcome')!='continue':
        # This is the formal human/delegated initial review endpoint, not an
        # automatic adoption. A linked new scope can supersede a system stall
        # without rewriting its old terminal review or reviving its old grant.
        from .recovery import candidate_link
        request=fresh_scope_request(store,plan['session_id'],review) if source.get('scope_proposal') else None
        if (new_scope_review and request and source.get('new_scope_request_id')==request['id']
                and request['created_at']<=plan['created_at']):
            return # The formal new-scope review creates a new grant; no old authority is reopened.
        if new_scope_review and source.get('scope_proposal') and candidate_link(store,plan['project_id'],previous['id'],review['id']):
            return
        raise ValueError('本轮建议结束，先由用户处置，不自动开始新方案')


def check_trial_dependencies(store,project_id,plan_id,trial_id):
    """Validate declared execution prerequisites, not scientific causation."""
    if not trial_id:return []
    trial=store.one("SELECT source FROM records WHERE id=? AND project_id=? AND kind='trial'",(trial_id,project_id))
    if not trial:raise ValueError('实验必须属于当前项目')
    dependencies=json.loads(trial['source']).get('depends_on_trial_ids',[])
    if not isinstance(dependencies,list) or len(dependencies)>30 or any(not isinstance(id,str) for id in dependencies):
        raise ValueError('前置实验身份无效，未启动计算')
    verified=[]
    for id in dependencies:
        dependency=store.one("SELECT source FROM records WHERE id=? AND project_id=? AND kind='trial'",(id,project_id))
        if not dependency or id==trial_id or json.loads(dependency['source']).get('plan_id') not in {None,plan_id}:
            raise ValueError('前置实验不属于本方案，不能用图谱顺序代替执行事实')
        local=store.rows('''SELECT r.id,r.state,r.plan_id FROM research_runs r JOIN research_run_context c ON c.run_id=r.id
            WHERE r.project_id=? AND c.trial_id=? ORDER BY r.rowid''',(project_id,id))
        cloud=store.rows("SELECT id,state,plan_id FROM company_training WHERE project_id=? AND trial_id=? AND state<>'prepared' ORDER BY rowid",(project_id,id)) if store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else []
        if any(r['plan_id']!=plan_id or r['state'] in {'requested','running','unknown'} for r in local) or any(r['plan_id']!=plan_id or r['state'].lower() not in CLOUD_TERMINAL for r in cloud):
            raise ValueError('前置实验仍在途、未知或属于其他方案，未开始后继计算')
        # A platform Succeeded alone is insufficient: verify original run
        # identity and the actual recovered bytes before enabling a successor.
        usable=None
        for run in reversed(local):
            if run['state']!='completed':continue
            summary=store.evidence.summary('local',run['id'])
            if summary and summary['analysis_ready'] and summary['analysis_scope']=='result_analysis':
                store.evidence.verify('local',run['id']);usable={'run_id':run['id']};break
        if not usable:
            from .cloud_results import read
            for run in reversed(cloud):
                if run['state'].lower() not in {'succeeded','success','completed'}:continue
                try:
                    original,_,_,_,archive=read(store,project_id,run['id'])
                except (ValueError,OSError,KeyError,TypeError):
                    continue  # Missing, diagnostic-only, and corrupted are never readiness.
                usable={'run_id':original['id'],'location':'cloud','task_uid':original['task_uid'],
                        'manifest_sha256':archive['manifest_sha256']}
                break
        if not usable:raise ValueError('前置实验尚无已完成且校验的结果；平台成功或失败取证不能冒充准备完成')
        verified.append({'trial_id':id,**usable})
    return verified


def closure_details(store,review):
    """Pinned closure provenance for both current and historical review views."""
    source=_source(review)
    if source.get('round_outcome')!='stop_requested':return None
    from .autonomy import policy
    p=policy(store,review['project_id'])
    autonomous=source.get('closure_mode')=='autonomous' or 'closure_mode' not in source and bool(p and p['mode']=='autonomous')
    return {'stop_reason':source.get('stop_reason','legacy_unspecified'),
            'target_verified':source.get('target_numeric_verified') is True,
            'closure_source':'autonomous_assessment' if autonomous else 'human_review_required',
            'requires_human_confirmation':not autonomous}


def state(store,project_id):
    current=approved_plan(store,project_id)
    if not current:return None
    review=round_review(store,project_id,current['id'])
    base={'plan_id':current['id'],'plan_version':current['version']}
    if not review:return {**base,'state':'active'}
    base.update(review_record_id=review['id'])
    if _source(review).get('round_outcome')=='stop_requested':
        details=closure_details(store,review)
        if not details['requires_human_confirmation']:
            return {**base,'state':'assessment_complete',**details}
        decision=store.one('SELECT decision FROM research_round_decisions WHERE review_record_id=? ORDER BY rowid DESC LIMIT 1',(review['id'],))
        return {**base,**details,'state':'settled' if decision and decision['decision']=='accept_stop' else 'stop_rejected' if decision else 'waiting_stop_review'}
    proposals=successors(store,project_id,current['id'],review['id'])
    if not proposals:return {**base,'state':'awaiting_next_plan'}
    candidate=proposals[0]
    from .autonomy import policy
    p=policy(store,project_id)
    decision=store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(candidate['id'],))
    return {**base,'next_plan_id':candidate['id'],'next_plan_version':candidate['version'],
            'state':'next_plan_rejected' if decision and decision['decision']=='reject' else 'waiting_plan_adoption' if p and p['mode']=='autonomous' else 'waiting_plan_review'}
