"""Direct-task metadata diagnostics; all transports are fixtures."""
import importlib.util
from pathlib import Path
import types
import unittest

from scripts import probe_task_output_identity as probe
from lab import cloud_evidence as cloud


class TaskOutputProbeTests(unittest.TestCase):
    def test_component_hint_lookup_uses_name_and_requires_returned_original_uid(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append(argv)
            if len(calls)==1:return {'status':'ok','data':{'uid':'task-uid','namespace':'fixture','name':'task',
                'project_uid':'component-uid','project_name':'component-name','status':'failed'}},{}
            return {'status':'ok','data':{'uid':'component-uid','configuration':{'cmd':'train --save_dir /mnt/results'}}},{}
        result=probe.inspect_task('task-uid','fixture',runner=runner,cli='fixture-cli',source_only=True,component_hints=True)
        self.assertEqual(calls[1],['fixture-cli','projects','get','--namespace','fixture','--name','component-name'])
        self.assertFalse(result['component_hints']['frozen_original_task_configuration_verified'])
        self.assertEqual(result['component_hints']['candidates'][0]['candidate_path'],'/mnt/results')

    def test_task_display_label_is_not_a_uid_but_identity_conflicts_still_fail(self):
        candidate={'kind':'tasks','name':'original-uid','version':None,'uid':'original-uid'}
        raw={'metadata':{'uid':'original-uid','name':'任务展示名称 / attempt 10'},'spec':{'dir':{'path':'s3://not-exported'}}}
        value=cloud.resource_observation(raw,candidate,'fixture')
        self.assertEqual(value['observation']['uid'],'original-uid')
        self.assertIsNone(value['observation']['name'])
        self.assertFalse(value['observation']['provenance_certified'])
        self.assertNotIn('任务展示名称',str(value));self.assertNotIn('not-exported',str(value))
        for change in [{'uid':'another'},{'namespace':'another'}]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                cloud.resource_observation({'metadata':{**raw['metadata'],**change}},candidate,'fixture')

    def test_resource_failure_keeps_enumerated_error_not_empty_or_raw_diagnostic(self):
        calls=[]
        def runner(argv,**kwargs):
            calls.append((argv,kwargs))
            if len(calls)==1:return {'status':'ok','data':{'uid':'original-uid','namespace':'fixture',
                'name':'original-name','project_uid':'component-1','status':'failed'}},{'exit_code':0}
            return {'ok':False,'error':'network_timeout'},{'exit_code':1}
        result=probe.inspect_task('original-uid','fixture',runner=runner,cli='fixture-cli',python=Path(__file__))
        self.assertEqual(len(calls),2)
        self.assertEqual(result['resource_error'],{'category':'network_timeout'})
        self.assertEqual(result['task']['uid'],'original-uid');self.assertFalse(result['collection_enabled'])
        self.assertEqual(result['platform_writes'],0);self.assertEqual(result['files_downloaded'],0)

    def test_wrong_uid_scope_or_nonterminal_prevents_resource_read(self):
        for change in [{'uid':'another'},{'namespace':'another'},{'status':'running'},{'status':'unknown'}]:
            calls=[]
            def runner(argv,**kwargs):
                calls.append(argv)
                return {'status':'ok','data':{'uid':'original-uid','namespace':'fixture','name':'name','status':'failed',**change}},{}
            with self.subTest(change=change),self.assertRaises(ValueError):
                probe.inspect_task('original-uid','fixture',runner=runner,cli='fixture-cli',python=Path(__file__))
            self.assertEqual(len(calls),1)

    def test_bridge_classifies_network_and_http_without_exporting_body_or_tokens(self):
        spec=importlib.util.spec_from_file_location('isolated_asset_bridge_errors',Path(__file__).resolve().parents[1]/'scripts/lpai_asset_bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        for name,expected in [('ReadTimeout','network_timeout'),('ProxyError','network_unavailable')]:
            exc=type(name,(Exception,),{})('Bearer do-not-export')
            result=bridge.public_error(exc)
            self.assertEqual(result['error'],expected);self.assertNotIn('do-not-export',str(result))
        exc=Exception('request headers contain do-not-export')
        exc.response=types.SimpleNamespace(status_code=403)
        self.assertEqual(bridge.public_error(exc)['error'],'access_denied')
        self.assertNotIn('do-not-export',str(bridge.public_error(exc)))

    def test_wrapped_sdk_failure_retains_category_and_numeric_status_only(self):
        spec=importlib.util.spec_from_file_location('isolated_asset_bridge_wrapped',Path(__file__).resolve().parents[1]/'scripts/lpai_asset_bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        inner=type('ReadTimeout',(Exception,),{})('PRIVATE response and token')
        outer=RuntimeError('PRIVATE url');outer.__context__=inner
        self.assertEqual(bridge.public_error(outer)['error'],'network_timeout')
        self.assertNotIn('PRIVATE',str(bridge.public_error(outer)))
        for status in [400,429,500,502]:
            inner=Exception('PRIVATE body');inner.status=status
            outer.__cause__=inner
            result=bridge.public_error(outer)
            self.assertEqual(result['http_status'],status);self.assertNotIn('PRIVATE',str(result))
        outer.__cause__=outer  # Malformed/cyclic chains remain bounded.
        self.assertNotIn('PRIVATE',str(bridge.public_error(outer)))


if __name__=='__main__':unittest.main()
