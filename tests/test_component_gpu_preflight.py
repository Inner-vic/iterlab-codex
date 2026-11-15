"""Allocated component GPU preflight and immutable payload; no real platform/GPU."""
import base64
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zlib

from lab import cloud_runtime,cloud_runtime_worker as worker
from lab.result_receipt_writer import digest
from tests import test_cloud_runtime as fixtures

ROOT=Path(__file__).resolve().parents[1]


class ComponentGpuPreflightTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'.evidence/tests'/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree,self.root)

    def contract(self):
        value=fixtures.contract()
        (self.root/'inputs').mkdir()
        value['expected']['runtime'].update(input_root=str(self.root/'inputs'),output_root=str(self.root/'outputs'))
        value['sha256']=digest(value['expected'])
        return value

    def payload(self):
        return {'contract':self.contract(),'command':'python train.py','requested_gpus':1,'timeout_seconds':10,
                'gpu_probe':(ROOT/'lab/gpu_preflight.py').read_text(encoding='utf-8'),
                'writer':(ROOT/'lab/result_receipt_writer.py').read_text(encoding='utf-8')}

    def response(self,ready):
        value={'schema':'iterlab-cuda-preflight/v1','ready':ready,'requested_gpus':1,'fitting':False,
               'reason':'cuMemAlloc_v2:2','devices':[{'index':0,'uuid':'GPU-00000000-0000-0000-0000-000000000001',
               'free_bytes':1000,'total_bytes':2000,'allocation_bytes':4}]}
        return SimpleNamespace(returncode=0 if ready else 1,stdout=json.dumps(value).encode())

    def test_exact_gpu_count_and_fixed_probe_embedded_from_component_role(self):
        runtime=cloud_runtime.freeze(fixtures.contract(),{'schema':1,'allowed_patch_paths':['/lpai_task/cmd']})
        raw={'lpai_task':{'cmd':'python train.py','roles':[{'replica':1,'resource':{'Gpu':1,'gpu':0},'cmd':'python train.py'}]}}
        result=cloud_runtime.apply(raw,runtime)
        bootstrap=shlex.split(result['lpai_task']['cmd'])[2]
        encoded=re.search("b64decode\\('([^']+)'\\)",bootstrap)[1]
        payload=json.loads(zlib.decompress(base64.b64decode(encoded)))
        self.assertEqual(payload['requested_gpus'],1)
        self.assertEqual(payload['gpu_probe'],runtime['gpu_probe'])
        bad={**runtime,'gpu_probe':runtime['gpu_probe']+'\n# altered'}
        with self.assertRaisesRegex(ValueError,'预检版本'):cloud_runtime.apply(raw,bad)

    def test_gpu_failure_never_invokes_training_and_persists_infrastructure_evidence(self):
        payload=self.payload()
        with patch.object(worker.subprocess,'run',return_value=self.response(False)) as probe, \
             patch.object(worker,'bounded_training') as train,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.run(payload),118)
        train.assert_not_called();self.assertEqual(probe.call_count,1)
        outcome=json.loads((self.root/'outputs/iterlab-runtime.json').read_text())
        self.assertEqual(outcome['state'],'gpu_environment_failed')
        self.assertEqual(outcome['failure_stage'],'gpu_preflight')
        self.assertEqual(outcome['gpu_preflight']['reason'],'cuMemAlloc_v2:2')
        self.assertFalse((self.root/'outputs/iterlab-run.json').exists())
        self.assertFalse((self.root/'outputs/metrics.json').exists())

    def test_gpu_probe_time_is_inside_the_original_entry_limit(self):
        payload=self.payload()
        def train(command,environment,seconds):
            self.assertEqual(seconds,7)
            (self.root/'outputs/metrics.json').write_text('{"fixture":true}')
            return 0
        with patch.object(worker.subprocess,'run',return_value=self.response(True)), \
             patch.object(worker,'bounded_training',side_effect=train) as execute, \
             patch.object(worker.time,'monotonic',side_effect=[100,102.2]),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(worker.run(payload),0)
        self.assertEqual(execute.call_count,1)
        self.assertTrue((self.root/'outputs/iterlab-run.json').exists())
        outcome=json.loads((self.root/'outputs/iterlab-runtime.json').read_text())
        self.assertEqual(outcome['state'],'results_published')
        self.assertTrue(outcome['gpu_preflight']['ready'])

    def test_cpu_legacy_payload_does_not_run_cuda_or_create_gpu_proof(self):
        with patch.object(worker.subprocess,'run') as probe:
            self.assertIsNone(worker.gpu_readiness({'requested_gpus':0},{},self.root))
        probe.assert_not_called()
        self.assertFalse((self.root/'gpu-preflight.json').exists())


if __name__=='__main__':unittest.main()
