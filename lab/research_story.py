"""Evidence-backed narrative overlays; original records and execution stay intact.

The append-only event journal is the storage mechanism. An annotation is a
retrospective reading of sources, never a recovered Agent action or a grant.
"""
import hashlib
import json
from pathlib import Path

from .store import dumps, now

SCHEMA = 'iterlab-research-story/v1'
ROLES = {'project', 'investigation', 'finding', 'correction', 'question', 'discussion'}
RELATIONS = {'research_step', 'claim_revision', 'raises_question'}
ROOT = Path(__file__).resolve().parents[1]


def load(store, project_id):
    row = store.one("SELECT seq,payload,created_at FROM events WHERE kind='research.story.saved' "
                    "AND json_extract(payload,'$.project_id')=? ORDER BY seq DESC LIMIT 1", (project_id,))
    return {**json.loads(row['payload']), 'event_seq': row['seq'], 'annotated_at': row['created_at']} if row else None


def text(value, limit=1200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('脉络文字为空或超过长度限制')
    return value.strip()


def citation(store, project_id, value):
    kind, ref = value.get('kind'), value.get('id')
    if kind == 'record':
        row = store.one('SELECT content,session_id,created_at FROM records WHERE id=? AND project_id=?', (ref, project_id))
        content = row['content'] if row else None
    elif kind == 'delivery':
        row = store.one('SELECT d.text AS content,d.session_id,d.created_at FROM deliveries d JOIN sessions s '
                        'ON s.id=d.session_id WHERE d.id=? AND s.project_id=?', (ref, project_id))
        content = row['content'] if row else None
    elif kind == 'document':
        # Explicit local acceptance sources only; never arbitrary credential files.
        path = (ROOT / text(ref, 400)).resolve()
        allowed = ROOT / 'docs' / 'acceptance'
        if not path.is_relative_to(allowed) or path.suffix != '.md' or path.stat().st_size > 2_000_000:
            raise ValueError('历史注解文档必须是本应用的验收 Markdown')
        content = path.read_text(encoding='utf-8')
        row = {'session_id': None, 'created_at': None}
    else:
        raise ValueError('脉络来源类型无效')
    if content is None:
        raise ValueError('脉络来源不存在或不属于当前项目')
    quote = text(value.get('quote'), 4000)
    if quote not in content:
        raise ValueError('脉络引用必须逐字存在于来源中')
    return {'kind': kind, 'id': ref, 'quote': quote,
            'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'digest_basis': 'utf8_source_text',
            'session_id': row['session_id'], 'occurred_at': row['created_at']}


def input_origin(store, delivery_id):
    if not store.one("SELECT name FROM sqlite_master WHERE name='research_input_origins'"):
        return 'unknown'
    row = store.one('SELECT origin FROM research_input_origins WHERE delivery_id=?', (delivery_id,))
    return row['origin'] if row else 'unknown'


def save(store, project_id, payload):
    """Append a reviewable annotation revision. Never insert research/run rows."""
    if not store.one('SELECT id FROM projects WHERE id=?', (project_id,)):
        raise ValueError('项目不存在')
    if not isinstance(payload, dict) or payload.get('schema') != SCHEMA:
        raise ValueError('脉络注解版本无效')
    nodes, edges = payload.get('nodes'), payload.get('edges')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 60 or not isinstance(edges, list) or len(edges) > 120:
        raise ValueError('脉络节点与关系需有界')
    records = {r['id']: r for r in store.rows('SELECT * FROM records WHERE project_id=?', (project_id,))}
    checked, ids = [], set()
    for node in nodes:
        node_id = text(node.get('id'), 200)
        if node_id in ids or node_id not in records and not node_id.startswith(f'story:{project_id}:'):
            raise ValueError('脉络节点必须关联原记录或使用本项目注解标识')
        ids.add(node_id)
        role, actor = node.get('kind'), node.get('actor')
        if role not in ROLES or actor not in {'agent', 'human', 'evaluator'}:
            raise ValueError('脉络角色或行动来源无效')
        if node_id in records and records[node_id]['kind'] in {'plan', 'trial', 'review'}:
            raise ValueError('注解不能改变方案、实验或轮次评审的类型')
        sources = node.get('sources')
        if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
            raise ValueError('每个脉络节点必须有明确来源')
        sources = [citation(store, project_id, s) for s in sources]
        if node_id in records and not any(s['kind'] == 'record' and s['id'] == node_id for s in sources):
            raise ValueError('原记录的注解必须引用该原文')
        trigger = node.get('trigger_delivery_id')
        if trigger:
            row = store.one('SELECT d.* FROM deliveries d JOIN sessions s ON s.id=d.session_id '
                            'WHERE d.id=? AND s.project_id=?', (trigger, project_id))
            if not row or not any(s['kind'] == 'delivery' and s['id'] == trigger for s in sources):
                raise ValueError('人工介入需引用同项目的实际输入')
            if input_origin(store, trigger) == 'background':
                raise ValueError('系统自动续接不能标记为人工介入')
            if node_id in records:
                source = json.loads(records[node_id]['source'])
                if row['session_id'] != records[node_id]['session_id'] or not row['turn_id'] or row['turn_id'] != source.get('turn_id'):
                    raise ValueError('人工输入必须对应原记录的实际回合')
        checked.append({'id': node_id, 'kind': role, 'actor': actor,
                        'title': text(node.get('title'), 160), 'summary': text(node.get('summary'), 500),
                        'content': text(node.get('content'), 8000), 'sources': sources,
                        'trigger_delivery_id': trigger,
                        'trigger_label': text(node.get('trigger_label'), 100) if trigger else '',
                        'status': 'not_verified' if role == 'question' else 'recorded'})
    checked_edges = []
    adjacency = {}
    edge_keys = set()
    for e in edges:
        a, b, kind = e.get('source'), e.get('target'), e.get('kind')
        if a not in ids or b not in ids or a == b or kind not in RELATIONS:
            raise ValueError('脉络关系必须引用本次注解的明确节点')
        if (a, b, kind) in edge_keys:
            raise ValueError('脉络关系不能重复')
        edge_keys.add((a, b, kind))
        refs = e.get('source_node_ids', [b])
        if not isinstance(refs, list) or not refs or any(ref not in ids for ref in refs):
            raise ValueError('脉络连线必须保留来源节点')
        if kind != 'claim_revision':
            adjacency.setdefault(a, []).append(b)
        checked_edges.append({'source': a, 'target': b, 'kind': kind, 'source_node_ids': refs})
    visited = set()
    def walk(key, path):
        if key in path:
            raise ValueError('研究推进关系不能形成循环')
        if key in visited:
            return
        for child in adjacency.get(key, []):
            walk(child, path | {key})
        visited.add(key)
    for key in ids:
        walk(key, set())
    with store.lock, store.db:
        prior = load(store, project_id)
        revision = prior['revision'] if prior else 0
        if payload.get('expected_revision') != revision:
            raise ValueError('脉络注解已更新，请先读取最新版本')
        value = {'schema': SCHEMA, 'project_id': project_id, 'revision': revision + 1,
                 'scope': text(payload.get('scope'), 500), 'reason': text(payload.get('reason'), 1000),
                 'nodes': checked, 'edges': checked_edges, 'history_unchanged': True}
        sid = next((s['session_id'] for n in checked for s in n['sources'] if s['session_id']), None)
        store.event(sid, 'research.story.saved', value)
    return load(store, project_id)


def prepare_record(store, session, record, source):
    """Optional semantic fields on the existing native record tool, not a loop."""
    context = record.get('research_context')
    if context is None:
        return source
    if not isinstance(context, dict) or context.get('role') not in ROLES - {'project'}:
        raise ValueError('研究脉络角色无效')
    if record['kind'] in {'plan', 'trial', 'review'}:
        raise ValueError('方案、实验与评审保留其原始类型')
    refs = context.get('revises_record_ids', [])
    if not isinstance(refs, list) or len(refs) > 8 or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs):
        raise ValueError('修订引用需为最多8个明确记录')
    continues = context.get('continues_record_id')
    for ref in refs + ([continues] if continues else []):
        if not store.one('SELECT id FROM records WHERE project_id=? AND id=?', (session['project_id'], ref)):
            raise ValueError('修订必须引用同项目已有记录')
    if context['role'] == 'correction' and not refs:
        raise ValueError('结论修订需要说明修订哪项原记录')
    # Human input association is derived from an exact native turn, not an
    # Agent declaration of autonomy or the nearest preceding message.
    deliveries = store.rows("SELECT id FROM deliveries WHERE session_id=? AND turn_id=? AND state='accepted'",
                            (session['id'], source.get('turn_id')))
    trigger = deliveries[0]['id'] if len(deliveries) == 1 else None
    return {**source, 'research_context': {'role': context['role'], 'revises_record_ids': refs,
            'continues_record_id': continues,
            'actor': 'agent', 'trigger_delivery_id': trigger, 'trigger_origin':input_origin(store, trigger)}}


def origin(store, project_id):
    if not store.one("SELECT name FROM sqlite_master WHERE name='research_input_origins'"):
        return None  # Missing provenance is not proof of a human research goal.
    row = store.one("SELECT d.* FROM deliveries d JOIN sessions s ON s.id=d.session_id "
                    "JOIN research_input_origins o ON o.delivery_id=d.id "
                    "WHERE s.project_id=? AND d.state='accepted' AND o.origin IN ('user','review') "
                    "ORDER BY d.created_at,d.rowid LIMIT 1", (project_id,))
    if not row:
        return None
    return {'id':f'story:{project_id}:origin', 'kind':'project', 'title':'研究起点：原始目标与输入',
            'summary':row['text'][:160], 'content':row['text'], 'actor':'human', 'status':'recorded',
            'sources':[citation(store, project_id, {'kind':'delivery','id':row['id'],'quote':row['text'][:4000]})]}


def project(store, graph):
    """Compose the narrative view without promoting chronology to causality."""
    story = load(store, graph['project_id'])
    nodes = {n['id']: dict(n) for n in graph['nodes']}
    edges = list(graph['edges'])
    native_contexts = []
    for record in store.rows('SELECT id,source FROM records WHERE project_id=?', (graph['project_id'],)):
        context = json.loads(record['source']).get('research_context')
        if not context or record['id'] not in nodes:
            continue
        node = nodes[record['id']]
        trigger = context.get('trigger_delivery_id')
        trigger_row = store.one('SELECT text FROM deliveries WHERE id=?', (trigger,)) if trigger else None
        presented_context = {**context, 'sources': [citation(store, graph['project_id'],
            {'kind':'delivery','id':trigger,'quote':trigger_row['text'][:4000]})] if trigger_row else []}
        node.update(kind=context['role'], original_kind=node['kind'], story_context=presented_context)
        native_contexts.append((node, context))
        if context['role'] == 'question':
            node['status'] = 'not_verified'
        elif node['status'] == 'completed':
            node['status'] = 'recorded'
        for ref in context['revises_record_ids']:
            edges.append({'id': f"claim-revision:{ref}:{record['id']}", 'source': ref, 'target': record['id'],
                          'kind': 'claim_revision', 'primary': False, 'story_visible': True,
                          'basis': 'explicit_record_research_context'})
        if context.get('continues_record_id'):
            edges.append({'id':f"research-step:{record['id']}", 'source':context['continues_record_id'],
                          'target':record['id'], 'kind':'research_step', 'primary':True,
                          'story_visible':True, 'basis':'explicit_record_research_context'})
    first_input = origin(store, graph['project_id']) if native_contexts else None
    if first_input and not story:
        nodes[first_input['id']] = {**first_input, 'milestone':True, 'projection_only':True,
                                   'created_at':first_input['sources'][0]['occurred_at'],
                                   'origin_session_id':first_input['sources'][0]['session_id'],
                                   'story_context':first_input}
        incoming = {e['target'] for e in edges if e.get('primary') is not False}
        for node in list(nodes.values()):
            if node['id'] != first_input['id'] and node['id'] not in incoming:
                edges.append({'id':f"research-origin:{node['id']}", 'source':first_input['id'],
                              'target':node['id'], 'kind':'organizes', 'primary':True,
                              'basis':'recorded_project_input_not_scientific_causality'})
    if not story:
        return {**graph, 'nodes': list(nodes.values()), 'edges': edges,
                **({'root_id':first_input['id']} if first_input else {})}
    for note in story['nodes']:
        original = nodes.get(note['id'])
        # A source timestamp is not the occurrence time of a later synthesis.
        occurred = original.get('created_at') if original else (
            note['sources'][0]['occurred_at'] if note['kind']=='project' and note['sources'][0]['kind']=='delivery' else None)
        nodes[note['id']] = {**(original or {}), **{k: note[k] for k in ('id', 'kind', 'title', 'summary', 'status')},
                            'milestone': True, 'projection_only': not bool(original),
                            'original_kind': original.get('original_kind', original.get('kind')) if original else None,
                            'original_title': original.get('title') if original else None,
                            'created_at': occurred, 'origin_session_id': note['sources'][0]['session_id'],
                            'story_context': {**note, 'retrospective': True, 'annotated_at': story['annotated_at'],
                                              'revision': story['revision'], 'scope': story['scope']}}
        if original and original.get('status') in {'failed','blocked','rejected'}:
            nodes[note['id']]['status'] = original['status']
    for e in story['edges']:
        edges.append({**e, 'id': f"story-edge:{graph['project_id']}:{e['source']}:{e['target']}:{e['kind']}",
                      'primary': e['kind'] != 'claim_revision', 'story_visible': True,
                      'projection_only': True, 'basis': 'cited_retrospective_annotation'})
    return {**graph, 'nodes': list(nodes.values()), 'edges': edges,
            'research_story': {k: story[k] for k in ('schema', 'revision', 'scope', 'event_seq', 'annotated_at')},
            'root_id': next((n['id'] for n in story['nodes'] if n['kind'] == 'project'), graph.get('root_id'))}


def detail(store, node_id, project_id=None):
    if node_id.startswith('story:'):
        project_id = node_id.split(':', 2)[1]
    else:
        row = store.one('SELECT project_id FROM records WHERE id=?', (node_id,))
        project_id = row['project_id'] if row else project_id
    story = load(store, project_id) if project_id else None
    note = next((n for n in story['nodes'] if n['id'] == node_id), None) if story else None
    if not note:
        if project_id and node_id == f'story:{project_id}:origin':
            note = origin(store, project_id)
            return {**note, 'project_id':project_id} if note else None
        return None
    return {**note, 'retrospective': True, 'annotated_at': story['annotated_at'],
            'revision': story['revision'], 'scope': story['scope'], 'project_id': project_id}
