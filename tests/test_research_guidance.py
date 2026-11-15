from pathlib import Path
import json
import unittest
import uuid
import threading
from unittest.mock import patch

from lab.store import Store
from lab.kernel import Kernel
from lab.research_guidance import INSTRUCTIONS,REVISION
from tests.test_core import FakeRPC

ROOT=Path(__file__).resolve().parents[1]


class ResearchGuidanceTests(unittest.TestCase):
    def test_method_snapshot_is_atomic_even_with_concurrent_publishers(self):
        from lab.sdm import SDM
        import lab.sdm as module
        store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        sdm=SDM(store);started=threading.Event();release=threading.Event();results=[];errors=[]
        original=module.shutil.copytree;count=0;lock=threading.Lock()
        def copying(source,target,*args,**kwargs):
            nonlocal count
            with lock:count+=1;first=count==1
            if first:
                Path(target).mkdir(parents=True)
                started.set()
                if not release.wait(5):raise TimeoutError('fixture release missing')
                return original(source,target,*args,dirs_exist_ok=True,**kwargs)
            return original(source,target,*args,**kwargs)
        def publish():
            try:results.append(sdm._builtin('fixture'))
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=publish)
        try:
            with patch.object(module.shutil,'copytree',copying):
                worker.start();self.assertTrue(started.wait(5))
                publish();release.set();worker.join(5)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(len(results),2)
            self.assertEqual(results[0]['digest'],results[1]['digest'])
            self.assertEqual(list((store.root/'methods').rglob('*.tmp')),[])
        finally:
            release.set();worker.join(5);store.close()

    def test_native_resume_receives_guidance_without_changing_pinned_method_or_starting_turn(self):
        store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        kernel=Kernel(store,FakeRPC)
        kernel.settings.command=lambda *args:['fixture']
        kernel.settings.verify_channel=lambda:None
        try:
            pid=store.create_project('communication-regression')['id']
            sid=store.create_session(pid,'ept')['id']
            binding=dict(kernel.sdm.binding(sid))
            kernel.load_thread(sid)
            rpc=kernel.rpc
            params=next(p for method,p in rpc.calls if method=='thread/start')
            self.assertIn(INSTRUCTIONS,params['developerInstructions'])
            self.assertFalse(any(method=='turn/start' for method,_ in rpc.calls))
            thread=store.session(sid)['thread_id']
            kernel.loaded.clear()
            kernel.load_thread(sid)
            resume=next(p for method,p in rpc.calls if method=='thread/resume')
            self.assertEqual(resume['threadId'],thread)
            self.assertIn(REVISION,resume['developerInstructions'])
            self.assertEqual(kernel.sdm.binding(sid),binding)
            self.assertEqual(store.rows('SELECT * FROM research_method_changes'),[])
            events=store.rows("SELECT payload FROM events WHERE kind='thread.loaded'")
            self.assertEqual(len(events),2)
            self.assertTrue(all(json.loads(r['payload'])['research_guidance']==REVISION for r in events))
        finally:
            kernel.close();store.close()


if __name__=='__main__':unittest.main()
