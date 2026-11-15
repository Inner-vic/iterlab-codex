"""Offline real-file acceptance. No model, cloud training, or network."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import uuid

from lab import cloud_results
from lab.cloud_evidence import TransferBudget
from lab.company import Company
from lab.result_receipt_writer import write
from lab.store import dumps,now
from lab.task_output_files import collect,verify_archive
from lab.task_outputs import TaskOutputs
from tests import test_comparison as comparison_fixtures
from tests.test_task_output_files import Client


class Missing(Exception):
    response={'Error':{'Code':'404'}}


class ExactClient(Client):
    def head_object(self,**kwargs):
        if kwargs['Key'][len('task/root/'):] not in self.objects:
            self.calls.append(('head',kwargs));raise Missing()
        return super().head_object(**kwargs)


class CloudResultsTests(unittest.TestCase):
    setUp=comparison_fixtures.ComparisonTests.setUp
    tearDown=comparison_fixtures.ComparisonTests.tearDown
    make_plan=comparison_fixtures.ComparisonTests.make_plan
    approve=comparison_fixtures.ComparisonTests.approve

    def test_budget_analysis_needs_current_plan_verified_bytes_not_ready_flag(self):
        from lab.budgets import cloud_assessment_ready
        id,uid,_,_,result=self.archive()
        self.assertEqual(result['state'],'ready')
        self.assertTrue(cloud_assessment_ready(self.store,self.pid,self.plan['id']))
        self.assertFalse(cloud_assessment_ready(self.store,self.pid,'another-plan'))
        self.store.execute("UPDATE company_output_jobs SET state='waiting_outputs' WHERE task_uid=?",(uid,))
        self.assertFalse(cloud_assessment_ready(self.store,self.pid,self.plan['id']))
        self.store.execute("UPDATE company_output_jobs SET state='ready' WHERE task_uid=?",(uid,))
        self.store.execute("UPDATE company_training SET state='running' WHERE id=?",(id,))
        self.assertFalse(cloud_assessment_ready(self.store,self.pid,self.plan['id']))
        self.store.execute("UPDATE company_training SET state='succeeded' WHERE id=?",(id,))
        _,_,manifest,root,_=cloud_results.read(self.store,self.pid,id)
        target=next(e for e in manifest['files'] if e['name']=='metrics.json')
        path=root/target['object'];path.write_bytes(path.read_bytes()+b' ')
        self.assertFalse(cloud_assessment_ready(self.store,self.pid,self.plan['id']))

    def archive(self,rows=None,missing=False,wrong_run=False,raw=None,trial_id='trial'):
        self.approve();id=uuid.uuid4().hex;uid='task-'+id
        component={'uid':'fixed-component','digest':'a'*64}
        frozen=cloud_results.freeze(self.store,self.plan,trial_id,id,component,{'required_files':['metrics.json']})
        stage=self.workspace/'producer'/id;stage.mkdir(parents=True)
        (stage/'metrics.json').write_text(raw if raw is not None else json.dumps({'results':comparison_fixtures.observations() if rows is None else rows}),encoding='utf-8')
        receipt=write(frozen,self.workspace,stage)
        if wrong_run:
            receipt['expected']['run_id']='other-run'
            (stage/'iterlab-run.json').write_text(json.dumps(receipt),encoding='utf-8')
        objects={p.name:p.read_bytes() for p in stage.iterdir()}
        if missing:objects.pop('metrics.json')
        self.company=Company(self.store,locator=lambda n:n);self.addCleanup(self.company.close)
        self.company.configure({'revision':0,'lpai_enabled':True})
        stamp=now()
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (id,self.sid,self.pid,self.plan['id'],trial_id,component['uid'],uid,dumps({'output_contract':frozen,'component':component}),'succeeded',None,stamp,stamp))
        self.store.execute('INSERT INTO company_watches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('watch-'+id,self.sid,'vc-chassis',uid,'fixture','succeeded',dumps({'watch_authority':{'revision':1}}),1,0,'none',stamp))
        def runner(argv,**kwargs):
            request=json.loads(kwargs['input_data'])
            manifest=collect(ExactClient(objects),{'endpoint':'fixture.invalid','bucket':'fixture','path':'task/root'},
                uid,'vc-chassis',request['output_dir'],TransferBudget(),required_files=request.get('required_files'))
            return {'ok':True,'manifest':manifest},{'owned_cleanup_confirmed':True}
        self.outputs=TaskOutputs(self.company,runner=runner)
        runtime=self.store.root/'runtime';python=runtime/'.runtime/lpai/Scripts/python.exe';python.parent.mkdir(parents=True);python.write_text('not executed')
        with patch('lab.task_outputs.ROOT',runtime),patch.object(self.company,'lpai',return_value={'item':{'uid':uid,'namespace':'vc-chassis','phase':'succeeded'}}):
            result=self.outputs.advance(self.sid,uid,clock=100)
        return id,uid,frozen,stage,result

    def test_full_archive_numeric_check_preserves_negative_recall_and_rereads_source(self):
        id,uid,_,stage,result=self.archive()
        self.assertEqual(result['state'],'ready',result);self.assertTrue(result['result_check']['identity_verified'])
        compared=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        self.assertEqual(compared['state'],'verified',compared);self.assertFalse(compared['rules_passed'])
        self.assertEqual([r['key'] for r in compared['failed_rules']],['recall'])
        self.assertTrue(compared['prospective']);self.assertFalse(compared['authorizes_execution'])
        self.research.comparison.read(self.pid,compared['comparison_id'])
        archive=self.store.one('SELECT * FROM company_output_reads WHERE task_uid=?',(uid,))
        manifest=verify_archive(self.workspace/archive['path']/'files',uid,'vc-chassis')
        entry=next(e for e in manifest['files'] if e['name']=='metrics.json')
        from lab.task_output_files import local_path
        local_path(self.workspace/archive['path']/'files'/entry['object']).write_text('tampered')
        with self.assertRaises(ValueError):self.research.comparison.read(self.pid,compared['comparison_id'])

    def test_only_exact_verified_cloud_archive_unlocks_trial_dependency(self):
        from lab.rounds import check_trial_dependencies
        from lab.task_output_files import local_path
        def trial(title,**fields):
            return self.store.add_record(self.sid,{'kind':'trial','title':title,'content':title,
                'plan_id':self.plan['id'],**fields},{})
        before=trial('前置');after=trial('后继',depends_on_trial_ids=[before['id']])
        check=lambda:check_trial_dependencies(self.store,self.pid,self.plan['id'],after['id'])
        with self.assertRaisesRegex(ValueError,'前置实验'):check()
        id,uid,_,_,result=self.archive(trial_id=before['id'])
        from lab.round_evidence import packet
        self.assertEqual(self.store.record_statuses(self.pid)[before['id']]['status'],'completed')
        cloud=next(r for r in packet(self.store,self.pid)['attempts'] if r['run_id']==id)
        self.assertEqual(cloud['collection']['manifest_sha256'],result['manifest_sha256'])
        self.assertEqual(check(),[{'trial_id':before['id'],'run_id':id,'location':'cloud','task_uid':uid,
                                  'manifest_sha256':result['manifest_sha256']}])
        archive=self.store.one('SELECT * FROM company_output_reads WHERE task_uid=?',(uid,))
        root=local_path(self.workspace/archive['path']/'files')
        manifest=verify_archive(root,uid,'vc-chassis')
        entry=next(e for e in manifest['files'] if e['name']=='metrics.json')
        (root/entry['object']).write_bytes(b'invalid')
        with self.assertRaisesRegex(ValueError,'前置实验'):check()

    def test_missing_required_does_not_wake_as_result_ready(self):
        id,_,_,_,result=self.archive(missing=True)
        self.assertEqual(result['state'],'waiting_outputs',result)
        self.assertIn('metrics.json',result['result_check']['missing_files'])
        compared=self.research.comparison.compare(self.sid,self.plan['id'],[id])
        self.assertEqual(compared['state'],'not_comparable');self.assertIsNone(compared['targets_met'])

    def test_cloud_packet_links_actual_assessment_and_basis_without_title_guessing(self):
        from lab.round_evidence import packet
        id,uid,_,_,_=self.archive()
        self.store.add_record(self.sid,{'kind':'result','title':'仅正文提到原任务','content':id,'evidence':[]},{})
        entry=next(r for r in packet(self.store,self.pid)['attempts'] if r['run_id']==id)
        self.assertTrue(entry['assessment_pending']);self.assertEqual(entry['result_records'],[])
        result=self.store.add_record(self.sid,{'kind':'decision','title':'负面结果与竞争解释','content':'评价结果仍未达标，下一步需区分解释。',
            'cloud_run_ids':[id],'evidence':[]},{})
        trial=self.store.add_record(self.sid,{'kind':'trial','title':'有明确依据的后继','content':'只检验一个竞争解释。',
            'plan_id':self.plan['id'],'basis_record_id':result['id'],'evidence':[]},{})
        # A second failed attempt has no assessment; it must not inherit the first.
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('next-run',self.sid,self.pid,self.plan['id'],trial['id'],'fixed-component','next-task','{}','failed',None,now(),now()))
        entries={r['run_id']:r for r in packet(self.store,self.pid)['attempts']}
        self.assertFalse(entries[id]['assessment_pending'])
        self.assertEqual(entries[id]['result_records'],[{'id':result['id'],'title':result['title'],'kind':'decision'}])
        self.assertTrue(entries['next-run']['assessment_pending'])
        self.assertEqual(entries['next-run']['basis']['id'],result['id'])

    def test_other_run_receipt_is_diagnostic_not_scientific_result(self):
        id,_,_,_,result=self.archive(wrong_run=True)
        self.assertEqual(result['state'],'diagnostic_only',result);self.assertEqual(result['archive_state'],'identity_mismatch')
        self.assertEqual(self.research.comparison.compare(self.sid,self.plan['id'],[id])['state'],'not_comparable')

    def test_nan_metrics_cannot_pass_even_with_correct_run_identity(self):
        id,_,_,_,result=self.archive(raw='{"results":[{"variant":"candidate","seed":42,"f1":NaN}]}')
        self.assertEqual(result['state'],'ready')
        self.assertEqual(self.research.comparison.compare(self.sid,self.plan['id'],[id])['state'],'not_comparable')

    def test_producer_refuses_changed_protocol_and_overwriting_receipt(self):
        _,_,frozen,stage,_=self.archive()
        (stage/'metrics.json').write_text('changed')
        with self.assertRaisesRegex(ValueError,'收据'):write(frozen,self.workspace,stage)
        (self.workspace/'protocol.json').write_text('changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'协议'):write(frozen,self.workspace,stage)

    def test_receipt_with_edited_protocol_hash_is_not_accepted(self):
        id,uid,_,_,_=self.archive()
        row=self.store.one('SELECT * FROM company_output_reads WHERE task_uid=?',(uid,))
        from lab.task_output_files import local_path
        root=local_path(self.workspace/row['path']/'files');manifest=verify_archive(root,uid,'vc-chassis')
        receipt=next(e for e in manifest['files'] if e['name']=='iterlab-run.json')
        data=json.loads((root/receipt['object']).read_text());data['protocols']['protocol.json']='b'*64
        body=json.dumps(data).encode();sha=hashlib.sha256(body).hexdigest();(root/sha).write_bytes(body)
        manifest['bytes_saved']+=len(body)-receipt['size'];receipt.update(object=sha,sha256=sha,size=len(body))
        with self.assertRaisesRegex(ValueError,'协议'):cloud_results.validate_archive(root,manifest,json.loads(self.store.one('SELECT request FROM company_training WHERE id=?',(id,))['request'])['output_contract'])

    def test_required_files_beat_logs_and_exact_head_survives_truncated_page(self):
        stage=self.workspace/'priority';stage.mkdir()
        objects={**{f'noise{i}.log':b'log' for i in range(10)},'metrics.json':b'{"f1":0.5}'}
        client=ExactClient(objects)
        client.list_objects_v2=lambda **kwargs:{'Contents':[{'Key':'task/root/'+n,'Size':len(b)} for n,b in objects.items() if n!='metrics.json'],'IsTruncated':True}
        budget=TransferBudget()
        manifest=collect(client,{'bucket':'fixture','path':'task/root'},'uid','ns',stage,budget,required_files=['metrics.json'])
        self.assertEqual(manifest['files'][0]['name'],'metrics.json');self.assertEqual(manifest['missing_required_files'],[])
        self.assertTrue(manifest['inventory_truncated']);self.assertLessEqual(budget.calls,24)

    def test_monitor_error_is_durable_redacted_and_defers_only_failed_watch(self):
        _,uid,_,_,_=self.archive()
        watch=self.store.one('SELECT id FROM company_watches WHERE task_uid=?',(uid,))['id']
        def fail(kernel):self.company._current_watch=watch;raise RuntimeError('TOKEN=private')
        with patch.object(self.company,'poll_watches',side_effect=fail):self.company.monitor_tick(None)
        health=self.store.one('SELECT * FROM company_watch_health WHERE watch_id=?',(watch,))
        self.assertEqual(health['failures'],1);self.assertEqual(health['error_type'],'RuntimeError');self.assertNotIn('private',str(health))
        from lab.session_runs import project
        projected=project(self.company,self.sid)
        self.assertEqual(projected['history'][0]['monitor']['failures'],1)
        self.assertEqual(projected['history'][0]['collection']['state'],'ready')
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])


if __name__=='__main__':unittest.main()
