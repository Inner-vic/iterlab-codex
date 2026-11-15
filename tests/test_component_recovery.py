"""Read-only recovery and timed readiness delivery; scripted SDK, no real model/cloud."""
import json,threading,time,unittest
from unittest.mock import Mock,patch
from lab.component_recovery import recover
from lab.continuation import Continuation
from lab.kernel import Kernel
from lab.store import now
from tests.test_core import FakeRPC
from tests import test_component_provision as fixtures
from tests import test_component_configuration as gpu_fixtures


class ComponentRecoveryTests(unittest.TestCase):
    setUp=fixtures.ComponentProvisionTests.setUp
    approval=fixtures.ComponentProvisionTests.approval
    request=fixtures.ComponentProvisionTests.request

    def sdk(self,operation,**kwargs):
        if operation=='verify_provisioned_component':
            self.sdk_calls.append(operation)
            self.assertEqual(kwargs['component_uid'],self.component)
            self.assertIsNone(self.store.one('SELECT component_uid FROM research_component_provisions')['component_uid'])
            return {'uid':self.component,'name':self.name,'namespace':'vc-chassis','cluster':'fixture-cluster',
                'digest':'a'*64,'gpu_count':0,'volumes':self.volumes}
        return fixtures.ComponentProvisionTests.sdk(self,operation,**kwargs)

    def make_unknown(self):
        preview=self.company.training.preview_component(self.sid,self.plan)
        self.research.review(self.sid,self.approval(preview));self.ambiguous=True
        self.store.event(self.sid,'item/tool/call',{'tool':'lpai_prepare','callId':'original-call',
            'turnId':'original-turn','arguments':self.request()})
        with self.assertRaisesRegex(ValueError,'创建结果未知'):self.company.training.prepare(self.sid,self.request())
        self.grant=self.store.one('SELECT * FROM research_grants')
        return self.grant['id']

    def test_readback_binds_exact_component_once_without_recreating_or_submitting(self):
        grant=self.make_unknown()
        self.assertEqual(recover(self.company.training,self.sid,grant)['state'],'not_observed')
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'unknown')
        self.visible=True;result=recover(self.company.training,self.sid,grant)
        self.assertTrue(result['changed']);self.assertEqual(result['trial_id'],self.trial['id'])
        self.assertFalse(result['created_again']);self.assertFalse(result['task_submitted'])
        self.assertEqual(recover(self.company.training,self.sid,grant),{'state':'ready','changed':False})
        self.assertEqual(self.sdk_calls.count('provision_component'),1);self.assertNotIn('submit',self.sdk_calls)
        self.assertEqual(len(self.store.rows("SELECT seq FROM events WHERE kind='company.component_reconciled'")),1)

    def test_pause_or_revocation_during_read_never_binds_or_wakes(self):
        grant=self.make_unknown();self.visible=True;original=self.company.training.sdk
        def sdk(operation,**kwargs):
            result=original(operation,**kwargs)
            if operation=='verify_provisioned_component':self.research.workflow.pause(self.sid,actor='user')
            return result
        self.company.training.sdk=sdk
        self.assertEqual(recover(self.company.training,self.sid,grant)['state'],'stale')
        self.assertFalse(self.store.rows('SELECT * FROM research_cloud_scopes'))
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'unknown')
        self.assertTrue(self.research.workflow.paused(self.sid))

    def test_wrong_gpu_or_identity_does_not_clear_unknown(self):
        grant=self.make_unknown();self.visible=True;original=self.company.training.sdk
        def sdk(operation,**kwargs):
            result=original(operation,**kwargs)
            return {**result,'gpu_count':8} if operation=='verify_provisioned_component' else result
        self.company.training.sdk=sdk
        with self.assertRaisesRegex(ValueError,'GPU读回未通过'):recover(self.company.training,self.sid,grant)
        self.assertFalse(self.store.rows('SELECT * FROM research_cloud_scopes'))
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'unknown')

    def test_service_stopping_after_read_preserves_unknown(self):
        grant=self.make_unknown();self.visible=True
        self.assertEqual(recover(self.company.training,self.sid,grant,cancelled=Mock(side_effect=[False,True]))['state'],'stale')
        self.assertFalse(self.store.rows('SELECT * FROM research_cloud_scopes'))
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'unknown')

    def test_disabled_adapter_is_not_queried_or_reenabled(self):
        grant=self.make_unknown();self.visible=True;before=list(self.sdk_calls)
        connection={**self.company.config(),'lpai_enabled':False}
        with patch.object(self.company,'config',return_value=connection):
            self.assertEqual(recover(self.company.training,self.sid,grant)['state'],'stale')
        self.assertEqual(self.sdk_calls,before)
        self.assertFalse(self.store.rows('SELECT * FROM research_cloud_scopes'))

    def kernel(self):
        kernel=Kernel(self.store,FakeRPC);self.addCleanup(kernel.close)
        kernel.company=self.company
        kernel.settings.value['channel']='ept';kernel.settings.value['profile']='fixture-profile'
        kernel.settings.value['agent_roles']=None
        return kernel

    def enqueue(self,received):
        def send(sid,text,mode,id,origin):
            self.assertEqual(origin,'background');self.assertEqual(sid,self.sid)
            self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
                (id,sid,text,mode,'accepted','scripted-native-turn',None,now(),now()))
            self.store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',(id,origin,now()))
            self.store.execute("UPDATE sessions SET status='running',active_turn='scripted-native-turn' WHERE id=?",(sid,))
            received.append(id)
            return self.store.one('SELECT * FROM deliveries WHERE id=?',(id,))
        return send

    def test_timer_recovers_and_delivers_once_without_human_or_manual_tick(self):
        self.make_unknown();self.visible=True;kernel=self.kernel();monitor=kernel.continuation
        event=threading.Event();received=[]
        class FastTimer:
            def is_set(self):return event.is_set()
            def set(self):event.set()
            def wait(self,seconds):return event.wait(.02)
        monitor.stop_event=FastTimer()
        with patch.object(kernel,'send',side_effect=self.enqueue(received)) as send:
            monitor.start();deadline=time.monotonic()+3
            while not received and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(received,monitor.status())
            time.sleep(.08);monitor.close()
            self.assertEqual(send.call_count,1)
        self.assertEqual(self.sdk_calls.count(getattr(self,'creation_operation','provision_component')),1)
        self.assertNotIn('submit',self.sdk_calls)
        self.assertEqual(self.store.one('SELECT state FROM research_component_provisions')['state'],'ready')
        self.assertFalse(self.store.rows('SELECT * FROM company_training'))

    def test_ready_event_survives_restart_and_only_missing_local_reservation_is_recovered(self):
        grant=self.make_unknown();self.visible=True;recover(self.company.training,self.sid,grant)
        kernel=self.kernel();monitor=kernel.continuation;received=[]
        from lab.autonomy import policy,active
        p=policy(self.store,self.project['id']);branch=active(self.store,self.project['id'])
        def reserve_only(*args,**kwargs):raise RuntimeError('isolated crash before local enqueue')
        with patch.object(kernel,'send',side_effect=reserve_only),self.assertRaises(RuntimeError):monitor.component_resume(self.sid,p,branch)
        original=self.store.one('SELECT id,state FROM research_continuations')
        self.assertEqual(original['state'],'reserved')
        restored=Continuation(kernel)
        with patch.object(kernel,'send',side_effect=self.enqueue(received)) as send:
            self.assertTrue(restored.component_resume(self.sid,p,branch))
            self.assertFalse(restored.component_resume(self.sid,p,branch))
            self.assertEqual(send.call_count,1)
        self.assertEqual(received,[original['id']])
        self.assertEqual(self.sdk_calls.count('provision_component'),1);self.assertNotIn('submit',self.sdk_calls)


class ConfiguredGPUReadbackTests(unittest.TestCase):
    def setUp(self):
        gpu_fixtures.GPUProvisionTests.setUp(self)
        self.creation_operation='provision_configured'
    sdk=fixtures.ComponentProvisionTests.sdk
    request=fixtures.ComponentProvisionTests.request
    approval=gpu_fixtures.GPUProvisionTests.approval
    make_unknown=ComponentRecoveryTests.make_unknown
    kernel=ComponentRecoveryTests.kernel
    enqueue=ComponentRecoveryTests.enqueue
    test_timer_recovers_and_delivers_once_without_human_or_manual_tick=ComponentRecoveryTests.test_timer_recovers_and_delivers_once_without_human_or_manual_tick

    def test_exact_eight_gpu_recipe_is_verified_by_official_read_adapter(self):
        grant=self.make_unknown();self.visible=True
        result=recover(self.company.training,self.sid,grant)
        self.assertEqual(result['gpu_count'],8)
        self.assertEqual(self.store.one('SELECT max_gpus FROM research_cloud_scopes')['max_gpus'],8)
        self.assertEqual(self.sdk_calls.count('provision_configured'),1)
        self.assertEqual(self.sdk_calls.count('verify_configured'),1)
        self.assertNotIn('submit',self.sdk_calls)


class CreationReceiptTests(unittest.TestCase):
    def test_direct_official_uid_and_dictionary_normalize_to_same_ack(self):
        from scripts.lpai_sdk_bridge import creation_receipt
        uid='project-3280ed6c-12db-453e-b586-fbe8eb08cb64'
        expected={'state':'created','uid':uid}
        self.assertEqual(creation_receipt(uid),expected)
        self.assertEqual(creation_receipt({'uid':uid}),expected)
        self.assertNotIn('verified',expected) # Configuration verification remains a separate step.

    def test_unknown_string_or_envelope_is_not_treated_as_success_or_exposed(self):
        from scripts.lpai_sdk_bridge import creation_receipt
        for value in ('private-secret','success','project-guess',{'data':'private-secret'},None):
            receipt=creation_receipt(value)
            self.assertEqual(receipt['state'],'unknown')
            self.assertTrue(receipt['write_attempted'])
            self.assertNotIn('private-secret',str(receipt))

    def test_only_safe_write_boundary_facts_survive(self):
        from scripts.lpai_sdk_bridge import uncertain_creation
        fact=uncertain_creation(error=RuntimeError("Invalid status code '201' with Authorization=private-secret"))
        self.assertEqual(fact['http_status'],201);self.assertTrue(fact['write_attempted'])
        self.assertNotIn('private-secret',str(fact))
        fact=uncertain_creation(response={'result':{'token':'private-secret'},'Authorization':'private-secret','code':0})
        self.assertEqual(fact['response_keys'],['code','result']);self.assertNotIn('private-secret',str(fact))
