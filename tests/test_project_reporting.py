"""Reporting context preserves exact project facts, never infers scientific causes."""
import unittest
from unittest.mock import patch
from tests import test_autonomy as fixtures
from tests.test_core import FakeRPC
from lab import assistant_behavior


class ProjectReportingTests(unittest.TestCase):
    setUp=fixtures.AutonomyTests.setUp
    tearDown=fixtures.AutonomyTests.tearDown
    record=fixtures.AutonomyTests.record

    def test_context_comes_from_exact_authority_and_plan_in_both_views(self):
        before=self.store.rows('SELECT * FROM records')
        for view in ('brief','full'):
            value=self.r.status(self.sid,view)['project_context']
            self.assertEqual(value['project_title'],'autonomy-isolated')
            self.assertEqual(value['confirmed_goal'],self.scope['objective'])
            self.assertEqual(value['evaluation'],self.scope['evaluation'])
            self.assertEqual(value['data_boundary'],self.scope['data_boundary'])
            self.assertEqual(value['current_plan']['id'],self.first['id'])
            self.assertEqual(value['original_plan']['id'],self.first['id'])
            self.assertIsNone(value['last_round_review'])
            self.assertNotIn('metrics',value);self.assertNotIn('cause',value)
        self.assertEqual(self.store.rows('SELECT * FROM records'),before)

    def test_unconfirmed_goal_is_unknown_not_guessed_from_a_project_name(self):
        pid=self.store.create_project('四类故障准确率90%，这个名称不是授权')['id']
        sid=self.store.create_session(pid,'ept')['id']
        context=self.r.status(sid,'brief')['project_context']
        self.assertIsNone(context['confirmed_goal']);self.assertIsNone(context['evaluation'])
        self.assertIsNone(context['current_plan'])

    def test_all_roles_remind_reporter_of_project_meaning_without_changing_permissions(self):
        self.kernel.settings.value['agent_roles']={'version':1,'models':dict.fromkeys(['planner','generator','critic','executor'],'fixture')}
        for role in ('planner','generator','critic','executor'):
            with self.subTest(role=role):
                with patch.object(self.kernel.pool,'submit'):
                    self.kernel.send(self.sid,'isolated reporting instruction',delivery_id='report-'+role)
                text=self.kernel.roles.instructions({'session_id':self.sid,'role':role,'model':'fixture','handoff_id':None,'delivery_id':'report-'+role})
                self.assertIn('已知未变的项目背景、分类标签和数据划分不重复',text)
                self.assertIn('没有模型结果不编造效果或原因',text)
                self.assertIn('自主探索时主动解读',text)
                self.assertIn('真实可比的指标及变化幅度',text)
                self.assertIn('不夸大显著性',text)
                self.assertIn('角色交接不会扩展研究授权',text)
                if role=='planner':self.assertIn('只读解读或进展汇报不新建方案',text)

    def test_background_communication_uses_existing_delivery_without_new_turn_or_authority(self):
        before=self.store.rows('SELECT * FROM research_authorities')
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.send(self.sid,'isolated existing result event',delivery_id='automatic-report',origin='background')
        self.kernel.drain(self.sid)
        request=next(p for m,p in self.kernel.rpc.calls if m=='turn/start')
        self.assertEqual(request['clientUserMessageId'],'automatic-report')
        reminder=next(p['text'] for p in request['input'] if p.get('text')==assistant_behavior.automatic_update_instructions())
        self.assertIn('重要失败与证据缺口仍须说明',reminder)
        self.assertIn('本提醒不改变研究步骤、角色权限或执行边界',reminder)
        # Same event is still the same durable request, never a report-only turn.
        self.kernel.send(self.sid,'isolated existing result event',delivery_id='automatic-report',origin='background')
        self.kernel.drain(self.sid)
        self.assertEqual(sum(m=='turn/start' for m,_ in self.kernel.rpc.calls),1)
        self.assertEqual(self.store.rows('SELECT * FROM research_authorities'),before)
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])
        self.assertEqual(self.store.rows('SELECT * FROM company_training'),[])

    def test_human_input_does_not_become_an_automatic_event(self):
        with patch.object(self.kernel.pool,'submit'):
            self.kernel.send(self.sid,'Why did the score decrease?',delivery_id='human-report')
        self.kernel.drain(self.sid)
        request=next(p for m,p in self.kernel.rpc.calls if m=='turn/start')
        self.assertFalse(any(p.get('text')==assistant_behavior.automatic_update_instructions() for p in request['input']))
        self.assertEqual(self.store.one('SELECT origin FROM research_input_origins WHERE delivery_id=?',('human-report',))['origin'],'user')
