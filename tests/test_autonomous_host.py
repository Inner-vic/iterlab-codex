"""Managed native-host integration, with scripted RPC decisions, not LLM quality.

Only the protocol peer is fake. The background scheduler, role transitions,
native tool callbacks, immutable records, and two local CPU fits are real.
No production data, model service, SSH, or platform request is used.
"""
import copy
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid

from lab.kernel import Kernel
from lab.research import plan_digest
from lab.roles import DEFAULT_POLICY
from lab.store import Store
from tests.test_core import FakeRPC


ROOT = Path(__file__).resolve().parents[1]


class ScriptedPeer(FakeRPC):
    def __init__(self, command, cwd, on_event, on_request, on_exit):
        super().__init__(command, cwd, on_event, on_request, on_exit)
        self.on_request = on_request
        self.workers = []
        self.stopped = threading.Event()

    def request(self, method, params, timeout=60):
        if method != 'turn/start':
            return super().request(method, params, timeout)
        self.calls.append((method, params))
        turn = 'host-turn-' + str(len(self.workers) + 1)
        self.on_event({'method': 'turn/started', 'params': {
            'threadId': params['threadId'], 'turn': {'id': turn}}})
        worker = threading.Thread(target=self.perform, args=(turn, params),
                                  name='isolated-native-peer')
        self.workers.append(worker)
        worker.start()
        return {'turn': {'id': turn}}

    def perform(self, turn, params):
        self.scenario.perform(self, turn, params)

    def close(self):
        self.stopped.set()
        for worker in self.workers:
            worker.join(10)
        if any(worker.is_alive() for worker in self.workers):
            raise RuntimeError('isolated native peer did not stop')


class AutonomousHostTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(ROOT / '.evidence/tests' / uuid.uuid4().hex)
        self.kernel = Kernel(self.store, ScriptedPeer)
        self.kernel.settings.value.update(channel='codex',
                                         agent_roles=copy.deepcopy(DEFAULT_POLICY))
        self.kernel.settings.command = lambda *args: ['isolated-peer']
        self.pid = self.store.create_project('scripted host integration')['id']
        self.sid = self.store.create_session(self.pid, 'codex')['id']
        self.workspace = Path(self.store.session(self.sid)['workspace'])
        (self.workspace / 'protocol.md').write_text(
            'Synthetic scalar control: fixed samples, one thread; not research evidence.',
            encoding='utf-8')
        (self.workspace / 'train.py').write_text(
            "import json,sys\nw=0.0\n"
            "for _ in range(int(sys.argv[1])):\n"
            " w-=0.01*sum(2*x*(w*x-2*x) for x in [1,2,3])/3\n"
            "json.dump({'loss':sum((w*x-2*x)**2 for x in [1,2,3])/3},"
            "open('metrics.json','w'))\n", encoding='utf-8')
        self.first = self.store.add_record(self.sid, {
            'kind': 'plan', 'title': '固定合成控制初始方案',
            'content': '仅检验宿主续接；优化次数是测试脚本指定，不冒充模型决策。',
            'evidence': ['protocol.md']}, {'origin': 'isolated-fixture'})
        self.kernel.research.review(self.sid, {
            'plan_id': self.first['id'], 'expected_digest': plan_digest(self.first),
            'decision': 'accept', 'reason': '隔离宿主工程验收', 'allow_local': True,
            # Leave capacity unused so this tests autonomous no-progress closure,
            # independently of the dedicated exhausted-budget pause scenario.
            'python': sys.executable, 'max_runs': 3, 'max_fits': 3, 'timeout': 5})
        self.kernel.research.autonomy.enable(self.sid, self.first['id'],
            plan_digest(self.first), {
                'objective': '验证后台续接和正式原生工具',
                'evaluation': '固定合成标量损失', 'data_boundary': '三个标量样本',
                'allowed_changes': '标量优化迭代次数', 'frozen_files': ['protocol.md']},
            '测试操作者显式授权；不涉及生产或真实模型')
        self.plan = self.first
        self.runs = []
        self.errors = []
        self.finished = threading.Event()
        self.proposal_saved = threading.Event()
        self.endpoints = []
        self.tool_number = 0
        self.kernel.ensure('codex')
        self.kernel.rpc.scenario = self

    def tearDown(self):
        self.kernel.close()
        self.store.close()

    def call(self, peer, turn, name, **arguments):
        self.tool_number += 1
        id = 'host-tool-' + str(self.tool_number)
        peer.on_request({'id': id, 'method': 'item/tool/call', 'params': {
            'threadId': 'native-thread', 'turnId': turn, 'callId': id,
            'tool': name, 'arguments': arguments}}, peer)
        reply = next(reply for reply in peer.replies if reply[0] == id)
        self.assertIsNone(reply[2], reply)
        self.assertTrue(reply[1]['success'], reply)
        return json.loads(reply[1]['contentItems'][0]['text'])

    def record(self, peer, turn, kind, title, **extra):
        return self.call(peer, turn, 'research_record', kind=kind, title=title,
            content='脚本化工程用例，不能证明科学判断质量。',
            evidence=['protocol.md'], **extra)['record_id']

    def handoff(self, peer, turn, target, record_id=None, verdict='handoff'):
        return self.call(peer, turn, 'research_handoff', target=target,
            plan_id=self.plan['id'], record_id=record_id or self.plan['id'],
            verdict=verdict, reason='隔离宿主角色交接，沿原授权和原线程')

    def perform(self, peer, turn, params):
        try:
            # Respect the actual acknowledgement boundary. No direct drain,
            # continuation.tick, role dispatch, or policy changes drive the flow.
            deadline = time.monotonic() + 5
            while not peer.stopped.wait(.01):
                delivery = self.store.one('SELECT * FROM deliveries WHERE id=?',
                                         (params['clientUserMessageId'],))
                if delivery and delivery['state'] == 'accepted':
                    break
                if time.monotonic() > deadline:
                    self.fail('native start acknowledgement deadline')
            if peer.stopped.is_set():
                return
            route = self.kernel.roles.current(self.sid, turn)
            self.assertIsNotNone(route)
            role = route['role']
            self.endpoints.append((role, params['model'], params['threadId']))
            if role == 'critic':
                note = self.record(peer, turn, 'decision', '独立审议控制记录',
                                   plan_id=self.plan['id'])
                self.handoff(peer, turn, 'planner', note, 'approve')
            elif role == 'executor':
                outcome = self.call(peer, turn, 'research_run', plan_id=self.plan['id'],
                    script='train.py', files=['train.py', 'protocol.md'],
                    args=[str(10 if not self.runs else 20)], purpose='fit',
                    question='隔离标量拟合能否保持原累计和正式收据',
                    expected_information='仅验证真实进程与宿主回传，不评价研究质量',
                    outputs=['metrics.json'])
                self.assertEqual(outcome['state'], 'completed', outcome)
                self.runs.append(outcome)
                result = self.call(peer, turn, 'research_record', kind='result',
                    title='实际合成拟合反馈', content='读取实际输出，脚本指定下一步。',
                    evidence=[outcome['directory'] + '/metrics.json'],
                    plan_id=self.plan['id'], run_ids=[outcome['run_id']])['record_id']
                self.handoff(peer, turn, 'planner', result)
            elif len(self.runs) == 2:
                self.record(peer, turn, 'review', '最终边界评审',
                    plan_id=self.plan['id'], run_ids=[self.runs[-1]['run_id']],
                    round_outcome='stop_requested',stop_reason='no_progress')
                self.finished.set()
            elif len(self.runs) == 1 and self.plan['id'] == self.first['id']:
                review = self.record(peer, turn, 'review', '第一轮完整证据评审',
                    plan_id=self.plan['id'], run_ids=[self.runs[0]['run_id']],
                    round_outcome='continue')
                autonomy = self.kernel.research.autonomy.status(self.sid)
                candidate = self.record(peer, turn, 'plan', '同范围后继方案',
                    previous_plan_id=self.plan['id'], review_record_id=review,
                    authority_id=autonomy['authority_id'],
                    scope_digest=autonomy['scope_digest'])
                self.plan = self.kernel.research.plan(self.sid, candidate)
                self.proposal_saved.set()
                # Deliberately finish after saving the proposal. Only the real
                # ten-second background scheduler may request its continuation.
            else:
                approved = self.store.one("SELECT id FROM research_role_handoffs "
                    "WHERE plan_id=? AND verdict='approve'", (self.plan['id'],))
                if not approved:
                    self.handoff(peer, turn, 'critic')
                else:
                    if self.plan['id'] != self.first['id']:
                        # Critic publication assigns the candidate's final version;
                        # use its current digest, not the pre-publication draft.
                        self.plan=self.kernel.research.plan(self.sid,self.plan['id'])
                        self.call(peer, turn, 'research_adopt', plan_id=self.plan['id'],
                            expected_digest=plan_digest(self.plan),
                            reason='脚本化后继满足原冻结协议；不新增额度')
                    self.handoff(peer, turn, 'executor')
        except BaseException as exc:
            self.errors.append(repr(exc))
            self.finished.set()
        finally:
            if not peer.stopped.is_set():
                peer.on_event({'method': 'turn/completed', 'params': {
                    'threadId': 'native-thread', 'turn': {'id': turn,
                                                        'status': 'completed'}}})

    def test_background_native_host_reaches_two_rounds_and_stops(self):
        self.kernel.continuation.start()
        # No user message, manual tick, or explicit host step after authorization.
        finished=self.finished.wait(45)
        self.assertTrue(finished, 'background host completion deadline; '+json.dumps({
            'errors':self.errors,'endpoints':self.endpoints,
            'session':{k:self.store.session(self.sid)[k] for k in ['status','active_turn']},
            'scheduler':self.store.one('SELECT * FROM research_scheduler_status WHERE session_id=?',(self.sid,)),
            'pending':self.store.rows("SELECT id,target,state FROM research_role_handoffs WHERE state='pending'")},ensure_ascii=False))
        self.assertEqual(self.errors, [])
        deadline = time.monotonic() + 3
        while self.store.session(self.sid)['active_turn'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.proposal_saved.is_set())
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.kernel.research.status(self.sid)['attempts'],
                         {'runs': 2, 'fits': 2})
        self.assertEqual(self.kernel.research.workflow.publish(self.sid)['state'],
                         'assessment_complete')
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_reviews')['n'], 1)
        grants = self.store.rows('SELECT * FROM research_grants')
        self.assertEqual(len(grants), 1)
        self.assertEqual({r['grant_id'] for r in self.store.rows('SELECT * FROM research_runs')},
                         {grants[0]['id']})
        self.assertEqual([r['version'] for r in self.store.rows(
            "SELECT version FROM records WHERE kind='plan' ORDER BY rowid")], ['1.0', '1.0.0'])
        self.assertEqual(len(self.store.rows("SELECT id FROM records WHERE kind='review'")), 2)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_continuations')), 2)
        self.assertFalse(self.store.rows("SELECT id FROM deliveries "
            "WHERE state<>'accepted'"))
        self.assertFalse(self.store.rows("SELECT delivery_id FROM research_input_origins "
            "WHERE origin IN ('user','review')"))
        self.assertEqual(len(self.endpoints), 10)
        for role, model, thread in self.endpoints:
            self.assertEqual(model, DEFAULT_POLICY['models'][role])
            self.assertEqual(thread, 'native-thread')
        # Allow a complete normal polling interval to catch a stale wakeup after
        # final review. Waiting here creates neither model nor computation.
        before = len(self.endpoints)
        self.assertFalse(self.kernel.rpc.stopped.wait(10.5))
        self.assertEqual(len(self.endpoints), before)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
