"""Exact closure API semantics through the isolated product HTTP server."""
import json
import unittest
import urllib.error
from unittest.mock import patch
import test_transport_web as fixtures
from lab.research import plan_digest


class ClosureHTTPTests(unittest.TestCase):
    setUp=fixtures.WebTests.setUp
    tearDown=fixtures.WebTests.tearDown
    request=fixtures.WebTests.request

    def study(self,autonomous=True):
        store=self.app.store;research=self.app.kernel.research
        project=store.create_project('closure-http-fixture')
        sid=store.create_session(project['id'],self.app.kernel.settings.value['channel'])['id']
        plan=store.add_record(sid,{'kind':'plan','title':'确切方案','content':'隔离API边界，不调用模型或平台'}, {})
        research.review(sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept',
            'reason':'隔离工程范围','allow_local':True,'max_runs':2,'max_fits':1,'timeout':3})
        if autonomous:
            research.autonomy.enable(sid,plan['id'],plan_digest(plan),
                {'objective':'API工程边界','evaluation':'固定协议','data_boundary':'隔离数据',
                 'allowed_changes':'只读核验','frozen_files':[]},'隔离自主授权')
        review=store.add_record(sid,{'kind':'review','title':'结束评审','content':'没有宣称数值目标达成',
            'plan_id':plan['id'],'run_ids':[],'round_outcome':'stop_requested','stop_reason':'budget'}, {})
        return project,sid,plan,review

    def read_json(self,path,data=None):
        with self.request(path,data) as response:return json.load(response)

    def test_autonomous_record_exposes_pinned_closure_and_rejects_extra_human_approval(self):
        project,sid,plan,review=self.study()
        record=self.read_json('/api/records/'+review['id'])
        self.assertEqual(record['record_id'],review['id'])
        self.assertEqual(record['project_id'],project['id'])
        self.assertEqual(record['document']['content'],review['content'])
        self.assertEqual(record['closure'],{'stop_reason':'budget','target_verified':False,
            'closure_source':'autonomous_assessment','requires_human_confirmation':False})
        self.assertIn('自主',record['record']['收束方式'])
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request('/api/research/round-stop',{'session_id':sid,'review_id':review['id'],
                'decision':'accept_stop','reason':'不得重复审批','confirmed':True})
        self.assertEqual(denied.exception.code,400)
        self.assertEqual(self.app.store.rows('SELECT * FROM research_round_decisions'),[])
        self.assertEqual(self.app.store.rows('SELECT * FROM deliveries'),[])
        self.assertIsNone(self.app.kernel.rpc)

    def test_manual_record_retains_human_stop_confirmation(self):
        project,sid,plan,review=self.study(False)
        record=self.read_json('/api/records/'+review['id'])
        self.assertTrue(record['closure']['requires_human_confirmation'])
        result=self.read_json('/api/research/round-stop',{'session_id':sid,'review_id':review['id'],
            'decision':'accept_stop','reason':'确认有限结束','confirmed':True})
        self.assertTrue(result['recorded'])
        self.assertEqual(self.app.kernel.research.workflow.publish(sid)['state'],'settled')
        self.assertIsNone(self.app.kernel.rpc)

    def test_nonreview_record_has_no_closure_or_mutation(self):
        project,sid,plan,review=self.study(False)
        record=self.read_json('/api/records/'+plan['id'])
        self.assertIsNone(record['closure'])
        self.assertEqual(record['plan_digest'],plan_digest(plan))
        self.assertEqual(self.app.store.one('SELECT * FROM records WHERE id=?',(plan['id'],)),plan)
        self.assertEqual(self.app.store.rows('SELECT * FROM deliveries'),[])


if __name__=='__main__':unittest.main()
