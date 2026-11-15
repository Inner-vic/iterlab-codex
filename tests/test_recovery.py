"""System-stall repair passage; old closures and manual stops remain intact."""
import json
import unittest
from unittest.mock import patch
from tests import test_autonomy as fixtures
from lab import recovery
from lab.store import now
from lab.research import plan_digest


class RecoveryTests(unittest.TestCase):
    setUp=fixtures.AutonomyTests.setUp
    tearDown=fixtures.AutonomyTests.tearDown
    record=fixtures.AutonomyTests.record
    adopt=fixtures.AutonomyTests.adopt
    # Reuse only the isolated setup, not inherited compute tests.
    def begin(self):
        self.kernel.settings.save({'agent_roles':{'version':1,'models':dict.fromkeys(['planner','executor','generator','critic'],'fixture-model')}})
        from lab.autonomy import policy,active
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.finalize_once(self.sid,policy(self.store,self.pid),active(self.store,self.pid),'no_progress')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='old-report'")
        self.review=self.store.add_record(self.sid,{'kind':'review','title':'system stall',
            'content':'无计算结果；原协议保留。','evidence':['protocol.md'],'plan_id':self.first['id'],
            'run_ids':[],'round_outcome':'stop_requested','stop_reason':'no_progress'}, {'origin':'codex-native-tool'})
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid)
        self.request=self.store.one('SELECT * FROM research_recoveries')
        self.assertIsNotNone(self.request)
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(self.request['delivery_id'],))

    def candidate(self,**changes):
        status=self.r.autonomy.status(self.sid)
        return self.store.add_record(self.sid,{'kind':'plan','title':'repair',
            'content':'依据原报错修复输入路径，保持模型、划分、指标和GPU上限不变。','evidence':['protocol.md'],
            'previous_plan_id':self.first['id'],'review_record_id':self.review['id'],
            'authority_id':status['authority_id'],'scope_digest':status['scope_digest'],**changes},
            {'origin':'codex-native-tool'})

    def critic(self,plan,verdict='approve'):
        stamp=now();delivery='isolated-critic'
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(delivery,self.sid,'fixture','queue','accepted','critic-turn',None,stamp,stamp))
        self.store.execute('INSERT INTO research_role_routes VALUES(?,?,?,?,?,?,?)',
            (delivery,self.sid,'critic','fixture-model',json.dumps(self.kernel.settings.value['agent_roles']),None,stamp))
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('critic-proof',self.sid,delivery,'planner',plan['id'],plan_digest(plan),plan['id'],verdict,'fixture independent review','critic-return','dispatched',stamp))

    def test_repair_needs_exact_critic_and_keeps_grant_and_closure(self):
        self.begin();plan=self.candidate()
        with self.assertRaisesRegex(ValueError,'Critic'):self.adopt(plan)
        self.critic(plan)
        before=self.store.rows('SELECT * FROM research_grants')
        self.adopt(plan)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertEqual(self.store.one('SELECT source FROM records WHERE id=?',(self.review['id'],))['source'],self.review['source'])
        self.assertEqual(self.store.one('SELECT admitted_plan_id FROM research_recoveries')['admitted_plan_id'],plan['id'])
        self.assertEqual(len(self.store.rows('SELECT * FROM research_finalizations')),1)
        self.assertEqual(self.r.status(self.sid)['attempts'],{'runs':0,'fits':0})

    def test_recovery_delivery_is_once_and_generic_background_stays_blocked(self):
        self.begin()
        self.kernel.check_automatic_delivery(self.sid,self.request['delivery_id'])
        stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('stale',self.sid,'stale','queue','rejected',None,None,stamp,stamp))
        self.store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',('stale','background',stamp))
        with self.assertRaises(ValueError):
            self.kernel.check_automatic_delivery(self.sid,'stale')
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.continuation.tick(self.sid);self.kernel.continuation.tick(self.sid)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_recoveries')),1)
        self.assertEqual(len(self.store.rows('SELECT * FROM deliveries')),3)

    def test_user_pause_and_rejection_and_unknown_do_not_pass(self):
        self.begin();plan=self.candidate();self.critic(plan)
        self.r.workflow.pause(self.sid)
        with self.assertRaises(ValueError):self.adopt(plan)
        self.store.execute('UPDATE research_controls SET paused=0 WHERE session_id=?',(self.sid,))
        with self.assertRaises(ValueError):self.adopt(plan) # A normal message is not resume authorization.
        self.store.execute('DELETE FROM research_user_stops WHERE session_id=?',(self.sid,)) # Fixture isolates the next boundary.
        self.store.execute("UPDATE deliveries SET state='unknown' WHERE id=?",(self.request['delivery_id'],))
        with self.assertRaisesRegex(ValueError,'未知投递'):self.adopt(plan)
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(self.request['delivery_id'],))
        self.store.execute("UPDATE research_role_handoffs SET verdict='revise' WHERE id='critic-proof'")
        with self.assertRaisesRegex(ValueError,'Critic'):self.adopt(plan)

    def test_user_stop_and_plain_closure_are_not_system_recovery(self):
        review=self.record('review','user stop',plan_id=self.first['id'],run_ids=[],round_outcome='stop_requested',stop_reason='user_stop')
        self.assertIsNone(recovery.context(self.store,self.pid))
        with patch.object(self.kernel.pool,'submit'):self.kernel.continuation.tick(self.sid)
        self.assertFalse(self.store.rows('SELECT * FROM research_recoveries'))

    def test_running_native_turn_does_not_revoke_plan_grant(self):
        self.store.patch_session(self.sid,status='running',active_turn='executor-turn')
        status=self.r.status(self.sid,'brief')
        self.assertEqual(status['lifecycle']['state'],'working')
        self.assertTrue(status['execution_authorization']['grant_valid'])
        self.assertTrue(status['execution_authorization']['can_request_preflight'])
        roles=self.kernel.roles.status(self.sid,self.kernel.settings.value)
        self.assertNotIn('execution_authority',roles)
        self.assertFalse(roles['role_routing_grants_execution'])

    def test_new_human_proposal_can_reach_critic_without_reviving_closed_execution(self):
        self.begin();old_grants=self.store.rows('SELECT * FROM research_grants')
        with patch.object(self.kernel.pool,'submit'):
            message=self.kernel.send(self.sid,'为新执行范围制定方案，审核后再执行',delivery_id='fresh-human')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(message['id'],))
        plan=self.candidate(scope_proposal={'objective':'新范围待审核'})
        stamp=now();id='fresh-critic'
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (id,self.sid,message['id'],'critic',plan['id'],plan_digest(plan),plan['id'],'handoff','independent review','fresh-critic-delivery','pending',stamp))
        h=self.store.one('SELECT * FROM research_role_handoffs WHERE id=?',(id,))
        self.assertIsNotNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))
        self.assertTrue(self.kernel.guard_role_dispatch(self.sid,h))
        self.kernel.check_automatic_delivery(self.sid,h['delivery_id'])
        with patch.object(self.kernel,'resume_roles') as resume:self.kernel.continuation.tick(self.sid)
        resume.assert_called_once_with(self.sid)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),old_grants)
        self.assertEqual(self.store.one('SELECT source FROM records WHERE id=?',(self.review['id'],))['source'],self.review['source'])
        with self.assertRaises(ValueError):
            self.r.run(self.sid,{**self.request,'plan_id':plan['id']},'unapproved-scope')
        self.store.execute("UPDATE research_role_handoffs SET target='executor' WHERE id=?",(id,))
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))
        self.assertFalse(self.kernel.guard_role_dispatch(self.sid,{**h,'target':'executor'}))

    def test_old_or_superseded_human_input_does_not_release_stale_proposal(self):
        self.begin()
        with patch.object(self.kernel.pool,'submit'):
            message=self.kernel.send(self.sid,'讨论新范围',delivery_id='first-human')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(message['id'],))
        plan=self.candidate(scope_proposal={'objective':'待审核'})
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('review-proposal',self.sid,message['id'],'critic',plan['id'],plan_digest(plan),plan['id'],'handoff','review','proposal-review-delivery','pending',now()))
        self.r.workflow.pause(self.sid)
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,'proposal-review-delivery'))
        with patch.object(self.kernel.pool,'submit'):self.kernel.send(self.sid,'暂时不继续',delivery_id='later-human')
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,'proposal-review-delivery'))

    def steered_scope_proposal(self):
        self.begin();self.store.patch_session(self.sid,thread_id='discussion-thread')
        with patch.object(self.kernel.pool,'submit'):
            original=self.kernel.send(self.sid,'prepare new scope',delivery_id='steer-source')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='discussion-turn' WHERE id=?",(original['id'],))
        plan=self.candidate(scope_proposal={'objective':'pending scope, no execution permission'})
        with patch.object(self.kernel.pool,'submit'):
            steer=self.kernel.send(self.sid,'clarify the pending implementation',mode='steer',delivery_id='exact-steer')
        stamp=now()
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='discussion-turn',updated_at=? WHERE id=?",(stamp,steer['id']))
        self.store.execute('INSERT INTO delivery_requests VALUES(?,?,?,?,?,?,?,?,?)',
            (steer['id'],'steer-request','turn/steer','discussion-thread','fixture-generation',
             'discussion-turn','discussion-turn',0,stamp))
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('steered-review',self.sid,original['id'],'critic',plan['id'],plan_digest(plan),plan['id'],
             'handoff','review clarified draft','steered-review-delivery','pending',now()))
        return self.store.one('SELECT * FROM research_role_handoffs WHERE id=?',('steered-review',))

    def test_accepted_same_turn_steer_allows_only_pending_deliberation(self):
        h=self.steered_scope_proposal();grants=self.store.rows('SELECT * FROM research_grants')
        context=recovery.proposal_discussion(self.store,self.sid,h['delivery_id'])
        self.assertEqual(context['root_delivery_id'],'exact-steer')
        self.assertFalse(context['execution_authorized'])
        sent=[]
        def send(sid,text,**args):sent.append(args['delivery_id']);return {'state':'queued'}
        with patch.object(self.kernel.roles.progress,'allow',return_value={'allowed':True}):
            self.kernel.roles.dispatch(self.sid,send,self.kernel.settings.value)
        self.assertEqual(sent,[h['delivery_id']])
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))
        self.store.execute("UPDATE research_role_handoffs SET state='pending',target='executor' WHERE id=?",(h['id'],))
        self.store.execute('INSERT INTO research_role_execution_stages VALUES(?,?)',(h['id'],'execute'))
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))
        with patch.object(self.kernel.roles.progress,'allow',return_value={'allowed':True}):
            self.kernel.roles.dispatch(self.sid,send,self.kernel.settings.value)
        self.assertEqual(len(sent),1)
        self.assertEqual(self.store.one('SELECT state FROM research_role_handoffs WHERE id=?',(h['id'],))['state'],'superseded')

    def test_unconfirmed_other_turn_late_and_queued_inputs_keep_barriers(self):
        h=self.steered_scope_proposal()
        for field,value,original in [('state','unknown','accepted'),('mode','queue','steer')]:
            self.store.execute('UPDATE deliveries SET '+field+'=? WHERE id=?',(value,'exact-steer'))
            self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))
            self.store.execute('UPDATE deliveries SET '+field+'=? WHERE id=?',(original,'exact-steer'))
        self.store.execute("UPDATE delivery_requests SET expected_turn_id='other-turn' WHERE delivery_id='exact-steer'")
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))
        self.store.execute("UPDATE delivery_requests SET expected_turn_id='discussion-turn' WHERE delivery_id='exact-steer'")
        self.store.execute("UPDATE deliveries SET updated_at='9999-01-01' WHERE id='exact-steer'")
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))
        self.store.execute("UPDATE deliveries SET updated_at=created_at WHERE id='exact-steer'")
        self.r.workflow.pause(self.sid)
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))

    def test_invalid_closed_plan_handoff_fails_in_source_turn_without_pending_dead_end(self):
        self.begin()
        with patch.object(self.kernel.pool,'submit'):
            message=self.kernel.send(self.sid,'继续离线准备新GPU范围，尚未批准计算',delivery_id='repair-source')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='source-turn' WHERE id=?",(message['id'],))
        self.store.execute('INSERT INTO research_role_routes VALUES(?,?,?,?,?,?,?)',
            (message['id'],self.sid,'planner','fixture-model',json.dumps(self.kernel.settings.value['agent_roles']),None,now()))
        plan=self.candidate() # Legacy fork/repair text is not a new-scope proposal.
        before=self.store.rows('SELECT * FROM research_role_handoffs')
        with self.assertRaisesRegex(ValueError,'scope_proposal'):
            self.kernel.roles.handoff(self.sid,'source-turn',{'target':'executor','stage':'prepare','plan_id':plan['id'],
                'record_id':plan['id'],'verdict':'handoff','reason':'prepare GPU work package'},'invalid-prepare',self.r)
        self.assertEqual(self.store.rows('SELECT * FROM research_role_handoffs'),before)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_later_human_input_can_resume_same_unapproved_candidate_without_new_version(self):
        self.begin()
        with patch.object(self.kernel.pool,'submit'):
            first=self.kernel.send(self.sid,'只准备新范围',delivery_id='proposal-first-input')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(first['id'],))
        plan=self.candidate(scope_proposal={'objective':'待审核GPU范围'})
        old_records=self.store.rows('SELECT * FROM records');old_grants=self.store.rows('SELECT * FROM research_grants')
        with patch.object(self.kernel.pool,'submit'):
            later=self.kernel.send(self.sid,'复用这个候选继续离线准备，不执行',delivery_id='proposal-resume-input')
        self.store.execute("UPDATE deliveries SET state='accepted',turn_id='resume-turn' WHERE id=?",(later['id'],))
        self.store.execute('INSERT INTO research_role_routes VALUES(?,?,?,?,?,?,?)',
            (later['id'],self.sid,'planner','fixture-model',json.dumps(self.kernel.settings.value['agent_roles']),None,now()))
        h=self.kernel.roles.handoff(self.sid,'resume-turn',{'target':'executor','stage':'prepare','plan_id':plan['id'],
            'record_id':plan['id'],'verdict':'handoff','reason':'resume exact unapproved candidate'},'resumed-prepare',self.r)
        self.assertIsNotNone(recovery.proposal_discussion(self.store,self.sid,h['delivery_id']))
        self.assertTrue(self.kernel.guard_role_dispatch(self.sid,h))
        self.assertEqual(self.store.rows('SELECT * FROM records'),old_records)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),old_grants)

    def prepared_return(self):
        self.begin()
        with patch.object(self.kernel.pool,'submit'):
            root=self.kernel.send(self.sid,'仅离线修复待确认GPU工作包',delivery_id='prepare-root')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(root['id'],))
        plan=self.candidate(scope_proposal={'objective':'待确认范围'},evidence=['train.py'])
        stamp=now()
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('prepare-in',self.sid,root['id'],'executor',plan['id'],plan_digest(plan),plan['id'],'handoff','offline implementation','prepare-delivery','dispatched',stamp))
        self.store.execute('INSERT INTO research_role_execution_stages VALUES(?,?)',('prepare-in','prepare'))
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
            ('prepare-delivery',self.sid,'offline only','queue','accepted','prepare-turn',None,stamp,stamp))
        self.store.execute('INSERT INTO research_input_origins VALUES(?,?,?)',('prepare-delivery','background',stamp))
        self.store.execute('INSERT INTO research_role_routes VALUES(?,?,?,?,?,?,?)',
            ('prepare-delivery',self.sid,'executor','fixture-model',json.dumps(self.kernel.settings.value['agent_roles']),'prepare-in',stamp))
        (self.workspace/'train.py').write_text('# corrected implementation, no execution\n',encoding='utf8')
        output=self.store.add_record(self.sid,{'kind':'research','title':'actual offline output','content':'implementation corrected',
            'plan_id':plan['id'],'evidence':['train.py']},{'origin':'codex-native-tool','turn_id':'prepare-turn'})
        h=self.kernel.roles.handoff(self.sid,'prepare-turn',{'target':'planner','plan_id':plan['id'],
            'record_id':output['id'],'verdict':'handoff','reason':'review actual changed implementation'},'prepare-out',self.r)
        return plan,output,h

    def test_closed_budget_candidate_missing_new_scope_reports_exact_cause(self):
        review=self.record('review','budget closure',plan_id=self.first['id'],run_ids=[],round_outcome='stop_requested',stop_reason='budget')
        with patch.object(self.kernel.pool,'submit'):
            message=self.kernel.send(self.sid,'仅讨论待审核的新GPU范围',delivery_id='budget-new-scope')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(message['id'],))
        with self.assertRaisesRegex(ValueError,'缺少scope_proposal'):
            self.record('plan','incomplete candidate',previous_plan_id=self.first['id'],review_record_id=review['id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_changed_prepare_input_returns_for_review_without_reusing_approval(self):
        plan,output,h=self.prepared_return()
        before=self.store.rows('SELECT * FROM research_grants')
        original=self.store.one('SELECT * FROM records WHERE id=?',(plan['id'],))
        with self.assertRaisesRegex(ValueError,'冻结证据已变化'):self.r.check_plan_evidence(plan)
        self.assertTrue(self.kernel.roles.prepare_return(self.sid,h,self.r))
        self.assertTrue(self.kernel.guard_role_dispatch(self.sid,h))
        self.kernel.check_automatic_delivery(self.sid,h['delivery_id'])
        # A direct Critic/execute route cannot inherit this read-only exception.
        self.assertFalse(self.kernel.roles.prepare_return(self.sid,{**h,'target':'critic'},self.r))
        self.assertFalse(self.kernel.roles.prepare_return(self.sid,{**h,'target':'executor'},self.r))
        self.assertFalse(self.kernel.roles.prepare_return(self.sid,{**h,'plan_digest':'0'*64},self.r))
        self.store.execute("UPDATE research_role_execution_stages SET stage='execute' WHERE handoff_id='prepare-in'")
        self.assertFalse(self.kernel.guard_role_dispatch(self.sid,h))
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(plan['id'],)),original)

    def test_prepare_return_rejects_unrelated_or_changed_output_and_user_pause(self):
        plan,output,h=self.prepared_return()
        self.assertFalse(self.kernel.roles.prepare_return(self.sid,{**h,'record_id':self.review['id']},self.r))
        (self.workspace/'train.py').write_text('# changed again after feedback\n',encoding='utf8')
        self.assertFalse(self.kernel.guard_role_dispatch(self.sid,h))
        (self.workspace/'train.py').write_text('# corrected implementation, no execution\n',encoding='utf8')
        self.r.workflow.pause(self.sid)
        self.assertFalse(self.kernel.guard_role_dispatch(self.sid,h))

    def test_formal_new_scope_review_can_supersede_system_stop_without_rewriting_it(self):
        import sys
        from lab import rounds
        self.begin();old_grants=self.store.rows('SELECT * FROM research_grants')
        old_review=self.store.one('SELECT * FROM records WHERE id=?',(self.review['id'],))
        previous=self.r.autonomy.status(self.sid)
        plan=self.record('plan','明确的新范围',previous_plan_id=self.first['id'],review_record_id=self.review['id'],scope_proposal=self.scope,
            authority_id=previous['authority_id'],scope_digest=previous['scope_digest'])
        with self.assertRaises(ValueError):rounds.check_approval(self.store,plan)
        request={'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept','reason':'用户委托验收操作者首次审核新范围',
            'actor':'用户委托开发助手审批','execution':'local','allow_local':True,'python':sys.executable,
            'max_runs':4,'max_fits':3,'timeout':5,'research_scope':self.scope}
        with self.assertRaises(ValueError):self.r.review(self.sid,request) # No implicit new autonomous scope.
        result=self.r.review(self.sid,{**request,'autonomous':True})
        self.assertTrue(result['reviewed'])
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(self.review['id'],)),old_review)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants')[:len(old_grants)],old_grants)
        self.assertEqual(len(self.store.rows('SELECT * FROM research_grants')),2)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))
        historical=self.store.one('SELECT source FROM records WHERE id=?',(plan['id'],))
        adopted=self.r.autonomy.adopt(self.sid,plan['id'],plan_digest(plan),'核对刚正式批准的初始方案')
        self.assertTrue(adopted['reused']);self.assertEqual(adopted['source'],'formal_initial_approval')
        self.assertNotEqual(adopted['authority_id'],previous['authority_id'])
        self.assertEqual(self.store.one('SELECT source FROM records WHERE id=?',(plan['id'],)),historical)
        self.assertFalse(self.store.rows('SELECT * FROM research_plan_admissions'))
        self.r.review(self.sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'reject','reason':'用户撤销'})
        with self.assertRaises(ValueError):self.r.autonomy.adopt(self.sid,plan['id'],plan_digest(plan),'不能借重复采纳恢复撤销')

    def test_formal_new_scope_exception_never_revives_user_stop(self):
        from lab import rounds
        self.begin()
        plan=self.record('plan','新范围',previous_plan_id=self.first['id'],review_record_id=self.review['id'],scope_proposal=self.scope)
        self.store.execute('INSERT INTO research_round_decisions VALUES(?,?,?,?,?,?)',
            ('user-end',self.review['id'],'accept_stop','用户','用户明确停止',now()))
        with self.assertRaises(ValueError):rounds.check_approval(self.store,plan,new_scope_review=True)

    def test_fresh_request_after_budget_closure_allows_offline_prepare_not_old_grant(self):
        from lab import rounds
        review=self.record('review','预算已结束',plan_id=self.first['id'],run_ids=[],
                           round_outcome='stop_requested',stop_reason='budget')
        old_grants=self.store.rows('SELECT * FROM research_grants')
        with self.assertRaises(ValueError):
            self.record('plan','无新请求',previous_plan_id=self.first['id'],review_record_id=review['id'],scope_proposal=self.scope)
        with patch.object(self.kernel.pool,'submit'):
            message=self.kernel.send(self.sid,'先讨论GPU新范围，确认后运行',delivery_id='fresh-budget-proposal')
        self.store.execute("UPDATE deliveries SET state='accepted' WHERE id=?",(message['id'],))
        plan=self.record('plan','GPU候选',previous_plan_id=self.first['id'],review_record_id=review['id'],scope_proposal=self.scope)
        self.assertEqual(json.loads(plan['source'])['new_scope_request_id'],message['id'])
        self.assertIsNone(recovery.context(self.store,self.pid)) # Not a no-progress repair.
        stamp=now()
        self.store.execute('INSERT INTO research_role_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            ('fresh-prepare',self.sid,message['id'],'executor',plan['id'],plan_digest(plan),plan['id'],'handoff',
             'offline configuration only','fresh-prepare-delivery','pending',stamp))
        self.store.execute('INSERT INTO research_role_execution_stages VALUES(?,?)',('fresh-prepare','prepare'))
        handoff=self.store.one("SELECT * FROM research_role_handoffs WHERE id='fresh-prepare'")
        self.assertTrue(self.kernel.guard_role_dispatch(self.sid,handoff))
        with self.assertRaises(ValueError):rounds.check_approval(self.store,plan)
        rounds.check_approval(self.store,plan,new_scope_review=True)
        with self.assertRaises(ValueError):self.adopt(plan)
        with self.assertRaises(ValueError):self.r.run(self.sid,{**self.request,'plan_id':plan['id']},'unapproved-new-gpu')
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),old_grants)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.store.execute("UPDATE research_role_execution_stages SET stage='execute' WHERE handoff_id='fresh-prepare'")
        self.assertIsNone(recovery.proposal_discussion(self.store,self.sid,handoff['delivery_id']))
        self.assertFalse(self.kernel.guard_role_dispatch(self.sid,handoff))
        self.r.workflow.pause(self.sid)
        with self.assertRaises(ValueError):rounds.check_approval(self.store,plan,new_scope_review=True)

    def test_human_fork_cannot_activate_or_switch_around_closed_round(self):
        from lab import rounds
        review=self.record('review','停止容量搜索',plan_id=self.first['id'],run_ids=[],round_outcome='stop_requested',stop_reason='budget')
        before=self.store.rows('SELECT * FROM research_plan_admissions')
        with self.assertRaisesRegex(ValueError,'已收束'):
            self.r.autonomy.fork(self.sid,self.first['id'],'GPU讨论','尚未批准新GPU','先提出建议',activate=True)
        candidate=self.r.autonomy.fork(self.sid,self.first['id'],'GPU备选','尚未批准新GPU','先提出建议',activate=False)
        with self.assertRaisesRegex(ValueError,'已收束'):
            self.r.autonomy.switch(self.sid,candidate['branch_id'],'不能把讨论当执行')
        self.assertEqual(rounds.approved_plan(self.store,self.pid)['id'],self.first['id'])
        self.assertEqual(self.store.rows('SELECT * FROM research_plan_admissions'),before)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        # Historical buggy admissions are retained, but cannot bypass the new guard.
        from lab.autonomy import policy,Autonomy
        p=policy(self.store,self.pid)
        authority=self.store.one('SELECT scope_digest FROM research_authorities WHERE id=?',(p['authority_id'],))
        plan=self.r.plan(self.sid,candidate['plan_id'])
        self.store.execute('INSERT INTO research_plan_admissions VALUES(?,?,?,?,?,?,?)',
            (plan['id'],p['authority_id'],candidate['branch_id'],plan_digest(plan),authority['scope_digest'],'old bug fixture',now()))
        with self.assertRaisesRegex(ValueError,'旧采纳无效'):
            Autonomy.permission(self.store,plan,require_active=False)
        with self.assertRaisesRegex(ValueError,'旧采纳无效'):
            self.r.autonomy.switch(self.sid,candidate['branch_id'],'旧错误不能复活')

    def test_initial_new_scope_message_has_preparation_contract_not_old_budget_ban(self):
        from lab import rounds
        review=self.record('review','预算结束',plan_id=self.first['id'],run_ids=[],round_outcome='stop_requested',stop_reason='budget')
        with patch.object(self.kernel.pool,'submit'):
            message=self.kernel.send(self.sid,'先准备新的GPU方案',delivery_id='queued-new-scope')
        self.assertIsNone(rounds.fresh_scope_request(self.store,self.sid,review))
        self.kernel.settings.value['effort']=None
        self.store.patch_session(self.sid,thread_id='fixture-thread')
        with patch.object(self.kernel,'load_thread',side_effect=lambda sid:self.store.session(sid)), \
             patch.object(self.kernel.roles,'route',return_value={'role':'planner','model':'fixture'}), \
             patch.object(self.kernel.roles,'instructions',return_value='roles'), \
             patch.object(self.kernel.sdm,'input',return_value=[{'type':'text','text':'human scope request'}]), \
             patch('lab.budgets.exhausted',return_value=[True]), \
             patch('lab.budgets.cloud_assessment_ready',return_value=True), \
             patch.object(self.kernel,'_deliver') as deliver:
            self.kernel.drain(self.sid)
        text='\n'.join(x['text'] for x in deliver.call_args.args[2]['input'])
        self.assertIn('新范围讨论',text)
        self.assertIn('非执行授权',text)
        self.assertNotIn('不再交Executor',text)
        self.assertIsNone(rounds.fresh_scope_request(self.store,self.sid,review)) # Queueing still grants nothing.


