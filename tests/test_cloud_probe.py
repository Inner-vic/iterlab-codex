"""Offline shape, transfer and official-metadata bridge boundaries; no live SDK calls."""
from __future__ import annotations

import copy
from datetime import datetime,timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from lab import cloud_evidence as cloud

ROOT=Path(__file__).resolve().parents[1]


def script(name):
    spec=importlib.util.spec_from_file_location('test_cloud_'+name,ROOT/'scripts'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def origin_export():
    origin={key:'public-id' for key in cloud.ORIGIN_IDS}
    origin.update({key:'a'*64 for key in cloud.ORIGIN_HASHES})
    origin.update(plan_version='1.0',task_name='original-name',profile='original-profile',
        session_channel='ept',read_channel='lpai-agent',requested_at='2026-10-30T01:00:00Z',platform_identity=None)
    return {'schema':'iterlab-cloud-origin/v1','origin':origin,'sha256':cloud.fingerprint(origin),
            'exported_at':'2026-10-31T01:00:00Z','platform_read_performed':False,'meaning':cloud.ORIGIN_MEANING}


class OfflineBoundaries(unittest.TestCase):
    def test_origin_checksum_is_required_but_not_authentication(self):
        value=origin_export();self.assertEqual(cloud.validate_origin(value),value['origin'])
        bad=copy.deepcopy(value);bad['origin']['task_uid']='changed'
        with self.assertRaises(ValueError):cloud.validate_origin(bad)
        bad['sha256']=cloud.fingerprint(bad['origin'])
        self.assertEqual(cloud.validate_origin(bad)['task_uid'],'changed')
        # Rechecksummed input remains a query hint; no new source authority.
        with self.assertRaises(ValueError):cloud.require_confirmed_mapping({'origin':bad,'state':'confirmed'})

    def test_origin_extra_sensitive_fields_and_naive_dates_are_rejected(self):
        for mutation in ('extra','naive','earlier','channel','version','identity'):
            bad=origin_export()
            if mutation=='extra':bad['origin']['lpai_envs']=[{'value':'private'}]
            if mutation=='naive':bad['origin']['requested_at']='2026-10-30T01:00:00'
            if mutation=='earlier':bad['exported_at']='2026-10-29T01:00:00Z'
            if mutation=='channel':bad['origin']['read_channel']='other'
            if mutation=='version':bad['origin']['plan_version']=True
            if mutation=='identity':bad['origin']['platform_identity']={'account_uid':'private\nvalue'}
            bad['sha256']=cloud.fingerprint(bad['origin'])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):cloud.validate_origin(bad)

    def test_confirmed_strings_annotations_and_adapters_cannot_unlock_reader(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'never-created'
            for mapping in ({'state':'confirmed','live_verified':True},{'state':'verified','provenance_adapter':'fake'},None):
                with self.subTest(mapping=mapping),self.assertRaises(ValueError):
                    cloud.require_confirmed_mapping(mapping,reader=lambda:self.fail('no read'),output=output)
                self.assertFalse(output.exists())

    def test_key_rejects_cross_platform_path_and_sensitive_names(self):
        for key in ('../metrics.json','/metrics.json','a//b','a/./b','a\\b','a%2fb','a:stream','a?b',
                    '.lpai/conf.json','token','private_key.pem','envs.json','a/NUL.txt','a/CON','a/name.','a/name '):
            with self.subTest(key=key),self.assertRaises(ValueError):cloud.cloud_key(key)
        self.assertEqual(cloud.cloud_key('output/metrics.json'),'output/metrics.json')

    def test_shape_is_bounded_and_dynamic_annotation_values_are_not_field_names(self):
        raw={'metadata':{'annotations':{'private-as-key':'do-not-store'},'labels':{'private-label':'secret'}},
             'outputs':[{'metrics':'private-value','PrivateAccountAsKey':'secret'}]*1000}
        text=json.dumps(cloud.response_shape(raw))
        for value in ('private-as-key','do-not-store','private-label','private-value','PrivateAccountAsKey'):self.assertNotIn(value,text)
        self.assertLess(len(text),32768)
        raw={};current=raw
        for i in range(100):current['nested']={};current=current['nested']
        self.assertTrue(cloud.response_shape(raw)['truncated'])

    def test_child_protocol_rejects_raw_values_extra_fields_and_false_source_proof(self):
        query={'kind':'models','name':'model-set','version':'v1','uid':'version-uid'}
        raw={'metadata':{'uid':'version-uid','name':'v1','namespace':'ns'}}
        base=cloud.resource_observation(raw,query,'ns')
        sources=script('lpai_asset_bridge').SDK_SOURCES
        value={**base,'sdk':'official_asset','sdk_package':'lpai-asset','sdk_version':'3.0.23',
            'sdk_sources':{name:{'sha256':digest,'matches_reviewed_source':True} for name,digest in sources.items()},
            'api_calls':1,'metadata_bytes_read':1024,'credentials_exported':False,'login_or_refresh_requested':False,
            'account_scope_verified':False,'mapping_state':'mapping_unconfirmed','platform_writes':0,'files_downloaded':0}
        self.assertEqual(cloud.validate_resource_projection(value,query,'ns',sources),value)
        for mutation in ('extra','scalar','provenance','scope','sources','mapping'):
            bad=copy.deepcopy(value)
            if mutation=='extra':bad['raw_body']='secret-body'
            if mutation=='scalar':bad['response_shape']['shape']={'type':'string','value':'secret-body'}
            if mutation=='provenance':bad['observation']['provenance_certified']=True
            if mutation=='scope':bad['observation']['namespace']='foreign'
            if mutation=='sources':bad['sdk_sources']['unreviewed']='fake'
            if mutation=='mapping':bad['mapping_state']='verified'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):cloud.validate_resource_projection(bad,query,'ns',sources)

    def test_resource_identity_missing_scope_is_not_filled_from_query(self):
        query={'kind':'models','name':'model-set','version':'v1','uid':'version-uid'}
        result=cloud.resource_observation({'metadata':{'uid':'version-uid','name':'v1'}},query,'ns')
        self.assertIsNone(result['observation']['namespace'])
        self.assertEqual(result['observation']['identity_state'],'query_identity_unconfirmed')
        self.assertFalse(result['observation']['provenance_certified'])
        for raw in ({'metadata':{'uid':'wrong','name':'v1','namespace':'ns'}},
                    {'metadata':{'uid':'version-uid','name':'v2','namespace':'ns'}},
                    {'metadata':{'uid':'version-uid','name':'v1','namespace':'other'}},
                    {'uid':'conflict','metadata':{'uid':'version-uid'}}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):cloud.resource_observation(raw,query,'ns')


class StreamBoundaries(unittest.TestCase):
    def test_real_memory_stream_short_chunks_digest_and_close(self):
        class Small(io.BytesIO):
            def read(self,n=-1):return super().read(min(n,2))
        stream=Small(b'actual');sink=io.BytesIO();budget=cloud.TransferBudget(max_total=8)
        result=cloud.stream_snapshot(stream,sink,budget,expected_size=6)
        self.assertTrue(stream.closed);self.assertEqual(sink.getvalue(),b'actual')
        self.assertEqual(budget.bytes_read,6);self.assertTrue(result['snapshot_bytes_verified'])
        self.assertFalse(result['provenance_certified']);self.assertFalse(result['platform_immutable_content_verified'])

    def test_failed_short_read_still_consumes_cumulative_budget(self):
        stream=io.BytesIO(b'ab');budget=cloud.TransferBudget(max_total=8)
        with self.assertRaisesRegex(ValueError,'短读'):cloud.stream_snapshot(stream,io.BytesIO(),budget,expected_size=4)
        self.assertTrue(stream.closed);self.assertEqual(budget.bytes_read,2)
        stream=io.BytesIO(b'abcdef')
        with self.assertRaisesRegex(ValueError,'累计额度'):cloud.stream_snapshot(stream,io.BytesIO(),budget,expected_size=6)
        self.assertTrue(stream.closed);self.assertEqual(budget.bytes_read,2)

    def test_overdelivery_and_drift_never_write_oversized_block_or_publish(self):
        class IgnoreSize(io.BytesIO):
            def read(self,n=-1):return super().read()
        stream=IgnoreSize(b'abcd');sink=io.BytesIO();budget=cloud.TransferBudget(max_total=6)
        with self.assertRaisesRegex(ValueError,'忽略'):cloud.stream_snapshot(stream,sink,budget,expected_size=2)
        self.assertEqual(sink.getvalue(),b'');self.assertEqual(budget.bytes_read,4);self.assertTrue(stream.closed)
        stream=io.BytesIO(b'abcd');budget=cloud.TransferBudget(max_total=6)
        with self.assertRaisesRegex(ValueError,'HEAD'):cloud.stream_snapshot(stream,io.BytesIO(),budget,expected_size=3)
        self.assertEqual(budget.bytes_read,4)

    def test_deadline_after_read_accounts_bytes_and_always_closes(self):
        clock=[0]
        class Expire(io.BytesIO):
            def read(self,n=-1):clock[0]=2;return super().read(n)
        stream=Expire(b'abc');budget=cloud.TransferBudget(max_total=6,timeout=1,clock=lambda:clock[0])
        with self.assertRaises(TimeoutError):cloud.stream_snapshot(stream,io.BytesIO(),budget,expected_size=3)
        self.assertTrue(stream.closed);self.assertEqual(budget.bytes_read,3)

    def test_sha_mismatch_empty_head_and_api_budget_do_not_reset_accounting(self):
        budget=cloud.TransferBudget(max_total=8,max_calls=1);budget.call()
        with self.assertRaises(ValueError):budget.call()
        self.assertEqual(budget.calls,1)
        stream=io.BytesIO(b'abc')
        with self.assertRaisesRegex(ValueError,'SHA-256'):cloud.stream_snapshot(stream,io.BytesIO(),budget,expected_size=3,expected_sha256='f'*64)
        self.assertTrue(stream.closed);self.assertEqual(budget.bytes_read,3)
        stream=io.BytesIO()
        with self.assertRaises(ValueError):cloud.stream_snapshot(stream,io.BytesIO(),budget,expected_size=0)
        self.assertTrue(stream.closed)


class AssetBridgeBoundaries(unittest.TestCase):
    def setUp(self):self.bridge=script('lpai_asset_bridge')

    def request(self,**changes):
        return {'operation':'inspect_resource','namespace':'ns',
            'candidate':{'kind':'models','name':'model-set','version':'v1','uid':'version-uid'},
            'timeout':20,'max_response_bytes':1024,**changes}

    def conf(self,raw):
        class Response:
            status_code=200;closed=False
            def iter_content(self,chunk_size):yield json.dumps(raw).encode()
            def close(self):self.closed=True
        response=Response();calls=[];token_calls=[]
        def request(*args,**kwargs):calls.append((args,kwargs));return response
        session=types.SimpleNamespace(request=request,close=lambda:None)
        def token():token_calls.append(1);return {'token_type':'fixture','access_token':'never-output'}
        conf=types.SimpleNamespace(_env='prod',_grey=False,_jwt_token='fixture-in-memory',_bearer_file=None,
            _get_token=token,_load_cached_bearer_token=lambda:None,session=session,_lpai_endpoint='https://fixture.invalid')
        return conf,calls,response,token_calls

    def test_explicit_metadata_read_is_get_only_and_discards_secret_values(self):
        raw={'metadata':{'uid':'version-uid','name':'v1','namespace':'ns','annotations':{'secret':'never-output'}},
             'spec':{'dir':{'path':'s3://private-root'}},'data':{'password':'never-output'}}
        conf,calls,response,token_calls=self.conf(raw)
        class Resource:
            def get(self,**kwargs):
                conf._get_token()
                response=conf.session.request(method='GET',url=conf._lpai_endpoint+'/lpai/api/assets/models/model-set/versions/v1',
                    headers={},params={'namespace':'ns'})
                return json.loads(response._content)
        asset=types.SimpleNamespace(resource=lambda *args,**kwargs:Resource())
        result=self.bridge.execute(self.request(),loader=lambda:(asset,conf,{'reviewed':True}))
        self.assertEqual(len(calls),1);self.assertEqual(len(token_calls),1);self.assertTrue(response.closed)
        self.assertTrue(calls[0][1]['stream']);self.assertFalse(calls[0][1]['allow_redirects'])
        self.assertEqual(result['mapping_state'],'mapping_unconfirmed')
        self.assertEqual(result['files_downloaded'],0);self.assertFalse(result['account_scope_verified'])
        self.assertNotIn('never-output',json.dumps(result));self.assertNotIn('private-root',json.dumps(result))

    def test_collect_and_write_request_fail_before_sdk_loader(self):
        for operation in ('collect','head','range','list_files','upload','freeze','register','inspect_resource'):
            request=self.request(operation=operation)
            if operation=='inspect_resource':request['credentials']='must-not-load'
            with self.subTest(operation=operation),self.assertRaises(ValueError):
                self.bridge.execute(request,loader=lambda:self.fail('must not import or configure SDK'))

    def test_session_denies_sts_other_target_post_and_user_override(self):
        conf,calls,response,_=self.conf({})
        budget=cloud.TransferBudget(max_total=1024,max_calls=1)
        self.bridge.install_read_only_boundary(conf,'models/model-set/versions/v1',budget,1024)
        for method,url,headers in [('POST','models/model-set/versions/v1',{}),
            ('GET','models/model-set/versions/v1/sts',{}),('GET','models/other/versions/v1',{}),
            ('GET','models/model-set/versions/v1',{'x-lpai-user':'somebody'})]:
            with self.subTest(method=method,url=url),self.assertRaises(ValueError):
                conf.session.request(method=method,url=conf._lpai_endpoint+'/lpai/api/assets/'+url,headers=headers)
        self.assertEqual(calls,[]);self.assertEqual(budget.calls,0)

    def test_expired_bearer_cannot_refresh_or_launch_auth(self):
        conf,calls,response,token_calls=self.conf({})
        conf._jwt_token=None;conf._bearer_file='fixture-existing-official-reference'
        self.bridge.install_read_only_boundary(conf,'models/model-set/versions/v1',cloud.TransferBudget(max_total=1024),1024)
        with self.assertRaises(ValueError):conf._get_token()
        self.assertEqual(token_calls,[]);self.assertEqual(calls,[])
        conf._load_cached_bearer_token=lambda:{'token_type':'fixture','access_token':'in-memory-only'}
        self.assertEqual(conf._get_token()['token_type'],'fixture');self.assertEqual(token_calls,[])

    def test_explicit_download_sts_only_and_no_user_or_target_override(self):
        conf,calls,response,_=self.conf({})
        self.bridge.install_read_only_boundary(conf,'tasks/task-uid',cloud.TransferBudget(max_total=1024),1024,download_sts=True)
        base={'method':'GET','url':conf._lpai_endpoint+'/lpai/api/assets/tasks/task-uid/sts'}
        for params in ({'use':'upload','storage':'bos'},{'use':'download','storage':'bos','targetId':'another'},
                       {'use':'download','storage':'bos','user':'another'}):
            with self.subTest(params=params),self.assertRaises(ValueError):conf.session.request(**base,params=params)
        self.assertEqual(calls,[])
        conf.session.request(**base,params={'use':'download','storage':'bos'})
        self.assertEqual(len(calls),1)

    def test_wrong_environment_blocks_request(self):
        for env,grey in [('ontest',False),('prod',True),('prod',None)]:
            conf,calls,response,_=self.conf({});conf._env=env;conf._grey=grey
            with self.subTest(env=env,grey=grey),self.assertRaises(ValueError):
                self.bridge.install_read_only_boundary(conf,'models/model-set/versions/v1',cloud.TransferBudget(),1024)
            self.assertEqual(calls,[])

    def test_metadata_actual_byte_budget_and_one_call_survive_error(self):
        conf,calls,response,_=self.conf({'huge':'x'*2000})
        budget=cloud.TransferBudget(max_total=1024,max_calls=1)
        self.bridge.install_read_only_boundary(conf,'models/model-set/versions/v1',budget,1024)
        query={'method':'GET','url':conf._lpai_endpoint+'/lpai/api/assets/models/model-set/versions/v1'}
        with self.assertRaises(ValueError):conf.session.request(**query)
        self.assertTrue(response.closed);self.assertGreater(budget.bytes_read,1024)
        with self.assertRaises(ValueError):conf.session.request(**query)
        self.assertEqual(len(calls),1)

    def test_official_errors_preserve_category_not_sdk_message(self):
        class Failure(RuntimeError):status=403
        result=self.bridge.public_error(Failure('Authorization: PRIVATE_CREDENTIAL'))
        self.assertEqual(result['error'],'access_denied');self.assertNotIn('PRIVATE_CREDENTIAL',json.dumps(result))
        result=self.bridge.public_error(RuntimeError('any unsafe provider body'))
        self.assertEqual(result['error'],'read_unconfirmed');self.assertNotIn('unsafe',json.dumps(result))

    def test_sdk_import_path_excludes_project_and_prior_shadow_package(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/'reviewed-site';base.mkdir()
            paths=[str(self.bridge.ROOT),'/usr/lib/python-fixture',str(base)]
            with patch.object(self.bridge.sys,'path',paths):
                self.bridge.isolate_sdk_import_path(base)
                self.assertEqual(self.bridge.sys.path[0],str(base))
                self.assertNotIn(str(self.bridge.ROOT),self.bridge.sys.path)
                self.assertEqual(self.bridge.sys.path.count(str(base)),1)

    def test_source_drift_static_check_does_not_import_or_request_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            report=self.bridge.source_check(Path(temp))
            self.assertFalse(all(value['matches_reviewed_source'] for value in report.values()))
            with patch.object(self.bridge.sysconfig,'get_path',return_value=temp):
                with self.assertRaises(ValueError):self.bridge.load_official()


class ProbeBoundaries(unittest.TestCase):
    def setUp(self):self.probe=script('probe_cloud_outputs')

    def test_preflight_performs_no_process_network_sdk_or_store(self):
        with patch('subprocess.Popen',side_effect=AssertionError('preflight child forbidden')),patch('subprocess.run',side_effect=AssertionError('preflight run forbidden')):
            result=self.probe.preflight()
        self.assertTrue(result['preflight_only']);self.assertFalse(result['sdk_imported'])
        self.assertFalse(result['store_opened']);self.assertEqual(result['platform_reads'],0)
        self.assertEqual(result['processes_started'],0)

    def test_collect_denies_before_input_process_or_receipt_directory(self):
        with patch.object(self.probe,'load_origin',side_effect=AssertionError('collect file forbidden')),\
             patch.object(self.probe,'receipt_directory',side_effect=AssertionError('collect mkdir forbidden')),\
             patch.object(self.probe,'run_child',side_effect=AssertionError('collect child forbidden')),\
             patch('sys.stdout',new=io.StringIO()) as out:
            code=self.probe.main(['--collect','--origin','definitely-missing.json'])
        self.assertEqual(code,2);self.assertEqual(json.loads(out.getvalue())['files_downloaded'],0)

    def test_missing_original_cli_precondition_retains_failed_receipt_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);origin=root/'origin.json';origin.write_text(json.dumps(origin_export()))
            directory=root/'own-receipt';directory.mkdir()
            with patch.object(self.probe,'ROOT',root),patch.object(self.probe,'load_origin',return_value=origin_export()),\
                 patch.object(self.probe,'receipt_directory',return_value=directory),\
                 patch.object(self.probe.shutil,'which',return_value=None),patch('sys.stdout',new=io.StringIO()):
                code=self.probe.main(['--inspect','--origin',str(origin),'--acknowledge-network-read'])
            self.assertEqual(code,2)
            receipt=json.loads((directory/'receipt.json').read_text())
            self.assertEqual(receipt['state'],'read_unconfirmed');self.assertIn('lpai-agent',receipt['message'])
            self.assertEqual(receipt['live_acceptance']['file_recovery'],'not_run')

    def test_origin_file_outside_system_symlink_or_oversize_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);good=root/'origin.json';good.write_text(json.dumps(origin_export()))
            self.assertEqual(self.probe.load_origin(good,root)['sha256'],origin_export()['sha256'])
            for path in (root/'.data/workbench.sqlite3',root/'.runtime/conf.json',root.parent/'outside.json',Path('relative.json')):
                with self.subTest(path=path),self.assertRaises(ValueError):self.probe.load_origin(path,root)
            link=root/'linked.json'
            try:link.symlink_to(good)
            except OSError:pass
            else:
                with self.assertRaises(ValueError):self.probe.load_origin(link,root)
            good.write_bytes(b'a'*16385)
            with self.assertRaises(ValueError):self.probe.load_origin(good,root)

    def test_append_receipts_do_not_overwrite_previous_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);first=self.probe.receipt_directory(root);second=self.probe.receipt_directory(root)
            self.assertNotEqual(first,second)
            self.probe.write_json(first/'receipt.json',{'state':'failed'})
            with self.assertRaises(FileExistsError):self.probe.write_json(first/'receipt.json',{'state':'passed'})
            self.assertEqual(json.loads((first/'receipt.json').read_text())['state'],'failed')

    def test_original_missing_namespace_or_wrong_uid_never_fills_from_export(self):
        export=origin_export();raw={'uid':'public-id','name':'original-name','project_uid':'public-id','status':'Succeeded',
            'create_timestamp':'2026-10-30T02:00:00Z'}
        result=cloud.original_task_observation(raw,export)
        self.assertEqual(result['observation']['identity_state'],'unconfirmed');self.assertIsNone(result['observation']['namespace'])
        raw['namespace']='ns-other'
        with self.assertRaises(ValueError):cloud.original_task_observation(raw,export)

    def test_real_owned_offline_child_json_cleanup_and_deadline(self):
        from lab.cloud_evidence import TransferBudget
        import sys,time
        raw,stats=self.probe.run_child([sys.executable,'-I','-c','import json; print(json.dumps({"offline_fixture": True}))'],deadline=time.monotonic()+5)
        self.assertTrue(raw['offline_fixture']);self.assertTrue(stats['owned_cleanup_confirmed'])
        self.assertEqual(stats['exit_code'],0)
        with self.assertRaises(ValueError):
            self.probe.run_child([sys.executable,'-I','-c','import time; time.sleep(20)'],deadline=time.monotonic()+0.15)

    def test_service_shutdown_cancels_only_owned_reader_and_keeps_json_default_strict(self):
        import sys,time,threading
        cancel=threading.Event();cancel.set()
        with patch.object(self.probe.subprocess,'Popen',side_effect=AssertionError('must not spawn')):
            with self.assertRaises(self.probe.ProbeError):self.probe.run_child([sys.executable],deadline=time.monotonic()+5,cancel_event=cancel)
        cancel.clear();timer=threading.Timer(.2,cancel.set);timer.start()
        try:
            with self.assertRaises(self.probe.ProbeError) as stopped:
                self.probe.run_child([sys.executable,'-I','-c','import time; time.sleep(20)'],deadline=time.monotonic()+5,cancel_event=cancel)
            self.assertTrue(stopped.exception.process['owned_cleanup_confirmed'])
        finally:timer.cancel();timer.join()
        raw,stats=self.probe.run_child([sys.executable,'-I','-c','print("offline log")'],deadline=time.monotonic()+5,text_result=True)
        self.assertEqual(raw['text'].strip(),'offline log')
        with self.assertRaises(self.probe.ProbeError):
            self.probe.run_child([sys.executable,'-I','-c','print("offline log")'],deadline=time.monotonic()+5)

    def test_owned_child_surviving_descendant_is_cleaned_before_success(self):
        import os,sys,time
        if os.name=='nt':self.skipTest('POSIX独立会话后代检查；Windows Job需本机验收')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pidfile=root/'descendant-pid';late=root/'late-output'
            child='import pathlib,time; time.sleep(0.6); pathlib.Path('+repr(str(late))+').write_text("must not appear")'
            parent='import subprocess,sys,pathlib,json; p=subprocess.Popen([sys.executable,"-I","-c",'+repr(child)+']); pathlib.Path('+repr(str(pidfile))+').write_text(str(p.pid)); print(json.dumps({"offline_fixture":True}))'
            self.probe.run_child([sys.executable,'-I','-c',parent],deadline=time.monotonic()+5)
            self.assertGreater(int(pidfile.read_text()),0)
            # run_child already owns and cleans this group. Do not signal the
            # old PID after waiting: it may have been reaped and reused.
            time.sleep(0.7);self.assertFalse(late.exists())

    def test_nonzero_child_receipt_retains_code_but_not_private_stderr(self):
        import sys,time
        with self.assertRaises(self.probe.ProbeError) as denied:
            self.probe.run_child([sys.executable,'-I','-c','import sys; sys.stderr.write("PRIVATE_DATA"); raise SystemExit(7)'],deadline=time.monotonic()+5)
        self.assertEqual(denied.exception.process['exit_code'],7)
        self.assertTrue(denied.exception.process['owned_cleanup_confirmed'])
        self.assertNotIn('PRIVATE_DATA',str(denied.exception))

    def test_safe_child_error_category_survives_nonzero_without_body(self):
        import sys,time
        code='import json; print(json.dumps({"ok":False,"error":"access_denied","message":"PRIVATE_DATA"})); raise SystemExit(1)'
        raw,stats=self.probe.run_child([sys.executable,'-I','-c',code],deadline=time.monotonic()+5,allow_safe_error=True)
        self.assertEqual(raw,{'ok':False,'error':'access_denied'});self.assertEqual(stats['exit_code'],1)
        self.assertNotIn('PRIVATE_DATA',json.dumps(raw))

    def test_owned_child_response_limit_nonzero_and_past_deadline_are_not_success(self):
        import sys,time
        with self.assertRaises(ValueError):self.probe.run_child([sys.executable,'-I','-c','print("x"*10000)'],deadline=time.monotonic()+5,max_output=1024)
        with self.assertRaises(ValueError):self.probe.run_child([sys.executable,'-I','-c','raise SystemExit(4)'],deadline=time.monotonic()+5)
        with patch('subprocess.Popen',side_effect=AssertionError('expired child forbidden')):
            with self.assertRaises(ValueError):self.probe.run_child([sys.executable],deadline=time.monotonic()-1)

    def test_inspect_no_fallback_and_incomplete_task_stops_before_asset(self):
        export=origin_export();calls=[]
        raw={'uid':'public-id','name':'original-name','project_uid':'public-id','status':'Succeeded','create_timestamp':'2026-10-30T02:00:00Z'}
        def runner(args,**kwargs):calls.append(args);return {'status':'ok','data':raw},{'exit_code':0}
        with patch.object(self.probe.shutil,'which',return_value='fixture-cli'):
            result=self.probe.inspect(export,None,runner=runner)
        self.assertEqual(result['state'],'read_unconfirmed');self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1:5],['--timeout','25','tasks','get']);self.assertIn('public-id',calls[0])
        self.assertEqual(result['files_downloaded'],0)

    def test_missing_original_public_account_blocks_further_asset_reads(self):
        export=origin_export();export['origin']['platform_identity']={'platform':'lpai','account_uid':'original-account'}
        export['sha256']=cloud.fingerprint(export['origin']);calls=[]
        task={'uid':'public-id','name':'original-name','namespace':'public-id','project_uid':'public-id',
            'status':'Succeeded','create_timestamp':'2026-10-30T02:00:00Z'}
        def runner(args,**kwargs):calls.append(args);return {'status':'ok','data':task},{'exit_code':0}
        candidate={'kind':'models','name':'model-set','version':'v1','uid':'version-uid'}
        with patch.object(self.probe.shutil,'which',return_value='fixture-cli'),patch.object(Path,'is_file',return_value=True):
            result=self.probe.inspect(export,candidate,runner=runner)
        self.assertEqual(result['state'],'read_unconfirmed');self.assertEqual(len(calls),1)
        self.assertEqual(result['failed_stage'],'original_task');self.assertNotIn('candidate_version',result['stages'])

    def test_offline_fixture_metadata_never_enables_file_or_research_acceptance(self):
        export=origin_export();calls=[]
        raw={'uid':'public-id','name':'original-name','namespace':'public-id','project_uid':'public-id',
             'status':'Succeeded','create_timestamp':'2026-10-30T02:00:00Z','lpai_model_register':[{'version':'v1'}]}
        def runner(args,**kwargs):calls.append(args);return {'status':'ok','data':raw},{'exit_code':0}
        with patch.object(self.probe.shutil,'which',return_value='fixture-cli'):
            result=self.probe.inspect(export,None,runner=runner)
        self.assertEqual(result['state'],'inspected_mapping_unconfirmed');self.assertEqual(len(calls),1)
        self.assertFalse(result['automatic_collection_enabled']);self.assertFalse(result['origin_authenticity_verified'])
        self.assertEqual(result['live_acceptance']['file_recovery'],'not_run');self.assertEqual(result['live_acceptance']['research_loop'],'not_run')

    def test_identity_overwritten_platform_fields_cannot_escape_projection(self):
        export=origin_export();export['origin']['platform_identity']={'platform':'lpai','account_uid':'expected-account'}
        export['sha256']=cloud.fingerprint(export['origin'])
        raw={'uid':'public-id','name':'original-name','namespace':'public-id','project_uid':'public-id',
             'status':'Succeeded','create_timestamp':'2026-10-30T02:00:00Z','platform_identity':{'account_uid':'wrong-account'}}
        with self.assertRaises(ValueError):cloud.original_task_observation(raw,export)
        raw['platform_identity']={'authorization':'do-not-echo'}
        result=cloud.original_task_observation(raw,export)
        self.assertEqual(result['observation']['account_scope_state'],'unconfirmed')
        self.assertNotIn('do-not-echo',json.dumps(result))

    def test_explicit_task_output_id_is_not_replaced_by_original_name(self):
        export=origin_export();calls=[];candidate={'kind':'models','name':'candidate','version':'v1','uid':'candidate-uid'}
        task={'uid':'public-id','name':'original-name','namespace':'public-id','project_uid':'public-id','status':'Succeeded','create_timestamp':'2026-10-30T02:00:00Z'}
        def runner(args,**kwargs):
            calls.append((args,kwargs))
            if len(calls)==1:return {'status':'ok','data':task},{'exit_code':0}
            request=json.loads(kwargs['input_data']);c=request['candidate']
            raw={'metadata':{'uid':c['uid'] or 'different-output-uid','name':c['version'] or c['name'],'namespace':'public-id'}}
            result=cloud.resource_observation(raw,c,'public-id')
            bridge=script('lpai_asset_bridge')
            public={**result,'sdk':'official_asset','sdk_package':'lpai-asset','sdk_version':'3.0.23',
                'sdk_sources':{name:{'sha256':digest,'matches_reviewed_source':True} for name,digest in bridge.SDK_SOURCES.items()},
                'api_calls':1,'metadata_bytes_read':1024,'credentials_exported':False,'login_or_refresh_requested':False,
                'account_scope_verified':False,'mapping_state':'mapping_unconfirmed','platform_writes':0,'files_downloaded':0}
            return {'ok':True,'result':public},{'exit_code':0}
        with patch.object(self.probe.shutil,'which',return_value='fixture-cli'),patch.object(Path,'is_file',return_value=True):
            result=self.probe.inspect(export,candidate,task_output_id='explicit-resource-id',runner=runner)
        self.assertEqual(len(calls),3);last=json.loads(calls[2][1]['input_data'])
        self.assertEqual(last['candidate']['name'],'explicit-resource-id')
        self.assertEqual(result['mapping']['state'],'mapping_unconfirmed')
        self.assertFalse(result['automatic_collection_enabled'])



if __name__=='__main__':unittest.main()
