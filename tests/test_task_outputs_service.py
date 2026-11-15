import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from lab.company import Company
from lab.store import Store,dumps,now
from lab.cloud_evidence import TransferBudget
from lab.task_output_files import collect,local_path
from lab.task_outputs import TaskOutputs
from tests.test_task_output_files import Client


class TaskOutputsServiceTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.evidence/tests'/uuid.uuid4().hex
        self.store=Store(self.root)
        self.project=self.store.create_project('output-service')
        self.session=self.store.create_session(self.project['id'],'ept')
        self.company=Company(self.store,locator=lambda name:name)
        self.company.configure({'revision':0,'lpai_enabled':True})
        self.uid='original-task';self.calls=[];self.objects={'metrics.json':b'{"loss":0.3}','model.pt':b'weight'}
        self.bind(self.session['id'])
        def runner(argv,**kwargs):
            self.calls.append((argv,kwargs));request=json.loads(kwargs['input_data'])
            self.assertEqual(argv[-2],'--archive-root')
            self.assertEqual(Path(request['output_dir']),Path(argv[-1])/'files')
            manifest=collect(Client(self.objects),{'endpoint':'fixture.invalid','bucket':'fixture','path':'task/root'},
                             request['task_uid'],request['namespace'],request['output_dir'],TransferBudget())
            return {'ok':True,'manifest':manifest},{'owned_cleanup_confirmed':True}
        self.outputs=TaskOutputs(self.company,runner=runner)
        self.read=patch.object(self.company,'lpai',return_value={'item':{'uid':self.uid,'namespace':'vc-chassis','phase':'failed'}})
        self.read.start()
        # The child runner is injected; no SDK, credentials, model or cloud is used.
        import os
        executable=self.root/'.runtime/lpai'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        executable.parent.mkdir(parents=True);executable.write_text('fixture, never executed',encoding='utf-8')
        self.python=patch('lab.task_outputs.ROOT',self.root)
        self.python.start()

    def bind(self,sid):
        stamp=now()
        self.store.execute('INSERT INTO company_watches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex,sid,'vc-chassis',self.uid,'fixture','failed',dumps({'watch_authority':{'revision':1}}),0,0,'none',stamp))

    def tearDown(self):
        self.python.stop();self.read.stop();self.company.close();self.store.close()
        assert self.root.resolve().is_relative_to((Path(__file__).resolve().parents[1]/'.evidence/tests').resolve())
        shutil.rmtree(local_path(self.root))

    def test_same_session_archive_verified_cache_and_model_tool_registered(self):
        first=self.outputs.collect(self.session['id'],self.uid)
        self.assertEqual(first['state'],'files_archived',first);self.assertEqual(first['files_saved'],1)
        self.assertFalse(first['scientific_result_validated']);self.assertIn('科学',first['report'])
        second=self.outputs.collect(self.session['id'],self.uid)
        self.assertTrue(second['cached']);self.assertEqual(len(self.calls),1)
        from lab.kernel import COMPANY_TOOLS
        tool=next(item for item in COMPANY_TOOLS if item['name']=='lpai_collect')
        self.assertNotIn('output_dir',tool['inputSchema']['properties'])

    def test_other_session_channel_namespace_and_revision_rejected_before_read(self):
        other=self.store.create_session(self.project['id'],'ept')
        with self.assertRaisesRegex(ValueError,'当前会话'):self.outputs.collect(other['id'],self.uid)
        self.bind(other['id']);self.store.execute("UPDATE company_watches SET namespace='elsewhere' WHERE session_id=?",(other['id'],))
        with self.assertRaises(ValueError):self.outputs.collect(other['id'],self.uid)
        self.store.execute("UPDATE sessions SET channel='codex' WHERE id=?",(self.session['id'],))
        with self.assertRaisesRegex(ValueError,'EPT'):self.outputs.collect(self.session['id'],self.uid)
        self.store.execute("UPDATE sessions SET channel='ept' WHERE id=?",(self.session['id'],))
        self.company.configure({'revision':1,'lpai_enabled':True,'namespace':'other'})
        with self.assertRaises(ValueError):self.outputs.collect(self.session['id'],self.uid)
        self.assertEqual(self.calls,[])

    def test_evidence_identity_tracks_bytes_not_archive_attempt_or_copy_location(self):
        first=self.outputs.advance(self.session['id'],self.uid,clock=100)
        second=self.outputs.collect(self.session['id'],self.uid,refresh=True)
        self.assertNotEqual(first['manifest_path'],second['manifest_path'])
        self.assertEqual(first['evidence_digest'],second['evidence_digest'])
        self.objects['metrics.json']=b'{"loss":0.7}'
        third=self.outputs.collect(self.session['id'],self.uid,refresh=True)
        self.assertNotEqual(first['evidence_digest'],third['evidence_digest'])
        from lab.task_outputs import status
        self.assertEqual(status(self.store,self.session['id'],self.uid)['evidence_digest'],third['evidence_digest'])
        self.assertFalse(third['scientific_result_validated'])
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])

    def test_empty_is_diagnostic_refresh_is_explicit_and_cumulative_bound_survives_restart(self):
        self.objects={}
        first=self.outputs.collect(self.session['id'],self.uid)
        self.assertEqual(first['state'],'directory_empty');self.assertIn('不代表挂载盘',first['report'])
        self.outputs.collect(self.session['id'],self.uid)
        for _ in range(2):self.outputs.collect(self.session['id'],self.uid,True)
        restart=TaskOutputs(self.company,runner=self.outputs.runner)
        with self.assertRaisesRegex(ValueError,'三次'):restart.collect(self.session['id'],self.uid,True)
        self.assertEqual(len(self.calls),3)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM company_output_reads')['n'],3)

    def test_timeout_never_replays_unknown_even_with_refresh_and_reconciles_published_manifest(self):
        def timeout(*args,**kwargs):self.calls.append('timeout');raise TimeoutError('fixture')
        self.outputs.runner=timeout
        result=self.outputs.collect(self.session['id'],self.uid)
        self.assertEqual(result['state'],'unconfirmed')
        again=TaskOutputs(self.company,runner=timeout).collect(self.session['id'],self.uid,True)
        self.assertEqual(again['attempt'],1);self.assertEqual(len(self.calls),1)
        row=self.store.one('SELECT * FROM company_output_reads')
        root=self.store.safe_path(self.project['id'],row['path'])
        collect(Client(self.objects),{'endpoint':'fixture.invalid','bucket':'fixture','path':'task/root'},
                self.uid,'vc-chassis',root/'files',TransferBudget())
        reconciled=self.outputs.collect(self.session['id'],self.uid,True)
        self.assertTrue(reconciled['reconciled_locally']);self.assertEqual(len(self.calls),1)

    def test_cached_manifest_and_report_tampering_rejected_not_repaired_by_redownload(self):
        result=self.outputs.collect(self.session['id'],self.uid)
        report=local_path(self.store.safe_path(self.project['id'],result['report_path']));report.write_text('changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'已变化'):self.outputs.collect(self.session['id'],self.uid)
        self.assertEqual(len(self.calls),1)

    def test_removed_project_rejected_before_any_remote_read_or_attempt_reservation(self):
        self.store.execute('INSERT INTO workbench_trash VALUES(?,?,?)',('project',self.project['id'],now()))
        with self.assertRaisesRegex(ValueError,'已删除'):self.outputs.collect(self.session['id'],self.uid)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM company_output_reads')['n'],0)

    def test_background_upload_wait_persists_and_new_bytes_are_collected_without_model(self):
        from lab.task_outputs import status
        self.objects={}
        first=self.outputs.advance(self.session['id'],self.uid,clock=100)
        self.assertEqual(first['state'],'waiting_outputs');self.assertEqual(first['next_check'],160)
        self.outputs.advance(self.session['id'],self.uid,clock=159)
        self.assertEqual(len(self.calls),1)
        restarted=TaskOutputs(self.company,runner=self.outputs.runner)
        self.objects={'metrics.json':b'{"loss":0.2}'}
        result=restarted.advance(self.session['id'],self.uid,clock=160)
        self.assertEqual(result['state'],'ready');self.assertEqual(result['attempts'],2)
        self.assertFalse(result['scientific_result_validated'])
        restarted.advance(self.session['id'],self.uid,clock=999)
        self.assertEqual(len(self.calls),2);self.assertEqual(status(self.store,self.session['id'],self.uid)['state'],'ready')

    def test_empty_background_directory_stops_waiting_after_shared_three_attempts(self):
        self.objects={}
        for stamp in (100,160,340):result=self.outputs.advance(self.session['id'],self.uid,clock=stamp)
        self.assertEqual(result['state'],'diagnostic_only');self.assertEqual(result['attempts'],3)
        self.assertEqual(result['analysis_scope'],'failure_or_evidence_gap_only')
        self.outputs.advance(self.session['id'],self.uid,clock=999)
        self.assertEqual(len(self.calls),3)

    def test_background_unknown_does_not_download_again_and_concurrent_reader_is_not_started(self):
        def timeout(*args,**kwargs):self.calls.append('timeout');raise TimeoutError('fixture')
        self.outputs.runner=timeout
        result=self.outputs.advance(self.session['id'],self.uid,clock=100)
        self.assertEqual(result['state'],'diagnostic_only');self.assertEqual(result['archive_state'],'unconfirmed')
        self.outputs.advance(self.session['id'],self.uid,clock=999)
        self.assertEqual(self.calls,['timeout'])

    def test_native_wakeup_gate_ends_after_archive_without_certifying_science(self):
        from lab.task_outputs import pending,require_collected
        from lab.workflow import Workflow
        workflow=Workflow(self.store)
        self.store.execute('UPDATE company_watches SET auto_resume=1')
        self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('submit',self.session['id'],self.project['id'],'fixture-plan','trial','component',self.uid,'{}','failed',None,now(),now()))
        self.assertIsNotNone(pending(self.store,self.session['id']))
        with self.assertRaisesRegex(ValueError,'不能跳过回收'):require_collected(self.store,self.project['id'])
        # A disconnected native thread must not be restored just to wait for files.
        self.assertTrue(workflow.external_pending(self.session['id']))
        self.outputs.advance(self.session['id'],self.uid,clock=100)
        self.assertIsNone(pending(self.store,self.session['id']))
        require_collected(self.store,self.project['id'])
        self.assertFalse(workflow.external_pending(self.session['id']))

    def test_known_network_failure_waits_persistently_then_archives_without_training(self):
        from lab.task_outputs import TaskOutputs as Collector,status
        original=self.outputs.runner;calls=[]
        def transient(*args,**kw):
            calls.append('network')
            return {'ok':False,'error':'network_timeout','message':'SECRET never persist'},{'owned_cleanup_confirmed':True}
        self.outputs.runner=transient
        first=self.outputs.advance(self.session['id'],self.uid,clock=100)
        self.assertEqual(first['state'],'waiting_outputs');self.assertTrue(first['retryable'])
        self.assertEqual(first['attempts'],1);self.assertEqual(first['next_check'],160)
        # Recreate the collector: backoff and the counter belong to the Store.
        restarted=Collector(self.company,runner=original)
        self.assertEqual(restarted.advance(self.session['id'],self.uid,clock=159),status(self.store,self.session['id'],self.uid))
        self.assertEqual(len(self.calls),0)
        result=restarted.advance(self.session['id'],self.uid,clock=160)
        self.assertEqual(result['state'],'ready');self.assertEqual(result['attempts'],2)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('SECRET',json.dumps(self.store.rows('SELECT * FROM company_output_reads')))
        self.assertEqual(self.store.rows('SELECT * FROM company_training'),[])
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])

    def test_network_read_retries_share_three_attempt_limit_and_never_loop(self):
        calls=[]
        def unavailable(*args,**kw):
            calls.append(1)
            return {'ok':False,'error':'read_unconfirmed','http_status':503},{'owned_cleanup_confirmed':True}
        self.outputs.runner=unavailable
        results=[self.outputs.advance(self.session['id'],self.uid,clock=stamp) for stamp in [100,160,340,1000]]
        self.assertEqual([r['state'] for r in results],['waiting_outputs','waiting_outputs','diagnostic_only','diagnostic_only'])
        self.assertEqual(len(calls),3);self.assertEqual(results[-1]['attempts'],3)
        self.assertEqual(results[-1]['analysis_scope'],'failure_or_evidence_gap_only')

    def test_readonly_task_lookup_timeout_is_not_an_unknown_transfer(self):
        with patch.object(self.company,'lpai',side_effect=TimeoutError('private network diagnostic')):
            first=self.outputs.advance(self.session['id'],self.uid,clock=100)
        self.assertEqual(first['state'],'waiting_outputs');self.assertEqual(first['archive_state'],'read_failed')
        self.assertTrue(first['retryable']);self.assertEqual(self.calls,[])
        result=self.outputs.advance(self.session['id'],self.uid,clock=160)
        self.assertEqual(result['state'],'ready');self.assertEqual(result['attempts'],2)
        self.assertEqual(len(self.calls),1)

    def test_auth_boundary_and_unconfirmed_child_are_never_retried(self):
        for category,cleanup in [('auth_unavailable',True),('access_denied',True),('boundary_rejected',True),('network_timeout',False)]:
            sid=self.store.create_session(self.project['id'],'ept')['id'];self.bind(sid);calls=[]
            def failed(*a,**kw):
                calls.append(1)
                return {'ok':False,'error':category,'http_status':503},{'owned_cleanup_confirmed':cleanup}
            self.outputs.runner=failed
            first=self.outputs.advance(sid,self.uid,clock=100)
            again=self.outputs.advance(sid,self.uid,clock=999)
            self.assertEqual(first['state'],'diagnostic_only');self.assertEqual(again['attempts'],1)
            self.assertEqual(len(calls),1)
            if not cleanup:self.assertEqual(again['archive_state'],'unconfirmed')

    def test_concurrent_manual_read_cannot_start_second_transfer(self):
        import threading
        entered=threading.Event();release=threading.Event();result=[];original=self.outputs.runner
        def blocked(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise TimeoutError('test release')
            return original(*args,**kwargs)
        self.outputs.runner=blocked
        worker=threading.Thread(target=lambda:result.append(self.outputs.collect(self.session['id'],self.uid)))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            second=self.outputs.collect(self.session['id'],self.uid,True)
            self.assertEqual(second['state'],'collecting');self.assertEqual(self.calls,[])
        finally:
            release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(len(self.calls),1)
        self.assertEqual(result[0]['state'],'files_archived')

    def test_manual_new_archive_updates_terminal_job_without_reset_or_model(self):
        self.outputs.runner=lambda *a,**kw:(_ for _ in ()).throw(ValueError('fixture read failure'))
        first=self.outputs.advance(self.session['id'],self.uid,clock=100)
        self.assertEqual(first['state'],'diagnostic_only');self.assertEqual(first['attempts'],1)
        # Explicit refresh uses the original cumulative counter, not a new job.
        def runner(argv,**kwargs):
            request=json.loads(kwargs['input_data']);self.calls.append(request)
            manifest=collect(Client(self.objects),{'bucket':'fixture','path':'task/root'},self.uid,'vc-chassis',request['output_dir'],TransferBudget())
            return {'ok':True,'manifest':manifest},{'owned_cleanup_confirmed':True}
        self.outputs.runner=runner
        result=self.outputs.collect(self.session['id'],self.uid,True)
        from lab.task_outputs import status
        job=status(self.store,self.session['id'],self.uid)
        self.assertEqual(result['attempt'],2);self.assertEqual(job['state'],'ready');self.assertEqual(job['attempts'],2)
        self.outputs.advance(self.session['id'],self.uid,clock=999)
        self.assertEqual(len(self.calls),1);self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])


if __name__=='__main__':unittest.main()
