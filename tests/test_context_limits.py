import json
import unittest
from lab.context_limits import rejection,for_thread
from lab.store import dumps,now
from tests import test_core as core_fixtures

ERROR="Requested token count exceeds the model's maximum context length of 204800 tokens. You requested a total of 211786 tokens: 171786 tokens from the input messages and 40000 tokens for the completion."


class ContextLimitsTests(unittest.TestCase):
    setUp=core_fixtures.CoreTests.setUp
    tearDown=core_fixtures.CoreTests.tearDown

    def history(self):
        sid=self.store.create_session(self.project['id'],'ept')['id']
        self.store.patch_session(sid,thread_id='original-thread',status='failed')
        policy={'version':1,'models':{role:'azure-gpt-6-astra' for role in ['planner','generator','critic','executor']}}
        self.kernel.settings.value.update(channel='ept',agent_roles=policy)
        stamp=now()
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',('failed-delivery',sid,'original','queue','accepted','failed-turn',None,stamp,stamp))
        self.store.execute('INSERT INTO research_role_routes VALUES(?,?,?,?,?,?,?)',('failed-delivery',sid,'planner','azure-gpt-6-astra',dumps(policy),None,stamp))
        self.store.event(sid,'turn/completed',{'threadId':'original-thread','turn':{'id':'failed-turn','status':'failed','error':{'message':ERROR}}})
        return sid

    def test_rejection_uses_explicit_output_reservation_not_fixed_context_size(self):
        value=rejection(ERROR)
        self.assertEqual(value['auto_compact_tokens'],148320)
        self.assertLess(value['auto_compact_tokens']+value['output_reservation'],value['provider_window'])
        self.assertEqual(rejection(ERROR.replace('204800','409600').replace('171786','400000'))['auto_compact_tokens'],332640)
        for text in ['context full','quota exceeded',ERROR.replace('40000','204800'),ERROR.replace('171786','100000')]:
            self.assertIsNone(rejection(text))

    def test_same_native_thread_receives_only_compaction_override_on_resume(self):
        sid=self.history()
        settings_before=self.kernel.settings.path.read_bytes() if self.kernel.settings.path.exists() else None
        self.kernel.load_thread(sid)
        params=next(p for m,p in self.kernel.rpc.calls if m=='thread/resume')
        self.assertEqual(params['threadId'],'original-thread')
        self.assertEqual(params['model'],'azure-gpt-6-astra')
        self.assertEqual(params['config'],{'model_auto_compact_token_limit':148320})
        self.assertFalse(any(m in {'thread/start','turn/start','thread/compact/start'} for m,p in self.kernel.rpc.calls))
        self.assertEqual(self.kernel.settings.path.read_bytes() if self.kernel.settings.path.exists() else None,settings_before)
        loaded=json.loads(self.store.one("SELECT payload FROM events WHERE kind='thread.loaded' ORDER BY seq DESC LIMIT 1")['payload'])
        self.assertEqual(loaded['context_calibration']['model'],'azure-gpt-6-astra')

    def test_missing_exact_route_different_model_channel_or_thread_cannot_calibrate(self):
        sid=self.history();session=self.store.session(sid);settings=self.kernel.settings.value
        self.assertIsNotNone(for_thread(self.store,session,settings))
        self.assertIsNone(for_thread(self.store,{**session,'thread_id':'other'},settings))
        self.assertIsNone(for_thread(self.store,session,{**settings,'channel':'codex'}))
        self.assertIsNone(for_thread(self.store,session,{**settings,'agent_roles':{'version':1,'models':{'planner':'other','executor':'azure-gpt-6-astra'}}}))
        self.store.execute('DELETE FROM research_role_routes')
        self.assertIsNone(for_thread(self.store,session,settings))

    def test_loaded_active_thread_is_not_hot_reconfigured_or_restarted(self):
        sid=self.history();self.kernel.load_thread(sid);calls=len(self.kernel.rpc.calls)
        self.store.patch_session(sid,status='running',active_turn='active-original')
        self.kernel.load_thread(sid)
        self.assertEqual(len(self.kernel.rpc.calls),calls)
        self.assertEqual(self.store.session(sid)['active_turn'],'active-original')
