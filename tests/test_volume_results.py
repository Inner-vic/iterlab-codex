"""Official-reader protocol exercised with scoped SDK/storage doubles only."""
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts import lpai_task_files as reader
from lab.task_output_files import verify_archive


class VolumeResultReaderTests(unittest.TestCase):
    def execute(self,task_uid='task-1',volume_uid='volume-uid'):
        source={'kind':'volume','name':'volume-1','uid':'volume-uid','mount_root':'/mnt/volumes/volume-1',
            'prefix':'user/runs/'+'a'*32,'run_id':'a'*32}
        calls=[];objects={'metrics.json':b'{"f1":0.7}','iterlab-run.json':b'{}'};prefix='base/'+source['prefix']+'/'
        class Events:
            def __init__(self):self.handlers={}
            def register(self,name,fn):self.handlers[name]=fn
        class Client:
            def __init__(self):self.meta=types.SimpleNamespace(events=Events())
            def guard(self,name,params):self.meta.events.handlers['before-parameter-build.s3'](params,types.SimpleNamespace(name=name))
            def list_objects_v2(self,**kwargs):
                self.guard('ListObjectsV2',kwargs);calls.append(('list',kwargs))
                return {'Contents':[{'Key':prefix+n,'Size':len(b)} for n,b in objects.items()]}
            def head_object(self,**kwargs):
                self.guard('HeadObject',kwargs);calls.append(('head',kwargs))
                return {'ContentLength':len(objects[kwargs['Key'][len(prefix):]]),'ETag':'v1'}
            def get_object(self,**kwargs):
                self.guard('GetObject',kwargs);calls.append(('get',kwargs));body=objects[kwargs['Key'][len(prefix):]]
                return {'ContentLength':len(body),'Body':io.BytesIO(body)}
            def close(self):calls.append(('client_closed',))
        client=Client();conf=types.SimpleNamespace(session=types.SimpleNamespace(request=lambda:None,close=lambda:calls.append(('session_closed',))))
        class Resource:
            def __init__(self,path):self.path=path;self._s3_client=self
            def get(self,**kwargs):
                calls.append(('metadata',self.path,kwargs));self.assert_scope()
                return {'metadata':{'uid':task_uid,'name':'task-1','namespace':'vc-chassis'}} if self.path.startswith('tasks/') else {'metadata':{'uid':volume_uid,'name':'volume-1','namespace':'vc-chassis'}}
            def assert_scope(self):
                if conf.path!=self.path:raise AssertionError('metadata escaped exact boundary')
            def get_s3_sts(self,**kwargs):
                self.assert_scope();calls.append(('sts',kwargs))
                if kwargs!={'use':'download','storage':'bos'}:raise AssertionError('upload grant forbidden')
                return {'storage':{'not_real':'never exported'}}
            def create_refreshable_s3_client(self,*args):return client
        util=types.ModuleType('asset.util');util.parse_s3_path=lambda value:{'endpoint':'fixture.invalid','bucket':'fixture','path':'base'}
        s3=types.ModuleType('asset.io.s3');s3._decode_list_objects_v2_response=lambda response,**kwargs:response
        def boundary(conf,path,*args,**kwargs):conf.path=path
        with tempfile.TemporaryDirectory(prefix='iterlab-volume-result-') as temp:
            root=Path(temp)/'attempt';(root/'files').mkdir(parents=True)
            request={'task_uid':'task-1','namespace':'vc-chassis','output_dir':str(root/'files'),
                'required_files':['iterlab-run.json','metrics.json'],'result_source':source}
            with patch.dict('sys.modules',{'asset.util':util,'asset.io.s3':s3}),patch.object(reader.bridge,'load_official',return_value=(types.SimpleNamespace(resource=lambda p,**kw:Resource(p)),conf,{})),patch.object(reader.bridge,'install_read_only_boundary',side_effect=boundary):
                result=reader.execute(request,str(root))
            self.assertEqual(verify_archive(root/'files','task-1','vc-chassis'),result['manifest'])
            self.assertEqual(result['manifest']['result_source'],source)
        return result,calls

    def test_task_then_exact_volume_download_and_parent_byte_verification(self):
        result,calls=self.execute()
        self.assertTrue(result['ok']);self.assertEqual(result['platform_writes'],0)
        self.assertEqual([c[1] for c in calls if c[0]=='metadata'],['tasks/task-1','volumes/volume-1'])
        self.assertEqual([c[1] for c in calls if c[0]=='sts'],[{'use':'download','storage':'bos'}])
        self.assertEqual(len([c for c in calls if c[0]=='get']),2)

    def test_task_or_volume_mismatch_never_publishes_archive(self):
        for change in [{'task_uid':'wrong-task'},{'volume_uid':'wrong-volume'}]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.execute(**change)

    def test_arbitrary_shared_prefix_rejected_before_official_sdk_import(self):
        with patch.object(reader.bridge,'load_official',side_effect=AssertionError('must not load SDK')):
            with self.assertRaises(ValueError):reader.execute({'task_uid':'task','namespace':'vc-chassis','output_dir':'irrelevant',
                'required_files':['iterlab-run.json'],'result_source':{'kind':'volume','name':'volume','uid':'uid',
                    'prefix':'user/shared','mount_root':'/mnt/volumes/volume','run_id':'a'*32}})


if __name__=='__main__':unittest.main()
