"""Durable question coverage and bounded native review delivery, not a model loop."""
import hashlib
import json
from pathlib import Path

from .store import dumps, now

REVISION = 'inquiry-review-3'
ENGINE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
NEXT_CHECK = {'type': 'object', 'additionalProperties': False, 'properties': {
    'kind': {'type': 'string', 'enum': ['local', 'needs_input', 'out_of_scope']},
    'action': {'type': 'string'}, 'reason': {'type': 'string'}},
    'required': ['kind', 'action', 'reason']}
FIELDS = {
    'review_of': {'type': 'array', 'maxItems': 12, 'items': {'type': 'string'},
                  'description': '本次独立评审实际核对的研究记录ID；不以角色标签替代核查'},
    'inquiry_outcomes': {'type': 'array', 'maxItems': 20, 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {'question_id': {'type': 'string'},
                       'outcome': {'type': 'string', 'enum': ['supported', 'refuted', 'unresolved', 'deferred']},
                       'reason': {'type': 'string'},
                       'evidence': {'type': 'array', 'items': {'type': 'string'}},
                       'next_check': NEXT_CHECK},
        'required': ['question_id', 'outcome', 'reason', 'evidence']},
        'description': '复用kind=question保存会影响下一选择的问题，再引用实际核查证据。未解决/暂缓不表示科学上已回答'},
    'inquiry_checks': {'type': 'array', 'maxItems': 12, 'items': {
        'type': 'object', 'additionalProperties': False, 'properties': {
            'topic': {'type': 'string', 'enum': ['objective', 'decision']},
            'claim': {'type': 'string'}, 'basis': {'type': 'string', 'enum': ['source', 'calculation', 'unverified']},
            'reason': {'type': 'string'}, 'question_id': {'type': 'string'},
            'source_quotes': {'type': 'array', 'maxItems': 4, 'items': {
                'type': 'object', 'additionalProperties': False,
                'properties': {'path': {'type': 'string'}, 'text': {'type': 'string'}}, 'required': ['path', 'text']}},
            'command_ids': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}}},
        'required': ['topic', 'claim', 'basis', 'reason', 'source_quotes', 'command_ids']},
        'description': '本次选择的关键目标解释和决策依据。原文摘录须出自本记录evidence；计算须引用同项目已成功完成的原生命令item id。无法验证用unverified，不以文件存在冒充核查'},
}


def policy_bytes():
    return (dumps({'revision': REVISION, 'sha256': ENGINE_SHA256, 'max_reviews_per_goal': 2}) + '\n').encode()


def enabled(store, sid):
    from .sdm import SDM, io_path
    binding = SDM(store).binding(sid)
    manifest = json.loads(binding['manifest'])
    name = 'inquiry-engine.json'
    if binding['mode'] != 'builtin' or name not in manifest:
        return False
    body = io_path(Path(binding['path']).parent / name).read_bytes()
    return hashlib.sha256(body).hexdigest() == manifest[name] and body == policy_bytes()


def prepare_record(store, session, record, checked, source):
    if not any(k in record for k in FIELDS):
        return source
    if not enabled(store, session['id']):
        raise ValueError('当前固定方法没有问题覆盖引擎；旧项目须空闲时显式升级')
    if record['kind'] not in {'research', 'result', 'decision', 'delivery'}:
        raise ValueError('问题核查结论应保存为research/result/decision/delivery')
    refs = record.get('review_of', [])
    if not isinstance(refs, list) or len(refs) > 12 or any(not isinstance(r,str) for r in refs) or len(set(refs)) != len(refs):
        raise ValueError('review_of需要最多12个不重复的真实记录ID')
    for ref in refs:
        if not isinstance(ref, str) or not store.one('SELECT id FROM records WHERE id=? AND project_id=?', (ref, session['project_id'])):
            raise ValueError('被评记录必须已存在且属于当前项目')
    outcomes = record.get('inquiry_outcomes', [])
    if not isinstance(outcomes, list) or len(outcomes) > 20:
        raise ValueError('单次核查最多关联20个实际问题，不是必填清单')
    paths = {r['path'] for r in checked}
    seen = set()
    for item in outcomes:
        if not isinstance(item, dict) or not {'question_id', 'outcome', 'reason', 'evidence'} <= set(item) or set(item)-{'question_id', 'outcome', 'reason', 'evidence', 'next_check'}:
            raise ValueError('问题结论需要question_id/outcome/reason/evidence')
        qid = item['question_id']
        if not isinstance(qid, str) or qid in seen or not store.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind='question'", (qid, session['project_id'])):
            raise ValueError('关联问题必须是本项目已保存且不重复的question')
        seen.add(qid)
        if item['outcome'] not in {'supported', 'refuted', 'unresolved', 'deferred'} or not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ValueError('需明确问题核查状态及其实际依据')
        evidence = item['evidence']
        if not isinstance(evidence, list) or any(not isinstance(p, str) or p not in paths for p in evidence):
            raise ValueError('问题结论证据必须同时列入本记录evidence固定真实文件')
        if item['outcome'] in {'supported', 'refuted'} and not evidence:
            raise ValueError('支持/反对结论须引用实际文件；未知或未做不能填成已验证')
        if 'next_check' in item:
            step = item['next_check']
            if (item['outcome'] not in {'unresolved', 'deferred'} or not isinstance(step, dict)
                    or set(step) != {'kind', 'action', 'reason'} or step['kind'] not in {'local', 'needs_input', 'out_of_scope'}
                    or any(not isinstance(step[k], str) or not step[k].strip() for k in ['action', 'reason'])):
                raise ValueError('未解问题的next_check需区分local/needs_input/out_of_scope并说明实际行动与依据')
    checks = prepare_checks(store, session, record.get('inquiry_checks', []), checked)
    prepared = {**source, 'review_of': refs, 'inquiry_outcomes': outcomes, 'inquiry_checks': checks}
    # Reports can be incremental. A declared unresolved review outcome must
    # identify its next step before save succeeds; full coverage is checked at
    # turn completion so intermediate findings need not invent finished answers.
    if refs and source.get('turn_id') and store.one("SELECT name FROM sqlite_master WHERE name='research_inquiry_reviews'"):
        job = store.one('''SELECT j.* FROM research_inquiry_reviews j JOIN deliveries d ON d.id=j.critic_delivery
            WHERE j.session_id=? AND d.turn_id=? AND d.state='accepted' ''', (session['id'], source['turn_id']))
        if job and job['target_id'] in refs:
            issues = [i for i in report_issues(prepared, set()) if i['kind'] == 'next_check']
            if issues:
                raise ValueError('补审记录尚不能回收，请补齐以下结构；不是要求改成支持结论：' + dumps(issues))
    return prepared


def required_questions(store, job, turn_id):
    required = {q['id'] for q in json.loads(job['packet'])['questions']['items'] if q['open']}
    required.update(r['id'] for r in store.rows("SELECT id,source FROM records WHERE session_id=? AND kind='question'", (job['session_id'],))
                    if json.loads(r['source']).get('turn_id') == turn_id)
    return required


def report_issues(source, required):
    outcomes = source.get('inquiry_outcomes', [])
    covered = {q['question_id'] for q in outcomes}
    topics = {c['topic'] for c in source.get('inquiry_checks', [])}
    issues = []
    if required-covered: issues.append({'kind': 'question_coverage', 'question_ids': sorted(required-covered)})
    if {'objective', 'decision'}-topics: issues.append({'kind': 'check_topics', 'topics': sorted({'objective', 'decision'}-topics)})
    pending = [q['question_id'] for q in outcomes if q['outcome'] in {'unresolved', 'deferred'} and not q.get('next_check')]
    if pending: issues.append({'kind': 'next_check', 'question_ids': pending, 'meaning': '区分local/needs_input/out_of_scope，记录行动与依据；未解不要求改成已证实'})
    return issues


def prepare_checks(store, session, checks, checked):
    if not isinstance(checks, list) or len(checks) > 12:
        raise ValueError('只保存最多12项决策关键主张的实际核查')
    files = {r['path']: r for r in checked}
    prepared = []
    required = {'topic', 'claim', 'basis', 'reason', 'source_quotes', 'command_ids'}
    for check in checks:
        if (not isinstance(check, dict) or not required <= set(check) or set(check)-required-{'question_id'}
                or check['topic'] not in {'objective', 'decision'} or check['basis'] not in {'source', 'calculation', 'unverified'}
                or any(not isinstance(check[k], str) or not check[k].strip() or len(check[k]) > 4000 for k in ['claim', 'reason'])):
            raise ValueError('关键核查需要topic/claim/basis/reason/source_quotes/command_ids')
        if 'question_id' in check and not store.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind='question'", (check['question_id'], session['project_id'])):
            raise ValueError('核查关联问题必须属于本项目')
        quotes, ids = check['source_quotes'], check['command_ids']
        if not isinstance(quotes, list) or len(quotes) > 4 or not isinstance(ids, list) or len(ids) > 6 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('核查摘录最多4项，真实命令最多6个不重复item id')
        for quote in quotes:
            if (not isinstance(quote, dict) or set(quote) != {'path', 'text'} or not isinstance(quote['path'], str)
                    or quote['path'] not in files or not isinstance(quote['text'], str) or not 1 <= len(quote['text'].strip()) <= 1200):
                raise ValueError('原文摘录须引用本记录evidence中的真实文件，文本长度1至1200')
            file = store.evidence_path(session['project_id'], quote['path'])
            if file.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('原文核查使用不超过4MiB的文本来源；大文件请保存可追溯摘录')
            body = file.read_bytes()
            if hashlib.sha256(body).hexdigest() != files[quote['path']]['sha256']:
                raise ValueError('原文文件在固定证据后发生改变')
            try: text = body.decode('utf-8-sig').replace('\r\n', '\n')
            except UnicodeDecodeError as exc: raise ValueError('原文摘录需要UTF-8文本来源') from exc
            if quote['text'].replace('\r\n', '\n') not in text:
                raise ValueError('摘录不是所引用文件的实际原文；不要用改写代替摘录')
        receipts = []
        for item_id in ids:
            row = store.one("""SELECT e.seq,e.session_id,e.payload FROM events e JOIN sessions s ON s.id=e.session_id
                WHERE s.project_id=? AND e.kind='item/completed' AND json_extract(e.payload,'$.item.id')=? ORDER BY e.seq DESC LIMIT 1""", (session['project_id'], item_id))
            event = json.loads(row['payload']) if row else {}
            item = event.get('item', {})
            if not row or item.get('type') != 'commandExecution' or item.get('status') != 'completed' or item.get('exitCode') != 0:
                raise ValueError('计算核查需同项目真实成功完成的原生命令item id，不能引用未运行或失败命令')
            receipts.append({'session_id': row['session_id'], 'seq': row['seq'], 'item_id': item_id,
                             'turn_id': event.get('turnId'), 'sha256': hashlib.sha256(dumps(item).encode()).hexdigest()})
        if check['basis'] == 'source' and not quotes or check['basis'] == 'calculation' and (not receipts or not files):
            raise ValueError('source核查需实际摘录；calculation核查需原生命令收据和真实输入文件')
        prepared.append({**check, 'command_receipts': receipts})
    return prepared


def questions(store, pid):
    rows = store.rows("SELECT id,title,content FROM records WHERE project_id=? AND kind='question' ORDER BY rowid DESC LIMIT 21", (pid,))
    updates = {}
    for r in store.rows('SELECT id,source FROM records WHERE project_id=? ORDER BY rowid', (pid,)):
        for item in json.loads(r['source']).get('inquiry_outcomes', []):
            updates[item['question_id']] = {**item, 'record_id': r['id']}
    return {'items': [{**r, 'content': r['content'][:1800], 'resolution': updates.get(r['id']),
                        'open': updates.get(r['id'], {}).get('outcome') not in {'supported', 'refuted'}} for r in rows[:20]],
            'truncated': len(rows) > 20, 'meaning': '程序只跟踪声明与真实文件引用，不认证问题选择、因果或结论正确'}


def native_commands(store, pid):
    rows = store.rows("""SELECT e.seq,e.session_id,e.payload FROM events e JOIN sessions s ON s.id=e.session_id
        WHERE s.project_id=? AND e.kind='item/completed' AND json_extract(e.payload,'$.item.type')='commandExecution'
        ORDER BY e.seq DESC LIMIT 13""", (pid,))
    items = []
    for row in rows[:12]:
        event = json.loads(row['payload']); item = event['item']
        items.append({'item_id': item['id'], 'seq': row['seq'], 'session_id': row['session_id'],
                      'turn_id': event.get('turnId'), 'exit_code': item.get('exitCode'), 'status': item.get('status'),
                      'command': (item.get('command') or '')[:1200], 'output_excerpt': (item.get('aggregatedOutput') or '')[:1600]})
    return {'items': items, 'truncated': len(rows) > 12, 'meaning': '原生工具完成收据，exit 0不认证计算方法或结论；完整事件按seq留存'}


def source_text(store, sid, path, line_start=1, line_count=100):
    """Bounded source bytes to JSON text, bypassing shell output code pages."""
    if not isinstance(path, str) or not path or type(line_start) is not int or line_start < 1 or type(line_count) is not int or not 1 <= line_count <= 200:
        raise ValueError('文本来源需项目内路径，line_start>=1，line_count为1..200')
    file = store.evidence_path(store.session(sid)['project_id'], path)
    if not file.is_file() or file.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('此入口只读不超过4MiB的文本文件；大型数据使用已有原生分析工具')
    body = file.read_bytes()
    try: text = body.decode('utf-8-sig')
    except UnicodeDecodeError as exc: raise ValueError('来源不是有效UTF-8文本；不以替换字符默默修复') from exc
    lines = text.splitlines(keepends=True)
    excerpt = ''.join(lines[line_start-1:line_start-1+line_count])
    truncated = len(excerpt) > 16000
    return {'path': path, 'encoding': 'utf-8-sig', 'sha256': hashlib.sha256(body).hexdigest(),
            'bytes': len(body), 'line_start': line_start, 'line_count': min(line_count, max(0,len(lines)-line_start+1)),
            'total_lines': len(lines), 'text': excerpt[:16000],
            'truncated': truncated or line_start-1+line_count < len(lines),
            'contains_replacement_character': '\ufffd' in text,
            'meaning': '直接解码原件，不经Shell；真实字节与内容不代表目标解释已正确'}


def digest(record):
    return hashlib.sha256(dumps({k: record[k] for k in ['id', 'content', 'evidence']}).encode()).hexdigest()


class InquiryReview:
    def __init__(self, store, before_dispatch=None):
        self.store, self.before_dispatch = store, before_dispatch
        with store.lock, store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS research_inquiry_reviews(
              id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
              goal_delivery TEXT NOT NULL, target_id TEXT NOT NULL REFERENCES records(id),
              target_digest TEXT NOT NULL, packet TEXT NOT NULL,
              critic_delivery TEXT NOT NULL UNIQUE, feedback_delivery TEXT NOT NULL UNIQUE,
              state TEXT NOT NULL, review_id TEXT, created_at TEXT NOT NULL)''')

    def route(self, sid, delivery_id):
        row = self.store.one('SELECT * FROM research_inquiry_reviews WHERE session_id=? AND (critic_delivery=? OR feedback_delivery=?)', (sid, delivery_id, delivery_id))
        if row:
            current=self.store.one("SELECT d.id FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id WHERE d.session_id=? AND o.origin IN ('user','review') ORDER BY d.rowid DESC LIMIT 1",(sid,))
            if current and current['id']!=row['goal_delivery']:
                raise ValueError('已有较新人工目标，旧覆盖复核不自动执行')
            target = self.store.one('SELECT * FROM records WHERE id=?', (row['target_id'],))
            if not target or digest(target) != row['target_digest']:
                raise ValueError('原研究结论身份已改变，不能用新内容重放评审')
            return {**row, 'role': 'critic' if delivery_id == row['critic_delivery'] else 'planner'}

    def completed(self,sid,turn_id):
        for event in self.store.rows("SELECT payload FROM events WHERE session_id=? AND kind='turn/completed' ORDER BY seq DESC LIMIT 100",(sid,)):
            turn=json.loads(event['payload']).get('turn',{})
            if turn.get('id')==turn_id:return turn.get('status')

    def context(self, sid):
        if not enabled(self.store, sid):
            return {'enabled': False, 'reason': '固定方法未启用当前问题覆盖引擎；不热换旧项目'}
        pid = self.store.session(sid)['project_id']
        jobs = self.store.rows('SELECT id,target_id,state,review_id FROM research_inquiry_reviews WHERE session_id=? ORDER BY rowid DESC LIMIT 4', (sid,))
        for job in jobs:
            supplement = self.store.one("SELECT payload FROM events WHERE session_id=? AND kind='research.inquiry_review_supplemented' AND json_extract(payload,'$.id')=? ORDER BY seq DESC LIMIT 1", (sid, job['id']))
            job['completion_evidence'] = json.loads(supplement['payload']) if supplement else None
            job['coverage'] = 'recorded_with_feedback' if supplement else 'recorded' if job['review_id'] else 'not_recorded'
        return {'enabled': True, 'questions': questions(self.store, pid), 'reviews': jobs,
                'native_commands': native_commands(self.store, pid),
                'usage': '原始文本文档可用research_snapshot(file_path,line_start,line_count)读取，直接UTF-8解码绕过Shell乱码。问题用question保存；inquiry_outcomes记录结论，next_check分开本地可做与外部阻塞。inquiry_checks核对关键目标解释和决策依据，引用原文或实际成功的命令item id。',
                'limit': '每个原始人工目标最多两次补审；有界退出或未解决不代表科学通过'}

    def instructions(self, job):
        packet = json.loads(job['packet'])
        if job['role'] == 'critic':
            return ('当前是宿主补发的研究结论覆盖复核，不是方案批准或新增执行授权。'
                    '独立从原始目标和原始材料发现会改变下一选择的关键问题，不只确认作者计划已列出的检查。'
                    '用research_snapshot读取被评结论、来源方案及其原始文件，必要时用原生工具做无拟合计算。'
                    '重要的新问题用kind=question记录；用research/decision保存review_of=[被评记录ID]，并用inquiry_outcomes逐项回应已知开放问题和本次新增问题，记录核查状态与真实文件证据。'
                    '保存inquiry_checks分别核对影响选择的原始目标解释(topic=objective)与决策依据(topic=decision)：区分原文、实际计算、未验证；source_quotes逐字引用真实文件，command_ids使用原生命令完成收据的item id。'
                    '真实命令ID可从research_status.inquiry_coverage.native_commands读取，不猜ID，不重跑已有成功计算只为造收据。'
                    '原始文本文档可用research_snapshot(file_path=项目内路径)直接读取UTF-8原件；工具输出有乱码时不能靠残留数字猜含义。'
                    '材料未给或无法确定时保留unverified，不从作者的转述认定目标。未解项next_check区分local/needs_input/out_of_scope；当前可算与真正外部定义分开，必要时拆成不同question。'
                    '未做/证据不足明确保留，不以unknown冒充已证实；已有核查足够可复用。结束后宿主自动交回Planner，不调用research_handoff、不批准训练。\n'
                    + dumps(packet))
        return ('当前是研究结论覆盖复核的自动回收，不是新人工指令或训练授权。'
                '读取被评结果及实际评审，处理影响下一行动的开放问题；已有工具能解决的自行核查，保留原记录与原件。'
                '用inquiry_outcomes保存处理结果；本地待核查项须完成实际核查并保存关联question_id的inquiry_checks，或记录新发现的外部阻塞。不得只转述评审后宣布完成。'
                '必要实现沿用已有Executor prepare交接；无法处理则限制结论并说明对行动的影响，不把补审完成称科学通过。\n'
                + dumps({**packet, 'review_record_id': job['review_id'], 'local_checks': self.local_checks(job),
                         'incomplete_review': self.partial_review(job) if not job['review_id'] else None}))

    def partial_review(self, job):
        delivery = self.store.one('SELECT turn_id FROM deliveries WHERE id=?', (job['critic_delivery'],))
        if not delivery or not delivery['turn_id']:
            return None
        reports = self.store.rows("SELECT id,source FROM records WHERE session_id=? AND kind IN ('research','decision') ORDER BY rowid DESC", (job['session_id'],))
        for report in reports:
            source = json.loads(report['source'])
            if source.get('turn_id') == delivery['turn_id'] and job['target_id'] in source.get('review_of', []):
                return {'candidate_record_id': report['id'], 'issues': report_issues(source, required_questions(self.store, job, delivery['turn_id'])),
                        'meaning': '原评审原样保留；候选不等于结构完整或科学通过'}

    def local_checks(self, job):
        report = self.store.one('SELECT source FROM records WHERE id=?', (job['review_id'],)) if job['review_id'] else None
        return [q for q in json.loads(report['source']).get('inquiry_outcomes', [])
                if q.get('next_check', {}).get('kind') == 'local'] if report else []

    def feedback_complete(self, job, turn_id):
        return self.completion(job, turn_id) is not None

    def completion(self, job, turn_id):
        if self.completed(job['session_id'], turn_id) != 'completed': return None
        partial = self.partial_review(job)
        review_id = job['review_id'] or (partial or {}).get('candidate_record_id')
        report = self.store.one('SELECT source FROM records WHERE id=?', (review_id,)) if review_id else None
        if not report: return None
        source = json.loads(report['source'])
        critic = self.store.one('SELECT state,turn_id FROM deliveries WHERE id=?', (job['critic_delivery'],))
        if not critic or critic['state'] != 'accepted' or self.completed(job['session_id'], critic['turn_id']) != 'completed': return None
        issues = report_issues(source, required_questions(self.store, job, critic['turn_id']))
        # Reuse an actual feedback disposition instead of another model call
        # just to fill a field. Missing independent checks cannot be self-approved.
        if any(i['kind'] != 'next_check' for i in issues): return None
        rows = self.store.rows('SELECT id,source FROM records WHERE session_id=? ORDER BY rowid', (job['session_id'],))
        feedback = [(r['id'], json.loads(r['source'])) for r in rows if json.loads(r['source']).get('turn_id') == turn_id]
        latest = {o['question_id']: (rid, o) for rid, s in feedback for o in s.get('inquiry_outcomes', [])}
        def checked(qid):
            return any(c.get('question_id') == qid and c['basis'] != 'unverified' for _, s in feedback for c in s.get('inquiry_checks', []))
        supplements = set()
        for issue in issues:
            for qid in issue['question_ids']:
                if qid not in latest: return None
                rid, outcome = latest[qid]
                if outcome['outcome'] in {'supported', 'refuted'}:
                    if not checked(qid): return None
                elif not outcome.get('next_check'): return None
                supplements.add(rid)
        local_ids = {q['question_id'] for q in source.get('inquiry_outcomes', []) if q.get('next_check', {}).get('kind') == 'local'}
        local_ids.update(qid for qid, (_, outcome) in latest.items() if outcome.get('next_check', {}).get('kind') == 'local')
        for qid in local_ids:
            if qid not in latest: return None
            last = latest[qid][1]
            if last['outcome'] in {'supported', 'refuted'}:
                if not checked(qid): return None
            elif last.get('next_check', {}).get('kind') not in {'needs_input', 'out_of_scope'}:
                return None
        return {'review_id': review_id, 'supplement_record_ids': sorted(supplements)}

    def save_completion(self, job, completion):
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE research_inquiry_reviews SET review_id=?,state='done' WHERE id=?", (completion['review_id'], job['id']))
            if completion['supplement_record_ids'] and not self.store.one("SELECT seq FROM events WHERE session_id=? AND kind='research.inquiry_review_supplemented' AND json_extract(payload,'$.id')=?", (job['session_id'], job['id'])):
                self.store.event(job['session_id'], 'research.inquiry_review_supplemented', {'id': job['id'], **completion,
                    'meaning': '复用真实反馈记录补齐未解事项去向；原报告不改，不代表科学通过或新增执行授权'})

    def settle(self, sid, goal):
        for job in self.store.rows("SELECT * FROM research_inquiry_reviews WHERE session_id=? AND goal_delivery=? AND state='incomplete' ORDER BY rowid", (sid, goal['id'])):
            delivery = self.store.one('SELECT state,turn_id FROM deliveries WHERE id=?', (job['feedback_delivery'],))
            target = self.store.one('SELECT * FROM records WHERE id=?', (job['target_id'],))
            if not delivery or delivery['state'] != 'accepted' or not target or digest(target) != job['target_digest']: continue
            completion = self.completion(job, delivery['turn_id'])
            if completion:
                self.save_completion(job, completion)
                return True
        return False

    def advance(self, sid, send, settings):
        store = self.store
        session = store.session(sid)
        if session['archived'] or session['status'] != 'idle' or session['active_turn'] or not enabled(store, sid):
            return False
        # The admitted autonomous experiment loop already owns scheduling and
        # budget finalization. Its reviewer can use the same inquiry records;
        # this fallback must not become a competing continuation owner.
        from .autonomy import policy,finalizing
        owner=policy(store,session['project_id'])
        if owner and owner['mode']=='autonomous' or finalizing(store,session['project_id']):
            return False
        control = store.one('SELECT paused FROM research_controls WHERE session_id=?', (sid,))
        if control and control['paused']:
            return False
        if store.one("SELECT id FROM deliveries WHERE session_id=? AND state IN ('queued','requested','unknown')", (sid,)):
            return False
        # Respect explicit native handoffs first; never race their dispatcher.
        if store.one("SELECT id FROM research_role_handoffs WHERE session_id=? AND state='pending'", (sid,)):
            return False
        goal = store.one("SELECT d.* FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id WHERE d.session_id=? AND o.origin IN ('user','review') ORDER BY d.rowid DESC LIMIT 1", (sid,))
        if not goal or goal['state'] != 'accepted':
            return False
        job = store.one("SELECT * FROM research_inquiry_reviews WHERE session_id=? AND state NOT IN ('done','superseded','incomplete') ORDER BY rowid LIMIT 1", (sid,))
        if job and job['goal_delivery'] != goal['id']:
            store.execute("UPDATE research_inquiry_reviews SET state='superseded' WHERE id=?", (job['id'],))
            return False
        if job:
            delivery = store.one('SELECT * FROM deliveries WHERE id=?', (job['critic_delivery'],))
            if delivery and delivery['state'] in {'unknown', 'requested', 'rejected', 'cancelled'}:
                return True  # Reconcile the original identity; never fabricate another.
            if delivery and delivery['state'] == 'accepted':
                completed=self.completed(sid,delivery['turn_id'])
                if completed is None:return True
                if completed!='completed':
                    store.execute("UPDATE research_inquiry_reviews SET state='incomplete' WHERE id=?",(job['id'],))
                    return True
                reports = store.rows("SELECT * FROM records WHERE session_id=? AND kind IN ('research','decision') ORDER BY rowid DESC", (sid,))
                report = next((r for r in reports if json.loads(r['source']).get('turn_id') == delivery['turn_id']
                               and job['target_id'] in json.loads(r['source']).get('review_of', [])), None)
                if report and report_issues(json.loads(report['source']), required_questions(store, job, delivery['turn_id'])):
                    report = None
                if not report:
                    store.event(sid, 'research.inquiry_review_incomplete', {'id': job['id'], 'target_id': job['target_id'], 'partial_review': self.partial_review(job)})
                review_id=report['id'] if report else None
                store.execute("UPDATE research_inquiry_reviews SET review_id=?,state='feedback_pending' WHERE id=?", (review_id, job['id']))
                job = {**job, 'review_id': review_id, 'state': 'feedback_pending'}
            if job['state'] == 'feedback_pending':
                feedback = store.one('SELECT * FROM deliveries WHERE id=?', (job['feedback_delivery'],))
                if feedback:
                    if feedback['state'] == 'accepted':
                        if not self.completed(sid,feedback['turn_id']):return True
                        completion = self.completion(job, feedback['turn_id'])
                        if not completion:
                            store.execute("UPDATE research_inquiry_reviews SET state='incomplete' WHERE id=?", (job['id'],))
                            store.event(sid, 'research.inquiry_feedback_incomplete', {'id': job['id'], 'local_checks': self.local_checks(job)})
                            return True
                        self.save_completion(job, completion)
                        return self.advance(sid, send, settings)
                    return True
                return self._send(job, 'planner', job['feedback_delivery'], send)
            return self._send(job, 'critic', job['critic_delivery'], send)
        if self.settle(sid, goal): return True
        # Only authored research conclusions produce review work, not ordinary
        # messages, tool chatter, engineering failures or copied record titles.
        candidates = store.rows("SELECT * FROM records WHERE session_id=? AND kind IN ('result','research','delivery') AND rowid>(SELECT COALESCE(MAX(rowid),0) FROM records WHERE session_id=? AND created_at<?) ORDER BY rowid DESC", (sid, sid, goal['created_at']))
        target = next((r for r in candidates if json.loads(r['evidence'])
                       and json.loads(r['source']).get('origin') == 'codex-native-tool'
                       and self.completed(sid,json.loads(r['source']).get('turn_id'))=='completed'
                       and store.one("SELECT role FROM research_role_routes r JOIN deliveries d ON d.id=r.delivery_id WHERE r.session_id=? AND d.turn_id=? AND r.role IN ('planner','executor','generator')", (sid, json.loads(r['source']).get('turn_id')))
                       ), None)
        if not target:
            return False
        if store.one('SELECT id FROM research_inquiry_reviews WHERE session_id=? AND target_id=?',(sid,target['id'])):return False
        count = store.one('SELECT COUNT(*) n FROM research_inquiry_reviews WHERE session_id=? AND goal_delivery=?', (sid, goal['id']))['n']
        if count >= 2:
            return False
        prior = store.rows('SELECT source FROM records WHERE session_id=?', (sid,))
        if any(target['id'] in json.loads(r['source']).get('review_of', [])
               and store.one("SELECT r.delivery_id FROM research_role_routes r JOIN deliveries d ON d.id=r.delivery_id WHERE r.session_id=? AND r.role='critic' AND d.turn_id=?",(sid,json.loads(r['source']).get('turn_id')))
               for r in prior):
            return False
        plan = store.one("SELECT id,title,evidence FROM records WHERE project_id=? AND kind='plan' ORDER BY rowid DESC LIMIT 1", (session['project_id'],))
        packet = {'original_goal': {'delivery_id': goal['id'], 'text': goal['text']},
                  'target': {'record_id': target['id'], 'title': target['title'], 'digest': digest(target)},
                  'source_plan': plan, 'questions': questions(store, session['project_id']),
                  'scope': '原始目标、原材料与结论的覆盖，不以作者计划列出的检查为完整问题集；程序不提供正确答案'}
        key = hashlib.sha256(dumps([sid, goal['id'], digest(target), REVISION]).encode()).hexdigest()
        with store.lock, store.db:
            store.db.execute('INSERT OR IGNORE INTO research_inquiry_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                             (key, sid, goal['id'], target['id'], digest(target), dumps(packet),
                              'inquiry-critic:' + key, 'inquiry-feedback:' + key, 'queued', None, now()))
        job = store.one('SELECT * FROM research_inquiry_reviews WHERE id=?', (key,))
        store.event(sid, 'research.inquiry_review_queued', {'id': key, 'target_id': target['id']})
        return self._send(job, 'critic', job['critic_delivery'], send)

    def _send(self, job, role, delivery_id, send):
        if self.before_dispatch and not self.before_dispatch(job['session_id'], {'target': role, 'delivery_id': delivery_id}):
            return True
        existing = self.store.one('SELECT id FROM deliveries WHERE id=?', (delivery_id,))
        if not existing:
            text = '[研究结论覆盖复核 · 宿主事件，非人工新授权]\n' + self.instructions({**job, 'role': role})
            send(job['session_id'], text, delivery_id=delivery_id, origin='background')
        return True
