import io
import json
from pathlib import Path
import tempfile
import unittest

from lab.cloud_evidence import TransferBudget
from lab.task_output_files import collect,verify_archive,output_hints,inline_bundle_hints,LOG_TAIL


class Client:
    def __init__(self,objects):self.objects=objects;self.calls=[];self.changed=False
    def list_objects_v2(self,**kwargs):
        self.calls.append(('list',kwargs))
        return {'Contents':[{'Key':'task/root/'+key,'Size':len(body)} for key,body in self.objects.items()],'IsTruncated':False}
    def head_object(self,**kwargs):
        self.calls.append(('head',kwargs));data=self.objects[kwargs['Key'][len('task/root/'):]]
        return {'ContentLength':len(data),'ETag':'changed' if self.changed and any(c[0]=='get' for c in self.calls) else 'v1'}
    def get_object(self,**kwargs):
        self.calls.append(('get',kwargs));data=self.objects[kwargs['Key'][len('task/root/'):]]
        if 'Range' in kwargs:data=data[-LOG_TAIL:]
        return {'ContentLength':len(data),'Body':io.BytesIO(data)}


class TaskFilesTests(unittest.TestCase):
    location={'endpoint':'fixture.invalid','bucket':'fixture','path':'task/root'}
    def bundle(self,files,digest=None):
        import base64,hashlib,lzma
        raw=json.dumps(files).encode();encoded=base64.b64encode(lzma.compress(raw)).decode()
        return "raw=lzma.decompress(base64.b64decode('"+encoded+"')); assert hashlib.sha256(raw).hexdigest()=='"+(digest or hashlib.sha256(raw).hexdigest())+"'"

    def test_inline_legacy_code_is_inspected_never_executed_or_exported(self):
        with tempfile.TemporaryDirectory() as temp:
            marker=Path(temp)/'must-not-execute'
            source=f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nOUT='/lpai/volumes/experiment/results'\nTOKEN='PRIVATE'\ntorch.save(model,OUT+'/epoch_040.pt')"
            command=self.bundle({'normal_training.py':source})
            result=output_hints({'roles':[{'cmd':command},{'cmd':command}]})
            self.assertFalse(marker.exists());self.assertEqual(len(result['inline_bundles']),1)
            bundle=result['inline_bundles'][0]
            self.assertTrue(bundle['checksum_verified']);self.assertFalse(bundle['platform_authenticity_verified'])
            self.assertEqual(bundle['checkpoint_save_calls'],1)
            self.assertIn('/lpai/volumes/experiment/results',[item['candidate_path'] for item in result['candidates']])
            self.assertNotIn('PRIVATE',str(result));self.assertNotIn('write_text',str(result));self.assertNotIn(command,str(result))

    def test_inline_bundle_bad_hash_and_expansion_limit_cannot_publish_paths(self):
        bad=inline_bundle_hints(self.bundle({'training.py':"OUT='/mnt/results'"},digest='0'*64))
        self.assertEqual(bad['state'],'checksum_mismatch');self.assertFalse(bad['checksum_verified']);self.assertEqual(bad['path_literals'],[])
        big=inline_bundle_hints(self.bundle({'training.py':' '*(2*1024*1024)}))
        self.assertFalse(big['checksum_verified']);self.assertEqual(big['path_literals'],[])

    def test_inline_path_joins_use_only_declared_public_mounts_and_preserve_unicode(self):
        source="import os\nfrom pathlib import Path\npvc=Path(os.environ['LPAI_PVC_DIR_0'])\nwork_dir=pvc/'研究结果'/'case1'\nunknown=Path(os.environ['API_SECRET'])\n"
        result=output_hints({'lpai_input_pvc':[{'env_key':'LPAI_PVC_DIR_0','env_value':'/mnt/volumes/数据'}],
            'roles':[{'cmd':self.bundle({'training.py':source})}]})
        self.assertIn('/mnt/volumes/数据/研究结果/case1',[p['candidate_path'] for p in result['candidates']])
        self.assertFalse(result['paths_verified']);self.assertNotIn('API_SECRET',str(result))
        alias=output_hints({'inputs':[{'env_key':'LPAI_INPUT_MODEL_4','env_value':'/lpai/inputs/models/backbone'}],
            'roles':[{'cmd':self.bundle({'train.py':"import os\nfrom pathlib import Path\ncheckpoint=Path(os.environ['LPAI_INPUT_MODEL_4'])/'best.pth'"})}]})
        self.assertIn('/lpai/inputs/models/backbone/best.pth',[p['candidate_path'] for p in alias['candidates']])
        self.assertFalse(alias['paths_verified'])
    def test_legacy_output_hints_exclude_commands_and_auth_maps_not_claim_files_exist(self):
        task={'configuration':json.dumps({'lpai_task':{'cmd':'train --work-dir /mnt/research/results --token PRIVATE'},
            'lpai_envs':{'cmd':'do-not-read /mnt/private/token'},'headers':{'command':'/mnt/auth/token'}})}
        result=output_hints(task)
        self.assertEqual([r['candidate_path'] for r in result['candidates']],['/mnt/research/results'])
        self.assertFalse(result['paths_verified']);self.assertNotIn('PRIVATE',str(result));self.assertNotIn('/mnt/private',str(result))
        nested=output_hints({'legacy_body':json.dumps({'task':{'cmd':'train --save_dir /mnt/actual/results'}})})
        self.assertEqual(nested['candidates'],[{'field':'cmd','candidate_path':'/mnt/actual/results'}])

    def test_empty_directory_markers_are_distinct_from_uncollected_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Client({});client.list_objects_v2=lambda **kwargs:{'Contents':[{'Key':'task/root/empty/','Size':0}]}
            manifest=collect(client,self.location,'uid','ns',temp,TransferBudget())
            self.assertEqual(manifest['listed_objects'],1);self.assertEqual(manifest['directory_markers'],1)
            self.assertEqual(manifest['files'],[])
    def test_archive_small_results_log_tail_weights_index_and_verify_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Client({'metrics.json':b'{"loss":0.5}','train.log':b'a'*(LOG_TAIL+20),'model.pt':b'weights'})
            result=collect(client,self.location,'uid','ns',temp,TransferBudget(max_total=1024*1024,max_calls=20))
            self.assertEqual(len(result['files']),2)
            self.assertTrue(result['files'][1]['partial']);self.assertEqual(result['files'][1]['source_size'],LOG_TAIL+20)
            self.assertEqual(result['index'][2]['reason'],'weight_index_only')
            self.assertEqual(verify_archive(temp,'uid','ns'),result)
            self.assertFalse(any(c[0]=='get' and c[1]['Key'].endswith('model.pt') for c in client.calls))
            obj=Path(temp)/result['files'][0]['object'];obj.write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_archive(temp,'uid','ns')
            with self.assertRaises(ValueError):collect(client,self.location,'uid','ns',temp,TransferBudget())

    def test_changed_or_short_object_never_publishes_manifest(self):
        for failure in ('changed','short','quota'):
            with tempfile.TemporaryDirectory() as temp:
                client=Client({'metrics.json':b'123456789'})
                if failure=='changed':client.changed=True
                if failure=='short':
                    original=client.get_object
                    def short(**kwargs):
                        response=original(**kwargs);response['Body']=io.BytesIO(b'123');return response
                    client.get_object=short
                budget=TransferBudget(max_total=3 if failure=='quota' else 1024,max_calls=20)
                with self.subTest(failure=failure),self.assertRaises(ValueError):collect(client,self.location,'uid','ns',temp,budget)
                self.assertFalse((Path(temp)/'manifest.json').exists())

    def test_only_declared_small_npz_is_archived_as_opaque_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Client({'model_arrays.npz':b'opaque', 'unrequested.npz':b'opaque too','model.pt':b'weights'})
            archived=collect(client,self.location,'uid','ns',temp,TransferBudget(),required_files=['model_arrays.npz'])
            self.assertEqual([e['name'] for e in archived['files']],['model_arrays.npz'])
            self.assertEqual(archived['missing_required_files'],[])
            self.assertEqual(verify_archive(temp,'uid','ns'),archived)
        with tempfile.TemporaryDirectory() as temp:
            client=Client({'large.npz':b'x'*(2*1024*1024+1)})
            archived=collect(client,self.location,'uid','ns',temp,TransferBudget(),required_files=['large.npz'])
            self.assertEqual(archived['missing_required_files'],['large.npz'])
            self.assertFalse(any(c[0]=='get' for c in client.calls))

    def test_cross_task_listing_rejected_before_get_and_wrong_receipt_scope_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Client({});client.list_objects_v2=lambda **kwargs:{'Contents':[{'Key':'other/task/metrics.json','Size':1}]}
            with self.assertRaises(ValueError):collect(client,self.location,'uid','ns',temp,TransferBudget())
            self.assertEqual(client.calls,[])
        with tempfile.TemporaryDirectory() as temp:
            collect(Client({}),self.location,'uid','ns',temp,TransferBudget())
            with self.assertRaises(ValueError):verify_archive(temp,'other','ns')


if __name__=='__main__':unittest.main()
