import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

from lab.store import Store
from lab.research import Research, plan_digest

ROOT = Path(__file__).resolve().parents[1]


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT/'.evidence/tests'/uuid.uuid4().hex
        self.store = Store(self.root)
        self.pid = self.store.create_project('lineage-fixture')['id']
        self.sid = self.store.create_session(self.pid, 'ept')['id']
        self.research = Research(self.store)

    def tearDown(self):
        self.research.close()
        self.store.close()

    def record(self, kind, **fields):
        return self.store.add_record(self.sid, {'kind':kind, 'title':kind, 'content':'isolated engineering evidence', **fields}, {'origin':'lineage-test'})

    def approve(self, plan):
        return self.research.review(self.sid, {'plan_id':plan['id'], 'expected_digest':plan_digest(plan), 'decision':'accept',
            'reason':'isolated scope, no scientific validity claim', 'execution':'local', 'allow_local':True,
            'python':sys.executable, 'max_runs':4, 'max_fits':0, 'timeout':3})

    def successor(self, plan, **fields):
        review=self.record('review', plan_id=plan['id'])
        return self.record('plan', previous_plan_id=plan['id'], review_record_id=review['id'], **fields)

    def test_incremental_major_and_incremental_numbering_with_real_review_gate(self):
        one=self.record('plan'); self.approve(one)
        minor=self.successor(one, revision_scope='incremental', revision_reason='retain route, address observed weakness')
        self.approve(minor)
        major=self.successor(minor, revision_scope='major', revision_reason='evidence contradicts the representation assumption')
        self.assertEqual([r['version'] for r in [one,minor,major]], ['1.0','1.0.0','2.0'])
        before=self.store.rows('SELECT * FROM research_reviews')
        self.assertEqual(len(before),2)  # Creating series 2 never approves it.
        self.approve(major)
        next_minor=self.successor(major, revision_scope='incremental')
        self.assertEqual(next_minor['version'],'2.0.0')
        graph=self.store.graph(self.pid)
        self.assertEqual([f['number'] for f in graph['plan_series']['families']],[1,2])
        memberships={e['target']:e['source'] for e in graph['edges'] if e['kind']=='contains_plan'}
        self.assertEqual(memberships[one['id']],memberships[minor['id']])
        self.assertEqual(memberships[major['id']],memberships[next_minor['id']])
        self.assertNotEqual(memberships[one['id']],memberships[major['id']])
        progression=next(e for e in graph['edges'] if e['kind']=='series_progression')
        self.assertEqual(progression['target'],'series:'+major['id'])
        self.assertEqual(self.store.one('SELECT kind FROM records WHERE id=?',(progression['source'],))['kind'],'review')
        self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(one['id'],)),one)

    def test_no_major_without_reason_review_or_valid_trigger_and_no_version_consumed(self):
        one=self.record('plan'); self.approve(one)
        review=self.record('review',plan_id=one['id'])
        count=len(self.store.rows('SELECT * FROM records'))
        with self.assertRaisesRegex(ValueError,'revision_reason') as missing:
            self.record('plan',previous_plan_id=one['id'],review_record_id=review['id'],revision_scope='major',
                        summary='This prose is not the required explicit revision reason.')
        self.assertNotIn('previous_plan_id',str(missing.exception))
        with self.assertRaises(ValueError):
            self.record('plan',revision_scope='major',revision_reason='unlinked claim')
        with self.assertRaises(ValueError):
            self.record('plan',previous_plan_id=one['id'],review_record_id=review['id'],revision_scope='major',revision_reason='changed route',trigger_record_ids=['missing'])
        self.assertEqual(len(self.store.rows('SELECT * FROM records')),count)
        major=self.record('plan',previous_plan_id=one['id'],review_record_id=review['id'],revision_scope='major',revision_reason='documented evidence')
        self.assertEqual(major['version'],'2.0')

    def test_route_change_requires_review_and_starts_new_patch_sequence(self):
        with self.assertRaisesRegex(ValueError, '切换路径'):
            self.record('plan', revision_scope='route')
        first = self.record('plan')
        self.approve(first)
        patch = self.successor(first)
        self.approve(patch)
        route = self.successor(patch, revision_scope='route',
                               revision_reason='Original direction exhausted; test a separate explanation')
        self.approve(route)
        revised = self.successor(route)
        self.assertEqual([r['version'] for r in (first, patch, route, revised)],
                         ['1.0', '1.0.0', '1.1', '1.1.0'])

    def test_sibling_experiments_and_evidence_do_not_become_hierarchy(self):
        plan=self.record('plan')
        first=self.record('trial',plan_id=plan['id'])
        second=self.record('trial',plan_id=plan['id'])
        evaluation=self.record('trial',plan_id=plan['id'],depends_on_trial_ids=[first['id'],second['id']])
        graph=self.store.graph(self.pid)
        for trial in [first,second,evaluation]:
            parents=[e['source'] for e in graph['edges'] if e['target']==trial['id'] and e['primary']]
            self.assertEqual(parents,[plan['id']])
        self.assertEqual(second['parent_id'],first['id'])  # Audit history is unchanged.
        refs=[e for e in graph['edges'] if e['kind']=='depends_on']
        self.assertEqual({e['source'] for e in refs},{first['id'],second['id']})
        self.assertTrue(all(not e['primary'] for e in refs))

    def test_actual_trial_run_scope_cannot_be_reassigned_to_another_plan(self):
        one=self.record('plan'); self.approve(one)
        trial=self.record('trial',plan_id=one['id'])
        two=self.successor(one); self.approve(two)
        (Path(self.store.session(self.sid)['workspace'])/'probe.py').write_text("open('out.txt','w').write('ok')")
        request={'plan_id':two['id'],'trial_id':trial['id'],'script':'probe.py','files':['probe.py'],'outputs':['out.txt'],
                 'purpose':'diagnostic','question':'scope guard','expected_information':'no run should start'}
        with self.assertRaisesRegex(ValueError,'已归属其他'):
            self.research.run(self.sid,request,'must-not-run')
        self.assertEqual(self.store.rows('SELECT * FROM research_runs'),[])

    def test_additive_legacy_migration_preserves_rows_digest_and_approval(self):
        one=self.record('plan'); self.approve(one)
        two=self.successor(one)
        before={table:self.store.rows('SELECT * FROM '+table) for table in ['records','research_reviews','research_grants']}
        old_digest=plan_digest(one)
        self.research.close()
        self.store.db.execute('DROP TABLE research_plan_lineage');self.store.db.commit();self.store.close()
        self.store=Store(self.root);self.research=Research(self.store)
        for table,rows in before.items():self.assertEqual(self.store.rows('SELECT * FROM '+table),rows)
        self.assertEqual(plan_digest(self.store.one('SELECT * FROM records WHERE id=?',(one['id'],))),old_digest)
        lineage=self.store.rows('SELECT * FROM research_plan_lineage ORDER BY minor_number')
        self.assertEqual([m['minor_number'] for m in lineage],[0,1])
        self.assertEqual(lineage[1]['previous_plan_id'],one['id'])
        self.assertEqual(json.loads(lineage[1]['trigger_record_ids']),[json.loads(two['source'])['review_record_id']])
        original=lineage
        self.research.close();self.store.close();self.store=Store(self.root);self.research=Research(self.store)
        self.assertEqual(self.store.rows('SELECT * FROM research_plan_lineage ORDER BY minor_number'),original)

    def test_cross_project_links_and_nonplan_revision_fields_rejected(self):
        other=self.store.create_project('other')['id'];sid=self.store.create_session(other,'ept')['id']
        foreign=self.store.add_record(sid,{'kind':'plan','title':'foreign','content':'foreign'},{})
        with self.assertRaises(ValueError):self.record('trial',plan_id=foreign['id'])
        with self.assertRaises(ValueError):self.record('decision',trigger_record_ids=[foreign['id']])
        with self.assertRaises(ValueError):self.record('trial',revision_scope='major')

    def fake_run(self, name, plan, trial, receipt, files=None):
        request=json.dumps({'purpose':'diagnostic','files':files or []})
        self.store.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (name,name,self.pid,self.sid,plan['id'],'fixture-grant','fixture-hash',request,'completed','fixture/'+name,json.dumps(receipt),'fixture-time','fixture-time'))
        self.store.execute('INSERT INTO research_run_context VALUES(?,?,?)',(name,trial['id'],'fixture-time'))

    def test_reuse_requires_frozen_hash_and_a_citation_is_not_positive_support(self):
        plan=self.record('plan');first=self.record('trial',plan_id=plan['id']);evaluation=self.record('trial',plan_id=plan['id'])
        self.fake_run('fixture-original',plan,first,{'outputs':[{'path':'frozen/pred.csv','sha256':'original'}]})
        self.fake_run('fixture-eval',plan,evaluation,{'inputs':{'frozen/pred.csv':{'sha256':'different'}}},['frozen/pred.csv'])
        result=self.record('result',plan_id=plan['id'],run_ids=['fixture-original'])
        graph=self.store.graph(self.pid)
        self.assertFalse(any(e['kind']=='uses' for e in graph['edges']))
        self.assertFalse(any(e['kind']=='supports' for e in graph['edges']))
        self.assertTrue(any(e['kind']=='informed_by' and e['target']==result['id'] for e in graph['edges']))
        self.store.execute('UPDATE research_runs SET receipt=? WHERE id=?',(json.dumps({'inputs':{'frozen/pred.csv':{'sha256':'original'}}}),'fixture-eval'))
        self.assertTrue(any(e['kind']=='uses' and e['source']==first['id'] and e['target']==evaluation['id'] for e in self.store.graph(self.pid)['edges']))

    def test_conflicting_legacy_run_ownership_is_not_resolved_by_latest_record(self):
        one=self.record('plan');two=self.record('plan');trial=self.record('trial',plan_id=one['id'])
        self.fake_run('fixture-conflict-a',one,trial,{})
        self.fake_run('fixture-conflict-b',two,trial,{})
        node=next(n for n in self.store.graph(self.pid)['nodes'] if n['id']==trial['id'])
        self.assertIsNone(node['plan_id'])
        self.assertEqual(node['membership_basis'],'conflicting_explicit_links')

    def test_comparison_belongs_to_current_plan_but_may_cite_previous_controls(self):
        one=self.record('plan');two=self.record('plan');trial=self.record('trial',plan_id=one['id'])
        self.fake_run('fixture-exact-result',one,trial,{})
        result=self.record('result',plan_id=two['id'],run_ids=['fixture-exact-result'])
        graph=self.store.graph(self.pid)
        node=next(n for n in graph['nodes'] if n['id']==result['id'])
        self.assertEqual(node['plan_id'],two['id'])
        self.assertTrue(any(e['source']==trial['id'] and e['target']==result['id'] and not e['primary'] for e in graph['edges']))

    def candidate(self, source, parent_id=None):
        return self.store.add_record(self.sid,
            {'kind':'plan','title':'same candidate title','content':'isolated unreleased proposal',
             'status':'proposed',**({'parent_id':parent_id} if parent_id else {})},
            {'origin':'lineage-test','release_candidate':True,**source})

    def test_seven_unreleased_candidates_follow_exact_previous_without_consuming_versions(self):
        first=self.record('plan');self.approve(first)
        review=self.record('review',plan_id=first['id'])
        unrelated=self.record('plan')
        trigger=self.record('research',plan_id=first['id'])
        formal=self.store.graph(self.pid)
        proposals=[]
        for index,scope in enumerate(['incremental','route','major',None,'incremental','route',None]):
            previous=proposals[0]['id'] if index==6 else first['id']
            proposals.append(self.candidate({'previous_plan_id':previous,'review_record_id':review['id'],
                'trigger_record_ids':[review['id'],trigger['id']],
                # The declared scientific/context owner cannot override the
                # exact previous-plan link, even when these IDs disagree.
                'plan_id':unrelated['id'],**({'revision_scope':scope} if scope else {})},
                parent_id=unrelated['id'] if index%2 else review['id']))
        tables=['records','research_plan_lineage','research_reviews','research_grants']
        before={table:self.store.rows('SELECT * FROM '+table) for table in tables}
        graph=self.store.graph(self.pid);nodes={node['id']:node for node in graph['nodes']}
        self.assertEqual(len([node for node in graph['nodes'] if node.get('plan_candidate')]),7)
        for index,proposal in enumerate(proposals):
            node=nodes[proposal['id']]
            previous=proposals[0]['id'] if index==6 else first['id']
            incoming=[edge for edge in graph['edges'] if edge['target']==proposal['id'] and edge['primary']]
            self.assertEqual([(edge['source'],edge['kind'],edge['basis']) for edge in incoming],
                             [(previous,'candidate_revision','explicit_candidate_previous_plan')])
            self.assertEqual(node['candidate_parent_id'],previous)
            self.assertEqual(node['previous_plan_id'],previous)
            self.assertEqual(node['candidate_lineage_basis'],'explicit_previous_plan')
            self.assertEqual(node['plan_id'],proposal['id'])
            self.assertIsNone(node['series_id']);self.assertIsNone(node['version_label'])
            self.assertEqual(node['status'],'proposed')
            self.assertNotIn('plan_lineage',node)
            evidence=[edge for edge in graph['edges'] if edge['target']==proposal['id'] and not edge['primary']]
            self.assertEqual({edge['source'] for edge in evidence},{review['id'],trigger['id']})
            self.assertTrue(all(edge['kind']=='motivates' for edge in evidence))
        self.assertEqual(graph['plan_series'],formal['plan_series'])
        self.assertEqual([edge for edge in graph['edges'] if edge['kind']=='contains_plan'],
                         [edge for edge in formal['edges'] if edge['kind']=='contains_plan'])
        for table,rows in before.items():self.assertEqual(self.store.rows('SELECT * FROM '+table),rows)
        successor=self.record('plan')
        self.assertEqual(successor['version'],'1.0.1','unreleased candidates consume no official version')

    def test_unresolved_candidate_previous_never_falls_back_to_parent_owner_or_latest(self):
        first=self.record('plan')
        nonplan=self.record('research',plan_id=first['id'])
        other=self.store.create_project('foreign lineage')['id'];sid=self.store.create_session(other,'ept')['id']
        foreign=self.store.add_record(sid,{'kind':'plan','title':'same candidate title','content':'foreign'},{})
        invalid=[None,'missing',nonplan['id'],foreign['id'],[],123]
        proposals=[]
        for previous in invalid:
            proposals.append(self.candidate({'previous_plan_id':previous,'plan_id':first['id']},parent_id=first['id']))
        self_link=self.candidate({'plan_id':first['id']},parent_id=first['id'])
        source=json.loads(self_link['source']);source['previous_plan_id']=self_link['id']
        self.store.execute('UPDATE records SET source=? WHERE id=?',(json.dumps(source),self_link['id']))
        proposals.append(self_link)
        graph=self.store.graph(self.pid);nodes={node['id']:node for node in graph['nodes']}
        for proposal in proposals:
            node=nodes[proposal['id']]
            self.assertTrue(node['plan_candidate'])
            self.assertIsNone(node['candidate_parent_id'])
            self.assertEqual(node['candidate_lineage_basis'],'unclassified_candidate')
            self.assertFalse(any(edge['target']==proposal['id'] and edge['primary'] for edge in graph['edges']))
        self.assertFalse(any(edge['source']==edge['target'] for edge in graph['edges']))
        self.assertFalse(any(edge['source']==foreign['id'] or edge['target']==foreign['id'] for edge in graph['edges']))

    def test_candidate_review_evidence_and_own_process_survive_without_publishing(self):
        first=self.record('plan');self.approve(first)
        review=self.record('review',plan_id=first['id'])
        proposal=self.candidate({'previous_plan_id':first['id'],'review_record_id':review['id']},parent_id=review['id'])
        critic=self.record('decision',parent_id=proposal['id'])
        exact_critic=self.record('decision',plan_id=proposal['id'],parent_id=first['id'])
        graph=self.store.graph(self.pid);nodes={node['id']:node for node in graph['nodes']}
        self.assertEqual(nodes[critic['id']]['plan_id'],proposal['id'])
        self.assertEqual(nodes[exact_critic['id']]['plan_id'],proposal['id'])
        self.assertEqual(nodes[exact_critic['id']]['membership_basis'],'exact_plan_or_run_link')
        self.assertTrue(any(edge['source']==proposal['id'] and edge['target']==critic['id'] and edge['primary'] for edge in graph['edges']))
        self.assertTrue(any(edge['source']==proposal['id'] and edge['target']==exact_critic['id'] and edge['primary'] for edge in graph['edges']))
        self.assertTrue(any(edge['source']==review['id'] and edge['target']==proposal['id']
                            and edge['kind']=='motivates' and not edge['primary'] for edge in graph['edges']))
        self.assertEqual(self.store.rows('SELECT plan_id FROM research_reviews'),[{'plan_id':first['id']}])
        self.assertIsNone(self.store.one('SELECT * FROM research_plan_lineage WHERE plan_id=?',(proposal['id'],)))

    def test_candidate_cycle_remains_unclassified_and_does_not_create_structural_loop(self):
        first=self.record('plan')
        one=self.candidate({'previous_plan_id':first['id']})
        two=self.candidate({'previous_plan_id':one['id']})
        source=json.loads(one['source']);source['previous_plan_id']=two['id']
        self.store.execute('UPDATE records SET source=? WHERE id=?',(json.dumps(source),one['id']))
        graph=self.store.graph(self.pid)
        for node in graph['nodes']:
            if node['id'] not in {one['id'],two['id']}:continue
            self.assertIsNone(node['candidate_parent_id'])
            self.assertEqual(node['candidate_lineage_basis'],'conflicting_candidate_cycle')
        self.assertFalse(any(edge['target'] in {one['id'],two['id']} and edge['primary'] for edge in graph['edges']))

    def test_candidate_links_preserve_exact_component_retry_mainline(self):
        plan=self.record('plan')
        first=self.record('trial',plan_id=plan['id'])
        second=self.record('trial',plan_id=plan['id'])
        proposal=self.candidate({'previous_plan_id':plan['id']})
        attempts=[{'id':'attempt-one','trial_id':first['id'],'plan_id':plan['id'],'session_id':self.sid,
                   'task_uid':'original-task','predecessor_uid':None},
                  {'id':'attempt-two','trial_id':second['id'],'plan_id':plan['id'],'session_id':self.sid,
                   'task_uid':'retry-task','predecessor_uid':'original-task'}]
        with patch('lab.lineage.cloud_attempts',return_value=attempts):
            graph=self.store.graph(self.pid)
        incoming=[edge for edge in graph['edges'] if edge['target']==second['id']]
        self.assertEqual([(edge['source'],edge['kind']) for edge in incoming if edge['primary']],
                         [(first['id'],'retries')])
        self.assertTrue(any(edge['source']==plan['id'] and edge['kind']=='executes' and not edge['primary'] for edge in incoming))
        self.assertTrue(any(edge['target']==proposal['id'] and edge['source']==plan['id']
                            and edge['kind']=='candidate_revision' and edge['primary'] for edge in graph['edges']))


if __name__=='__main__':unittest.main()
