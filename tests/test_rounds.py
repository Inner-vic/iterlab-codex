from pathlib import Path
import sys
import unittest
import uuid

from lab.store import Store
from lab.research import Research,plan_digest
from lab import rounds

ROOT=Path(__file__).resolve().parents[1]


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        self.pid=self.store.create_project('round-fixture')['id']
        self.sid=self.store.create_session(self.pid,'ept')['id'];self.research=Research(self.store)
        self.first=self.record('plan','原方案')
        self.approve(self.first)
        workspace=Path(self.store.session(self.sid)['workspace']);(workspace/'probe.py').write_text("open('metrics.json','w').write('{}')",encoding='utf-8')
        self.request={'plan_id':self.first['id'],'script':'probe.py','files':['probe.py'],'args':[],'purpose':'diagnostic',
                      'question':'程序轮次边界是否准确','expected_information':'只验证编排，不验证科学质量','outputs':['metrics.json']}
    def tearDown(self):self.research.close();self.store.close()
    def record(self,kind,title,**fields):
        return self.store.add_record(self.sid,{'kind':kind,'title':title,'content':'实际研究判断由Codex负责，此为程序回归','status':'recorded',**fields},{'origin':'isolated-engineering-test'})
    def approve(self,plan,**fields):
        return self.research.review(self.sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept',
            'reason':'隔离回归，核对具体范围','execution':'local','allow_local':True,'python':sys.executable,
            'max_runs':4,'max_fits':2,'timeout':3,**fields})
    def close_round(self,run=None,**fields):
        return self.record('review','本轮方案评审',plan_id=self.first['id'],run_ids=[run['run_id']] if run else [],**fields)
    def successor(self,review,**fields):
        return self.record('plan','下一轮方案',previous_plan_id=self.first['id'],review_record_id=review['id'],**fields)
    def test_results_do_not_close_round_and_successor_waits_for_user_review(self):
        run=self.research.run(self.sid,self.request,'one-experiment')
        self.record('result','结果记录',run_ids=[run['run_id']])
        self.research.focus(self.sid,'deliver','仅交付结果也不能绕过轮次评审')
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'awaiting_round_review')
        review=self.close_round(run)
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'awaiting_next_plan')
        with self.assertRaises(ValueError):self.research.run(self.sid,self.request,'old-grant-cannot-cross')
        candidate=self.successor(review)
        self.assertEqual(candidate['parent_id'],review['id'])
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'waiting_plan_review')
        location=self.research.workflow.location(self.sid,self.store.graph(self.pid))
        self.assertEqual((location['state'],location['focus_node_id']),('waiting',candidate['id']))
        with self.assertRaises(ValueError):self.research.run(self.sid,{**self.request,'plan_id':candidate['id']},'unapproved-next')
        self.approve(candidate)
        self.assertEqual(rounds.state(self.store,self.pid)['plan_id'],candidate['id'])
        executed=self.research.run(self.sid,{**self.request,'plan_id':candidate['id']},'approved-next')
        self.assertEqual(executed['state'],'completed')
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),2)
    def test_unlinked_plan_cannot_be_approved_as_another_round(self):
        candidate=self.record('plan','无收尾关系的新方案')
        with self.assertRaises(ValueError):self.approve(candidate)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_grants')),1)
    def test_multiple_experiments_inside_one_plan_do_not_repeat_human_review(self):
        first=self.research.run(self.sid,self.request,'within-plan-1')
        second=self.research.run(self.sid,self.request,'within-plan-2')
        self.assertEqual([first['state'],second['state']],['completed','completed'])
        self.assertEqual(len(self.store.rows('SELECT * FROM research_reviews')),1)
        review=self.record('review','多实验后的轮次评审',plan_id=self.first['id'],run_ids=[first['run_id'],second['run_id']])
        import json
        self.assertEqual(len(json.loads(review['source'])['indexed_runs']),2)
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'awaiting_next_plan')
    def test_early_revision_needs_no_mechanical_training_and_rejection_preserves_versions(self):
        review=self.close_round()
        candidate=self.successor(review)
        self.research.review(self.sid,{'plan_id':candidate['id'],'expected_digest':plan_digest(candidate),'decision':'reject','reason':'比较条件不够清楚'})
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'next_plan_rejected')
        revised=self.successor(review,content='已补比较边界')
        self.assertEqual((candidate['version'],revised['version']),('1.0.0','1.0.1'))
        self.assertEqual(self.store.one('SELECT content FROM records WHERE id=?',(candidate['id'],))['content'],candidate['content'])
        self.approve(revised)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
    def test_unknown_run_cannot_be_hidden_by_round_review(self):
        run=self.research.run(self.sid,self.request,'unknown-original')
        self.store.execute("UPDATE research_runs SET state='unknown',receipt=NULL WHERE id=?",(run['run_id'],))
        with self.assertRaises(ValueError):self.close_round(run)
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'blocked')
    def test_stop_is_a_human_review_not_model_self_completion(self):
        review=self.close_round(round_outcome='stop_requested')
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'waiting_stop_review')
        with self.assertRaises(ValueError):self.research.review_stop(self.sid,review['id'],'accept_stop','自评通过',actor='研究模型')
        first=self.research.review_stop(self.sid,review['id'],'reject_stop','请补充依据')
        again=self.research.review_stop(self.sid,review['id'],'reject_stop','请补充依据')
        self.assertEqual(first['decision_id'],again['decision_id'])
        self.assertFalse(again['changed'])
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'stop_rejected')
        self.research.review_stop(self.sid,review['id'],'accept_stop','已理解局限，确认本轮结束')
        self.assertEqual(self.research.workflow.publish(self.sid)['state'],'settled')
        with self.assertRaises(ValueError):self.research.run(self.sid,self.request,'stop-cannot-retrain')
    def test_review_links_cannot_cross_projects_or_omit_actual_run_evidence(self):
        run=self.research.run(self.sid,self.request,'actual')
        with self.assertRaises(ValueError):self.close_round()
        review=self.close_round(run)
        other=self.store.create_project('foreign')['id'];sid=self.store.create_session(other,'ept')['id']
        with self.assertRaises(ValueError):self.store.add_record(sid,{'kind':'plan','title':'错误承接','content':'跨项目',
            'previous_plan_id':self.first['id'],'review_record_id':review['id']},{})

    def test_round_cannot_hide_failed_attempt_behind_a_successful_run(self):
        import json
        first=self.research.run(self.sid,self.request,'successful-attempt')
        workspace=Path(self.store.session(self.sid)['workspace'])
        (workspace/'probe.py').write_text("raise RuntimeError('isolated failure')",encoding='utf-8')
        failed=self.research.run(self.sid,self.request,'failed-attempt')
        self.assertEqual(failed['state'],'failed')
        with self.assertRaisesRegex(ValueError,'不能遗漏'):
            self.close_round(first)
        self.assertIsNone(rounds.round_review(self.store,self.pid,self.first['id']))
        review=self.record('review','成功与失败都须解释',plan_id=self.first['id'],run_ids=[first['run_id'],failed['run_id']])
        self.assertTrue(json.loads(review['source'])['evidence_coverage']['all_attempts_referenced'])
        self.assertEqual(len(self.store.rows('SELECT * FROM research_runs')),2)

    def test_evidence_index_keeps_failed_attempts_and_old_records_without_claiming_quality(self):
        from lab.round_evidence import packet
        import json
        first=self.research.run(self.sid,self.request,'first-attempt')
        second=self.research.run(self.sid,self.request,'cancelled-attempt')
        self.store.execute("UPDATE research_runs SET state='cancelled' WHERE id=?",(second['run_id'],))
        result=self.record('result','原始结果',run_ids=[first['run_id']])
        before=self.store.rows('SELECT * FROM records')
        page=packet(self.store,self.pid,0,page_size=1)
        self.assertEqual(page['all_run_ids'],[first['run_id'],second['run_id']])
        self.assertEqual((page['attempt_count'],page['next_offset']),(2,1))
        self.assertEqual(page['attempts'][0]['result_records'][0]['id'],result['id'])
        self.assertIn('receipt.json',page['attempts'][0]['receipt_path'])
        tail=packet(self.store,self.pid,page['next_offset'],page_size=1)
        self.assertEqual(tail['attempts'][0]['state'],'cancelled')
        self.assertIsNone(tail['next_offset'])
        self.assertNotIn('metrics',page['attempts'][0]) # no automatic metric extraction/ranking
        self.assertEqual(self.store.rows('SELECT * FROM records'),before)
        self.assertEqual(self.research.status(self.sid,'brief')['round_evidence']['attempt_count'],2)
        for bad in [-1,True,'1']:
            with self.assertRaises(ValueError):packet(self.store,self.pid,bad)

    def test_cloud_review_covers_failed_submission_and_excludes_preparation_only(self):
        from lab.company import Company
        from lab.store import now
        import json
        Company(self.store) # schema only; no platform request
        trial=self.record('trial','云实验',plan_id=self.first['id'])
        for id,state in [('cloud-ok','succeeded'),('cloud-bad','failed'),('preview','prepared')]:
            self.store.execute('INSERT INTO company_training VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (id,self.sid,self.pid,self.first['id'],trial['id'],'component',id,'{}',state,None,now(),now()))
        with self.assertRaisesRegex(ValueError,'不能遗漏'):
            self.close_round(cloud_run_ids=['cloud-ok'])
        evidence=self.research.status(self.sid,'brief')['round_evidence']
        self.assertEqual(evidence['all_cloud_run_ids'],['cloud-ok','cloud-bad'])
        review=self.close_round(cloud_run_ids=['cloud-ok','cloud-bad'])
        self.assertEqual(len(json.loads(review['source'])['indexed_cloud_runs']),2)

    def test_legacy_review_is_not_rewritten_by_reading_status(self):
        import json
        review=self.close_round()
        source=json.loads(review['source']);source.pop('evidence_coverage')
        self.store.execute('UPDATE records SET source=? WHERE id=?',(json.dumps(source),review['id']))
        before=self.store.one('SELECT * FROM records WHERE id=?',(review['id'],))
        self.research.status(self.sid,'brief')
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(review['id'],)),before)


if __name__=='__main__':unittest.main()
