"""Real file collection/recovery, with frozen fixture receipts. No model/training."""
import hashlib
import json
from pathlib import Path
import unittest
import uuid
import threading
import time
from unittest.mock import patch

from lab.store import Store,dumps,now
from lab.research import Research
from lab.kernel import Kernel
from lab import run_evidence
from tests.test_core import FakeRPC

ROOT=Path(__file__).resolve().parents[1]


class RunEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'.evidence/tests'/uuid.uuid4().hex
        self.store=Store(self.root);self.research=Research(self.store)
        self.pid=self.store.create_project('evidence-fixture')['id']
        self.sid=self.store.create_session(self.pid,'ept')['id']
        self.workspace=Path(self.store.session(self.sid)['workspace'])
        self.plan=self.store.add_record(self.sid,{'kind':'plan','title':'隔离证据方案','content':'不训练；检验真实结果文件与原收据的关系。'}, {})

    def tearDown(self):
        self.research.close();self.store.close()

    def make_run(self,state='completed',metrics=b'{"f1":0.8}',registered=True,checkpoint=False):
        id=uuid.uuid4().hex;directory=self.workspace/'runs'/id;directory.mkdir(parents=True)
        outputs=['metrics.json'];collected=[]
        if metrics is not None:
            (directory/'metrics.json').write_bytes(metrics)
            collected.append({'path':'runs/'+id+'/metrics.json','size':len(metrics),'sha256':hashlib.sha256(metrics).hexdigest()})
        if checkpoint:
            outputs.append('model.pt');(directory/'model.pt').write_bytes(b'fixture weights')
            collected.append({'path':'runs/'+id+'/model.pt','size':15,'sha256':hashlib.sha256(b'fixture weights').hexdigest()})
        (directory/'input-manifest.json').write_text('{}',encoding='utf-8')
        (directory/'stdout.log').write_text('fixture terminal observation\n',encoding='utf-8')
        (directory/'stderr.log').write_text('failed to load data\n' if state!='completed' else '',encoding='utf-8')
        request={'plan_id':self.plan['id'],'outputs':outputs,'purpose':'fit','question':'fixture only'}
        receipt={'run_id':id,'plan_id':self.plan['id'],'plan_version':self.plan['version'],'state':state,
            'directory':'runs/'+id,'outputs':collected,'inputs':{},'trial_id':None,'error':None,
            'exit_code':0 if state=='completed' else 1,'missing_outputs':outputs if metrics is None else []}
        (directory/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (id,'fixture:'+id,self.pid,self.sid,self.plan['id'],'fixture-grant',hashlib.sha256(dumps(request).encode()).hexdigest(),dumps(request),state,'runs/'+id,dumps(receipt),now(),now()))
        self.store.execute('INSERT INTO research_run_context VALUES(?,?,?)',(id,None,now()))
        if registered:self.store.evidence.register_local(self.store.one('SELECT * FROM research_runs WHERE id=?',(id,)))
        return id,directory,receipt

    def manifest(self,id):return self.store.evidence.verify('local',id)

    def test_actual_files_are_copied_and_same_terminal_does_not_republish(self):
        id,directory,receipt=self.make_run(checkpoint=True)
        summary=self.store.evidence.collect_local(self.sid,id);manifest=self.manifest(id)
        self.assertTrue(summary['analysis_ready']);self.assertEqual(summary['state'],'complete')
        metrics=next(f for f in manifest['files'] if f['name']=='metrics.json')
        archive=self.workspace/'research-evidence/local'/id/metrics['object']
        self.assertEqual(archive.read_bytes(),b'{"f1":0.8}')
        self.assertTrue(manifest['deferred']);self.assertEqual(manifest['deferred'][0]['name'],'model.pt')
        self.assertFalse(any(f['name']=='model.pt' for f in manifest['files']))
        before=archive.stat().st_mtime_ns
        (directory/'metrics.json').write_bytes(b'new mutable working result')
        self.assertEqual(self.store.evidence.collect_local(self.sid,id),summary)
        self.assertEqual(archive.stat().st_mtime_ns,before)
        self.assertEqual(archive.read_bytes(),b'{"f1":0.8}')
        self.assertEqual(self.store.one("SELECT count(*) n FROM events WHERE kind='research.evidence_collected'")['n'],1)
        self.assertEqual(json.loads(self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt']),receipt)

    def test_interrupted_copy_resumes_original_run_after_store_restart(self):
        id,directory,receipt=self.make_run();original=self.store.evidence._copy
        def fail_one(root,source,name,expected=None):
            if name=='metrics.json':raise OSError('fixture temporary transport failure')
            return original(root,source,name,expected)
        with patch.object(self.store.evidence,'_copy',side_effect=fail_one):
            first=self.store.evidence.collect_local(self.sid,id)
        self.assertFalse(first['analysis_ready']);self.assertEqual(first['state'],'partial')
        first_manifest=self.manifest(id);first_path=self.workspace/first['manifest_path']
        archived={f['object']:(self.workspace/'research-evidence/local'/id/f['object']).stat().st_mtime_ns for f in first_manifest['files']}
        self.store.execute('UPDATE run_evidence SET next_check=0')
        self.research.close();self.store.close();self.store=Store(self.root);self.research=Research(self.store)
        self.store.evidence.recover_local()
        summary=self.store.evidence.summary('local',id);manifest=self.manifest(id)
        self.assertTrue(summary['analysis_ready']);self.assertEqual(summary['attempts'],2)
        self.assertEqual(manifest['copied_bytes'],len(b'{"f1":0.8}'))
        self.assertTrue(first_path.is_file())
        for name,mtime in archived.items():self.assertEqual((self.workspace/'research-evidence/local'/id/name).stat().st_mtime_ns,mtime)
        self.assertEqual(self.store.one('SELECT count(*) n FROM research_runs')['n'],1)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])

    def test_automatic_collection_stops_after_three_attempts_across_restart(self):
        id,_,_=self.make_run(state='artifacts_missing',metrics=None)
        original=self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt']
        for _ in range(2):
            self.store.execute('UPDATE run_evidence SET next_check=0')
            self.store.evidence.recover_local(self.sid)
        self.research.close();self.store.close();self.store=Store(self.root);self.research=Research(self.store)
        self.store.execute('UPDATE run_evidence SET next_check=0')
        self.store.evidence.recover_local(self.sid)
        self.assertEqual(self.store.evidence.summary('local',id)['attempts'],3)
        before=self.store.rows('SELECT * FROM events')
        for _ in range(3):
            self.store.execute('UPDATE run_evidence SET next_check=0')
            self.store.evidence.recover_local(self.sid)
        self.assertEqual(self.store.evidence.summary('local',id)['attempts'],3)
        self.assertEqual(self.store.rows('SELECT * FROM events'),before)
        self.assertFalse(self.store.evidence.summary('local',id)['analysis_ready'])
        self.assertEqual(self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt'],original)

    def test_early_collection_exception_counts_and_does_not_starve_other_runs(self):
        id,_,_=self.make_run();other,_,_=self.make_run()
        collect=self.store.evidence.collect_local
        def fail_early(sid,run):
            if run==id:raise OSError('fixture disk unavailable before attempt registration')
            return collect(sid,run)
        with patch.object(self.store.evidence,'collect_local',side_effect=fail_early):
            for _ in range(4):
                self.store.execute('UPDATE run_evidence SET next_check=0')
                self.store.evidence.recover_local(self.sid)
        self.assertEqual(self.store.evidence.summary('local',id)['attempts'],3)
        self.assertTrue(self.store.evidence.summary('local',other)['analysis_ready'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM research_runs')['n'],2)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])

    def test_real_collection_readiness_is_progress_without_changing_run_or_budget(self):
        id,_,_=self.make_run()
        kernel=Kernel(self.store,FakeRPC)
        try:
            progress=kernel.roles.progress;progress.status(self.sid)
            self.store.execute("UPDATE research_role_progress SET consecutive_unchanged=8,state='no_progress',progress_pending=0 WHERE session_id=?",(self.sid,))
            before=self.store.rows('SELECT * FROM research_runs')
            self.assertTrue(progress.status(self.sid)['blocked'])
            self.store.evidence.collect_local(self.sid,id)
            self.assertFalse(progress.status(self.sid)['blocked'])
            self.assertEqual(progress.status(self.sid)['consecutive_unchanged'],0)
            self.assertEqual(self.store.rows('SELECT * FROM research_runs'),before)
        finally:kernel.close()

    def test_missing_successful_output_blocks_new_experiment_and_role_wakeup(self):
        id,directory,receipt=self.make_run(state='artifacts_missing',metrics=None)
        summary=self.store.evidence.collect_local(self.sid,id)
        self.assertFalse(summary['analysis_ready'])
        self.assertEqual(self.research.workflow.project(self.sid)['state'],'blocked')
        with self.assertRaisesRegex(ValueError,'证据'):
            self.research.run(self.sid,{'plan_id':self.plan['id']},'not-another-experiment')
        kernel=Kernel(self.store,FakeRPC)
        try:
            self.assertFalse(kernel.guard_role_dispatch(self.sid,{'delivery_id':'not-dispatched'}))
            kernel.continuation.tick(self.sid)
            self.assertIsNone(kernel.rpc)
            self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
        finally:kernel.close()

    def test_failure_keeps_available_evidence_without_claiming_model_performance(self):
        id,directory,receipt=self.make_run(state='failed',metrics=None)
        summary=self.store.evidence.collect_local(self.sid,id)
        self.assertTrue(summary['analysis_ready']);self.assertEqual(summary['analysis_scope'],'failure_diagnosis')
        self.assertEqual(summary['state'],'partial');self.assertTrue(self.manifest(id)['missing'])
        self.store.execute('UPDATE run_evidence SET next_check=0')
        self.store.evidence.recover_local(self.sid)
        self.assertEqual(self.store.evidence.summary('local',id)['attempts'],1)

    def test_empty_declared_result_is_diagnostic_evidence_not_a_model_result(self):
        id,directory,receipt=self.make_run(metrics=b'')
        saved=self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt']
        result=self.store.evidence.collect_local(self.sid,id)
        self.assertTrue(result['analysis_ready']);self.assertEqual(result['analysis_scope'],'failure_diagnosis')
        self.assertEqual(self.manifest(id)['invalid_outputs'],['metrics.json'])
        self.assertEqual(self.store.one('SELECT receipt FROM research_runs WHERE id=?',(id,))['receipt'],saved)
        self.store.evidence.recover_local(self.sid)
        self.assertEqual(self.store.evidence.summary('local',id)['attempts'],1)

    def test_archive_corruption_is_not_silently_repaired_or_overwritten(self):
        id,directory,receipt=self.make_run();self.store.evidence.collect_local(self.sid,id)
        manifest=self.manifest(id);entry=next(f for f in manifest['files'] if f['name']=='metrics.json')
        path=self.workspace/'research-evidence/local'/id/entry['object'];path.write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.store.evidence.collect_local(self.sid,id)
        self.assertEqual(self.store.evidence.summary('local',id)['state'],'corrupt')
        self.assertFalse(self.store.evidence.summary('local',id)['analysis_ready'])
        self.store.evidence.recover_local(self.sid);self.assertEqual(path.read_bytes(),b'tampered')
        self.assertEqual((directory/'metrics.json').read_bytes(),b'{"f1":0.8}')

    def test_receipt_mismatch_and_run_path_escape_cannot_be_collected(self):
        for bad in ['identity','traversal']:
            with self.subTest(bad=bad):
                id,directory,receipt=self.make_run()
                if bad=='identity':receipt['plan_id']='other-plan'
                else:receipt['outputs'][0]['path']='runs/'+id+'/../outside.json'
                self.store.execute('UPDATE research_runs SET receipt=? WHERE id=?',(dumps(receipt),id))
                (directory/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
                summary=self.store.evidence.collect_local(self.sid,id)
                self.assertFalse(summary['analysis_ready']);self.assertEqual(summary['state'],'failed')

    def test_changed_output_before_first_collection_is_not_accepted(self):
        id,directory,receipt=self.make_run();(directory/'metrics.json').write_bytes(b'{"f1":1.0}')
        summary=self.store.evidence.collect_local(self.sid,id)
        self.assertFalse(summary['analysis_ready']);self.assertEqual(summary['state'],'partial')
        self.assertFalse(any(f['name']=='metrics.json' for f in self.manifest(id)['files']))

    def test_unknown_and_legacy_runs_are_not_replayed_or_backfilled(self):
        id,directory,receipt=self.make_run(state='unknown')
        other,_,_=self.make_run(registered=False)
        self.store.evidence.recover_local(self.sid)
        self.assertEqual(self.store.evidence.summary('local',id)['attempts'],0)
        self.assertIsNone(self.store.evidence.summary('local',other))
        self.assertEqual(self.store.one('SELECT state FROM research_runs WHERE id=?',(id,))['state'],'unknown')
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])

    def test_no_cross_session_collection_or_backend_overwrite(self):
        id,directory,receipt=self.make_run();sid2=self.store.create_session(self.pid,'ept')['id']
        with self.assertRaises(ValueError):self.store.evidence.collect_local(sid2,id)
        with self.assertRaises(ValueError):self.research.write(self.sid,'research-evidence/local/receipt.json','overwritten')

    def test_large_logs_are_bounded_excerpts_with_original_offsets(self):
        id,directory,receipt=self.make_run()
        (directory/'stdout.log').write_bytes(b'0123456789ABCDEFGHIJ')
        with patch.object(run_evidence,'LOG_TAIL',8):summary=self.store.evidence.collect_local(self.sid,id)
        self.assertTrue(summary['analysis_ready'])
        manifest=self.manifest(id);entry=next(f for f in manifest['files'] if f['role']=='stdout')
        self.assertTrue(entry['excerpt']);self.assertEqual(entry['source_offset'],12)
        self.assertEqual(entry['source_size'],20)
        self.assertEqual((self.workspace/'research-evidence/local'/id/entry['object']).read_bytes(),b'CDEFGHIJ')
        self.assertEqual((directory/'stdout.log').read_bytes(),b'0123456789ABCDEFGHIJ')

    def test_guard_rechecks_changed_or_lost_archives_without_loading_model(self):
        for mutation in ['modify','delete']:
            with self.subTest(mutation=mutation):
                id,directory,receipt=self.make_run();self.store.evidence.collect_local(self.sid,id)
                self.store.evidence.blocking(self.pid,verify_ready=True)
                entry=next(f for f in self.manifest(id)['files'] if f['name']=='metrics.json')
                path=self.workspace/'research-evidence/local'/id/entry['object']
                if mutation=='modify':path.write_bytes(b'changed')
                else:path.unlink()
                self.assertIsNotNone(self.store.evidence.blocking(self.pid,verify_ready=True))
                self.assertEqual(self.store.evidence.summary('local',id)['state'],'corrupt')

    def test_prelaunch_failure_can_be_diagnosed_without_fake_training_results(self):
        id,directory,receipt=self.make_run(state='failed',metrics=None)
        receipt['error']='fixture input snapshot failed before process start'
        self.store.execute('UPDATE research_runs SET receipt=? WHERE id=?',(dumps(receipt),id))
        (directory/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
        for name in ['input-manifest.json','stdout.log','stderr.log']:(directory/name).unlink()
        summary=self.store.evidence.collect_local(self.sid,id)
        self.assertTrue(summary['analysis_ready']);self.assertEqual(summary['analysis_scope'],'failure_diagnosis')
        self.assertEqual([entry['name'] for entry in self.manifest(id)['files']],['receipt.json'])

    def test_frozen_configuration_is_archived_and_manifest_mismatch_blocks_success(self):
        id,directory,receipt=self.make_run();body=b'{"learning_rate":0.01}'
        (directory/'config.json').write_bytes(body)
        receipt['inputs']={'config.json':{'sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}}
        self.store.execute('UPDATE research_runs SET receipt=? WHERE id=?',(dumps(receipt),id))
        (directory/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
        (directory/'input-manifest.json').write_text(json.dumps(receipt['inputs']),encoding='utf-8')
        self.assertTrue(self.store.evidence.collect_local(self.sid,id)['analysis_ready'])
        entry=next(f for f in self.manifest(id)['files'] if f['name']=='config.json')
        (directory/'config.json').write_text('changed after archival')
        self.assertEqual((self.workspace/'research-evidence/local'/id/entry['object']).read_bytes(),body)
        other,folder,_=self.make_run();(folder/'input-manifest.json').write_text('{"wrong":true}')
        self.assertFalse(self.store.evidence.collect_local(self.sid,other)['analysis_ready'])

    def test_published_snapshot_verification_does_not_wait_for_collection_writer(self):
        id,directory,receipt=self.make_run();summary=self.store.evidence.collect_local(self.sid,id)
        done=threading.Event();errors=[]
        def read():
            try:
                with self.store.lock:self.store.evidence.verify('local',id)
            except Exception as exc:errors.append(type(exc).__name__)
            finally:done.set()
        with self.store.evidence._lock(summary['id']):
            worker=threading.Thread(target=read);worker.start()
            completed=done.wait(3)
        worker.join(5)
        self.assertFalse(worker.is_alive());self.assertTrue(completed,'published readers must not wait for writer/DB lock inversion')
        self.assertEqual(errors,[])

    def test_background_timer_recovers_files_without_model_or_user_delivery(self):
        id,directory,receipt=self.make_run();original=self.store.evidence._copy
        def unavailable(root,source,name,expected=None):
            if name=='metrics.json':raise OSError('fixture temporary copy failure')
            return original(root,source,name,expected)
        with patch.object(self.store.evidence,'_copy',side_effect=unavailable):
            self.assertFalse(self.store.evidence.collect_local(self.sid,id)['analysis_ready'])
        self.store.execute('UPDATE run_evidence SET next_check=0')
        kernel=Kernel(self.store,FakeRPC)
        try:
            kernel.continuation.start();deadline=time.monotonic()+18
            with self.store.changed:
                while not self.store.evidence.summary('local',id)['analysis_ready']:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    self.store.changed.wait(remaining)
            self.assertTrue(self.store.evidence.summary('local',id)['analysis_ready'],'real background timer recovery deadline')
            self.assertEqual(self.store.evidence.summary('local',id)['attempts'],2)
            self.assertIsNone(kernel.rpc)
            self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])
            self.assertEqual(self.store.one('SELECT count(*) n FROM research_runs')['n'],1)
            self.assertEqual(self.manifest(id)['identity']['run_id'],id)
        finally:kernel.close()

    def test_global_recovery_uses_each_original_session_identity(self):
        first,_,_=self.make_run();first_sid=self.sid
        second_sid=self.store.create_session(self.pid,'ept')['id']
        try:
            self.sid=second_sid;second,_,_=self.make_run()
        finally:self.sid=first_sid
        self.store.evidence.recover_local()
        self.assertTrue(self.store.evidence.summary('local',first)['analysis_ready'])
        self.assertTrue(self.store.evidence.summary('local',second)['analysis_ready'])
        self.assertEqual(self.manifest(first)['identity']['session_id'],first_sid)
        self.assertEqual(self.manifest(second)['identity']['session_id'],second_sid)
        self.assertEqual(self.store.rows('SELECT * FROM deliveries'),[])


if __name__=='__main__':unittest.main()
