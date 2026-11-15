import json
import unittest
from unittest.mock import patch
from tests import test_gpu_execution as fixtures
from lab import execution_request


class ExecutionRequestTests(unittest.TestCase):
    setUp=fixtures.GPUExecutionTests.setUp
    approve=fixtures.GPUExecutionTests.approve
    approve_space=fixtures.GPUExecutionTests.approve_space

    def test_missing_ssh_blocks_component_and_compute_without_spending(self):
        self.approve_space(configured=False)
        before=self.store.rows('SELECT * FROM research_grants')
        value=execution_request.select(self.store,self.sid,'lpai_devspace','用同一公开数据上传开发空间测试')
        self.assertEqual(value['state'],'needs_ssh')
        self.assertEqual(self.research.status(self.sid)['execution_request']['state'],'needs_ssh')
        self.assertFalse(self.research.status(self.sid)['execution_authorization']['can_request_preflight'])
        with patch.object(self.company.training,'_enabled'):
            with self.assertRaisesRegex(ValueError,'用户已指定'):
                self.company.training.prepare(self.sid,{})
        with patch('lab.research.subprocess.Popen') as start:
            with self.assertRaisesRegex(ValueError,'SSH'):
                self.research.run(self.sid,self.request,'missing-ssh')
        start.assert_not_called()
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.assertEqual(self.research.workflow.project(self.sid)['label'],'等待开发空间 SSH 配置')

    def test_choice_preserves_grants_and_rebinding_does_not_reuse_old_ssh(self):
        self.approve_space()
        before=self.store.rows('SELECT * FROM research_grants')
        value=execution_request.select(self.store,self.sid,'lpai_devspace','本次使用开发空间')
        self.assertEqual(value['state'],'selected')
        execution_request.require_route(self.store,self.project['id'],'lpai_devspace')
        self.store.execute("UPDATE company_bindings SET uid='different-space' WHERE project_id=?",(self.project['id'],))
        self.assertEqual(execution_request.status(self.store,self.project['id'])['state'],'needs_binding')
        with self.assertRaisesRegex(ValueError,'绑定'):
            execution_request.require_route(self.store,self.project['id'],'lpai_devspace')
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)

    def test_input_size_is_executor_specific_and_files_remain_hashed(self):
        root=self.store.safe_path(self.project['id'],'input.txt');root.write_bytes(b'1234')
        with self.assertRaisesRegex(ValueError,'传输范围'):
            self.research._paths(self.sid,['input.txt'],3)
        manifest=self.research._paths(self.sid,['input.txt'],8)
        self.assertEqual(manifest['input.txt']['size'],4)
        self.assertEqual(len(manifest['input.txt']['sha256']),64)
