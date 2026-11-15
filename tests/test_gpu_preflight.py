import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lab import devspace, gpu_preflight as gpu


ROWS=[{'index':0,'uuid':'GPU-00000000-0000-0000-0000-000000000001'},
      {'index':1,'uuid':'GPU-00000000-0000-0000-0000-000000000002'}]


class Driver:
    def __init__(self, oom=False):self.calls=[];self.oom=oom
    def __getattr__(self,name):
        def invoke(*args):
            self.calls.append(name)
            if name=='cuDevicePrimaryCtxRetain':args[0]._obj.value=123
            if name=='cuMemGetInfo_v2':args[0]._obj.value=4096;args[1]._obj.value=8192
            if name=='cuMemAlloc_v2':
                if self.oom:return 2
                self.assert_size=args[1].value;args[0]._obj.value=456
            return 0
        return invoke


class GPUPreflightTests(unittest.TestCase):
    def test_only_approved_count_and_original_mask_no_idle_device_search(self):
        self.assertEqual(gpu.selected_devices(ROWS,1,'1,0'),[ROWS[1]])
        self.assertEqual(gpu.selected_devices(ROWS,1,ROWS[1]['uuid']),[ROWS[1]])
        with self.assertRaises(gpu.CudaUnavailable):gpu.selected_devices(ROWS,1,'-1')
        with self.assertRaises(gpu.CudaUnavailable):gpu.selected_devices(ROWS,2,'1')
        with self.assertRaises(gpu.CudaUnavailable):gpu.selected_devices(ROWS,1,'GPU-0000')

    def test_actual_minimum_allocation_and_context_released(self):
        driver=Driver()
        result=gpu.probe(ROWS,1,'1',driver)
        self.assertEqual(result[0]['uuid'],ROWS[1]['uuid'])
        self.assertEqual(result[0]['allocation_bytes'],4)
        self.assertEqual(result[0]['free_bytes'],4096)
        self.assertEqual(driver.assert_size,4)
        self.assertEqual(driver.calls.count('cuMemAlloc_v2'),1)
        self.assertEqual(driver.calls.count('cuMemFree_v2'),1)
        self.assertEqual(driver.calls[-1],'cuDevicePrimaryCtxRelease')

    def test_allocation_failure_releases_context_and_does_not_choose_another_gpu(self):
        driver=Driver(oom=True)
        with self.assertRaisesRegex(gpu.CudaUnavailable,'cuMemAlloc_v2:2'):
            gpu.probe(ROWS,1,'0,1',driver)
        self.assertEqual(driver.calls.count('cuDeviceGetByUuid'),1)
        self.assertEqual(driver.calls[-1],'cuDevicePrimaryCtxRelease')

    def test_cpu_probe_does_not_load_cuda_or_nvidia_smi(self):
        output=io.StringIO()
        with patch.object(gpu.sys,'argv',['probe','0']),patch.object(gpu.subprocess,'run') as run,contextlib.redirect_stdout(output):
            self.assertEqual(gpu.main(),0)
        run.assert_not_called()
        self.assertTrue(json.loads(output.getvalue())['ready'])

    def test_remote_cuda_failure_is_pre_submit_error(self):
        value={'schema':'iterlab-cuda-preflight/v1','ready':False,'requested_gpus':1,'fitting':False,'reason':'cuMemAlloc_v2:2'}
        with patch.object(devspace.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=json.dumps(value).encode())) as run:
            with self.assertRaisesRegex(devspace.ComputePreflightError,'cuMemAlloc_v2:2') as error:devspace.check_compute(['ssh','fixed'],{'gpus':1})
        self.assertEqual(error.exception.report['reason'],'cuMemAlloc_v2:2')
        self.assertEqual(run.call_count,1)
        self.assertNotIn('pip install',run.call_args.args[0][-1])

    def test_unexpected_protocol_cannot_become_ready(self):
        value={'schema':'iterlab-cuda-preflight/v1','ready':True,'requested_gpus':2,'fitting':False}
        with patch.object(devspace.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(value).encode())):
            with self.assertRaisesRegex(ValueError,'未确认'):devspace.check_compute(['ssh','fixed'],{'gpus':1})

    def test_ready_receipt_keeps_only_allocation_evidence(self):
        device={**ROWS[0],'free_bytes':100,'total_bytes':200,'allocation_bytes':4,'extra':'do-not-project'}
        value={'schema':'iterlab-cuda-preflight/v1','ready':True,'requested_gpus':1,'fitting':False,
               'devices':[device],'extra':'do-not-project'}
        with patch.object(devspace.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(value).encode())):
            result=devspace.check_compute(['ssh','fixed'],{'gpus':1})
        self.assertNotIn('do-not-project',json.dumps(result))
        self.assertEqual(len(result['probe_sha256']),64)


if __name__=='__main__':unittest.main()
