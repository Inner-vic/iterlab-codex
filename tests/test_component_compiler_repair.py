"""Compiler migration fixtures: no real SDK, GPU, model or production data."""
import copy
import json
import unittest
from unittest.mock import patch

from lab.component_compiler_repair import role_only,refresh
from lab.kernel import Kernel
from lab.research import plan_digest
from lab.store import dumps,now
from tests.test_core import FakeRPC
from tests import test_component_configuration as gpu
from tests.test_component_configuration import build,recipe,directory,asset


class CompilerDeltaTests(unittest.TestCase):
    def test_only_master_role_is_allowed(self):
        new=build(recipe(),directory(),asset,'new','vc-chassis');old=copy.deepcopy(new)
        old['config']['name']='old';old['config']['task_role_specs'][0]['rolename']='worker'
        self.assertTrue(role_only(old,new))
        for change in [lambda t:t['config'].update(cmd='different'),lambda t:t.update(gpu_count=16),
                       lambda t:t['config'].update(image='different'),lambda t:t['config'].update(queue_code='other'),
                       lambda t:t.update(recipe={**recipe(),'training_parameters':{'batch_size':64}})]:
            wrong=copy.deepcopy(new);change(wrong)
            self.assertFalse(role_only(old,wrong))


class BindingRepairTests(unittest.TestCase):
    setUp=gpu.GPUProvisionTests.setUp
    sdk=gpu.GPUProvisionTests.sdk
    request=gpu.GPUProvisionTests.request
    approval=gpu.GPUProvisionTests.approval

    def setup_old(self):
        self.kernel=Kernel(self.store,FakeRPC);self.addCleanup(self.kernel.close)
        self.kernel.settings.save({'agent_roles':{'version':1,'models':dict.fromkeys(['planner','executor','generator','critic'],'fixture-model')}})
        training=self.company.training;preview=training.preview_component(self.sid,self.plan)
        self.research.review(self.sid,self.approval(preview));self.grant=self.store.one('SELECT * FROM research_grants')
        # Prepare the isolated old component without submitting any task.
        training.provision_component(self.sid,self.plan,self.grant,self.request(),[
            {'path':'/lpai_task/cmd','value':'python train.py'},
            {'path':'/lpai_task/roles/0/cmd','value':'python train.py'}])
        self.original_plan=self.plan
        row=self.provision();frozen=json.loads(row['configuration'])
        frozen['template']['config']['task_role_specs'][0]['rolename']='worker'
        frozen['template']['config_sha256']='d'*64
        self.store.execute('UPDATE research_component_previews SET configuration=? WHERE id=?',(dumps(frozen),row['preview_id']))
        self.old=self.provision();self.old_scope=self.store.one('SELECT * FROM research_cloud_scopes')
        review=self.store.add_record(self.sid,{'kind':'review','title':'fixture evidence review','content':'Retain scientific protocol.',
            'plan_id':self.plan['id'],'run_ids':[],'round_outcome':'continue'},{'origin':'codex-native-tool'})
        status=self.research.autonomy.status(self.sid)
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'compiler role repair','content':'Change only compiler role; retain scientific protocol.',
            'evidence':['run-config.json'],'previous_plan_id':self.plan['id'],'review_record_id':review['id'],
            'authority_id':status['authority_id'],'scope_digest':status['scope_digest']},{'origin':'codex-native-tool'})
        stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('fixture-critic',self.sid,'fixture','queue','accepted','critic-turn',None,stamp,stamp))
        self.store.execute('INSERT INTO research_role_routes VALUES(?,?,?,?,?,?,?)',('fixture-critic',self.sid,'critic','fixture-model',dumps(self.kernel.settings.value['agent_roles']),None,stamp))
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('critic-proof',self.sid,'fixture-critic','planner',self.plan['id'],plan_digest(self.plan),self.plan['id'],'approve','fixture independent review','fixture-return','dispatched',stamp))
        self.research.autonomy.adopt(self.sid,self.plan['id'],plan_digest(self.plan),'fixture exact repair')

    def provision(self):
        return self.store.one('''SELECT p.*,v.session_id,v.configuration FROM research_component_provisions p
          JOIN research_component_previews v ON v.id=p.preview_id WHERE p.grant_id=?''',(self.grant['id'],))

    def test_repair_versions_binding_preserves_grant_and_old_preview_and_is_idempotent(self):
        self.setup_old();before=self.store.rows('SELECT * FROM research_grants')
        result=refresh(self.company.training,self.sid,self.plan,self.grant,self.old)
        self.assertEqual(result['state'],'approved');self.assertIsNone(result['component_uid'])
        self.assertNotEqual(result['preview_id'],self.old['preview_id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertEqual(self.store.one('SELECT configuration FROM research_component_previews WHERE id=?',(self.old['preview_id'],))['configuration'],self.old['configuration'])
        self.assertFalse(self.store.rows('SELECT * FROM research_cloud_scopes'))
        receipt=json.loads(self.store.one('SELECT receipt FROM research_component_binding_repairs')['receipt'])
        self.assertEqual(receipt['old_scope'],self.old_scope)
        before=list(self.sdk_calls)
        self.assertEqual(refresh(self.company.training,self.sid,self.plan,self.grant,result),result)
        self.assertEqual(self.sdk_calls,before)

    def test_missing_critic_or_changed_recipe_never_replaces_binding(self):
        self.setup_old();self.store.execute("UPDATE research_role_handoffs SET verdict='revise'")
        with self.assertRaisesRegex(ValueError,'Critic'):refresh(self.company.training,self.sid,self.plan,self.grant,self.old)
        self.assertEqual(self.provision(),self.old)
        self.store.execute("UPDATE research_role_handoffs SET verdict='approve'")
        original=self.company.training.sdk
        def changed(operation,**kw):
            value=original(operation,**kw)
            if operation=='configured_template':value['config']['image']='different'
            return value
        with patch.object(self.company.training,'sdk',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'超出'):refresh(self.company.training,self.sid,self.plan,self.grant,self.old)
        self.assertEqual(self.provision(),self.old)
        self.assertEqual(self.store.one('SELECT * FROM research_cloud_scopes'),self.old_scope)

    def test_unknown_original_and_user_pause_block_migration(self):
        self.setup_old();self.store.execute("UPDATE research_component_provisions SET state='unknown'")
        with self.assertRaisesRegex(ValueError,'尚未核对'):refresh(self.company.training,self.sid,self.plan,self.grant,self.provision())
        self.store.execute("UPDATE research_component_provisions SET state='ready'")
        self.research.workflow.pause(self.sid,actor='user')
        with self.assertRaisesRegex(ValueError,'未创建组件'):refresh(self.company.training,self.sid,self.plan,self.grant,self.provision())
        self.assertFalse(self.store.rows('SELECT * FROM research_component_binding_repairs'))

    def test_successor_unknown_creation_reconciles_under_original_grant_without_create(self):
        self.setup_old()
        fresh=refresh(self.company.training,self.sid,self.plan,self.grant,self.old)
        self.store.execute("UPDATE research_component_provisions SET state='unknown'")
        self.visible=True;before=list(self.sdk_calls)
        from lab.component_recovery import recover
        recovered=recover(self.company.training,self.sid,self.grant['id'])
        self.assertEqual(recovered['state'],'ready')
        self.assertEqual(recovered['plan_id'],self.plan['id'])
        self.assertNotEqual(self.plan['id'],self.grant['plan_id'])
        self.assertFalse(recovered['created_again']);self.assertFalse(recovered['task_submitted'])
        self.assertNotIn('provision_configured',self.sdk_calls[len(before):])
        self.assertEqual(self.store.one('SELECT preview_id FROM research_component_provisions')['preview_id'],fresh['preview_id'])

    def test_binding_history_obeys_project_deletion_closure(self):
        self.setup_old()
        refresh(self.company.training,self.sid,self.plan,self.grant,self.old)
        from lab.purge import purge
        self.assertTrue(purge(self.kernel,'project',self.project['id'],'确认',direct=True)['purged'])
        self.assertFalse(self.store.rows('SELECT * FROM research_component_binding_repairs'))
        self.assertFalse(self.store.rows('PRAGMA foreign_key_check'))
