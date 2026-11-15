import tempfile
import unittest
from lab.store import Store,now
from lab.kernel import Kernel
from lab.transport import RPCUnknown
from tests.test_core import FakeRPC


class CatalogRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.instances=[];self.failures=0
        outer=self
        class Discovery(FakeRPC):
            def __init__(self,*a):super().__init__(*a);outer.instances.append(self)
            def request(self,method,params,timeout=60):
                if method=='model/list' and outer.failures:
                    outer.failures-=1;raise RPCUnknown('process exited')
                return super().request(method,params,timeout)
        self.kernel=Kernel(self.store,Discovery);self.kernel.settings.value['channel']='codex'
        self.kernel.settings.command=lambda *a:['fixture'];self.kernel.settings.verify_channel=lambda:None
    def tearDown(self):self.kernel.close();self.store.close();self.temp.cleanup()
    def test_read_retry_and_cache_never_create_research_runtime_or_delivery(self):
        self.failures=1;result=self.kernel.model_catalog('codex')
        self.assertEqual(len(result['models']),4);self.assertEqual(len(self.instances),2)
        self.assertTrue(all(r.closed for r in self.instances));self.assertIsNone(self.kernel.rpc)
        for peer in self.instances:
            for method,params in peer.calls:
                if method=='model/list':self.assertIs(params.get('includeHidden'),True)
        self.assertTrue(self.kernel.model_catalog('codex')['cached'])
        self.assertEqual(len(self.instances),2);self.assertFalse(self.store.rows('SELECT * FROM deliveries'))
    def test_stale_cache_is_marked_and_unknown_writes_are_untouched(self):
        self.kernel.model_catalog('codex')
        for value in self.kernel.catalog_cache.values():value['time']-=61
        pid=self.store.create_project('fixture')['id'];sid=self.store.create_session(pid,'codex')['id'];stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('original',sid,'original','queue','unknown',None,'keep',stamp,stamp))
        self.failures=2;result=self.kernel.model_catalog('codex')
        self.assertTrue(result['stale']);self.assertEqual(len(self.instances),3)
        self.assertEqual(self.store.one('SELECT state FROM deliveries')['state'],'unknown')
    def test_persistent_failure_is_bounded_without_cache(self):
        self.failures=10
        with self.assertRaisesRegex(ValueError,'只读连接'):self.kernel.model_catalog('codex')
        self.assertEqual(len(self.instances),2);self.assertIsNone(self.kernel.rpc)
