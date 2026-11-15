import copy
import hashlib
import json
import tempfile
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

from lab.store import Store,now,dumps
from lab.kernel import Kernel
from lab.roles import DEFAULT_POLICY
from lab.sdm import SDM
from lab.inquiry import InquiryReview,questions,source_text
from tests.test_core import FakeRPC


class InquiryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='iterlab-inquiry-')
        self.store=Store(Path(self.temp.name))
        self.pid=self.store.create_project('independent evidence')['id']
        self.sid=self.store.create_session(self.pid,'codex')['id']
        self.kernel=Kernel(self.store,FakeRPC)
        self.roles=self.kernel.roles
        self.review=InquiryReview(self.store)
        self.settings={'agent_roles':copy.deepcopy(DEFAULT_POLICY)}
        self.catalog=[{'id':m} for m in set(DEFAULT_POLICY['models'].values())]
        self.path=Path(self.store.session(self.sid)['workspace'])/'measurement.json'
        self.path.write_text('{"observed":4}',encoding='utf-8')
        SDM(self.store).binding(self.sid)
        self.sent=[]
        self.goal=self.delivery('Decide what to investigate from actual evidence','user')
        self.activate(self.goal)
        self.target=self.record('result',evidence=['measurement.json'])
        self.finish()

    def tearDown(self):
        self.kernel.close();self.store.close();self.temp.cleanup()

    def delivery(self,text,origin='background',delivery_id=None):
        id=delivery_id or uuid.uuid4().hex
        stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(id,self.sid,text,'queue','queued',None,None,stamp,stamp))
        self.kernel.research.workflow.input(self.sid,id,origin)
        return self.store.one('SELECT * FROM deliveries WHERE id=?',(id,))

    def activate(self,delivery):
        self.turn=uuid.uuid4().hex
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id=? WHERE id=?",(self.turn,delivery['id']))
        d=self.store.one('SELECT * FROM deliveries WHERE id=?',(delivery['id'],))
        self.roles.route(self.sid,d,self.settings,self.catalog)
        self.store.patch_session(self.sid,status='running',active_turn=self.turn)

    def finish(self,status='completed'):
        self.store.event(self.sid,'turn/completed',{'turn':{'id':self.turn,'status':status}})
        self.store.patch_session(self.sid,status='idle',active_turn=None)

    def record(self,kind,**fields):
        if fields.get('review_of') and fields.get('evidence') and 'inquiry_checks' not in fields:
            fields['inquiry_checks']=[self.check(topic) for topic in ['objective','decision']]
        return self.store.add_record(self.sid,{'kind':kind,'title':'Generic inquiry','content':'Investigate the source of a decision-relevant measurement',**fields},
            {'origin':'codex-native-tool','turn_id':self.turn})

    def check(self,topic='decision',**fields):
        return {'topic':topic,'claim':'The observed quantity is recorded','basis':'source','reason':'Read original material',
                'source_quotes':[{'path':'measurement.json','text':'{"observed":4}'}],'command_ids':[],**fields}

    def receipt(self,exit_code=0,sid=None):
        item_id=uuid.uuid4().hex
        self.store.event(sid or self.sid,'item/completed',{'turnId':self.turn,'item':{
            'id':item_id,'type':'commandExecution','status':'completed','exitCode':exit_code,
            'command':'python local_check.py','aggregatedOutput':'observed=4'}})
        return item_id

    def test_utf8_source_is_preserved_through_actual_dynamic_tool_response(self):
        path=self.path.parent/'目标.md'
        text='指标以原始定义为准：相对参考值。\n第二行：μm，毫米。\n'
        path.write_text(text,encoding='utf-8-sig')
        self.store.patch_session(self.sid,thread_id='native-thread')
        rpc=FakeRPC([],str(self.path.parent),None,None,None)
        self.kernel.on_request({'id':'unicode-source','method':'item/tool/call','params':{
            'threadId':'native-thread','turnId':self.turn,'callId':'unicode-source','tool':'research_snapshot',
            'arguments':{'file_path':'目标.md','line_start':1,'line_count':1}}},rpc)
        response=rpc.replies[-1][1]
        self.assertTrue(response['success'])
        result=json.loads(response['contentItems'][0]['text'])
        self.assertEqual(result['text'],path.read_bytes().decode('utf-8-sig').splitlines(keepends=True)[0])
        self.assertNotIn('\ufffd',result['text'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())

    def test_utf8_reader_rejects_bad_bytes_and_outside_paths(self):
        invalid=self.path.parent/'invalid.txt';invalid.write_bytes(b'\xff\xfe\x00')
        with self.assertRaisesRegex(ValueError,'不是有效UTF-8'):
            source_text(self.store,self.sid,'invalid.txt')
        with self.assertRaises(ValueError):source_text(self.store,self.sid,'../outside.txt')
        with self.assertRaises(ValueError):source_text(self.store,self.sid,'measurement.json',line_count=201)

    def test_native_failure_with_null_output_remains_readable(self):
        self.store.event(self.sid,'item/completed',{'turnId':self.turn,'item':{
            'id':'empty-output','type':'commandExecution','status':'completed','exitCode':1,
            'command':None,'aggregatedOutput':None}})
        item=self.review.context(self.sid)['native_commands']['items'][0]
        self.assertEqual(item['item_id'],'empty-output')
        self.assertEqual(item['output_excerpt'],'')
        self.assertEqual(item['command'],'')
        self.assertEqual(item['exit_code'],1)

    def unresolved_review(self):
        job=self.start_review();q=self.record('question')
        self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
            'question_id':q['id'],'outcome':'unresolved','reason':'A locally testable explanation remains',
            'evidence':['measurement.json'],'next_check':{'kind':'local','action':'Recompute the observed quantity','reason':'Inputs already exist'}}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(job['feedback_delivery'],)))
        return job,q

    def test_source_paraphrase_cannot_be_claimed_as_exact_original(self):
        self.start_review()
        bad=self.check(source_quotes=[{'path':'measurement.json','text':'observed is forty'}])
        with self.assertRaisesRegex(ValueError,'实际原文'):
            self.record('decision',evidence=['measurement.json'],inquiry_checks=[bad])
        r=self.record('decision',evidence=['measurement.json'],inquiry_checks=[self.check()])
        self.assertEqual(json.loads(r['source'])['inquiry_checks'][0]['source_quotes'][0]['text'],'{"observed":4}')

    def test_computation_requires_successful_same_project_native_receipt(self):
        self.start_review()
        for item_id in ['invented-command',self.receipt(exit_code=1)]:
            with self.assertRaisesRegex(ValueError,'真实成功'):
                self.record('decision',evidence=['measurement.json'],inquiry_checks=[self.check(basis='calculation',source_quotes=[],command_ids=[item_id])])
        other=self.store.create_project('unrelated')['id'];sid=self.store.create_session(other,'codex')['id']
        with self.assertRaisesRegex(ValueError,'真实成功'):
            self.record('decision',evidence=['measurement.json'],inquiry_checks=[self.check(basis='calculation',source_quotes=[],command_ids=[self.receipt(sid=sid)])])
        item_id=self.receipt()
        r=self.record('decision',evidence=['measurement.json'],inquiry_checks=[self.check(basis='calculation',source_quotes=[],command_ids=[item_id])])
        receipt=json.loads(r['source'])['inquiry_checks'][0]['command_receipts'][0]
        self.assertEqual(receipt['item_id'],item_id)
        self.assertEqual(len(receipt['sha256']),64)
        self.assertEqual(self.review.context(self.sid)['native_commands']['items'][0]['item_id'],item_id)

    def test_files_and_question_labels_alone_do_not_complete_review(self):
        self.start_review();q=self.record('question')
        self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_checks=[],inquiry_outcomes=[{
            'question_id':q['id'],'outcome':'supported','reason':'A real file exists','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertIsNone(self.store.one('SELECT review_id FROM research_inquiry_reviews')['review_id'])

    def test_local_work_cannot_be_finished_by_merely_repeating_conclusion(self):
        job,q=self.unresolved_review()
        self.assertIn(q['id'],self.review.instructions({**self.store.one('SELECT * FROM research_inquiry_reviews'),'role':'planner'}))
        self.record('decision',evidence=['measurement.json'],inquiry_outcomes=[{
            'question_id':q['id'],'outcome':'supported','reason':'Repeating the original conclusion','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'incomplete')
        self.assertEqual(len(self.sent),2)

    def test_local_work_closes_with_linked_actual_check(self):
        job,q=self.unresolved_review();item_id=self.receipt()
        self.record('decision',evidence=['measurement.json'],inquiry_checks=[self.check(question_id=q['id'],basis='calculation',source_quotes=[],command_ids=[item_id])],inquiry_outcomes=[{
            'question_id':q['id'],'outcome':'refuted','reason':'Actual recomputation contradicts the explanation','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'done')

    def test_new_external_blocker_is_retained_without_unbounded_retries(self):
        job,q=self.unresolved_review()
        self.record('decision',inquiry_outcomes=[{'question_id':q['id'],'outcome':'unresolved','reason':'Required source column is absent','evidence':[],
            'next_check':{'kind':'needs_input','action':'Obtain missing source column','reason':'Local materials do not contain it'}}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'done')
        self.assertTrue(questions(self.store,self.pid)['items'][0]['open'])
        self.assertEqual(len(self.sent),2)

    def test_unresolved_review_needs_explicit_next_check_disposition(self):
        self.start_review();q=self.record('question')
        with self.assertRaisesRegex(ValueError,'next_check'):
            self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
                'question_id':q['id'],'outcome':'unresolved','reason':'Unknown','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertIsNone(self.store.one('SELECT review_id FROM research_inquiry_reviews')['review_id'])

    def test_legacy_partial_review_reuses_real_feedback_without_another_model_turn(self):
        old=self.start_review();q=self.record('question')
        # Simulate the persisted v2 report: real source checks, but its unknown
        # outcome was saved without a next-check classification.
        with patch('lab.inquiry.report_issues',return_value=[]):
            partial=self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
                'question_id':q['id'],'outcome':'unresolved','reason':'Missing independent input','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        oldrow=self.store.one('SELECT * FROM research_inquiry_reviews WHERE id=?',(old['id'],))
        note=self.review.instructions({**oldrow,'role':'planner'})
        self.assertIn(partial['id'],note)
        self.assertIn('next_check',note)
        self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(old['feedback_delivery'],)))
        supplement=self.record('decision',inquiry_outcomes=[{'question_id':q['id'],'outcome':'unresolved','reason':'Input remains absent','evidence':[],
            'next_check':{'kind':'needs_input','action':'Obtain independent input','reason':'Not in current materials'}}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        jobs=self.store.rows('SELECT * FROM research_inquiry_reviews ORDER BY rowid')
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['state'],'done')
        self.assertEqual(jobs[0]['review_id'],partial['id'])
        self.assertNotIn('next_check',json.loads(self.store.one('SELECT source FROM records WHERE id=?',(partial['id'],))['source'])['inquiry_outcomes'][0])
        event=self.store.one("SELECT payload FROM events WHERE kind='research.inquiry_review_supplemented'")
        self.assertEqual(json.loads(event['payload'])['supplement_record_ids'],[supplement['id']])
        self.assertEqual(len(self.sent),2)
        self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(len(self.sent),2)
        self.assertTrue(questions(self.store,self.pid)['items'][0]['open'])

    def test_reconcile_old_incomplete_state_from_actual_completed_records(self):
        job=self.start_review();q=self.record('question')
        with patch('lab.inquiry.report_issues',return_value=[]):
            self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
                'question_id':q['id'],'outcome':'unresolved','reason':'Missing input','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(job['feedback_delivery'],)))
        self.record('decision',inquiry_outcomes=[{'question_id':q['id'],'outcome':'unresolved','reason':'Input absent','evidence':[],
            'next_check':{'kind':'needs_input','action':'Obtain data','reason':'Data absent'}}])
        self.finish()
        self.store.execute("UPDATE research_inquiry_reviews SET state='incomplete',review_id=NULL WHERE id=?",(job['id'],))
        InquiryReview(self.store).advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'done')
        self.assertEqual(len(self.sent),2)

    def test_missing_real_disposition_is_not_filled_by_reconciliation(self):
        job=self.start_review();q=self.record('question')
        with patch('lab.inquiry.report_issues',return_value=[]):
            self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
                'question_id':q['id'],'outcome':'unresolved','reason':'Missing input','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(job['feedback_delivery'],)))
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        InquiryReview(self.store).advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'incomplete')
        self.assertEqual(len(self.sent),2)

    def test_source_checked_review_does_not_need_to_invent_new_questions(self):
        job=self.start_review()
        report=self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT review_id FROM research_inquiry_reviews WHERE id=?',(job['id'],))['review_id'],report['id'])

    def test_incremental_review_can_leave_other_question_for_later_same_turn(self):
        job=self.start_review();one=self.record('question');two=self.record('question')
        self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
            'question_id':one['id'],'outcome':'supported','reason':'First part checked','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        current=self.store.one('SELECT * FROM research_inquiry_reviews WHERE id=?',(job['id'],))
        self.assertIsNone(current['review_id'])
        self.assertIn(two['id'],self.review.instructions({**current,'role':'planner'}))

    def test_failed_feedback_turn_is_incomplete_even_with_valid_review(self):
        job=self.start_review();q=self.record('question')
        self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
            'question_id':q['id'],'outcome':'supported','reason':'Original material checked','evidence':['measurement.json']}])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(job['feedback_delivery'],)))
        self.finish(status='failed');self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'incomplete')

    def send(self,sid,text,delivery_id,origin):
        self.sent.append(delivery_id)
        return self.delivery(text,origin,delivery_id)

    def start_review(self):
        self.review.advance(self.sid,self.send,self.settings)
        job=self.store.one("SELECT * FROM research_inquiry_reviews WHERE state NOT IN ('done','incomplete','superseded') ORDER BY rowid DESC LIMIT 1")
        self.assertIsNotNone(job)
        d=self.store.one('SELECT * FROM deliveries WHERE id=?',(job['critic_delivery'],))
        self.activate(d)
        return job

    def test_native_review_is_durable_original_goal_scoped_and_idempotent(self):
        job=self.start_review()
        packet=json.loads(job['packet'])
        self.assertEqual(packet['original_goal']['delivery_id'],self.goal['id'])
        self.assertEqual(packet['target']['record_id'],self.target['id'])
        self.assertEqual(self.roles.current(self.sid,self.turn)['role'],'critic')
        self.assertFalse(InquiryReview(self.store).advance(self.sid,self.send,self.settings))
        self.assertEqual(len(self.sent),1)

    def test_host_dispatch_uses_existing_guard_and_native_critic_route(self):
        self.roles.dispatch(self.sid,self.send,self.settings)
        self.assertEqual(len(self.sent),1)
        job=self.store.one('SELECT * FROM research_inquiry_reviews')
        self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(job['critic_delivery'],)))
        route=self.roles.current(self.sid,self.turn)
        self.assertEqual(route['role'],'critic')
        self.assertIn(self.goal['text'],self.roles.instructions(route))
        with self.assertRaises(ValueError):self.roles.check_tool(self.sid,self.turn,'research_handoff',{})

    def test_changed_target_cannot_be_replayed_under_original_review_id(self):
        self.review.advance(self.sid,self.send,self.settings)
        job=self.store.one('SELECT * FROM research_inquiry_reviews')
        self.store.execute('UPDATE records SET content=? WHERE id=?',('Mutated frozen conclusion',self.target['id']))
        with self.assertRaises(ValueError):self.review.route(self.sid,job['critic_delivery'])

    def test_question_evidence_links_and_automatic_feedback(self):
        job=self.start_review()
        question=self.record('question')
        report=self.record('decision',review_of=[self.target['id']],evidence=['measurement.json'],inquiry_outcomes=[{
            'question_id':question['id'],'outcome':'supported','reason':'Observed in recorded output','evidence':['measurement.json']}])
        self.finish()
        self.review.advance(self.sid,self.send,self.settings)
        row=self.store.one('SELECT * FROM research_inquiry_reviews')
        self.assertEqual(row['review_id'],report['id'])
        self.assertEqual(self.sent[-1],job['feedback_delivery'])
        state=questions(self.store,self.pid)['items'][0]
        self.assertFalse(state['open'])
        self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(job['feedback_delivery'],)))
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'done')
        self.assertEqual(len(self.sent),2)

    def test_unknown_outcomes_stay_open_and_checked_requires_files(self):
        self.start_review();q=self.record('question')
        item={'question_id':q['id'],'outcome':'supported','reason':'No measurement','evidence':[]}
        with self.assertRaises(ValueError):self.record('decision',inquiry_outcomes=[item])
        item['outcome']='unresolved'
        self.record('decision',inquiry_outcomes=[item])
        self.assertTrue(questions(self.store,self.pid)['items'][0]['open'])

    def test_review_without_problem_coverage_is_not_certified(self):
        self.start_review();self.record('decision',review_of=[self.target['id']])
        self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertIsNone(self.store.one('SELECT review_id FROM research_inquiry_reviews')['review_id'])

    def test_foreign_question_and_unfrozen_file_are_rejected(self):
        self.start_review();q=self.record('question')
        item={'question_id':q['id'],'outcome':'refuted','reason':'Measured','evidence':['absent.json']}
        with self.assertRaises(ValueError):self.record('decision',inquiry_outcomes=[item])
        with self.assertRaises(ValueError):self.record('decision',review_of=['not-a-record'])
        other=self.store.create_project('other project')['id']
        other_sid=self.store.create_session(other,'codex')['id']
        foreign=self.store.add_record(other_sid,{'kind':'question','title':'Other','content':'Other task'}, {})
        item.update(question_id=foreign['id'],outcome='unresolved',evidence=[])
        with self.assertRaises(ValueError):self.record('decision',inquiry_outcomes=[item])

    def test_unknown_pause_and_newer_human_input_do_not_replay(self):
        self.review.advance(self.sid,self.send,self.settings)
        job=self.store.one('SELECT * FROM research_inquiry_reviews')
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(job['critic_delivery'],))
        self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(len(self.sent),1)
        self.store.execute("UPDATE deliveries SET state='rejected' WHERE id=?",(job['critic_delivery'],))
        newer=self.delivery('A different objective','user');self.activate(newer);self.finish()
        with self.assertRaises(ValueError):self.review.route(self.sid,job['critic_delivery'])
        self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT state FROM research_inquiry_reviews')['state'],'superseded')

    def test_paused_and_failed_source_turn_dont_schedule(self):
        self.kernel.research.workflow.pause(self.sid)
        self.assertFalse(self.review.advance(self.sid,self.send,self.settings))
        self.assertFalse(self.sent)

    def test_failed_source_turn_does_not_schedule(self):
        self.store.event(self.sid,'turn/completed',{'turn':{'id':self.turn,'status':'failed'}})
        self.assertFalse(self.review.advance(self.sid,self.send,self.settings))

    def test_old_fixed_binding_does_not_enable_new_engine(self):
        binding=self.store.one('SELECT * FROM research_method_bindings WHERE project_id=?',(self.pid,))
        manifest=json.loads(binding['manifest']);manifest.pop('inquiry-engine.json')
        original=Path(binding['path']).parent
        legacy=Path(self.temp.name)/'legacy-method';legacy.mkdir()
        for name in manifest:
            target=legacy/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((original/name).read_bytes())
        self.store.execute('UPDATE research_method_bindings SET manifest=?,digest=?,path=? WHERE project_id=?',
                           (dumps(manifest),hashlib.sha256(dumps(manifest).encode()).hexdigest(),str(legacy/'contract.md'),self.pid))
        self.assertFalse(self.review.context(self.sid)['enabled'])

    def test_two_reviews_bound_one_original_goal(self):
        for n in range(2):
            job=self.start_review()
            q=self.record('question')
            self.record('decision',review_of=[job['target_id']],evidence=['measurement.json'],inquiry_outcomes=[{
                'question_id':q['id'],'outcome':'supported','reason':'Observed','evidence':['measurement.json']}])
            self.finish();self.review.advance(self.sid,self.send,self.settings)
            self.activate(self.store.one('SELECT * FROM deliveries WHERE id=?',(job['feedback_delivery'],)))
            self.record('result',content='Updated decision '+str(n),evidence=['measurement.json'])
            self.finish();self.review.advance(self.sid,self.send,self.settings)
        self.assertEqual(self.store.one('SELECT COUNT(*) n FROM research_inquiry_reviews')['n'],2)


if __name__=='__main__':unittest.main()
