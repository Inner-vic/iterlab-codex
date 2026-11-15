import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lab.preparation_checks import check_text,check_source
from lab.component_configuration import recipe_contract,recipe_shape_errors,RECIPE_FIELDS
from lab.research import Research,plan_digest
from lab.store import Store


def recipe():
    return {'schema':1,'account_scope':{'subject':'a'*64,'namespace':'fixture','connection_revision':1},
        'queue_key':'fixture-queue','package_id':5,'workers':1,'packages_per_worker':1,
        'image':{'uid':'fixture-image','version':'1'},'volumes':[],'datasets':[],
        'output_volume_uid':'fixture-volume','command':'python train.py',
        'training_parameters':{},'rationale':'Small CPU task','compatibility':'Python standard library only'}


class PreparationChecksTests(unittest.TestCase):
    def test_python_is_compiled_without_execution_imports_or_bytecode(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'must-not-exist'
            source=f"import package_that_does_not_exist\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
            result=check_text('train.py',source)
            self.assertTrue(result['valid']);self.assertFalse(marker.exists())
            self.assertEqual(list(Path(directory).iterdir()),[])
            self.assertIn('no_dependency',result['scope'])

    def test_invalid_python_reports_position_not_entire_source(self):
        result=check_text('train.py','if True\n    pass\n')
        self.assertFalse(result['valid']);self.assertEqual(result['error']['line'],1)
        self.assertNotIn('source',result['error'])
        self.assertFalse(check_text('train.py','return 1')['valid'])

    def test_json_reports_invalid_duplicates_nonfinite_and_valid_scalar(self):
        for text in ['{bad}', '{"nested":{"a":1,"a":2}}','{"value":NaN}','{"value":1e999}']:
            self.assertFalse(check_text('config.json',text)['valid'])
        self.assertTrue(check_text('config.json','[1,"two",null]')['valid'])
        self.assertIsNone(check_text('notes.md','# Not executable'))

    def test_component_recipe_distinguishes_json_from_structure_and_live_validation(self):
        value=recipe();value.update(account_scope='fixture',package_spec={'gpu':0},output_contract={'required_files':['metrics.json']})
        value['image']['name']='human display name'
        value['compatibility']={'cuda_required':False}
        result=check_text('config/component_config.json',json.dumps(value))
        self.assertFalse(result['valid']);self.assertTrue(result['syntax_valid'])
        shape=result['component_recipe'];self.assertFalse(shape['valid'])
        self.assertTrue({'account_scope','package_spec','output_contract','image','compatibility'}<=
                        {error['field'] for error in shape['errors']})
        self.assertIn('live_preview',shape['scope'])

    def test_contract_matches_actual_compiler_fields_without_personal_defaults(self):
        contract=recipe_contract()
        self.assertEqual(set(contract['required_fields']),RECIPE_FIELDS)
        self.assertEqual(recipe_shape_errors(recipe()),[])
        self.assertEqual(check_text('resource.json',json.dumps(recipe()))['component_recipe']['errors'],[])
        self.assertNotIn('vc-chassis',json.dumps(contract))
        self.assertNotIn('fixture-queue',json.dumps(contract))

    def test_missing_and_unsupported_fields_return_specific_bounded_errors(self):
        errors=recipe_shape_errors({'schema':True,'workers':False,'image':{'uid':'x'}})
        self.assertIn({'field':'command','issue':'missing'},errors)
        self.assertIn({'field':'workers','issue':'expected_positive_integer'},errors)
        self.assertIn({'field':'schema','issue':'expected_integer_1'},errors)
        self.assertEqual(recipe_shape_errors({str(i):i for i in range(100)}),[{'field':'$','issue':'too_many_fields_max_64'}])


class WritePreparationIntegrationTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[1]/'.evidence/tests';base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.store=Store(Path(self.temp.name));self.project=self.store.create_project('preparation-fixture')
        self.sid=self.store.create_session(self.project['id'],'ept')['id'];self.research=Research(self.store)

    def tearDown(self):
        self.research.close();self.store.close();self.temp.cleanup()

    def test_write_returns_check_in_same_receipt_preserves_bad_file_for_repair(self):
        with patch('subprocess.run',side_effect=AssertionError('No shell or SDK for syntax feedback')):
            first=self.research.write(self.sid,'implementation/check.py','if True\n    pass')
            self.assertFalse(first['preparation_check']['valid'])
            self.assertEqual(self.store.safe_path(self.project['id'],first['path']).read_text(),'if True\n    pass')
            second=self.research.write(self.sid,first['path'],'raise RuntimeError("must not execute")')
            self.assertTrue(second['preparation_check']['valid'])
            self.assertNotEqual(first['sha256'],second['sha256'])
            last=json.loads(self.store.one("SELECT payload FROM events WHERE kind='research.file_written' ORDER BY seq DESC LIMIT 1")['payload'])
            self.assertEqual(last['preparation_check'],second['preparation_check'])
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_file_guards_still_precede_checks_and_prevent_overwrite(self):
        self.research.write(self.sid,'config.json','{"value":1}')
        plan=self.store.add_record(self.sid,{'kind':'plan','title':'Frozen config','content':'Bound local check',
                                           'evidence':['config.json']},{})
        import sys
        self.research.review(self.sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept',
            'reason':'Fixture scope','actor':'用户委托开发助手审批','execution':'local','allow_local':True,'python':sys.executable,
            'max_runs':1,'max_fits':1,'timeout':2})
        with self.assertRaisesRegex(ValueError,'冻结'):
            self.research.write(self.sid,'config.json','{"value":2}')
        with self.assertRaises(ValueError):self.research.write(self.sid,'runs/receipt.json','{}')
        self.assertEqual(self.store.safe_path(self.project['id'],'config.json').read_text(),'{"value":1}')

    def test_invalid_recipe_is_rejected_before_cold_sdk_and_evidence_still_verified(self):
        from lab.component_configuration import recommendation
        invalid={**recipe(),'account_scope':'namespace-is-not-account-identity','output_contract':{}}
        path='config/component_config.json';self.research.write(self.sid,path,json.dumps(invalid))
        plan=self.store.add_record(self.sid,{'kind':'plan','title':'Component candidate','content':'Small task',
            'evidence':[path],'scope_proposal':{'component_config':path}}, {})
        with patch('subprocess.run',side_effect=AssertionError('Invalid local shape must not reach SDK')):
            with self.assertRaisesRegex(ValueError,'account_scope:expected_fields'):
                recommendation(self.store,plan)
        self.research.write(self.sid,path,json.dumps(recipe()))
        with self.assertRaisesRegex(ValueError,'已变化'):
            recommendation(self.store,plan)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
    def test_read_check_refuses_changed_bytes_and_skips_large_or_other_formats(self):
        written=self.research.write(self.sid,'source.py','raise RuntimeError("must not run")')
        checked=check_source(self.store,self.sid,'source.py',written['sha256'])
        self.assertTrue(checked['valid'])
        self.assertEqual(check_source(self.store,self.sid,'source.py','0'*64)['state'],'changed_during_read')
        path=self.store.safe_path(self.project['id'],'large.py');path.write_bytes(b'#'+b' '*(512*1024))
        self.assertIsNone(check_source(self.store,self.sid,'notes.md','unused'))
        large=check_source(self.store,self.sid,'large.py','unused')
        self.assertEqual(large['state'],'not_checked_size');self.assertIsNone(large['valid'])
        self.assertFalse((path.parent/'__pycache__').exists())


if __name__=='__main__':unittest.main()
