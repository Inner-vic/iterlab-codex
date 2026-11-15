import json
import time
import threading
import tempfile
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

from lab.store import Store
from lab.kernel import Kernel
from lab.transport import RPCError, RPCUnknown


class FakeProcess:
    pid = 123
    def poll(self):
        return None


class FakeRPC:
    def __init__(self, command, cwd, on_event, on_request, on_exit):
        self.process, self.generation = FakeProcess(), 'generation-1'
        self.calls, self.replies = [], []
        self.on_event, self.on_exit, self.unknown = on_event, on_exit, False
        self.closed = False
        self.turns = [{'id': 'original-turn', 'status': 'completed', 'items': []}]
    def start(self):
        return {'userAgent': 'fixture'}
    def request(self, method, params, timeout=60):
        self.calls.append((method, params))
        if method == 'model/list':
            return {'data': [{'id': model, 'model': model, 'supportedReasoningEfforts':[{'reasoningEffort': e} for e in ['low','medium','high']]} for model in ['gpt-6-astra','gpt-6.1-sol','azure-gpt-6-astra','azure-gpt-6_1-sol']]}
        if method=='skills/extraRoots/set':
            self.skill_roots=params['extraRoots']
            return {}
        if method=='skills/list':
            return {'data':[{'cwd':params['cwds'][0],'skills':[{'name':'sdm-research','path':str(Path(self.skill_roots[0])/'sdm-research/SKILL.md'),'enabled':True}],'errors':[]}]}
        if method == 'thread/start':
            return {'thread': {'id': 'native-thread', 'turns': []}}
        if method == 'thread/resume':
            return {'thread': {'id': params['threadId'], 'turns': []}}
        if method == 'thread/read':
            return {'thread': {'id': params['threadId'], 'turns': self.turns}}
        if method == 'thread/turns/list':
            return {'data': list(reversed(self.turns))}
        if method == 'turn/start':
            if self.unknown:
                raise RPCUnknown('lost acknowledgement')
            self.on_event({'method': 'turn/started', 'params': {'threadId': params['threadId'], 'turn': {'id': 'turn-1'}}})
            return {'turn': {'id': 'turn-1'}}
        if method == 'turn/steer':
            return {'turnId': 'turn-1'}
        return {}
    def respond(self, request_id, result=None, error=None):
        self.replies.append((request_id, result, error))
    def close(self):
        self.closed = True


class BarrierRPC(FakeRPC):
    """A sent start can block while notifications/late receipts keep flowing."""
    def __init__(self, *args):
        super().__init__(*args)
        self.start_entered = threading.Event()
        self.release_start = threading.Event()
        self.interrupted = threading.Event()
        self.reply_sent = threading.Event()
        self.receipt_callback = None
        self.start_result = {'turn': {'id': 'original-turn'}}
        self.start_failure = None
        self.interrupt_failure = None
        self.interrupt_threads = []
        self.start_params = None

    def request_tracked(self, method, params, on_receipt, timeout=60):
        if method != 'turn/start':
            result = self.request(method, params, timeout)
            on_receipt({'result': result}, False)
            return result
        self.calls.append((method, params))
        self.start_params = params
        self.receipt_callback = on_receipt
        self.start_entered.set()
        if not self.release_start.wait(3):
            raise RPCUnknown('fixture start barrier expired')
        if self.start_failure:
            raise self.start_failure
        on_receipt({'result': self.start_result}, False)
        self.reply_sent.set()
        return self.start_result

    def request(self, method, params, timeout=60):
        if method == 'turn/interrupt':
            self.calls.append((method, params))
            self.interrupt_threads.append(threading.current_thread().name)
            self.interrupted.set()
            if self.interrupt_failure:
                raise self.interrupt_failure
            return {}
        return super().request(method, params, timeout)

    def emit_start(self, turn_id='original-turn', echo=None):
        turn = {'id': turn_id}
        if echo:
            turn['clientUserMessageId'] = echo
        self.on_event({'method': 'turn/started', 'params': {'threadId': 'native-thread', 'turn': turn}})

    def late_reply(self, turn_id='original-turn', error=None):
        packet = {'error': error} if error else {'result': {'turn': {'id': turn_id}}}
        self.receipt_callback(packet, True)

    def close(self):
        self.release_start.set()
        super().close()


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='iterlab-core-')
        self.test_root = Path(self.directory.name)
        self.store = Store(self.test_root)
        self.project = self.store.create_project('test')
        self.session = self.store.create_session(self.project['id'], 'codex')
        self.kernel = Kernel(self.store, FakeRPC)
        self.kernel.settings.command = lambda *args: ['fixture']
        self.kernel.settings.verify_channel = lambda: None
        self.kernel.settings.value['channel'] = 'codex'
    def tearDown(self):
        try:
            self.kernel.close()
        finally:
            self.store.close()
            self.directory.cleanup()
    def wait(self, predicate):
        for _ in range(100):
            if predicate():
                return
            time.sleep(.01)
        self.fail('fixture deadline expired')
    def record(self, **extra):
        return self.store.add_record(self.session['id'], {'kind': 'plan', 'title': '方案', 'content': '完整内容', **extra}, {'origin': 'test'})
    def test_immutable_plan_versions_and_chain(self):
        first, second = self.record(), self.record(content='修订内容')
        self.assertEqual((first['version'], second['version']), ('1.0', '1.0.0'))
        self.assertEqual(second['parent_id'], first['id'])
        self.assertEqual(self.store.one('SELECT content FROM records WHERE id=?', (first['id'],))['content'], '完整内容')
        graph = self.store.graph(self.project['id'])
        membership=[e for e in graph['edges'] if e['kind']=='contains_plan']
        self.assertEqual({e['target'] for e in membership},{first['id'],second['id']})
        self.assertEqual(len({e['source'] for e in membership}),1)
        self.assertFalse(any(e['source']==first['id'] and e['target']==second['id'] and e['primary'] for e in graph['edges']))
    def test_cross_project_parent_and_path_escape_rejected(self):
        first = self.record()
        other = self.store.create_project('other')
        other_session = self.store.create_session(other['id'], 'codex')
        with self.assertRaises(ValueError):
            self.store.add_record(other_session['id'], {'kind':'result','title':'x','content':'x','parent_id':first['id']}, {})
        with self.assertRaises(ValueError):
            self.record(evidence=['../../outside.txt'])
    def test_nonexistent_evidence_and_unjustified_branch_rejected(self):
        with self.assertRaises(ValueError):
            self.record(evidence=['not-found.json'])
        with self.assertRaises(ValueError):
            self.record(kind='branch')
    def test_evidence_hash_and_dedupe(self):
        (Path(self.project['workspace'])/'metrics.json').write_text('{"score":0.5}')
        record = {'kind':'result','title':'result','content':'observed','evidence':['metrics.json']}
        a = self.store.add_record(self.session['id'], record, {}, 'same-call')
        b = self.store.add_record(self.session['id'], record, {}, 'same-call')
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(len(json.loads(a['evidence'])[0]['sha256']), 64)
    def test_draft_revision_prevents_silent_overwrite(self):
        sid = self.session['id']
        self.store.draft(sid, 'first', 0)
        conflict = self.store.draft(sid, 'second-tab', 0)
        self.assertTrue(conflict['conflict'])
        self.assertEqual(conflict['text'], 'first')
    def test_native_loop_and_idempotent_message_delivery(self):
        sid = self.session['id']
        from lab.sdm import SDM
        self.kernel.sdm=SDM(self.store,default_mode='native_skill')
        self.kernel.send(sid, 'run task', delivery_id='same')
        self.wait(lambda: self.store.one("SELECT state FROM deliveries WHERE id='same'")['state']=='accepted')
        self.kernel.send(sid, 'run task', delivery_id='same')
        calls = self.kernel.rpc.calls
        self.assertEqual(len([m for m,p in calls if m=='turn/start']), 1)
        params = next(p for m,p in calls if m=='thread/start')
        self.assertEqual(params['sandbox'], 'workspace-write')
        self.assertIn('dynamicTools', params)
        self.assertNotIn('outputSchema', next(p for m,p in calls if m=='turn/start'))
        inputs=next(p for m,p in calls if m=='turn/start')['input']
        self.assertEqual([item['type'] for item in inputs],['text','text','skill'])
        self.assertIn('Planner–Executor',inputs[0]['text'])
        self.assertIn('iterlab-experiment/v1',inputs[0]['text'])
        self.assertEqual(self.store.one("SELECT json_extract(payload,'$.revision') AS revision FROM events WHERE session_id=? AND kind='research.native_tool_transport_bound' ORDER BY seq DESC LIMIT 1",(sid,))['revision'],'iterlab-native-transport/v1')
        self.kernel.save_item(sid,'turn-1',{'id':'native-user-1','type':'userMessage','content':[{'type':'text','text':'$sdm-research\nrun task'},{'type':'skill','name':'sdm-research'}]},'completed')
        self.assertEqual(len(self.store.rows("SELECT * FROM messages WHERE session_id=? AND role='user' AND kind='message'",(sid,))),1)
    def test_builtin_contract_is_attached_to_native_thread_without_skill_input(self):
        sid=self.session['id']
        self.kernel.send(sid,'研究问题',delivery_id='builtin')
        self.wait(lambda:self.store.one("SELECT state FROM deliveries WHERE id='builtin'")['state']=='accepted')
        calls=self.kernel.rpc.calls
        self.assertFalse(any(method.startswith('skills/') for method,params in calls))
        binding=self.kernel.sdm.binding(sid)
        self.assertEqual(binding['mode'],'builtin')
        self.assertIn(Path(binding['path']).read_text(encoding='utf-8'),
                      next(p for m,p in calls if m=='thread/start')['developerInstructions'])
        self.assertEqual(next(p for m,p in calls if m=='turn/start')['input'][-1],{'type':'text','text':'研究问题'})
    def test_pause_blocks_background_but_fresh_user_message_keeps_conversation_usable(self):
        sid=self.session['id']
        self.kernel.load_thread(sid)
        self.kernel.interrupt(sid)
        with self.assertRaises(ValueError):self.kernel.send(sid,'后台通知',delivery_id='bg',origin='background')
        self.assertEqual(self.store.rows("SELECT id FROM deliveries WHERE id='bg'"),[])
        self.kernel.send(sid,'先讨论结果',delivery_id='human')
        self.wait(lambda:self.store.one("SELECT state FROM deliveries WHERE id='human'")['state']=='accepted')
        self.assertFalse(self.kernel.research.workflow.paused(sid))
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.kernel.interrupt(sid)
        self.kernel.send(sid,'先讨论结果',delivery_id='human')
        self.assertTrue(self.kernel.research.workflow.paused(sid),'重放旧请求不能解除新暂停')
    def test_unknown_delivery_not_replayed(self):
        sid = self.session['id']
        self.kernel.load_thread(sid)
        self.kernel.rpc.unknown = True
        self.kernel.send(sid, 'run', delivery_id='lost')
        self.wait(lambda: self.store.one("SELECT state FROM deliveries WHERE id='lost'")['state']=='unknown')
        self.kernel.drain(sid)
        self.assertEqual(len([m for m,p in self.kernel.rpc.calls if m=='turn/start']), 1)
        self.kernel.restore(sid)
        self.assertEqual(len([m for m,p in self.kernel.rpc.calls if m=='turn/start']), 1)
    def test_queue_then_native_steer_and_interrupt(self):
        sid = self.session['id']
        self.kernel.send(sid, 'first', delivery_id='first')
        self.wait(lambda: self.store.session(sid)['active_turn']=='turn-1')
        self.kernel.send(sid, 'queued', delivery_id='q')
        self.kernel.send(sid, 'guidance', 'steer', 's')
        self.wait(lambda: any(m=='turn/steer' for m,p in self.kernel.rpc.calls))
        self.assertEqual(self.store.one("SELECT state FROM deliveries WHERE id='q'")['state'], 'queued')
        self.kernel.interrupt(sid)
        self.assertEqual(self.store.one("SELECT state FROM deliveries WHERE id='q'")['state'], 'cancelled')
        self.wait(lambda: any(m == 'turn/interrupt' for m, p in self.kernel.rpc.calls))
    def test_compact_delegates_to_native_compaction(self):
        sid = self.session['id']
        self.kernel.send(sid, '/compact', delivery_id='compact')
        self.wait(lambda: self.store.one("SELECT state FROM deliveries WHERE id='compact'")['state']=='accepted')
        self.assertTrue(any(m=='thread/compact/start' for m,p in self.kernel.rpc.calls))
        self.assertFalse(any(m=='turn/start' for m,p in self.kernel.rpc.calls))
    def test_approval_exact_generation_and_once_only(self):
        sid = self.session['id']
        self.kernel.load_thread(sid)
        self.kernel.on_request({'id': 7, 'method':'item/commandExecution/requestApproval', 'params':{'threadId':'native-thread','command':'python train.py'}}, self.kernel.rpc)
        approval = self.store.one("SELECT * FROM approvals WHERE state='pending'")
        self.assertIsNotNone(approval)
        self.kernel.answer(approval['id'], {'decision':'decline'})
        self.assertEqual(self.kernel.rpc.replies[-1][1], {'decision':'decline'})
        with self.assertRaises(ValueError):
            self.kernel.answer(approval['id'], {'decision':'accept'})
        self.kernel.on_request({'id':8,'method':'item/fileChange/requestApproval','params':{'threadId':'native-thread'}}, self.kernel.rpc)
        old = self.store.one("SELECT * FROM approvals WHERE state='pending'")
        self.kernel.rpc.generation='generation-2'
        with self.assertRaises(ValueError):
            self.kernel.answer(old['id'], {'decision':'accept'})
    def test_native_research_tool_records_actual_call_provenance(self):
        self.kernel.load_thread(self.session['id'])
        packet={'id':9,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':'turn-1','callId':'call-1','tool':'research_record','arguments':{'kind':'alignment','title':'共识','content':'真实边界'}}}
        self.kernel.on_request(packet,self.kernel.rpc)
        self.kernel.on_request(packet,self.kernel.rpc)
        self.assertEqual(len(self.store.rows('SELECT * FROM records')),1)
        self.assertEqual(json.loads(self.store.one('SELECT source FROM records')['source'])['call_id'],'call-1')
    def test_native_company_context_returns_recipe_contract_without_extra_tool_or_probe(self):
        sid=self.session['id'];self.kernel.load_thread(sid)
        self.store.execute("UPDATE sessions SET channel='ept' WHERE id=?",(sid,))
        packet={'id':910,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':'turn-1',
            'callId':'context-contract','tool':'company_context','arguments':{}}}
        with patch.object(self.kernel.company,'runner',side_effect=AssertionError('No SDK or model for contract')):
            self.kernel.on_request(packet,self.kernel.rpc)
        result=self.kernel.rpc.replies[-1][1]
        self.assertTrue(result['success'],result)
        body=json.loads(result['contentItems'][0]['text'])
        self.assertEqual(body['component_recipe_contract']['shapes']['image'],['uid','version'])
        self.assertEqual(self.store.rows('SELECT * FROM company_training'),[])

    def test_native_write_returns_actionable_shape_failure_without_fitting_or_hiding_file(self):
        self.kernel.load_thread(self.session['id'])
        packet={'id':911,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':'turn-1',
            'callId':'write-check','tool':'research_write','arguments':{'path':'config/component_config.json','text':'{"schema":1}'}}}
        self.kernel.on_request(packet,self.kernel.rpc)
        result=self.kernel.rpc.replies[-1][1];self.assertTrue(result['success'],result)
        body=json.loads(result['contentItems'][0]['text'])
        self.assertTrue(body['preparation_check']['syntax_valid'])
        self.assertFalse(body['preparation_check']['valid'])
        self.assertTrue(body['preparation_check']['component_recipe']['errors'])
        self.assertEqual(self.store.safe_path(self.project['id'],body['path']).read_text(),'{"schema":1}')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
    def test_native_source_preview_checks_full_file_without_cache_or_execution(self):
        self.kernel.load_thread(self.session['id'])
        workspace=Path(self.project['workspace'])
        (workspace/'review.py').write_text('# first preview line\n'+'\n'*120+'if True\n    pass\n')
        packet={'id':912,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':'turn-1',
            'callId':'source-check','tool':'research_snapshot','arguments':{'file_path':'review.py','line_count':1}}}
        self.kernel.on_request(packet,self.kernel.rpc)
        response=self.kernel.rpc.replies[-1][1];self.assertTrue(response['success'],response)
        body=json.loads(response['contentItems'][0]['text'])
        self.assertEqual(body['text'].splitlines(),['# first preview line'])
        self.assertFalse(body['preparation_check']['valid'])
        self.assertGreater(body['preparation_check']['error']['line'],120)
        self.assertEqual(body['preparation_check']['source_sha256'],body['sha256'])
        self.assertFalse((workspace/'__pycache__').exists())
    def test_model_change_applies_next_turn_without_restarting_active_runtime(self):
        sid=self.session['id']
        self.kernel.load_thread(sid)
        rpc=self.kernel.rpc
        self.store.patch_session(sid,status='running',active_turn='original-turn')
        self.kernel.save_settings({'model':'selected-model','effort':'high'})
        self.assertIs(self.kernel.rpc,rpc)
        self.assertEqual(self.store.session(sid)['active_turn'],'original-turn')
        with self.assertRaises(ValueError):
            self.kernel.save_settings({'channel':'ept'})
    def test_company_action_waits_for_exact_native_approval(self):
        sid=self.session['id']
        self.kernel.load_thread(sid)
        self.store.execute("UPDATE sessions SET channel='ept' WHERE id=?",(sid,))
        packet={'id':99,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':'turn-1','callId':'company-1','tool':'company_action','arguments':{'action':'git_branch','branch':'experiment/native'}}}
        with patch.object(self.kernel.company,'perform',return_value={'state':'completed'}) as perform:
            self.kernel.on_request(packet,self.kernel.rpc)
            self.assertFalse(perform.called)
            approval=self.store.one("SELECT * FROM approvals WHERE state='pending'")
            self.assertEqual(approval['method'],'workbench/companyAction')
            self.kernel.answer(approval['id'],{'decision':'accept'})
            self.assertEqual(perform.call_count,1)
            self.assertTrue(self.kernel.rpc.replies[-1][1]['success'])
            self.assertIn('contentItems',self.kernel.rpc.replies[-1][1])

    def test_native_component_id_with_space_start_fails_before_approval_or_platform(self):
        sid=self.session['id'];self.kernel.load_thread(sid)
        self.store.execute("UPDATE sessions SET channel='ept' WHERE id=?",(sid,))
        packet={'id':101,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':'turn-1','callId':'wrong-start','tool':'company_action','arguments':{'action':'lpai_start','submission_id':'prepared-id'}}}
        with patch.object(self.kernel.company,'perform') as perform,patch.object(self.kernel,'_pending_approval') as approval:
            self.kernel.on_request(packet,self.kernel.rpc)
            perform.assert_not_called();approval.assert_not_called()
        reply=self.kernel.rpc.replies[-1][1]
        self.assertFalse(reply['success'])
        self.assertIn('lpai_submit',reply['contentItems'][0]['text'])
        self.assertEqual(self.store.rows('SELECT * FROM approvals'),[])
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])

    def test_native_exact_delegated_component_submit_still_bypasses_repeat_approval(self):
        sid=self.session['id'];self.kernel.load_thread(sid)
        self.store.execute("UPDATE sessions SET channel='ept' WHERE id=?",(sid,))
        request={'action':'lpai_submit','submission_id':'prepared-id'}
        packet={'id':102,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':'turn-1','callId':'valid-submit','tool':'company_action','arguments':request}}
        authority={'kind':'delegated_research_scope','submission_id':'prepared-id'}
        with patch.object(self.kernel.company.training,'delegated_submission',return_value=authority),patch.object(self.kernel.company,'perform',return_value={'state':'submitted'}) as perform:
            self.kernel.on_request(packet,self.kernel.rpc)
            self.assertEqual(perform.call_args.kwargs['approval_source'],authority)
        self.assertTrue(self.kernel.rpc.replies[-1][1]['success'])
        self.assertEqual(self.store.rows('SELECT * FROM approvals'),[])


class DeliveryReliabilityTests(unittest.TestCase):
    """Isolated barriers test interleavings rather than probabilistic sleeps."""
    setUp = CoreTests.setUp
    tearDown = CoreTests.tearDown
    wait = CoreTests.wait

    def use_barrier(self):
        self.kernel.transport_factory = BarrierRPC
        self.kernel.load_thread(self.session['id'])
        return self.kernel.rpc

    def wait_event(self, event):
        self.assertTrue(event.wait(2), 'fixture event deadline expired')

    def row(self, delivery_id):
        return self.store.one('SELECT * FROM deliveries WHERE id=?', (delivery_id,))

    def pending_start(self, delivery_id='pending'):
        rpc = self.use_barrier()
        self.kernel.send(self.session['id'], 'same text is not an identity', delivery_id=delivery_id)
        self.wait_event(rpc.start_entered)
        self.addCleanup(rpc.release_start.set)
        return rpc

    def unknown_start(self, delivery_id='lost'):
        rpc = self.pending_start(delivery_id)
        rpc.start_failure = RPCUnknown('original lost acknowledgement')
        rpc.release_start.set()
        self.wait(lambda: self.row(delivery_id)['state'] == 'unknown')
        return rpc

    def complete(self, turn_id='original-turn', status='completed'):
        self.kernel.on_event({'method': 'turn/completed', 'params': {'threadId': 'native-thread', 'turn': {'id': turn_id, 'status': status}}})

    def test_pending_start_interrupt_records_before_turn_and_reader_never_waits(self):
        sid = self.session['id']
        rpc = self.pending_start()
        self.assertEqual(self.row('pending')['state'], 'requested')
        self.assertIsNone(self.store.session(sid)['active_turn'])
        response = self.kernel.interrupt(sid)
        self.assertTrue(response['requested'])
        intent = response['delivery_controls']['interrupt_intents'][0]
        self.assertEqual((intent['delivery_id'], intent['state'], intent['turn_id']), ('pending', 'pending', None))
        reader_returned = threading.Event()
        def reader():
            rpc.emit_start()
            reader_returned.set()
        thread = threading.Thread(target=reader, name='fixture-reader')
        thread.start(); self.wait_event(reader_returned); thread.join(2)
        self.wait_event(rpc.interrupted)
        self.assertNotIn('fixture-reader', rpc.interrupt_threads)
        self.assertEqual([p for m, p in rpc.calls if m == 'turn/interrupt'], [{'threadId': 'native-thread', 'turnId': 'original-turn'}])
        rpc.release_start.set(); self.wait_event(rpc.reply_sent)
        self.wait(lambda: self.row('pending')['state'] == 'accepted')
        self.assertTrue(self.kernel.delivery_control.barrier(sid), 'interrupt ACK is not completion')
        self.kernel.interrupt(sid)
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/interrupt']), 1)
        self.complete()
        self.assertFalse(self.kernel.delivery_control.barrier(sid))
        self.assertEqual(self.store.one('SELECT state FROM delivery_interrupt_intents')['state'], 'completed')

    def test_pending_start_ack_without_started_interrupts_exact_receipt_turn(self):
        rpc = self.pending_start()
        self.kernel.interrupt(self.session['id'])
        rpc.release_start.set(); self.wait_event(rpc.reply_sent); self.wait_event(rpc.interrupted)
        self.assertEqual([p['turnId'] for m, p in rpc.calls if m == 'turn/interrupt'], ['original-turn'])
        self.assertEqual(self.store.session(self.session['id'])['active_turn'], 'original-turn')

    def test_interrupt_can_target_accepted_receipt_before_active_projection(self):
        sid = self.session['id']; self.kernel.load_thread(sid); rpc = self.kernel.rpc
        self.kernel.send(sid, 'original', delivery_id='accepted')
        self.wait(lambda: self.row('accepted')['state'] == 'accepted')
        self.store.patch_session(sid, status='idle', active_turn=None)
        self.kernel.interrupt(sid)
        self.wait(lambda: any(m == 'turn/interrupt' for m, p in rpc.calls))
        self.assertEqual([p for m, p in rpc.calls if m == 'turn/interrupt'], [{'threadId': 'native-thread', 'turnId': 'turn-1'}])
        self.assertEqual(self.store.one('SELECT delivery_id,turn_id FROM delivery_interrupt_intents'), {'delivery_id': 'accepted', 'turn_id': 'turn-1'})
        self.complete('turn-1')
        self.assertFalse(self.kernel.delivery_control.barrier(sid))

    def test_late_start_and_ack_do_not_replay_or_clear_unknown(self):
        sid = self.session['id']; rpc = self.unknown_start()
        original = self.row('lost')['error']
        self.kernel.interrupt(sid)
        rpc.emit_start()
        self.wait_event(rpc.interrupted)
        rpc.late_reply()
        self.assertEqual((self.row('lost')['state'], self.row('lost')['error']), ('unknown', original))
        rpc.turns = [{'id': 'original-turn', 'status': 'inProgress', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual((self.row('lost')['state'], self.row('lost')['turn_id'], self.row('lost')['error']), ('accepted', 'original-turn', original))
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/interrupt']), 1)
        audit = json.loads(self.store.one("SELECT payload FROM events WHERE kind='delivery.reconciled'")['payload'])
        self.assertEqual(audit['basis'], 'late_ack_receipt')
        self.assertEqual(audit['original_error'], original)
        self.complete(); self.assertFalse(self.kernel.delivery_control.barrier(sid))

    def test_unknown_no_echo_text_or_notification_is_not_restore_evidence(self):
        sid = self.session['id']; rpc = self.unknown_start()
        rpc.emit_start()
        rpc.turns = [{'id': 'original-turn', 'status': 'completed', 'items': [{'id': 'native-user', 'type': 'userMessage', 'content': [{'type': 'text', 'text': self.row('lost')['text']}]}]}]
        self.kernel.restore(sid)
        self.assertEqual(self.row('lost')['state'], 'unknown')
        self.assertEqual(self.store.session(sid)['status'], 'unknown')
        with self.assertRaises(ValueError):
            self.kernel.send(sid, 'new text', delivery_id='new')
        self.kernel.drain(sid)
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)
        self.assertEqual(self.store.rows("SELECT * FROM events WHERE kind='delivery.reconciled'"), [])

    def test_restore_exact_echo_is_idempotent_preserves_error_and_not_rejected(self):
        sid = self.session['id']; rpc = self.unknown_start()
        rpc.turns = [{'id': 'original-turn', 'status': 'completed', 'clientUserMessageId': 'lost', 'items': [{'id': 'native-user', 'type': 'userMessage', 'clientUserMessageId': 'lost', 'content': []}]}]
        self.kernel.restore(sid); self.kernel.restore(sid)
        self.assertEqual((self.row('lost')['state'], self.row('lost')['turn_id'], self.row('lost')['error']), ('accepted', 'original-turn', 'original lost acknowledgement'))
        self.assertEqual(len(self.store.rows("SELECT * FROM events WHERE kind='delivery.reconciled'")), 1)
        self.assertEqual(len(self.store.rows("SELECT * FROM messages WHERE session_id=? AND item_id='delivery:lost'", (sid,))), 1)
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)
        stamp = self.row('lost')['created_at']
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', ('rejected', sid, 'rejected', 'queue', 'rejected', None, 'original rejection', stamp, stamp))
        rpc.turns = [{'id': 'rejected-turn', 'status': 'completed', 'clientUserMessageId': 'rejected', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual((self.row('rejected')['state'], self.row('rejected')['error']), ('rejected', 'original rejection'))

    def test_restore_duplicate_echo_conflicts_keep_original_unknown(self):
        sid = self.session['id']; rpc = self.unknown_start()
        for turns in [
            [{'id': 'turn-a', 'status': 'completed', 'clientUserMessageId': 'lost', 'items': []}, {'id': 'turn-b', 'status': 'completed', 'clientUserMessageId': 'lost', 'items': []}],
            [{'id': 'turn-a', 'status': 'completed', 'items': [{'id': 'u1', 'type': 'userMessage', 'clientUserMessageId': 'lost'}, {'id': 'u2', 'type': 'userMessage', 'clientUserMessageId': 'lost'}]}],
            [{'id': 'turn-a', 'status': 'completed', 'clientUserMessageId': 'lost', 'items': []}, {'id': 'turn-a', 'status': 'completed', 'clientUserMessageId': 'lost', 'items': []}],
        ]:
            with self.subTest(turns=turns):
                rpc.turns = turns; self.kernel.restore(sid)
                self.assertEqual((self.row('lost')['state'], self.row('lost')['error']), ('unknown', 'original lost acknowledgement'))
                self.assertTrue(self.kernel.delivery_control.barrier(sid))
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)
        self.assertEqual(len(self.store.rows("SELECT * FROM events WHERE kind='delivery.reconcile_conflict'")), 3)

    def test_restore_conflicting_receipt_and_echo_remain_unknown(self):
        sid = self.session['id']; rpc = self.unknown_start()
        rpc.late_reply('receipt-turn')
        rpc.turns = [{'id': 'receipt-turn', 'status': 'completed', 'items': []}, {'id': 'echo-turn', 'status': 'completed', 'clientUserMessageId': 'lost', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual(self.row('lost')['state'], 'unknown')
        self.assertEqual(self.row('lost')['error'], 'original lost acknowledgement')
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)

    def test_restore_local_accepted_receipt_without_echo_can_identify_original(self):
        sid = self.session['id']; self.kernel.load_thread(sid)
        self.kernel.send(sid, 'original', delivery_id='old-accepted')
        self.wait(lambda: self.row('old-accepted')['state'] == 'accepted')
        self.store.execute("UPDATE deliveries SET state='unknown',error='local crash after receipt' WHERE id='old-accepted'")
        self.kernel.rpc.turns = [{'id': 'turn-1', 'status': 'completed', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual((self.row('old-accepted')['state'], self.row('old-accepted')['turn_id'], self.row('old-accepted')['error']), ('accepted', 'turn-1', 'local crash after receipt'))
        self.assertEqual(json.loads(self.store.one("SELECT payload FROM events WHERE kind='delivery.reconciled'")['payload'])['basis'], 'local_accepted_receipt')

    def test_late_rejection_cannot_reclassify_original_unknown(self):
        sid = self.session['id']; rpc = self.unknown_start()
        rpc.late_reply(error={'code': -32603, 'message': 'late rejection'})
        self.kernel.restore(sid)
        self.assertEqual((self.row('lost')['state'], self.row('lost')['error']), ('unknown', 'original lost acknowledgement'))
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)

    def test_restore_releases_only_previously_queued_unsent_work_after_exact_resolution(self):
        sid = self.session['id']; rpc = self.unknown_start()
        stamp = self.row('lost')['created_at']
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', ('waiting', sid, 'never sent queued work', 'queue', 'queued', None, None, stamp, stamp))
        rpc.turns = [{'id': 'original-turn', 'status': 'completed', 'items': []}]
        self.kernel.restore(sid); self.kernel.drain(sid)
        self.assertEqual(self.row('waiting')['state'], 'queued')
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)
        rpc.start_failure = None; rpc.start_result = {'turn': {'id': 'next-turn'}}
        rpc.turns[0]['clientUserMessageId'] = 'lost'
        self.kernel.restore(sid)
        self.wait(lambda: self.row('waiting')['state'] == 'accepted')
        self.assertEqual((self.row('lost')['turn_id'], self.row('waiting')['turn_id']), ('original-turn', 'next-turn'))
        self.assertEqual([params['clientUserMessageId'] for method, params in rpc.calls if method == 'turn/start'], ['lost', 'waiting'])

    def test_missing_native_status_cannot_release_barrier_after_exact_identity(self):
        sid = self.session['id']; rpc = self.unknown_start()
        rpc.turns = [{'id': 'original-turn', 'clientUserMessageId': 'lost', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual(self.row('lost')['state'], 'accepted', 'identity is proven independently of execution status')
        self.assertEqual(self.store.session(sid)['status'], 'unknown', 'missing execution state is not an idle guarantee')
        with self.assertRaises(ValueError): self.kernel.send(sid, 'new', delivery_id='new')
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)

    def test_definite_pending_start_rejection_settles_only_unbound_interrupt(self):
        sid = self.session['id']; rpc = self.pending_start()
        self.kernel.interrupt(sid)
        rpc.start_failure = RPCError('definite start rejection'); rpc.release_start.set()
        self.wait(lambda: self.row('pending')['state'] == 'rejected')
        self.assertEqual(self.row('pending')['error'], 'definite start rejection')
        self.assertEqual(self.store.one('SELECT state FROM delivery_interrupt_intents')['state'], 'completed')
        self.assertFalse(self.kernel.delivery_control.barrier(sid))
        self.assertFalse(any(m == 'turn/interrupt' for m, p in rpc.calls))

    def test_late_ack_alone_after_unknown_interrupts_without_started(self):
        sid = self.session['id']; rpc = self.unknown_start()
        self.kernel.interrupt(sid)
        rpc.late_reply('exact-late-turn'); self.wait_event(rpc.interrupted)
        self.assertEqual([p for m, p in rpc.calls if m == 'turn/interrupt'], [{'threadId': 'native-thread', 'turnId': 'exact-late-turn'}])
        self.assertEqual((self.row('lost')['state'], self.row('lost')['error']), ('unknown', 'original lost acknowledgement'))
        self.assertTrue(self.kernel.delivery_control.barrier(sid))

    def test_pending_steer_keeps_exact_old_turn_and_rejection_does_not_clear_it(self):
        sid = self.session['id']; rpc = self.use_barrier()
        self.store.patch_session(sid, status='running', active_turn='original-turn')
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def steer(method, params, receipt, timeout=60):
            self.assertEqual(method, 'turn/steer')
            rpc.calls.append((method, params)); entered.set()
            self.assertTrue(release.wait(2))
            raise RPCError('definite steer rejection')
        with patch.object(rpc, 'request_tracked', side_effect=steer):
            self.kernel.send(sid, 'guidance', 'steer', 'steer')
            self.wait_event(entered)
            self.kernel.interrupt(sid); self.wait_event(rpc.interrupted)
            release.set(); self.wait(lambda: self.row('steer')['state'] == 'rejected')
        self.assertEqual([p for m, p in rpc.calls if m == 'turn/interrupt'], [{'threadId': 'native-thread', 'turnId': 'original-turn'}])
        self.assertEqual(self.store.session(sid)['active_turn'], 'original-turn')
        self.assertTrue(self.kernel.delivery_control.barrier(sid))
        self.complete(); self.assertFalse(self.kernel.delivery_control.barrier(sid))

    def test_restart_marks_requested_unknown_without_overwriting_original_error(self):
        sid = self.session['id']; stamp = self.session['created_at']
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', ('requested', sid, 'original', 'queue', 'requested', None, 'original persisted error', stamp, stamp))
        self.store.patch_session(sid, status='starting')
        self.kernel.close()
        self.kernel = Kernel(self.store, FakeRPC)
        self.kernel.settings.value['channel'] = 'codex'; self.kernel.settings.command = lambda *a: ['fixture']
        self.assertEqual((self.row('requested')['state'], self.row('requested')['error']), ('unknown', 'original persisted error'))
        self.assertEqual(self.store.session(sid)['status'], 'unknown')
        self.assertTrue(self.kernel.delivery_control.barrier(sid))
        self.assertIsNone(self.kernel.rpc)

    def test_unknown_human_pause_requires_confirmed_reason_and_keeps_barrier(self):
        sid = self.session['id']; rpc = self.unknown_start()
        grants = self.store.rows('SELECT * FROM research_grants')
        before = self.row('lost')
        for confirmed, reason in [(False, '人工暂停'), (1, '人工暂停'), (True, ''), (True, '   '), (True, None)]:
            with self.subTest(confirmed=confirmed, reason=reason), self.assertRaises(ValueError):
                self.kernel.pause_unknown_delivery(sid, 'lost', confirmed, reason)
        self.assertEqual(self.store.rows('SELECT * FROM delivery_human_pauses'), [])
        response = self.kernel.pause_unknown_delivery(sid, 'lost', True, '人工核查原执行，暂不继续')
        self.assertTrue(response['paused']); self.assertFalse(response['replayed'])
        self.kernel.pause_unknown_delivery(sid, 'lost', True, '人工核查原执行，暂不继续')
        self.assertEqual(self.row('lost'), before)
        self.assertEqual(len(self.store.rows('SELECT * FROM delivery_human_pauses')), 1)
        self.assertEqual(len(self.store.rows("SELECT * FROM events WHERE kind='delivery.human_paused'")), 1)
        self.assertTrue(self.kernel.research.workflow.paused(sid)); self.assertTrue(self.kernel.delivery_control.barrier(sid))
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'), grants)
        with self.assertRaises(ValueError): self.kernel.pause_unknown_delivery(sid, 'lost', True, '改写理由')
        stamp = before['created_at']
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', ('rejected', sid, 'x', 'queue', 'rejected', None, 'original', stamp, stamp))
        with self.assertRaises(ValueError): self.kernel.pause_unknown_delivery(sid, 'rejected', True, '人工暂停')
        with self.assertRaises(ValueError): self.kernel.send(sid, '不能重放', delivery_id='replacement')
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/start']), 1)

    def test_unknown_barrier_keeps_authority_policy_and_grant_unchanged(self):
        from lab.research import plan_digest
        sid = self.session['id']
        plan = self.store.add_record(sid, {'kind': 'plan', 'title': 'approved plan', 'content': 'immutable approved scope'}, {})
        self.kernel.research.review(sid, {'plan_id': plan['id'], 'expected_digest': plan_digest(plan), 'decision': 'accept', 'reason': 'fixture approval', 'allow_local': True, 'max_runs': 3, 'max_fits': 1, 'timeout': 5})
        scope = {'objective': 'fixture', 'evaluation': 'fixed', 'data_boundary': 'local fixture', 'allowed_changes': 'test candidate', 'frozen_files': []}
        self.kernel.research.autonomy.enable(sid, plan['id'], plan_digest(plan), scope, 'explicit fixture authority')
        tables = ['research_grants', 'research_authorities', 'research_autonomy_policies', 'research_active_branches']
        original = {table: self.store.rows('SELECT * FROM ' + table) for table in tables}
        rpc = self.unknown_start()
        self.kernel.pause_unknown_delivery(sid, 'lost', True, 'manual investigation without new authority')
        rpc.turns = [{'id': 'original-turn', 'status': 'completed', 'clientUserMessageId': 'lost', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual({table: self.store.rows('SELECT * FROM ' + table) for table in tables}, original)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'), [])
        self.assertEqual(self.row('lost')['error'], 'original lost acknowledgement')

    def test_pending_interrupt_survives_restart_and_only_exact_restore_binds(self):
        sid = self.session['id']; rpc = self.pending_start()
        self.kernel.interrupt(sid)
        rpc.start_failure = RPCUnknown('lost before restart'); rpc.release_start.set()
        self.wait(lambda: self.row('pending')['state'] == 'unknown')
        self.kernel.close()
        self.kernel = Kernel(self.store, FakeRPC)
        self.kernel.settings.value['channel'] = 'codex'; self.kernel.settings.command = lambda *a: ['fixture']
        self.kernel.load_thread(sid)
        self.assertEqual(self.store.session(sid)['status'], 'unknown', 'resume does not erase local unknown')
        self.kernel.rpc.turns = [{'id': 'old-exact-turn', 'status': 'inProgress', 'clientUserMessageId': 'pending', 'items': []}]
        self.kernel.restore(sid)
        self.wait(lambda: any(m == 'turn/interrupt' for m, p in self.kernel.rpc.calls))
        self.assertEqual([p for m, p in self.kernel.rpc.calls if m == 'turn/interrupt'], [{'threadId': 'native-thread', 'turnId': 'old-exact-turn'}])
        self.assertEqual(self.row('pending')['error'], 'lost before restart')
        self.assertFalse(any(m == 'turn/start' for m, p in self.kernel.rpc.calls))
        self.complete('old-exact-turn')
        self.assertFalse(self.kernel.delivery_control.barrier(sid))

    def test_durable_unknown_interrupt_survives_restart_until_exact_terminal_read(self):
        sid = self.session['id']; rpc = self.pending_start()
        rpc.interrupt_failure = RPCUnknown('original interrupt timeout')
        self.kernel.interrupt(sid); rpc.emit_start(); self.wait_event(rpc.interrupted)
        rpc.release_start.set(); self.wait_event(rpc.reply_sent)
        self.wait(lambda: self.store.one('SELECT state FROM delivery_interrupt_intents')['state'] == 'unknown')
        self.kernel.close()
        self.kernel = Kernel(self.store, FakeRPC)
        self.kernel.settings.value['channel'] = 'codex'; self.kernel.settings.command = lambda *a: ['fixture']
        self.kernel.load_thread(sid)
        self.kernel.rpc.turns = [{'id': 'original-turn', 'status': 'inProgress', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual(self.store.one('SELECT state,error FROM delivery_interrupt_intents'), {'state': 'unknown', 'error': 'original interrupt timeout'})
        self.assertFalse(any(m == 'turn/interrupt' for m, p in self.kernel.rpc.calls), 'unknown interrupt must not be retried')
        self.kernel.rpc.turns = [{'id': 'original-turn', 'status': 'completed', 'items': []}]
        self.kernel.restore(sid)
        self.assertEqual(self.store.one('SELECT state,error FROM delivery_interrupt_intents'), {'state': 'completed', 'error': 'original interrupt timeout'})
        self.assertFalse(self.kernel.delivery_control.barrier(sid))
        self.assertFalse(any(m == 'turn/start' for m, p in self.kernel.rpc.calls))

    def test_interrupt_unknown_is_not_retried_or_retargeted_to_new_turn(self):
        sid = self.session['id']; rpc = self.pending_start()
        rpc.interrupt_failure = RPCUnknown('interrupt acknowledgement lost')
        self.kernel.interrupt(sid); rpc.emit_start(); self.wait_event(rpc.interrupted)
        self.wait(lambda: self.store.one('SELECT state FROM delivery_interrupt_intents')['state'] == 'unknown')
        rpc.release_start.set(); self.wait_event(rpc.reply_sent)
        rpc.turns = [{'id': 'original-turn', 'status': 'inProgress', 'items': []}]
        self.kernel.restore(sid); self.kernel.interrupt(sid)
        self.assertEqual(len([m for m, p in rpc.calls if m == 'turn/interrupt']), 1)
        self.complete()
        rpc.emit_start('new-turn')
        rpc.emit_start('original-turn')
        self.assertEqual(self.store.session(sid)['active_turn'], 'new-turn')
        self.assertEqual([p['turnId'] for m, p in rpc.calls if m == 'turn/interrupt'], ['original-turn'])

    def test_completion_before_ack_does_not_resurrect_turn_or_interrupt_it(self):
        sid = self.session['id']; rpc = self.pending_start()
        self.kernel.interrupt(sid)
        self.complete()
        rpc.release_start.set(); self.wait_event(rpc.reply_sent)
        self.wait(lambda: self.row('pending')['state'] == 'accepted')
        self.assertIsNone(self.store.session(sid)['active_turn'])
        self.assertEqual(self.store.session(sid)['status'], 'idle')
        self.assertFalse(any(m == 'turn/interrupt' for m, p in rpc.calls))
        self.assertFalse(self.kernel.delivery_control.barrier(sid))

    def test_inline_compaction_cannot_release_active_turn_or_start_queue(self):
        sid = self.session['id']; self.kernel.load_thread(sid)
        self.store.patch_session(sid, status='running', active_turn='original-turn')
        self.kernel.send(sid, 'next', delivery_id='queued')
        for method in ['thread/compacted', 'item/contextCompaction/completed']:
            self.kernel.on_event({'method': method, 'params': {'threadId': 'native-thread', 'turnId': 'original-turn', 'itemId': 'compact'}})
            self.assertEqual(self.store.session(sid)['active_turn'], 'original-turn')
            self.kernel.drain(sid)
        self.assertEqual(self.row('queued')['state'], 'queued')
        self.assertFalse(any(m == 'turn/start' for m, p in self.kernel.rpc.calls))
        self.complete()
        self.wait(lambda: self.row('queued')['state'] == 'accepted')
        self.assertEqual(len([m for m, p in self.kernel.rpc.calls if m == 'turn/start']), 1)

    def test_explicit_compact_completion_releases_only_explicit_compact(self):
        sid = self.session['id']; self.kernel.load_thread(sid)
        self.kernel.send(sid, '/compact', delivery_id='compact')
        self.wait(lambda: self.row('compact')['state'] == 'accepted')
        self.assertEqual(self.store.session(sid)['status'], 'compacting')
        self.kernel.send(sid, 'next', delivery_id='after-compact')
        self.kernel.on_event({'method': 'thread/compacted', 'params': {'threadId': 'native-thread'}})
        self.wait(lambda: self.row('after-compact')['state'] == 'accepted')
        self.assertEqual(len([m for m, p in self.kernel.rpc.calls if m == 'turn/start']), 1)

    def test_explicit_compact_completion_before_ack_is_not_resurrected(self):
        sid = self.session['id']; self.kernel.load_thread(sid); rpc = self.kernel.rpc
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original_request = rpc.request
        def compact(method, params, timeout=60):
            if method != 'thread/compact/start':
                return original_request(method, params, timeout)
            rpc.calls.append((method, params)); entered.set()
            self.assertTrue(release.wait(2)); return {}
        with patch.object(rpc, 'request', side_effect=compact):
            self.kernel.send(sid, '/compact', delivery_id='compact')
            self.wait_event(entered)
            self.kernel.interrupt(sid)
            self.assertEqual(self.store.rows('SELECT * FROM delivery_interrupt_intents'), [])
            self.kernel.on_event({'method': 'thread/compacted', 'params': {'threadId': 'native-thread'}})
            release.set(); self.wait(lambda: self.row('compact')['state'] == 'accepted')
        self.assertEqual(self.store.session(sid)['status'], 'idle')
        self.assertFalse(self.kernel.delivery_control.barrier(sid))
        self.assertFalse(any(m == 'turn/interrupt' for m, p in rpc.calls))

    def test_same_delivery_id_cannot_change_mode_origin_or_session(self):
        sid = self.session['id']; self.kernel.load_thread(sid)
        self.kernel.send(sid, 'original', delivery_id='same')
        self.wait(lambda: self.row('same')['state'] == 'accepted')
        original = self.row('same')
        with self.assertRaises(ValueError): self.kernel.send(sid, 'original', 'steer', 'same')
        with self.assertRaises(ValueError): self.kernel.send(sid, 'original', 'queue', 'same', origin='background')
        other = self.store.create_session(self.project['id'], 'codex')['id']
        with self.assertRaises(ValueError): self.kernel.send(other, 'original', delivery_id='same')
        self.assertEqual(self.row('same'), original)
        self.assertEqual(self.kernel.send(sid, 'original', delivery_id='same'), original)
        self.assertEqual(len([m for m, p in self.kernel.rpc.calls if m == 'turn/start']), 1)


if __name__ == '__main__':
    unittest.main()
