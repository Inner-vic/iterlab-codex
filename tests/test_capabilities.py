import json
from pathlib import Path
import re
import unittest
import uuid
from unittest.mock import patch

from lab.capabilities import catalog
from lab.sdm import SDM
from lab.store import Store,now

ROOT=Path(__file__).resolve().parents[1]


class CapabilityTests(unittest.TestCase):
    def test_catalog_covers_original_responsibilities_and_matches_design(self):
        rows=catalog()['capabilities']
        self.assertEqual([r['id'] for r in rows],[f'C{i:02}' for i in range(1,27)])
        self.assertEqual(sorted(n for r in rows for n in r['legacy_ids']),list(range(1,35)))
        documented={}
        for line in (ROOT/'docs/capability-consolidation-20261028.md').read_text(encoding='utf-8').splitlines():
            if re.match(r'^\| C\d{2} \|',line):
                parts=line.split('|');documented[parts[1].strip()]=tuple(int(n) for n in parts[4].strip().split('、'))
        self.assertEqual(documented,{r['id']:r['legacy_ids'] for r in rows})


class BuiltinMethodTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(ROOT/'.evidence/tests'/uuid.uuid4().hex)
        self.project=self.store.create_project('built-in-method')
        self.sid=self.store.create_session(self.project['id'],'ept')['id']
        self.method=SDM(self.store)
    def tearDown(self):self.store.close()
    def delivery(self,id='delivery'):
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(id,self.sid,'原始问题','queue','accepted',None,None,now(),now()))
        return {'id':id,'text':'原始问题'}
    def test_builtin_operates_without_skill_discovery_and_preserves_user_text(self):
        class NoSkillsRPC:
            generation='no-skills'
            def request(self,*args):raise AssertionError('内置研究不应调用Skill发现')
        with patch('lab.sdm.SOURCE',ROOT/'missing-legacy-skill'):
            bound=self.method.prepare(self.sid,NoSkillsRPC())
            inputs=self.method.input(self.sid,self.delivery())
        self.assertEqual(bound['mode'],'builtin')
        self.assertEqual(inputs,[{'type':'text','text':'原始问题'}])
        self.assertEqual(bound['version'],'3.0')
        self.assertIn('内置研究合同 3.0',self.method.instructions(self.sid,'原生工具合同'))
        self.assertEqual(self.store.one('SELECT mode FROM method_invocation_contracts')['mode'],'builtin')
        self.assertEqual(self.store.rows('SELECT * FROM research_methods'),[])
    def test_missing_builtin_contract_or_modified_snapshot_does_not_silently_fallback(self):
        with patch('lab.sdm.BUILTIN_SOURCE',ROOT/'missing-contract'):
            with self.assertRaises(ValueError):self.method.binding(self.sid)
        bound=self.method.binding(self.sid)
        Path(bound['path']).write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):self.method.binding(self.sid)
    def test_legacy_upgrade_is_explicit_and_preserves_original_rows_and_invocations(self):
        legacy=SDM(self.store,default_mode='native_skill')
        before=legacy.binding(self.sid)
        delivery=self.delivery();legacy.input(self.sid,delivery)
        original=self.store.one('SELECT * FROM research_methods')
        invocation=self.store.one('SELECT * FROM method_invocations')
        self.assertEqual(self.method.binding(self.sid)['mode'],'native_skill')
        with self.assertRaises(ValueError):self.method.upgrade(self.sid,'stale','理由','授权')
        upgraded=self.method.upgrade(self.sid,before['digest'],'按用户要求将核心迁至内置合同','2026-10-28用户授权')
        self.assertTrue(upgraded['changed'])
        self.assertEqual(upgraded['binding']['mode'],'builtin')
        self.assertEqual(self.store.one('SELECT * FROM research_methods'),original)
        self.assertEqual(self.store.one('SELECT * FROM method_invocations'),invocation)
        self.assertEqual(Path(before['path']).read_bytes(),(ROOT/'skills/sdm-research/SKILL.md').read_bytes())
        self.assertEqual(len(self.store.rows('SELECT * FROM research_method_changes')),1)
        self.assertFalse(self.method.upgrade(self.sid,upgraded['binding']['digest'],'已是新版','用户授权')['changed'])
        with self.assertRaises(ValueError):self.method.input(self.sid,delivery)
        persisted=SDM(self.store).binding(self.sid)
        self.assertEqual(persisted['digest'],upgraded['binding']['digest'])
    def test_upgrade_checks_all_project_sessions_and_unresolved_deliveries(self):
        before=SDM(self.store,default_mode='native_skill').binding(self.sid)
        second=self.store.create_session(self.project['id'],'ept')['id']
        self.store.patch_session(second,status='running',active_turn='active')
        with self.assertRaises(ValueError):self.method.upgrade(self.sid,before['digest'],'理由','授权')
        self.store.patch_session(second,status='idle',active_turn=None)
        self.delivery();self.store.execute("UPDATE deliveries SET state='unknown'")
        with self.assertRaises(ValueError):self.method.upgrade(self.sid,before['digest'],'理由','授权')
        self.assertEqual(self.store.rows('SELECT * FROM research_method_changes'),[])
    def test_upgrade_reconciles_managed_roots_without_deleting_other_legacy_bindings(self):
        legacy=SDM(self.store,default_mode='native_skill')
        first=legacy.binding(self.sid)
        project=self.store.create_project('other-legacy');other=self.store.create_session(project['id'],'ept')['id']
        second=legacy.binding(other)
        class RPC:
            generation='root-reconciliation'
            calls=[]
            def request(rpc,name,params):
                rpc.calls.append((name,params))
                return {'data':[{'skills':[{'name':'sdm-research','path':first['path'],'enabled':True}]}]} if name=='skills/list' else {}
        rpc=RPC();self.method.prepare(self.sid,rpc)
        self.method.upgrade(self.sid,first['digest'],'内置合同迁移','用户明确授权')
        self.method.prepare(self.sid,rpc)
        self.assertEqual(len([c for c in rpc.calls if c[0]=='skills/extraRoots/set']),1,'另一旧模式项目仍需要共享方法根')
        self.method.upgrade(other,second['digest'],'第二项目迁移','用户明确授权')
        self.method.prepare(self.sid,rpc)
        self.assertEqual(rpc.calls[-1],('skills/extraRoots/set',{'extraRoots':[]}))
        self.assertEqual(len([c for c in rpc.calls if c[0]=='skills/list']),1)
        self.assertTrue(Path(first['path']).is_file(),'历史方法快照不能删除')


if __name__=='__main__':unittest.main()
