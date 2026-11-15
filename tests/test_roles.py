"""Native routing and durable handoff regression; synthetic records, no fitting."""
import copy
import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from lab.store import Store,now
from lab.kernel import Kernel
from lab.roles import DEFAULT_POLICY,validate_policy
from lab.research import plan_digest
from tests.test_core import FakeRPC


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='iterlab-roles-')
        root=Path(self.temp.name)
        self.store=Store(root);self.pid=self.store.create_project('role regression')['id']
        self.sid=self.store.create_session(self.pid,'codex')['id']
        self.kernel=Kernel(self.store,FakeRPC);self.r=self.kernel.research;self.roles=self.kernel.roles
        self.kernel.settings.value.update(channel='codex',agent_roles=copy.deepcopy(DEFAULT_POLICY))
        self.kernel.settings.command=lambda *a:['fixture']
        self.catalog=[{'id':m,'model':m,'supportedReasoningEfforts':[{'reasoningEffort':'high'}]}
                      for m in set(DEFAULT_POLICY['models'].values())]
        self.plan=self.record('plan')
        self.r.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'accept','reason':'isolated role regression','execution':'local','allow_local':True,
            'python':sys.executable,'max_runs':2,'max_fits':2,'timeout':3})
    def tearDown(self):
        self.kernel.close();self.store.close();self.temp.cleanup()
    def record(self,kind,**extra):
        turn=self.store.session(self.sid)['active_turn']
        return self.store.add_record(self.sid,{'kind':kind,'title':kind,'content':'synthetic engineering evidence',
            'status':'recorded',**extra},{'origin':'codex-native-tool','turn_id':turn})
    def activate(self,handoff=None):
        id=handoff['delivery_id'] if handoff else 'fixture:'+uuid.uuid4().hex
        t='turn:'+uuid.uuid4().hex;stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
            (id,self.sid,'synthetic role event','queue','accepted',t,None,stamp,stamp))
        delivery=self.store.one('SELECT * FROM deliveries WHERE id=?',(id,))
        route=self.roles.route(self.sid,delivery,self.kernel.settings.value,self.catalog)
        self.store.patch_session(self.sid,status='running',active_turn=t)
        self.turn=t;return route
    def handoff(self,target,record=None,verdict='handoff',plan=None,call_id=None,stage='execute'):
        return self.roles.handoff(self.sid,self.turn,{'target':target,'plan_id':(plan or self.plan)['id'],
            'record_id':(record or self.plan)['id'],'verdict':verdict,'reason':'synthetic bounded handoff','stage':stage},
            call_id or uuid.uuid4().hex,self.r)
    def approve(self):
        self.activate();h=self.handoff('critic');self.activate(h)
        criticism=self.record('research',plan_id=self.plan['id'])
        h=self.handoff('planner',criticism,'approve');self.activate(h)

    def test_candidate_rejection_does_not_allocate_version(self):
        self.activate()
        review = self.record('review', plan_id=self.plan['id'])

        def candidate(title):
            return self.store.add_record(self.sid, {
                'kind': 'plan', 'title': title, 'content': 'Compare a narrower hypothesis',
                'previous_plan_id': self.plan['id'], 'review_record_id': review['id'],
                'revision_scope': 'incremental',
            }, {'origin': 'codex-native-tool', 'release_candidate': True})

        first = candidate('First candidate')
        self.assertIsNone(first['version'])
        self.activate(self.handoff('critic', first, plan=first))
        feedback = self.record('decision', plan_id=first['id'])
        self.activate(self.handoff('generator', feedback, 'revise', plan=first))
        second = candidate('Revised candidate')
        self.activate(self.handoff('critic', second, plan=second))
        approval = self.record('decision', plan_id=second['id'])
        handoff = self.handoff('planner', approval, 'approve', plan=second, call_id='publish-once')
        published = self.r.plan(self.sid, second['id'])
        self.assertEqual(published['version'], '1.0.0')
        self.assertEqual(handoff['plan_digest'], plan_digest(published))
        self.assertIsNone(self.r.plan(self.sid, first['id'])['version'])
        retry = self.handoff('planner', approval, 'approve', plan=second, call_id='publish-once')
        self.assertEqual(retry['plan_digest'], handoff['plan_digest'])

    def test_full_generator_critic_executor_feedback_cycle_keeps_plan_and_authority(self):
        self.activate();h=self.handoff('generator');self.assertEqual(self.activate(h)['role'],'generator')
        h=self.handoff('critic');critic=self.activate(h)
        self.assertEqual(critic['model'],'gpt-6-astra')
        note=self.record('research',plan_id=self.plan['id'])
        h=self.handoff('planner',note,'approve');planner=self.activate(h)
        self.assertEqual(planner['model'],'gpt-6-astra')
        grants=self.store.rows('SELECT * FROM research_grants')
        h=self.handoff('executor');executor=self.activate(h)
        self.assertEqual(executor['model'],'gpt-6.1-sol')
        self.roles.check_tool(self.sid,self.turn,'research_run',{'plan_id':self.plan['id']})
        workspace=Path(self.store.session(self.sid)['workspace']);(workspace/'probe.json').write_text('{"checks":1}')
        feedback=self.record('research',plan_id=self.plan['id'],evidence=['probe.json'])
        h=self.handoff('planner',feedback);self.assertEqual(self.activate(h)['role'],'planner')
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_no_executor_without_current_exact_critic_approval(self):
        self.activate()
        with self.assertRaisesRegex(ValueError,'Critic'):self.handoff('executor')
        self.approve()
        other=self.record('plan',content='changed method')
        with self.assertRaisesRegex(ValueError,'Critic'):self.handoff('executor',plan=other,record=other)

    def test_critic_cannot_approve_a_referenced_factual_method_conflict(self):
        from lab.research_modules import ResearchModules
        workspace=Path(self.store.session(self.sid)['workspace'])
        (workspace/'splits.json').write_text(json.dumps({'train':[1,2],'validation':[2,3]}))
        (workspace/'protocol.json').write_text(json.dumps({'schema':1,'data':{'splits':{
            key:{'path':'splits.json','pointer':'/'+key} for key in ['train','validation']}}}))
        plan=self.record('plan',research_protocol='protocol.json',evidence=['splits.json','protocol.json'])
        report=ResearchModules(self.store).check(self.sid,'data',plan['id'])
        self.activate();self.activate(self.handoff('critic',plan,plan=plan))
        note=self.record('decision',plan_id=plan['id'],method_report_ids=[report['report_id']])
        with self.assertRaisesRegex(ValueError,'明确冲突'):self.handoff('planner',note,'approve',plan=plan)
        self.handoff('generator',note,'revise',plan=plan)

    def test_changed_preparation_evidence_cannot_be_sent_as_current_critic_input(self):
        workspace=Path(self.store.session(self.sid)['workspace'])
        (workspace/'probe.json').write_text('{"revision":1}',encoding='utf8')
        plan=self.record('plan',evidence=['probe.json'])
        self.activate()
        (workspace/'probe.json').write_text('{"revision":2}',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'冻结证据已变化'):
            self.handoff('critic',plan,plan=plan)
        self.assertFalse(self.store.rows('SELECT * FROM research_role_handoffs'))

    def test_needs_evidence_is_not_approval(self):
        self.activate();h=self.handoff('critic');self.activate(h)
        note=self.record('research',plan_id=self.plan['id'])
        h=self.handoff('planner',note,'needs-evidence');self.activate(h)
        with self.assertRaisesRegex(ValueError,'Critic'):self.handoff('executor')

    def test_critic_cannot_directly_execute_or_rewrite_plan(self):
        self.activate();h=self.handoff('critic');self.activate(h)
        for tool,args in [('research_run',{}),('lpai_prepare',{}),('research_record',{'kind':'plan'})]:
            with self.assertRaises(ValueError):self.roles.check_tool(self.sid,self.turn,tool,args)
        with self.assertRaises(ValueError):self.handoff('executor')

    def test_executor_cannot_change_plan_or_run_other_plan(self):
        self.approve();h=self.handoff('executor');self.activate(h)
        for tool,args in [('research_record',{'kind':'plan'}),('research_adopt',{}),('research_run',{'plan_id':'other'})]:
            with self.assertRaises(ValueError):self.roles.check_tool(self.sid,self.turn,tool,args)
        note=self.record('research',plan_id=self.plan['id'])
        with self.assertRaisesRegex(ValueError,'证据'):self.handoff('planner',note)

    def test_model_catalog_missing_and_invalid_pair_never_fallback(self):
        p=copy.deepcopy(DEFAULT_POLICY);p['models']['planner']='gpt-6.1-astra'
        self.kernel.settings.value['agent_roles']=p
        with self.assertRaisesRegex(ValueError,'model/list'):self.activate()
        p['models']['critic']='bad model name'
        with self.assertRaises(ValueError):validate_policy(p)

    def test_same_planning_model_retains_separate_review_provenance(self):
        self.assertEqual(DEFAULT_POLICY['models']['critic'],DEFAULT_POLICY['models']['planner'])
        self.assertEqual(validate_policy(DEFAULT_POLICY),DEFAULT_POLICY)
        self.activate();author_note=self.record('decision',plan_id=self.plan['id'])
        h=self.handoff('critic');route=self.activate(h)
        with self.assertRaisesRegex(ValueError,'自己收到'):
            self.handoff('planner',author_note,'approve')
        own_note=self.record('research',plan_id=self.plan['id'])
        self.handoff('planner',own_note,'needs-evidence')
        contract=self.roles.instructions(route)
        self.assertIn('什么观察会推翻判断',contract)
        self.assertIn('不等于隔离上下文盲审',contract)

    def test_connection_switch_uses_channel_defaults_and_preserves_hidden_settings(self):
        self.kernel.settings.save({'profile':'existing-profile','capability_scope':'research'})
        self.kernel.settings.save({'channel':'ept'})
        values=self.kernel.settings.value
        self.assertEqual(values['profile'],'existing-profile')
        self.assertEqual(values['capability_scope'],'research')
        self.assertEqual(values['agent_roles']['models']['planner'],'azure-gpt-6-astra')
        self.assertEqual(values['agent_roles']['models']['critic'],'azure-gpt-6-astra')

    def test_default_effort_does_not_restore_legacy_session_override(self):
        self.store.patch_session(self.sid,effort='high')
        self.kernel.settings.value['effort']=None
        delivery=self.kernel.send(self.sid,'isolated default effort check')
        for _ in range(150):
            saved=self.store.one('SELECT * FROM deliveries WHERE id=?',(delivery['id'],))
            if saved['state'] in {'accepted','rejected'}:break
            time.sleep(.01)
        self.assertEqual(saved['state'],'accepted',saved.get('error'))
        request=next(p for method,p in self.kernel.rpc.calls if method=='turn/start')
        self.assertNotIn('effort',request)

    def test_route_is_frozen_and_handoff_idempotent(self):
        route=self.activate();call='same-call';h=self.handoff('critic',call_id=call)
        self.assertEqual(self.handoff('critic',call_id=call)['id'],h['id'])
        with self.assertRaises(ValueError):self.handoff('generator',call_id=call)
        d=self.store.one('SELECT * FROM deliveries WHERE id=?',(route['delivery_id'],))
        self.kernel.settings.value['agent_roles']['models']['planner']='other'
        with self.assertRaises(ValueError):self.roles.route(self.sid,d,self.kernel.settings.value,self.catalog)

    def test_dispatch_reuses_delivery_identity_and_respects_new_human_input(self):
        self.activate();h=self.handoff('critic')
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        sent=[]
        def send(*args,**kw):sent.append(kw['delivery_id']);return {'state':'unknown'}
        self.roles.dispatch(self.sid,send);self.roles.dispatch(self.sid,send)
        self.assertEqual(sent,[h['delivery_id']])
        self.activate();h=self.handoff('critic')
        id=uuid.uuid4().hex;stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(id,self.sid,'human correction','queue','queued',None,None,stamp,stamp))
        self.kernel.research.workflow.input(self.sid,id,'user')
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        self.roles.dispatch(self.sid,send)
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?',(h['id'],))['state'],'superseded')

    def test_native_turn_start_carries_model_readonly_and_original_thread(self):
        d=self.kernel.send(self.sid,'Synthetic public greeting only; no tools.')
        for _ in range(150):
            saved=self.store.one('SELECT * FROM deliveries WHERE id=?',(d['id'],))
            if saved['state'] in {'accepted','rejected'}:break
            time.sleep(.01)
        self.assertEqual(saved['state'],'accepted',saved.get('error'))
        starts=[p for m,p in self.kernel.rpc.calls if m=='turn/start']
        self.assertEqual(starts[-1]['model'],'gpt-6-astra')
        self.assertEqual(starts[-1]['sandboxPolicy']['type'],'readOnly')
        self.assertEqual(starts[-1]['threadId'],'native-thread')
        self.assertIn('Planner–Executor',starts[-1]['input'][0]['text'])

    def test_legacy_disabled_keeps_original_route(self):
        self.kernel.settings.value['agent_roles']=None
        self.assertIsNone(self.activate())

    def test_role_envelope_is_frozen_and_not_a_second_user_message(self):
        route=self.activate();first=self.roles.instructions(route)
        self.assertEqual(self.roles.instructions(route),first)
        delivery=self.store.one('SELECT * FROM deliveries WHERE id=?',(route['delivery_id'],))
        self.kernel.save_item(self.sid,self.turn,{'id':'native-role-input','type':'userMessage',
            'content':[{'type':'text','text':first},{'type':'text','text':delivery['text']}]},'completed')
        self.kernel.save_item(self.sid,self.turn,{'id':'native-role-input','type':'userMessage',
            'content':[{'type':'text','text':first},{'type':'text','text':delivery['text']}]},'completed')
        self.assertEqual(len(self.store.rows("SELECT * FROM messages WHERE session_id=? AND role='user' AND kind='message'",(self.sid,))),1)
        self.assertEqual(self.store.one("SELECT kind FROM messages WHERE item_id='native-role-input'")['kind'],'method_envelope')

    def test_pending_role_handoff_precedes_generic_autonomous_wakeup(self):
        from unittest.mock import patch
        self.r.autonomy.enable(self.sid,self.plan['id'],plan_digest(self.plan),{
            'objective':'隔离角色优先级','evaluation':'固定控制','data_boundary':'无真实数据',
            'allowed_changes':'只测交接','frozen_files':[]},'工程测试操作者')
        self.activate();self.handoff('critic')
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        with patch.object(self.kernel,'resume_roles') as resume, patch.object(self.kernel,'send') as send:
            self.kernel.continuation.tick(self.sid)
            resume.assert_called_once_with(self.sid)
            send.assert_not_called()

    def test_revoked_grant_blocks_executor_before_model_turn(self):
        self.approve();handoff=self.handoff('executor')
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        self.r.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'reject','reason':'synthetic user revocation before execution'})
        delivery=self.kernel.send(self.sid,'synthetic executor event',delivery_id=handoff['delivery_id'],origin='background')
        for _ in range(150):
            current=self.store.one('SELECT * FROM deliveries WHERE id=?',(delivery['id'],))
            if current['state']=='rejected':break
            time.sleep(.01)
        self.assertEqual(current['state'],'rejected')
        self.assertFalse(any(method=='turn/start' for method,params in self.kernel.rpc.calls))

    def test_prepare_implementation_needs_no_formal_approval_but_cannot_compute(self):
        self.r.review(self.sid,{'plan_id':self.plan['id'],'expected_digest':plan_digest(self.plan),
            'decision':'reject','reason':'no formal computation authorization for this test'})
        self.activate();handoff=self.handoff('executor',stage='prepare');route=self.activate(handoff)
        self.assertEqual(self.roles.execution_stage(handoff['id']),'prepare')
        self.assertIn('当前仅为prepare',self.roles.instructions(route))
        self.roles.check_tool(self.sid,self.turn,'research_write',{'path':'implementation.py','text':'print(1)'})
        for tool in ['research_run','lpai_prepare','company_action']:
            with self.assertRaisesRegex(ValueError,'prepare'):self.roles.check_tool(self.sid,self.turn,tool,{'plan_id':self.plan['id']})
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_prepare_return_provides_executed_predecessor_not_candidate(self):
        self.activate()
        review=self.record('review',plan_id=self.plan['id'])
        candidate=self.store.add_record(self.sid,{'kind':'plan','title':'Prepared candidate',
            'content':'Engineering adaptation only','previous_plan_id':self.plan['id'],
            'review_record_id':review['id'],'revision_scope':'incremental'},
            {'origin':'codex-native-tool','release_candidate':True})
        self.activate(self.handoff('executor',plan=candidate,record=candidate,stage='prepare'))
        executor=self.roles.current(self.sid,self.turn)
        self.assertIn('plan_id必须为'+candidate['id'],self.roles.instructions(executor))
        workspace=Path(self.store.session(self.sid)['workspace'])
        (workspace/'prepare-proof.json').write_text('{"static_only":true}')
        feedback=self.record('research',plan_id=candidate['id'],evidence=['prepare-proof.json'])
        route=self.activate(self.handoff('planner',feedback,plan=candidate))
        grants=self.store.rows('SELECT * FROM research_grants')
        prefix=self.roles.instructions(route)
        facts=json.loads(prefix.split('\n交接事实：',1)[1])['proposal_lineage']
        self.assertEqual(facts,{'previous_plan_id':self.plan['id'],
            'review_record_id':review['id'],'source_plan_id':candidate['id']})
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertIsNone(self.r.plan(self.sid,candidate['id'])['version'])
        self.assertEqual(prefix,self.roles.instructions(route))

    def test_first_prepare_return_does_not_require_review_of_unapproved_draft(self):
        project=self.store.create_project('initial unapproved proposal')
        self.pid=project['id'];self.sid=self.store.create_session(self.pid,'codex')['id']
        self.plan=self.record('plan');original_grants=self.store.rows('SELECT * FROM research_grants')
        self.activate();self.activate(self.handoff('executor',stage='prepare'))
        (Path(self.store.session(self.sid)['workspace'])/'proof.json').write_text('{"syntax_valid":true}')
        feedback=self.record('research',plan_id=self.plan['id'],evidence=['proof.json'])
        route=self.activate(self.handoff('planner',feedback))
        prefix=self.roles.instructions(route)
        facts=json.loads(prefix.split('\n交接事实：',1)[1])['proposal_lineage']
        self.assertEqual(facts,{'previous_plan_id':None,'review_record_id':None,'source_plan_id':self.plan['id']})
        self.assertIn('这是初始提案准备',prefix)
        self.assertIn('不要为未执行草案创建kind=review',prefix)
        self.assertNotIn('缺少轮次评审时先核对并评审',prefix)
        with self.assertRaisesRegex(ValueError,'初始准备反馈'):
            self.record('review',plan_id=self.plan['id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),original_grants)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_generator_can_request_prepare_but_never_execute_or_write_itself(self):
        self.activate();self.activate(self.handoff('generator'))
        with self.assertRaises(ValueError):self.roles.check_tool(self.sid,self.turn,'research_write',{'path':'x.py','text':'x'})
        with self.assertRaisesRegex(ValueError,'Generator'):self.handoff('executor')
        grants=self.store.rows('SELECT * FROM research_grants')
        h=self.handoff('executor',stage='prepare');route=self.activate(h)
        self.assertEqual(route['role'],'executor');self.assertEqual(self.roles.execution_stage(h['id']),'prepare')
        self.roles.check_tool(self.sid,self.turn,'research_write',{'path':'implementation.py','text':'# offline only'})
        for tool in ['research_run','lpai_prepare','company_action']:
            with self.assertRaisesRegex(ValueError,'prepare'):self.roles.check_tool(self.sid,self.turn,tool,{'plan_id':self.plan['id']})
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))

    def test_uniform_role_model_also_pins_native_thread_default(self):
        self.kernel.settings.value['agent_roles']['models']=dict.fromkeys(DEFAULT_POLICY['models'],'gpt-6.1-sol')
        self.kernel.load_thread(self.sid)
        params=next(p for m,p in self.kernel.rpc.calls if m=='thread/start')
        self.assertEqual(params['model'],'gpt-6.1-sol')
        self.assertFalse(any(m in {'turn/start','thread/compact/start'} for m,p in self.kernel.rpc.calls))

    def test_critic_cannot_accidentally_end_round_before_training(self):
        self.activate();handoff=self.handoff('critic');self.activate(handoff)
        with self.assertRaisesRegex(ValueError,'收尾记录'):
            self.roles.check_tool(self.sid,self.turn,'research_record',{'kind':'review','plan_id':self.plan['id']})
        self.roles.check_tool(self.sid,self.turn,'research_record',{'kind':'decision','plan_id':self.plan['id']})

    def test_native_prepare_turn_does_not_require_a_training_grant(self):
        from unittest.mock import patch
        self.activate();handoff=self.handoff('executor',stage='prepare')
        self.store.patch_session(self.sid,status='idle',active_turn=None)
        with patch.object(self.r,'execution_grant',side_effect=AssertionError('prepare must not ask for compute grant')):
            delivery=self.kernel.send(self.sid,'synthetic implementation only',delivery_id=handoff['delivery_id'],origin='background')
            for _ in range(150):
                current=self.store.one('SELECT * FROM deliveries WHERE id=?',(delivery['id'],))
                if current['state'] in {'accepted','rejected'}:break
                time.sleep(.01)
        self.assertEqual(current['state'],'accepted',current.get('error'))
        request=next(params for method,params in self.kernel.rpc.calls if method=='turn/start')
        self.assertEqual(request['model'],'gpt-6.1-sol')
        self.assertEqual(request['sandboxPolicy']['type'],'workspaceWrite')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_old_native_thread_can_handoff_through_original_record_schema(self):
        from lab.roles import HANDOFF_RECORD_TITLE
        self.kernel.load_thread(self.sid);self.activate()
        count=self.store.one('SELECT COUNT(*) AS n FROM records')['n']
        payload={'schema':'iterlab-role-handoff/v1','target':'executor','plan_id':self.plan['id'],
            'record_id':self.plan['id'],'verdict':'handoff','reason':'prepare actual code before review','stage':'prepare'}
        packet={'id':123,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':self.turn,
            'callId':'legacy-record-handoff','tool':'research_record','arguments':{'kind':'decision',
            'title':HANDOFF_RECORD_TITLE,'summary':'implementation preparation','content':json.dumps(payload),
            'status':'proposed','evidence':[]}}}
        self.kernel.on_request(packet,self.kernel.rpc)
        handoff=self.store.one("SELECT * FROM research_role_handoffs WHERE id='native-thread:legacy-record-handoff'")
        self.assertIsNotNone(handoff)
        self.assertEqual(self.roles.execution_stage(handoff['id']),'prepare')
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM records')['n'],count)
        self.assertTrue(self.kernel.rpc.replies[-1][1]['success'])

    def test_plain_record_and_malformed_control_envelope_are_not_execution(self):
        from lab.roles import HANDOFF_RECORD_TITLE,decode_handoff_record
        self.assertIsNone(decode_handoff_record({'kind':'research','title':'ordinary evidence',
            'content':'{"target":"executor"}'}))
        for content in ['run training now',json.dumps({'schema':'wrong','target':'executor'}),
            json.dumps({'schema':'iterlab-role-handoff/v1','target':'executor','plan_id':'x','record_id':'y',
                'verdict':'handoff','reason':'x','command':'forbidden extra field'})]:
            with self.assertRaises(ValueError):decode_handoff_record({'kind':'decision','title':HANDOFF_RECORD_TITLE,
                'summary':'invalid','content':content,'status':'proposed','evidence':[]})
        self.assertEqual(self.store.rows('SELECT * FROM research_role_handoffs'),[])
