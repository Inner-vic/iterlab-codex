"""Built-in, evidence-backed research methods. No model, execution or approval loop."""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path

from .store import dumps, now
from .evaluation_contract import pointer

ENGINE_REVISION = 'research-modules-1'
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 8 * MAX_FILE
MODULES = {
    'data': ('数据独立性与领域语义', 'data.md', ('understand', 'plan')),
    'design': ('基线、实验协议与选择偏差', 'design.md', ('plan',)),
    'training': ('训练过程与异常诊断', 'training.md', ('conduct', 'assess')),
    'engineering': ('已有工程与实施就绪', 'engineering.md', ('conduct',)),
    'evidence': ('完整证据、反证与结论边界', 'evidence.md', ('assess', 'deliver')),
}


def sha(body):
    return hashlib.sha256(body).hexdigest()


# Fingerprint the loaded implementation, not a file edited under a running host.
ENGINE_SHA256 = sha(Path(__file__).read_bytes())


def json_value(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('方法输入JSON包含重复字段')
            result[key] = value
        return result
    # NaN in historical training output is an observation, not a finite metric.
    return json.loads(body.decode('utf-8-sig'), object_pairs_hook=pairs)


def install(store):
    with store.lock, store.db:
        store.db.execute('''CREATE TABLE IF NOT EXISTS research_method_reports(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
          session_id TEXT NOT NULL REFERENCES sessions(id), plan_id TEXT NOT NULL REFERENCES records(id),
          module TEXT NOT NULL, state TEXT NOT NULL, digest TEXT NOT NULL,
          body TEXT NOT NULL, created_at TEXT NOT NULL)''')


class Inputs:
    def __init__(self, store, plan):
        self.store, self.plan = store, plan
        self.refs = {r['path']: r for r in json.loads(plan['evidence'])}
        self.used, self.total = {}, 0

    def runtime(self, name):
        if name in self.refs:
            return
        exists=self.store.one("SELECT name FROM sqlite_master WHERE name='research_runs'")
        rows=self.store.rows('SELECT receipt FROM research_runs WHERE plan_id=? ORDER BY rowid LIMIT 101', (self.plan.get('id'),)) if exists else []
        if len(rows)>100:
            raise ValueError('本方案超过100次尝试，先使用有来源的有界证据索引')
        for row in rows:
            receipt=json.loads(row['receipt']) if row['receipt'] else {}
            for ref in receipt.get('outputs',[]):
                if ref.get('path')==name and {'path','sha256','size'}<=set(ref):
                    self.refs[name]={key:ref[key] for key in ['path','sha256','size']}
                    return
        raise ValueError('训练方法只读取本方案原运行收据声明的产物')

    def bytes(self, name):
        if name not in self.refs:
            raise ValueError('方法输入必须纳入当前方案冻结证据：' + str(name))
        ref = self.refs[name]
        if ref['size'] > MAX_FILE:
            raise ValueError('方法输入单文件超过2MiB，先提供有来源的有界索引')
        path = self.store.evidence_path(self.plan['project_id'], name)
        if path.is_symlink() or not path.is_file():
            raise ValueError('方法输入不存在或为链接')
        with path.open('rb') as stream:
            body = stream.read(MAX_FILE + 1)
        if len(body) != ref['size'] or sha(body) != ref['sha256']:
            raise ValueError('方法输入已改变，不能沿用原方案：' + name)
        if name not in self.used:
            self.total += len(body)
            if self.total > MAX_TOTAL:
                raise ValueError('方法输入累计超过16MiB')
            self.used[name] = ref
        return body

    def json(self, ref):
        if isinstance(ref, str):
            ref = {'path': ref}
        if not isinstance(ref, dict) or set(ref) - {'path', 'pointer'} or not isinstance(ref.get('path'), str):
            raise ValueError('方法引用需为path与可选JSON Pointer')
        return pointer(json_value(self.bytes(ref['path'])), ref.get('pointer', ''))


def prepare_plan(store, session, record, checked, content, source):
    name = record.get('research_protocol')
    if name is None:
        return source
    if not isinstance(name, str):
        raise ValueError('research_protocol需为已冻结的项目内JSON文件路径')
    inputs = Inputs(store, {'project_id': session['project_id'], 'evidence': dumps(checked)})
    protocol = inputs.json(name)
    if not isinstance(protocol, dict) or set(protocol) - {'schema', 'data', 'design', 'training', 'engineering'} or type(protocol.get('schema')) is not int or protocol['schema'] != 1:
        raise ValueError('内置研究协议需为schema=1及适用模块，不是26项必填表')
    for module, value in protocol.items():
        if module != 'schema' and not isinstance(value, dict):
            raise ValueError('方法模块配置需为对象')
    return {**source, 'research_protocol': inputs.used[name], 'research_module_engine': ENGINE_REVISION}


class ResearchModules:
    def __init__(self, store):
        self.store = store
        install(store)

    def plan(self, sid, plan_id):
        session = self.store.session(sid)
        plan = self.store.one("SELECT * FROM records WHERE id=? AND project_id=? AND kind='plan'", (plan_id, session['project_id']))
        if not plan:
            raise ValueError('方法检查需引用当前项目的确切方案')
        return plan

    def context(self, sid, role=None):
        from .sdm import SDM,io_path
        binding = SDM(self.store).binding(sid)
        if 'modules/data.md' not in json.loads(binding['manifest']):
            return {'available': False, 'reason': '旧固定方法保持原样；空闲时显式升级后启用内置模块'}
        engine_path=io_path(Path(binding['path']).parent/'module-engine.json')
        if not engine_path.is_file() or json.loads(engine_path.read_bytes()).get('sha256')!=ENGINE_SHA256:
            return {'available':False,'requires_upgrade':True,'reason':'方法固定的检查引擎与当前宿主不同；空闲时显式升级，不热换旧检查语义'}
        from .rounds import approved_plan
        pid = self.store.session(sid)['project_id']
        plan = approved_plan(self.store, pid)
        if not plan:
            plan = self.store.one("SELECT * FROM records WHERE project_id=? AND kind='plan' ORDER BY rowid DESC LIMIT 1", (pid,))
        focus = self.store.one('SELECT phase FROM research_focus WHERE project_id=?', (pid,))
        phase = focus['phase'] if focus else 'understand'
        wanted = {'critic': ['design', 'evidence'], 'generator': ['design'], 'executor': ['engineering', 'training']}.get(role)
        if not wanted:
            wanted = [key for key, (_, _, phases) in MODULES.items() if phase in phases]
        failure = self.store.one("SELECT id FROM research_runs WHERE plan_id=? AND state IN ('failed','timed_out','invalid_inputs','artifacts_missing','unknown') LIMIT 1", (plan['id'],)) if plan else None
        if failure:
            wanted = ['training', 'evidence']
        return {'available': True, 'engine': ENGINE_REVISION, 'plan_id': plan['id'] if plan else None,
                'protocol':json.loads(plan['source']).get('research_protocol') if plan else None,
                'applicable': [{'module': key, 'title': MODULES[key][0], 'reason': '已有失败/未知尝试' if failure else '当前角色/研究焦点'} for key in wanted],
                'recent_reports': self.store.rows('SELECT id,module,state,plan_id FROM research_method_reports WHERE project_id=? ORDER BY rowid DESC LIMIT 5', (pid,)),
                'usage': 'research_method(operation=read,module=...)读内置方法；operation=check,plan_id=...检查真实证据。报告不是科学认证或执行授权。'}

    def read(self, sid, module):
        from .sdm import SDM,io_path
        if module not in MODULES:
            raise ValueError('未知内置研究模块')
        binding = SDM(self.store).binding(sid)
        path = 'modules/' + MODULES[module][1]
        manifest = json.loads(binding['manifest'])
        if path not in manifest:
            raise ValueError('当前固定方法没有此模块；须显式升级，不能静默改旧方法')
        engine_path=io_path(Path(binding['path']).parent/'module-engine.json')
        if 'module-engine.json' not in manifest or not engine_path.is_file():
            raise ValueError('当前固定方法没有检查引擎身份；须显式升级')
        engine=json.loads(engine_path.read_bytes())
        if engine.get('sha256')!=ENGINE_SHA256:
            raise ValueError('固定方法与当前检查引擎不同；空闲时显式升级，不能静默换检查语义')
        body = io_path(Path(binding['path']).parent / path).read_bytes()
        if sha(body) != manifest[path]:
            raise ValueError('固定方法模块已改变')
        return {'module': module, 'title': MODULES[module][0], 'method_digest': binding['digest'], 'text': body.decode('utf-8')}

    def check(self, sid, module, plan_id, artifact=None):
        if module not in MODULES:
            raise ValueError('未知内置研究模块')
        method = self.read(sid, module)
        plan = self.plan(sid, plan_id)
        inputs = Inputs(self.store, plan)
        source = json.loads(plan['source'])
        protocol = inputs.json(source['research_protocol']['path']) if source.get('research_protocol') else {}
        config = protocol.get(module)
        if artifact is not None:
            if module!='training' or not isinstance(artifact,str):
                raise ValueError('artifact仅用于选择本方案实际训练history产物')
            inputs.runtime(artifact)
            config={**(config or {}),'history':artifact}
        findings, facts = [], {}

        def finding(code, level, message):
            value={'code': code, 'level': level, 'message': message}
            if value not in findings and len(findings)<80:findings.append(value)

        if module == 'evidence':
            facts = self.evidence(plan, finding)
        elif config is None:
            finding('not_declared', 'needs_evidence', '当前方案未固定本模块所需协议；可按方法补查，不能把未知当检查通过')
        elif module == 'data':
            facts = self.data(config, inputs, finding)
        elif module == 'design':
            facts = self.design(config, inputs, finding)
        elif module == 'training':
            facts = self.training(config, inputs, finding)
        elif module == 'engineering':
            facts = self.engineering(config, inputs, finding)
        from .research import plan_digest
        report = {'schema': 1, 'engine': ENGINE_REVISION, 'engine_sha256': ENGINE_SHA256,
                  'method_digest': method['method_digest'], 'project_id': plan['project_id'], 'plan_id': plan_id,
                  'plan_digest': plan_digest(plan), 'module': module, 'inputs': list(inputs.used.values()),
                  'artifact':artifact,
                  'facts': facts, 'findings': findings,
                  'state': 'blocked' if any(f['level'] == 'blocking' for f in findings) else 'needs_evidence' if any(f['level'] == 'needs_evidence' for f in findings) else 'observations',
                  'limitations': '只核对所列文件、声明与宿主运行事实；不证明标签/实现/因果/统计正确，不授予执行或独立科学认证。'}
        body = dumps(report)
        digest = sha(body.encode())
        id = 'method:' + digest
        self.store.execute('INSERT OR IGNORE INTO research_method_reports VALUES(?,?,?,?,?,?,?,?,?)',
                           (id, plan['project_id'], sid, plan_id, module, report['state'], digest, body, now()))
        return {'report_id': id, **report}

    @staticmethod
    def data(config, inputs, finding):
        splits = config.get('splits', {})
        if not isinstance(splits, dict) or not 2 <= len(splits) <= 6:
            raise ValueError('数据检查需声明2—6个实际划分引用')
        unit = config.get('independence_unit', 'sample')
        if unit not in {'sample', 'group', 'time'}:
            raise ValueError('独立单元需为sample/group/time')
        sets, groups, times, facts = {}, {}, {}, {'counts': {}, 'independence_unit': unit}
        for name, ref in splits.items():
            rows = inputs.json(ref)
            if not isinstance(rows, list) or not rows or len(rows) > 50000:
                raise ValueError('划分需为1—50000个有界实际ID记录')
            ids, group, stamps = [], [], []
            for row in rows:
                if not isinstance(row, dict):
                    row = {'id': row}
                value = row.get(config.get('id_key', 'id'))
                if type(value) not in {str, int} or value == '':
                    raise ValueError('样本ID需为非空字符串或整数')
                ids.append((type(value).__name__, value))
                if unit == 'group':
                    value = row.get(config.get('group_key', 'group'))
                    if type(value) not in {str, int} or value == '':
                        finding('missing_group', 'needs_evidence', '缺少真实组ID，不能认证组独立'); continue
                    group.append((type(value).__name__, value))
                if unit == 'time':
                    value = row.get(config.get('time_key', 'time'))
                    if not finite(value):
                        finding('missing_time', 'needs_evidence', '缺少有限时间值，不能认证时间顺序'); continue
                    stamps.append(value)
            if len(ids) != len(set(ids)):
                finding('duplicate_ids', 'blocking', name + '内部样本ID重复，先核对评价单位')
            sets[name], groups[name], times[name] = set(ids), set(group), stamps
            facts['counts'][name] = len(ids)
        names = list(splits)
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                overlap = len(sets[left] & sets[right])
                if overlap:
                    finding('split_overlap', 'blocking', f'{left}/{right}有{overlap}个共同样本ID')
                if unit == 'group' and groups[left] & groups[right]:
                    finding('group_overlap', 'blocking', f'{left}/{right}共享组，违反已声明的组独立')
        if unit == 'time':
            order = config.get('time_order')
            if not isinstance(order, list) or len(order) != len(names) or set(order) != set(names):
                finding('time_order_unknown', 'needs_evidence', '时间预测需明确划分先后，不能凭字典顺序推断')
            else:
                for left, right in zip(order, order[1:]):
                    if times[left] and times[right] and max(times[left]) >= min(times[right]):
                        finding('temporal_overlap', 'blocking', '实际时间边界不满足声明的先后：' + left + '/' + right)
        required = config.get('required_semantics', [])
        semantics = config.get('semantics', {})
        if not isinstance(required, list) or not isinstance(semantics, dict):
            raise ValueError('语义需为required_semantics列表及semantics对象')
        missing = [key for key in required if not isinstance(key, str) or not isinstance(semantics.get(key), str) or not semantics[key].strip()]
        if missing:
            finding('semantic_unknown', 'needs_evidence', '任务声明的必要语义尚未闭合')
        facts['semantic_fields_declared'] = sorted(semantics)
        facts['scope'] = '仅按提供的ID/组/时间检查；不读取受保护标签，不证明ID真实或不存在近重复'
        return facts

    @staticmethod
    def design(config, inputs, finding):
        mode = config.get('mode', 'exploratory')
        if mode not in {'exploratory', 'confirmatory'}:
            raise ValueError('实验模式需为exploratory/confirmatory')
        for key in ['hypothesis', 'selection_rule']:
            if not isinstance(config.get(key), str) or not config[key].strip():
                finding('missing_' + key, 'needs_evidence', '未固定' + key)
        if not config.get('alternatives'):
            finding('alternatives_unknown', 'needs_evidence', '未记录需区分的解释；已有明确答案时应说明而非编造')
        if config.get('selection_split') == 'test':
            finding('test_selection', 'blocking', '声明用正式test选择候选，不能沿用独立测试含义')
        arms = config.get('arms', [])
        if not isinstance(arms, list) or not 1 <= len(arms) <= 30:
            raise ValueError('实验协议需声明1—30个比较臂')
        names = set()
        budgets, searches = [], []
        for arm in arms:
            if not isinstance(arm, dict) or not isinstance(arm.get('name'), str) or not arm['name'] or arm['name'] in names:
                raise ValueError('比较臂名称需明确且唯一')
            names.add(arm['name'])
            if not finite(arm.get('budget')) or arm['budget'] <= 0 or type(arm.get('search_trials')) is not int or not 1 <= arm['search_trials'] <= 10000:
                raise ValueError('比较臂需声明正预算与真实搜索机会数')
            budgets.append(arm['budget']); searches.append(arm['search_trials'])
        controlled = config.get('claim') == 'controlled_effect'
        if len(set(searches)) > 1:
            finding('unequal_opportunity', 'blocking' if controlled else 'warning', '各臂搜索机会不同，不能直接解释成受控单因素效果')
        if len(set(budgets)) > 1:
            if config.get('study_type') == 'duration':
                finding('duration_budget_difference', 'info', '训练时长是本研究的变化因素；预算不同本身不使比较无效，仍须匹配调度前缀及选择机会')
            else:
                finding('unequal_opportunity', 'blocking' if controlled else 'warning', '各臂训练预算不同，不能直接解释成其他单因素的受控效果')
        factors = config.get('changed_factors', [])
        if not isinstance(factors, list):
            raise ValueError('changed_factors需为列表')
        if controlled and len(factors) != 1:
            finding('confounded_change', 'blocking', '受控单因素主张需要明确一个变化因素')
        duration = None
        if config.get('study_type') == 'duration':
            schedules = config.get('learning_rate_schedules', {})
            if not isinstance(schedules, dict) or set(schedules) != names:
                finding('schedule_prefix_unknown', 'needs_evidence', '训练时长比较尚未固定各臂学习率序列；不能把更长轨迹的最高值当最终epoch退化检验')
            else:
                sequences = [inputs.json(ref) for ref in schedules.values()]
                if any(not isinstance(seq, list) or not seq or len(seq)>10000 or any(not finite(v) or v<0 for v in seq) for seq in sequences):
                    raise ValueError('学习率序列需为有界的非负有限数值列表')
                length = min(map(len,sequences))
                matched = all(seq[:length]==sequences[0][:length] for seq in sequences)
                duration = {'matched_prefix_length':length,'prefix_equal':matched,'scope':'冻结的序列约定；实际执行仍需学习率日志核对'}
                if not matched:
                    finding('schedule_prefix_mismatch', 'blocking' if controlled else 'warning', '时长之外学习率前缀也不同，不能解释为纯训练时长效应')
        if mode == 'confirmatory' and config.get('independent_confirmation') is not True:
            finding('search_is_not_confirmation', 'blocking', '确认性主张没有独立确认约定，最佳搜索结果不能代替确认')
        if mode == 'confirmatory':
            finding('confirmation_needs_receipts', 'needs_evidence', '独立确认声明仍须由后续实际运行、锁定分析与测试访问边界核实')
        baseline = config.get('baseline_files', [])
        if not isinstance(baseline, list) or len(baseline) > 20:
            raise ValueError('基线文件需为有界列表')
        for name in baseline:
            inputs.bytes(name)
        if len(arms) > 1 and not baseline:
            finding('baseline_identity_unknown', 'needs_evidence', '未固定基线源码/配置/评价或既有结果身份')
        return {'mode': mode, 'claim': config.get('claim', 'method_comparison'), 'arms': arms,
                'baseline_files': baseline, 'changed_factors': factors, 'selection_split': config.get('selection_split', 'unknown'),
                'duration_comparison':duration}

    @staticmethod
    def training(config, inputs, finding):
        rows = inputs.json(config.get('history'))
        if not isinstance(rows, list) or not rows or len(rows) > 10000:
            raise ValueError('训练history需为1—10000条实际观测')
        values = {}
        for key in ['step', 'train_loss', 'validation_loss', 'metric']:
            path = config.get(key)
            if path is None:
                continue
            seq = []
            for row in rows:
                try: value = pointer(row, path)
                except (KeyError, IndexError, TypeError): value = None
                if not finite(value):
                    finding('nonfinite_' + key, 'needs_evidence', key + '出现缺失或非有限值；保留异常位置，先做工程/数值诊断')
                    seq.append(None)
                else: seq.append(value)
            valid = [value for value in seq if value is not None]
            values[key] = {'first': seq[0], 'last': seq[-1], 'observed': len(valid),
                           'increases': sum(a < b for a, b in zip(seq, seq[1:]) if a is not None and b is not None),
                           'decreases': sum(a > b for a, b in zip(seq, seq[1:]) if a is not None and b is not None)}
            if key == 'step' and any(a >= b for a, b in zip(valid, valid[1:])):
                finding('clock_not_monotonic', 'needs_evidence', '记录的更新时钟非单调；先区分日志重复、恢复语义与优化器真实更新')
        if not values:
            finding('no_observation_mapping', 'needs_evidence', '未声明真实history中步数/损失/指标的位置')
        train, valid = values.get('train_loss'), values.get('validation_loss')
        if train and valid and all(finite(x) for x in [train['first'], train['last'], valid['first'], valid['last']]) and train['last'] < train['first'] and valid['last'] > valid['first']:
            finding('diverging_endpoints', 'warning', '训练/验证端点分离是线索；须结合完整曲线、分类指标与配对复核，不证明过拟合或提前停止有效')
        return {'observations': len(rows), 'series': values, 'checkpoint_readiness': '未检查权重与优化器状态；history不证明可恢复或能复评指定epoch'}

    @staticmethod
    def engineering(config, inputs, finding):
        files = config.get('python_files', [])
        if not isinstance(files, list) or not 1 <= len(files) <= 20:
            raise ValueError('工程检查需声明1—20个实际Python文件')
        results = []
        entries = config.get('entrypoints', {})
        if not isinstance(entries, dict) or set(entries) - set(files):
            raise ValueError('入口声明必须属于本次Python文件')
        for name in files:
            body = inputs.bytes(name)
            try:
                tree = ast.parse(body, filename=name)
                symbols = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                missing = set(entries.get(name, [])) - symbols
                if missing:
                    finding('missing_entrypoint', 'blocking', name + '缺少声明入口：' + ', '.join(sorted(missing)))
                results.append({'path': name, 'syntax': 'valid', 'missing_entrypoints': sorted(missing)})
            except SyntaxError as exc:
                finding('syntax_error', 'blocking', name + ':' + str(exc.lineno) + '语法无效')
                results.append({'path': name, 'syntax': 'invalid', 'line': exc.lineno})
        return {'sources': results, 'scope': '仅静态语法/符号，不执行源码，不证明loader/张量/梯度/恢复/部署黄金场景通过'}

    def evidence(self, plan, finding):
        runs = self.store.rows('SELECT id,state,directory,receipt FROM research_runs WHERE plan_id=? ORDER BY rowid', (plan['id'],))
        cloud = self.store.rows("SELECT id,state FROM company_training WHERE plan_id=? AND state<>'prepared' ORDER BY rowid", (plan['id'],)) if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'") else []
        attempts = []
        for run in runs:
            collection = self.store.evidence.summary('local', run['id'])
            if collection and collection.get('analysis_ready'):
                try:self.store.evidence.verify('local',run['id'])
                except (OSError,ValueError):finding('collection_integrity','needs_evidence','归档原件缺失或摘要不一致，先恢复证据')
            attempts.append({'id': run['id'], 'state': run['state'], 'receipt_digest': sha((run['receipt'] or '').encode()), 'collection': collection})
            if run['state'] in {'requested', 'running', 'unknown'}:
                finding('unresolved_attempt', 'needs_evidence', '存在未决运行，不能按已有成功子集给出完整结论')
            if not collection or not collection.get('analysis_ready') or collection.get('analysis_scope') == 'failure_diagnosis':
                finding('attempt_not_scientific_evidence', 'needs_evidence', '有尝试尚不具备模型效果证据；失败需保留并另作诊断')
        if cloud:
            finding('cloud_scope', 'needs_evidence', '云尝试须继续核对原平台收据和RunEvidence，不能由平台终态认证模型效果')
        if not runs and not cloud:
            finding('no_runs', 'needs_evidence', '本方案没有实际运行；只能评议协议或引用明确的既有证据')
        comparisons = self.store.rows('SELECT id FROM research_comparisons WHERE plan_id=? ORDER BY rowid', (plan['id'],)) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_comparisons'") else []
        checked = []
        from .comparison import read_report
        for entry in comparisons:
            report = read_report(self.store, plan['project_id'], entry['id'])
            checked.append({key: report.get(key) for key in ['id', 'state', 'rules_passed', 'targets_met', 'prospective']})
            if report.get('state') == 'not_comparable' or report.get('rules_passed') is False:
                finding('comparison_not_supporting', 'warning', '实际核验包含不可比或未满足原条件的结果，不能只报告上涨的指标')
        return {'attempts': attempts, 'cloud_attempts': cloud, 'comparisons': checked,
                'review_lenses': ['领域/几何语义', '泄漏', '基线公平性', '统计有效性', '研究部署一致性'],
                'scientific_outcome': '未自动判定；先解释实际反证、统计支持和适用边界'}

    def read_report(self, sid, id, verify=True):
        session = self.store.session(sid)
        row = self.store.one('SELECT * FROM research_method_reports WHERE id=? AND project_id=?', (id, session['project_id']))
        if not row or sha(row['body'].encode()) != row['digest'] or id != 'method:' + row['digest']:
            raise ValueError('内置方法报告不存在、跨项目或内容已改变')
        value = json.loads(row['body'])
        if verify:
            from .research import plan_digest
            plan = self.plan(sid, value['plan_id'])
            if value['plan_digest'] != plan_digest(plan):
                raise ValueError('方法报告所绑定的方案已改变')
            inputs = Inputs(self.store, plan)
            for ref in value['inputs']:
                if ref['path'] not in inputs.refs:inputs.runtime(ref['path'])
                if inputs.refs[ref['path']]!=ref:
                    raise ValueError('方法报告的原输入身份与当前来源不一致')
                inputs.bytes(ref['path'])
            if value['module'] == 'evidence':
                # A new attempt/comparison must not inherit an old completeness assessment.
                fresh = self.check(sid, 'evidence', plan['id'])
                if fresh['report_id'] != id:
                    raise ValueError('研究尝试/核验状态已变化，重新读取最新证据评审报告')
        return {'report_id': id, **value}

    def preflight(self, sid, plan, purpose='fit'):
        from .study_integrity import preflight as study_preflight
        study_preflight(self.store,sid,plan,purpose)
        source = json.loads(plan['source'])
        if not source.get('research_protocol'):
            return []  # Historical/unstructured plans retain their original contract.
        inputs = Inputs(self.store, plan)
        protocol = inputs.json(source['research_protocol']['path'])
        reports = [self.check(sid, module, plan['id']) for module in ['data', 'design', 'engineering'] if module in protocol]
        blocking = [finding['message'] for report in reports for finding in report['findings'] if finding['level'] == 'blocking']
        if blocking:
            raise ValueError('内置研究方法发现执行冲突：' + '；'.join(blocking))
        return [report['report_id'] for report in reports]


def finite(value):
    try:return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:return False


TOOL = {'name': 'research_method', 'type': 'function',
        'description': '内置研究模块：read按需读方法；check从当前方案冻结文件/真实运行生成有界报告；report读原报告。不是Skill加载，不运行训练，不授予权限，不认证科学正确。',
        'inputSchema': {'type': 'object', 'properties': {
            'operation': {'type': 'string', 'enum': ['read', 'check', 'report']},
            'module': {'type': 'string', 'enum': list(MODULES)}, 'plan_id': {'type': 'string'}, 'report_id': {'type': 'string'},
            'artifact':{'type':'string','description':'仅training：本方案原运行收据中声明的实际history路径'}},
            'required': ['operation'], 'additionalProperties': False}}
