"""Origin-export API on a self-owned HTTP fixture; no SDK/model/cloud execution."""
import json
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

from lab.store import dumps
from lab.research import plan_digest
from lab.cloud_evidence import validate_origin
from tests import test_transport_web as fixture


class CloudOriginHTTPTests(unittest.TestCase):
    setUp=fixture.WebTests.setUp
    tearDown=fixture.WebTests.tearDown
    request=fixture.WebTests.request

    def original(self):
        store=self.app.store;company=self.app.kernel.company;research=self.app.kernel.research
        company.configure({'revision':0,'lpai_enabled':True})
        (store.root/'settings.json').write_text(dumps({'channel':'ept','profile':'fixture-original-profile'}),encoding='utf-8')
        project=store.create_project('cloud-origin-http-fixture');sid=store.create_session(project['id'],'ept')['id']
        plan=store.add_record(sid,{'kind':'plan','title':'原云方案','content':'HTTP门禁fixture，不调用真实平台','status':'proposed'}, {})
        trial=store.add_record(sid,{'kind':'trial','title':'原实验','content':'fixture only','plan_id':plan['id']}, {})
        grant=research.review(sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept',
            'reason':'fixture工程许可不是云额度','execution':'lpai','allow_cloud':True,'component_uid':'component-1',
            'component_digest':'a'*64,'max_gpus':1,'max_runs':1,'max_fits':1,'timeout':60})
        submission='fixture-original-submission';stamp='2026-10-30T00:00:00+00:00'
        connection=company.authority(sid)
        frozen={'plan_id':plan['id'],'trial_id':trial['id'],'component_uid':'component-1','task_name':'original-name',
            'component':{'namespace':'vc-chassis'},'authority':connection}
        receipt={'state':'submitted','item':{'uid':'original-task','name':'original-name','namespace':'vc-chassis'}}
        authority={'kind':'human_confirmation','session_id':sid,'plan_id':plan['id'],'submission_id':submission,'grant_id':grant['grant_id']}
        store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (submission,sid,project['id'],plan['id'],trial['id'],'component-1','original-task',dumps(frozen),'Succeeded',dumps(receipt),stamp,stamp))
        store.execute('INSERT INTO company_training_identity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (submission,stamp,'vc-chassis','fixture-original-profile','ept','lpai-agent',dumps(connection),dumps(authority),
             'iterlab-submission:'+submission,None,'original-task',dumps(receipt),stamp))
        return sid,submission

    def test_authenticated_export_repeats_without_network_delivery_or_new_grant(self):
        sid,submission=self.original();store=self.app.store
        events=store.rows('SELECT * FROM events');grants=store.rows('SELECT * FROM research_grants')
        with patch('subprocess.run',side_effect=AssertionError('no export process')),patch('subprocess.Popen',side_effect=AssertionError('no export child')):
            with self.request('/api/company/recovery-origin',{'session_id':sid,'submission_id':submission}) as response:export=json.load(response)
            with self.request('/api/company/recovery-origin',{'session_id':sid,'submission_id':submission}) as response:again=json.load(response)
        self.assertEqual(validate_origin(export)['task_uid'],'original-task');self.assertFalse(export['platform_read_performed'])
        self.assertEqual(export['sha256'],again['sha256']);self.assertNotIn('lpai_envs',dumps(export))
        self.assertEqual(store.rows('SELECT * FROM events'),events);self.assertEqual(store.rows('SELECT * FROM research_grants'),grants)
        self.assertEqual(store.rows('SELECT * FROM deliveries'),[]);self.assertIsNone(self.app.kernel.rpc)

    def test_auth_origin_and_extra_identity_override_are_denied(self):
        sid,submission=self.original();data={'session_id':sid,'submission_id':submission}
        for token,extra,payload,expected in ((False,None,data,403),(True,{'Origin':'https://unrelated.invalid'},data,403),
             (True,None,{**data,'task_uid':'foreign-task'},400),(True,None,{**data,'confirmed':True},400),
             (True,None,{**data,'session_id':'other-session'},400)):
            with self.subTest(payload=payload),self.assertRaises(urllib.error.HTTPError) as denied:
                self.request('/api/company/recovery-origin',payload,token=token,extra=extra)
            self.assertEqual(denied.exception.code,expected)
        self.assertEqual(self.app.store.rows('SELECT * FROM deliveries'),[])

    def test_plan_drift_and_prepared_identity_are_not_exported(self):
        sid,submission=self.original();store=self.app.store
        store.execute("UPDATE company_training SET state='prepared' WHERE id=?",(submission,))
        with self.assertRaises(urllib.error.HTTPError) as denied:self.request('/api/company/recovery-origin',{'session_id':sid,'submission_id':submission})
        self.assertEqual(denied.exception.code,400)
        store.execute("UPDATE company_training SET state='Succeeded' WHERE id=?",(submission,))
        plan_id=store.one('SELECT plan_id FROM company_training WHERE id=?',(submission,))['plan_id']
        store.execute("UPDATE records SET content='changed original plan' WHERE id=?",(plan_id,))
        with self.assertRaises(urllib.error.HTTPError) as denied:self.request('/api/company/recovery-origin',{'session_id':sid,'submission_id':submission})
        self.assertEqual(denied.exception.code,400)
        self.assertEqual(store.one('SELECT task_uid FROM company_training WHERE id=?',(submission,))['task_uid'],'original-task')


if __name__=='__main__':unittest.main()
