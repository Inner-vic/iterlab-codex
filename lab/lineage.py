"""Versioned plan families and read-only research graph projections.

Historical record parents remain an audit trail, not scientific membership.
Migration is additive: it never rewrites a record, approval, grant or run.
"""
import json
import re

from .store import dumps, now

SCHEMA = 'iterlab-plan-series/v1'


def install(store):
    store.db.executescript('''
    CREATE TABLE IF NOT EXISTS research_plan_lineage(
      plan_id TEXT PRIMARY KEY REFERENCES records(id),project_id TEXT NOT NULL REFERENCES projects(id),
      series_id TEXT NOT NULL,series_number INTEGER NOT NULL,minor_number INTEGER NOT NULL,
      previous_plan_id TEXT REFERENCES records(id),predecessor_series_id TEXT,
      revision_scope TEXT NOT NULL,reason TEXT NOT NULL,trigger_record_ids TEXT NOT NULL,
      basis TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(project_id,series_number,minor_number));
    INSERT OR IGNORE INTO schema_versions VALUES(8,datetime('now'));
    ''')
    # Only existing exact version labels establish a legacy family. Titles,
    # adjacency and model names never establish a scientific change of route.
    for plan in store.rows("SELECT * FROM records WHERE kind='plan' ORDER BY rowid"):
        if store.one('SELECT plan_id FROM research_plan_lineage WHERE plan_id=?', (plan['id'],)):
            continue
        match = re.fullmatch(r'([1-9]\d*)\.(\d+)(?:\.\d+)?', plan['version'] or '')
        if not match:
            continue  # An unrecognised legacy version stays explicitly unclassified.
        major, _ = map(int, match.groups())
        first = store.one('SELECT * FROM research_plan_lineage WHERE project_id=? AND series_number=? ORDER BY minor_number LIMIT 1', (plan['project_id'], major))
        minor = store.one('SELECT COALESCE(MAX(minor_number),-1)+1 AS n FROM research_plan_lineage WHERE project_id=? AND series_number=?',
                          (plan['project_id'], major))['n']
        source = json.loads(plan['source'])
        previous = source.get('previous_plan_id')
        predecessor = store.one('SELECT series_id FROM research_plan_lineage WHERE plan_id=?', (previous,)) if previous else None
        triggers = [source['review_record_id']] if source.get('review_record_id') else []
        store.db.execute('INSERT INTO research_plan_lineage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (plan['id'], plan['project_id'], first['series_id'] if first else plan['id'], major, minor, previous,
             predecessor['series_id'] if predecessor and not first else None,
             'incremental' if first else 'initial' if major == 1 else 'major',
             '历史确切版本归类；不据此声称修改有效，促成依据以原评审为准', dumps(triggers),
             'legacy_exact_version_and_explicit_review', now()))
    store.db.commit()


def allocate(store, project_id, record, source=None):
    """Called under the record transaction. Allocation does not approve a plan."""
    scope = record.get('revision_scope', 'incremental')
    if scope not in {'incremental', 'route', 'major'}:
        raise ValueError('方案修改范围须为同路径修订、路径切换或总体方案改动')
    previous_id = record.get('previous_plan_id')
    previous = store.one('SELECT * FROM research_plan_lineage WHERE plan_id=? AND project_id=?', (previous_id, project_id)) if previous_id else None
    latest = store.one('SELECT * FROM research_plan_lineage WHERE project_id=? ORDER BY series_number DESC,minor_number DESC LIMIT 1', (project_id,))
    if previous_id and not previous:
        raise ValueError('前轮方案的系列归属尚未确认，不能猜测版本编号')
    reason = str(record.get('revision_reason') or '').strip()
    triggers = list(record.get('trigger_record_ids') or [])
    review_id = record.get('review_record_id')
    if review_id and review_id not in triggers:
        triggers.append(review_id)
    if scope == 'route' and (not previous or not review_id or not reason):
        raise ValueError('切换路径须引用原方案及结束评审，并说明原路径为何截断')
    if scope == 'major':
        if not previous or (not review_id and (source or {}).get('origin')!='human_intervention') or not reason or not triggers:
            missing=[]
            if not previous:missing.append('previous_plan_id（本项目已归类的前轮方案）')
            if not review_id and (source or {}).get('origin')!='human_intervention':missing.append('review_record_id（对应轮次review）')
            if not reason:missing.append('revision_reason（独立字段，正文/summary中的理由不能代替）')
            if not triggers:missing.append('trigger_record_ids（促成证据；有效review也可作为依据）')
            raise ValueError('新方案系列须有前轮方案、轮次评审、实质改向理由与促成记录；当前缺少：'+
                             '、'.join(missing)+'。请只补齐这些确切字段后重试；不要用新增decision或research_fork替代校验。')
        major = (latest['series_number'] if latest else 0) + 1
        minor, series_id, predecessor = 0, None, previous['series_id']
    elif previous or latest:
        base = previous or latest
        major, series_id, predecessor = base['series_number'], base['series_id'], None
        minor = store.one('SELECT MAX(minor_number) AS n FROM research_plan_lineage WHERE project_id=? AND series_id=?', (project_id, series_id))['n'] + 1
    else:
        major, minor, series_id, predecessor = 1, 0, None, None
    version = f'{major}.{minor}'
    if latest and scope != 'major':
        versions = [tuple(map(int, row['version'].split('.'))) for row in store.rows(
            'SELECT r.version FROM records r JOIN research_plan_lineage l ON l.plan_id=r.id WHERE l.series_id=?',
            (series_id,)) if re.fullmatch(r'\d+\.\d+(?:\.\d+)?', row['version'] or '')]
        if scope == 'route':
            version = f'{major}.{max(v[1] for v in versions)+1}'
        else:
            base_version = store.one('SELECT version FROM records WHERE id=?', (base['plan_id'],))['version']
            route = int(base_version.split('.')[1])
            patch = max((v[2] for v in versions if v[1] == route and len(v) == 3), default=-1) + 1
            version = f'{major}.{route}.{patch}'
    return {'series_id': series_id, 'series_number': major, 'minor_number': minor,
            'previous_plan_id': previous_id, 'predecessor_series_id': predecessor,
            'revision_scope': 'initial' if not latest else scope, 'reason': reason,
            'trigger_record_ids': triggers, 'version': version}


def save(store, row, metadata):
    store.db.execute('INSERT INTO research_plan_lineage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (row['id'], row['project_id'], metadata['series_id'] or row['id'], metadata['series_number'], metadata['minor_number'],
         metadata['previous_plan_id'], metadata['predecessor_series_id'], metadata['revision_scope'], metadata['reason'],
         dumps(metadata['trigger_record_ids']), 'explicit_record_contract', row['created_at']))


def validate_links(store, project_id, record):
    if record['kind'] != 'plan' and any(field in record for field in ['revision_scope', 'revision_reason']):
        raise ValueError('方案修订范围与理由只用于plan')
    for field in ['trigger_record_ids', 'depends_on_trial_ids']:
        refs = record.get(field, [])
        if not isinstance(refs, list) or len(refs) > 30 or any(not isinstance(ref, str) for ref in refs):
            raise ValueError('研究关系需要最多30个明确的记录引用')
        if len(refs) != len(set(refs)):
            raise ValueError('研究关系不能重复')
        for ref in refs:
            target = store.one('SELECT kind FROM records WHERE id=? AND project_id=?', (ref, project_id))
            if not target or field == 'depends_on_trial_ids' and target['kind'] != 'trial':
                raise ValueError('研究关系必须引用本项目已有的确切记录；执行依赖须指向实验')
    if record.get('plan_id'):
        if not store.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind='plan'", (record['plan_id'], project_id)):
            raise ValueError('研究归属须引用本项目的具体方案')
    if record.get('depends_on_trial_ids') and record['kind'] != 'trial':
        raise ValueError('执行依赖只用于实验，促成修订请用trigger_record_ids')
    if record.get('plan_id') and record['kind'] == 'trial':
        for ref in record.get('depends_on_trial_ids', []):
            source = json.loads(store.one('SELECT source FROM records WHERE id=?', (ref,))['source'])
            if source.get('plan_id') and source['plan_id'] != record['plan_id']:
                raise ValueError('跨方案复用应引用原结果证据，不作为当前方案内的执行前置实验')


def cloud_attempts(store, project_id):
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
        return []
    attempts = store.rows("""SELECT id,trial_id,plan_id,session_id,task_uid,state,created_at,request
        FROM company_training WHERE project_id=? AND state<>'prepared' ORDER BY rowid""", (project_id,))
    for attempt in attempts:
        request = json.loads(attempt.pop('request'))
        attempt['predecessor_uid'] = request.get('predecessor_uid')
        attempt['change_summary'] = request.get('change_summary')
        attempt['validation'] = request.get('validation')
    return attempts


def candidate_links(project_id, records, metadata):
    """Keep unreleased proposals attached only to their declared exact plan.

    A candidate has its own record identity, but no published series/version.
    Its saved parent and plan_id may describe review context; neither is a
    substitute for the explicit previous_plan_id revision contract.
    """
    known = {record['id']: record for record in records}
    links = {}
    for record in records:
        source = json.loads(record['source'])
        if (record['kind'] != 'plan' or record['id'] in metadata or record['version']
                or source.get('release_candidate') is not True):
            continue
        previous = source.get('previous_plan_id')
        target = known.get(previous) if isinstance(previous, str) else None
        valid = (target is not None and target['kind'] == 'plan'
                 and target['project_id'] == project_id and previous != record['id'])
        links[record['id']] = {
            'plan_candidate': True,
            'previous_plan_id': previous if isinstance(previous, str) else None,
            'candidate_parent_id': previous if valid else None,
            'candidate_lineage_basis': 'explicit_previous_plan' if valid else 'unclassified_candidate',
            'candidate_revision_scope': source.get('revision_scope'),
            'candidate_notice': ('尚未发版的候选方案；前序引用不代表已审核或获准执行。' if valid else
                                 '候选前序尚未确认；未按标题、记录顺序或保存父节点归类。'),
        }
    # Invalid legacy references must not introduce a structural cycle. The
    # original declarations stay visible; no published lineage is rewritten.
    cyclic, checked = set(), set()
    for origin in links:
        path, positions = [], {}
        cursor = origin
        while cursor in links and cursor not in checked and cursor not in positions:
            positions[cursor] = len(path)
            path.append(cursor)
            cursor = links[cursor]['candidate_parent_id']
        if cursor in positions:
            cyclic.update(path[positions[cursor]:])
        checked.update(path)
    for rid in cyclic:
        links[rid].update(candidate_parent_id=None, candidate_lineage_basis='conflicting_candidate_cycle',
                          candidate_notice='候选前序存在循环，需核对原引用；未建立修订主线。')
    return links


def project(store, project_id, records):
    """Use explicit run/round links first; label weaker legacy parent inference."""
    by_id = {r['id']: r for r in records}
    attempts = cloud_attempts(store, project_id)
    from .execution_history import index as execution_index
    execution=execution_index(store,project_id,attempts)
    attempts_by_trial = {}
    for attempt in attempts:
        attempts_by_trial.setdefault(attempt['trial_id'], []).append(attempt)
    metadata = {r['plan_id']: r for r in store.rows('SELECT * FROM research_plan_lineage WHERE project_id=?', (project_id,))}
    candidates = candidate_links(project_id, records, metadata)
    runs = store.rows('SELECT r.id,r.plan_id,c.trial_id FROM research_runs r LEFT JOIN research_run_context c ON c.run_id=r.id WHERE r.project_id=?', (project_id,)) if store.one("SELECT name FROM sqlite_master WHERE name='research_run_context'") else []
    run_index = {r['id']: r for r in runs}
    trial_plans = {}
    for run in runs:
        if run['trial_id']:
            trial_plans.setdefault(run['trial_id'], set()).add(run['plan_id'])
    trials = {trial: next(iter(plans)) for trial, plans in trial_plans.items() if len(plans) == 1}
    record_runs = {}
    if store.one("SELECT name FROM sqlite_master WHERE name='research_record_runs'"):
        for link in store.rows('SELECT x.* FROM research_record_runs x JOIN records r ON r.id=x.record_id WHERE r.project_id=?', (project_id,)):
            if link['run_id'] in run_index:
                record_runs.setdefault(link['record_id'], []).append(run_index[link['run_id']])
    owners, bases = {}, {}
    for record in records:
        rid = record['id']; source = json.loads(record['source'])
        explicit = source.get('plan_id') or source.get('reviewed_plan_id') or trials.get(rid)
        linked = {r['plan_id'] for r in record_runs.get(rid, [])}
        if record['kind'] == 'plan':
            owners[rid], bases[rid] = rid, 'exact_plan'
        elif len(trial_plans.get(rid, [])) > 1 or (explicit and trials.get(rid) and explicit != trials[rid]):
            bases[rid] = 'conflicting_explicit_links'  # Never choose the latest run as truth.
        elif explicit in metadata or explicit in candidates:
            owners[rid], bases[rid] = explicit, 'exact_plan_or_run_link'
        elif len(linked) == 1:
            owners[rid], bases[rid] = next(iter(linked)), 'exact_result_run_link'
        elif len(linked) > 1:
            bases[rid] = 'multi_plan_evidence_no_declared_owner'
        else:
            ancestor = record.get('parent_id'); seen = {rid}
            while ancestor in by_id and ancestor not in seen:
                seen.add(ancestor)
                if by_id[ancestor]['kind'] == 'plan':
                    owners[rid], bases[rid] = ancestor, 'legacy_recorded_ancestor'
                    break
                ancestor = by_id[ancestor].get('parent_id')
    nodes = []
    from .autonomy import installed,active
    branches=store.rows('SELECT * FROM research_branches WHERE project_id=?',(project_id,)) if installed(store) else []
    active_branch=active(store,project_id)
    branch_members={row['plan_id']:row['branch_id'] for row in store.rows('SELECT a.plan_id,a.branch_id FROM research_plan_admissions a JOIN records r ON r.id=a.plan_id WHERE r.project_id=?',(project_id,))} if branches else {}
    for branch in branches:branch_members[branch['root_plan_id']]=branch['id']
    retry_parents = {}
    seen_tasks = {}
    record_order = {record['id']: i for i, record in enumerate(records)}
    for attempt in attempts:
        previous = seen_tasks.get((attempt['session_id'], attempt['predecessor_uid']))
        trial = attempt['trial_id']
        if previous:
            prior_trial = previous['trial_id']
            if (prior_trial in record_order and trial in record_order
                    and record_order[prior_trial] < record_order[trial]
                    and branch_members.get(previous['plan_id']) == branch_members.get(attempt['plan_id'])):
                retry_parents.setdefault(trial, set()).add(prior_trial)
        if attempt['task_uid']:
            seen_tasks[(attempt['session_id'], attempt['task_uid'])] = attempt
    for i, record in enumerate(records):
        owner = owners.get(record['id']); meta = metadata.get(owner, {})
        node = {'id': record['id'], 'kind': record['kind'], 'title': record['title'], 'summary': record['summary'],
                'status': record['current_status'] or record['status'], 'recorded_status': record['recorded_status'],
                'status_source': record['status_source'], 'version_label': record['version'], 'created_at': record['created_at'],
                'milestone': True, 'record_order': i, 'origin_session_id': record['session_id'],
                'ref': {'record_id': record['id']}, 'plan_id': owner, 'series_id': meta.get('series_id'),
                'membership_basis': bases.get(record['id'], 'unclassified'), 'recorded_parent_id': record['parent_id']}
        if owner in branch_members:
            node['branch_id']=branch_members[owner]
            node['active_branch']=bool(active_branch and active_branch['id']==node['branch_id'])
        if record['id'] in attempts_by_trial:
            count = len(attempts_by_trial[record['id']])
            node['summary'] = f'{count} 次组件库尝试 · ' + (node['summary'] or '')
        node['active_plan']=bool(active_branch and record['id']==active_branch['head_plan_id'])
        if record['status_source']=='program_checked_research_delegation':node['delegated_execution']=True
        source=json.loads(record['source'])
        if record['id'] in execution['details']:
            node['execution_detail_only']=True
            node['execution_trial_id']=execution['details'][record['id']]
            node['execution_detail_basis']='program_diagnostic' if source.get('origin')=='system-lpai-diagnostic' else 'explicit_failed_task_retry'
        if record['id'] in execution['groups']:
            group=execution['groups'][record['id']]
            node['execution_attempt_count']=len(group)
            node['summary']=f"{len(group)} 次运行尝试 · 修复与取证见运行详情 · "+(record['summary'] or '')
            node['execution_latest_state']=group[-1].get('state')
            if len(group)>1 and group[-1].get('state'):
                latest=by_id.get(group[-1]['trial_id'],{})
                node['status']=latest.get('current_status') or 'blocked'
                node['status_source']='explicit_retry_latest_attempt'
        if record['id'] in candidates:
            node.update(candidates[record['id']])
        if source.get('origin')=='human_intervention':
            node['intervention_origin']=source.get('intervention_source');node['fork_from_plan_id']=source.get('fork_from_plan_id')
            node['title']='人工探索 · '+record['title']
        if node['active_plan']:node['title']='当前 · '+node['title']
        if node['membership_basis'] in {'conflicting_explicit_links', 'multi_plan_evidence_no_declared_owner'}:
            node['membership_notice'] = ('引用跨方案证据且未声明本记录归属，请核对；未按保存先后自动归类'
                if node['membership_basis'] == 'multi_plan_evidence_no_declared_owner' else
                '归属存在矛盾，请核对原运行与方案；未按保存先后自动归类')
        if record['id'] in metadata:
            node['plan_lineage'] = {**metadata[record['id']], 'trigger_record_ids': json.loads(metadata[record['id']]['trigger_record_ids'])}
        nodes.append(node)
    edges, edge_keys = [], set()
    def edge(source, target, kind, primary=True, basis='explicit_record_contract'):
        if source == target or not source or not target or (source, target, kind) in edge_keys:
            return
        edge_keys.add((source, target, kind))
        edges.append({'id': f'{kind}:{source}:{target}', 'source': source, 'target': target, 'kind': kind, 'primary': primary, 'basis': basis})
    families = {}
    for meta in metadata.values():
        families.setdefault(meta['series_id'], []).append(meta)
    for family_id, members in families.items():
        members.sort(key=lambda m: m['minor_number']); first = members[0]; initial = by_id[family_id]
        header_id = 'series:' + family_id
        nodes.append({'id': header_id, 'kind': 'series', 'title': f"方案系列 {first['series_number']}",
                      'summary': first['reason'] if first['revision_scope'] == 'major' else initial['title'],
                      'status': 'recorded', 'milestone': True, 'series_id': family_id, 'series_number': first['series_number'],
                      'origin_session_id': initial['session_id'], 'ref': {'series_id': family_id}})
        predecessor = first['predecessor_series_id']
        source = json.loads(initial['source'])
        if predecessor:
            edge(source.get('review_record_id') or 'series:' + predecessor, header_id, 'series_progression', basis='explicit_major_revision')
        else:
            # Initial context is a real pre-plan ancestor, never the last saved event.
            context = initial['parent_id']
            if context and context not in owners:
                edge(context, header_id, 'organizes', basis='legacy_initial_context')
        for meta in members:
            version = by_id[meta['plan_id']]['version']
            patch_parent = meta['previous_plan_id'] if version.count('.') == 2 else None
            edge(header_id, meta['plan_id'], 'contains_plan', not patch_parent, 'exact_series_version')
            if meta['previous_plan_id']:
                edge(meta['previous_plan_id'], meta['plan_id'], 'revises', bool(patch_parent), 'explicit_previous_plan')
            for ref in json.loads(meta['trigger_record_ids']):
                edge(ref, meta['plan_id'], 'motivates', False, 'explicit_revision_evidence')
    for record in records:
        rid = record['id']; owner = owners.get(rid); source = json.loads(record['source'])
        if record['kind'] == 'plan':
            if rid in metadata:
                continue
            candidate = candidates.get(rid)
            if candidate:
                edge(candidate['candidate_parent_id'], rid, 'candidate_revision',
                     basis='explicit_candidate_previous_plan')
                review = source.get('review_record_id')
                if isinstance(review, str) and by_id.get(review, {}).get('kind') == 'review':
                    edge(review, rid, 'motivates', False, 'explicit_candidate_review_record')
            # A plan owns itself; that ownership must never replace its saved
            # context parent with a self-edge. Unclassified plans stay explicit.
            parent = None
        else:
            parent = record['parent_id']
        if owner and record['kind'] != 'plan':
            if record['kind'] in {'trial', 'review'}:
                parent = owner
            elif record['kind'] == 'result':
                linked_trials = {r['trial_id'] for r in record_runs.get(rid, []) if r['trial_id']}
                if len(linked_trials) == 1:
                    parent = next(iter(linked_trials))
                elif parent not in by_id or owners.get(parent) != owner:
                    parent = owner
            elif parent not in by_id or owners.get(parent) != owner:
                parent = owner
            retries = retry_parents.get(rid, set())
            if record['kind'] == 'trial' and len(retries) == 1:
                edge(next(iter(retries)), rid, 'retries', basis='explicit_predecessor_uid')
                edge(parent, rid, 'executes', False, bases[rid])
            else:
                edge(parent, rid, 'assesses' if record['kind'] == 'review' else 'executes' if record['kind'] == 'trial' else 'produces' if record['kind'] == 'result' else 'organizes', basis=bases[rid])
        elif parent in by_id:
            edge(parent, rid, 'recorded_after', False, 'legacy_order_only')
        for ref in source.get('trigger_record_ids', []):
            edge(ref, rid, 'motivates', False)
        for ref in source.get('depends_on_trial_ids', []):
            edge(ref, rid, 'depends_on', False)
        for run in record_runs.get(rid, []):
            if run['trial_id']:
                edge(run['trial_id'], rid, 'informed_by' if record['kind'] != 'review' else 'assessed_in', False, 'exact_cited_run')
    # Frozen input manifests establish data dependencies, not scientific causality.
    full_runs = store.rows('SELECT id,request,receipt FROM research_runs WHERE project_id=?', (project_id,)) if runs else []
    output_owners = {}
    for run in full_runs:
        for out in json.loads(run['receipt'] or '{}').get('outputs', []):
            # Old receipts may predate explicit trial binding. Their exact
            # plan is still known; never invent a trial from its title.
            original = run_index[run['id']]
            output_owners[out['path']] = (original['trial_id'] or original['plan_id'], out['sha256'])
    for run in full_runs:
        target = run_index[run['id']]['trial_id']
        if target:
            inputs = json.loads(run['receipt'] or '{}').get('inputs', {})
            for path in json.loads(run['request']).get('files', []):
                original = output_owners.get(path)
                if original and inputs.get(path, {}).get('sha256') == original[1]:
                    edge(original[0], target, 'uses', False, 'frozen_run_input_output_path_and_hash')
    known = {n['id'] for n in nodes}
    for branch in branches:
        if branch['fork_plan_id']:edge(branch['fork_plan_id'],branch['root_plan_id'],'branches',True,'explicit_human_intervention')
    edges = [e for e in edges if e['source'] in known and e['target'] in known]
    return {'project_id': project_id, 'nodes': nodes, 'edges': edges,'active_branch_id':active_branch['id'] if active_branch else None,
            'exploration_branches':branches,
            'plan_series': {'schema': SCHEMA, 'families': [
                {'id': key, 'number': members[0]['series_number'], 'predecessor_id': members[0]['predecessor_series_id'],
                 'plan_ids': [m['plan_id'] for m in members]} for key, members in families.items()]},
            'projection': {'kind': 'plan-series', 'history_unchanged': True,
                           'membership_notice': 'legacy_recorded_ancestor仅表示历史记录归属，不证明科学因果'}}
