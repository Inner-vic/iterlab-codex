import json
from pathlib import Path
import unittest
import uuid

from lab.store import Store, now
from lab.research_story import SCHEMA, save, load, detail


class ResearchStoryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.evidence/tests' / uuid.uuid4().hex
        self.store = Store(self.root)
        self.pid = self.store.create_project('unrelated research problem')['id']
        self.sid = self.store.create_session(self.pid, 'ept')['id']
        self.delivery = 'input-' + uuid.uuid4().hex
        self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',
                           (self.delivery, self.sid, 'Please check existing evidence.', 'new', 'accepted', 'turn-a', None, now(), now()))
        self.store.execute('CREATE TABLE research_input_origins(delivery_id TEXT PRIMARY KEY,origin TEXT)')
        self.store.execute('INSERT INTO research_input_origins VALUES(?,?)', (self.delivery,'user'))
        self.first = self.record('First observation; its cause is uncertain.')

    def tearDown(self):
        self.store.close()

    def record(self, content, **fields):
        return self.store.add_record(self.sid, {'kind':'result', 'title':'Actual analysis', 'content':content,
                                                'status':'completed', **fields},
                                     {'origin':'test', 'turn_id':'turn-a'})

    def payload(self):
        rid = self.first['id']
        return {'schema':SCHEMA, 'expected_revision':0, 'scope':'First local investigation only', 'reason':'Explain a real revision',
                'nodes':[{'id':rid, 'kind':'investigation', 'actor':'agent', 'title':'Observation',
                          'summary':'Cause unknown', 'content':'Actual evidence, with uncertainty.',
                          'trigger_delivery_id':self.delivery, 'trigger_label':'User requested a check',
                          'sources':[{'kind':'record','id':rid,'quote':'its cause is uncertain'},
                                     {'kind':'delivery','id':self.delivery,'quote':'check existing evidence'}]},
                         {'id':f'story:{self.pid}:question', 'kind':'question', 'actor':'evaluator',
                          'title':'Unresolved cause', 'summary':'Still untested', 'content':'A question, not a training job.',
                          'sources':[{'kind':'record','id':rid,'quote':'its cause is uncertain'}]}],
                'edges':[{'source':rid,'target':f'story:{self.pid}:question','kind':'raises_question'}]}

    def test_additive_projection_keeps_raw_records_and_unverified_questions(self):
        before = self.store.rows('SELECT * FROM records')
        saved = save(self.store, self.pid, self.payload())
        graph = self.store.graph(self.pid)
        self.assertEqual(before, self.store.rows('SELECT * FROM records'))
        self.assertEqual(1, saved['revision'])
        self.assertEqual(2, len(graph['nodes']))
        self.assertEqual('result', graph['nodes'][0]['original_kind'])
        self.assertEqual('recorded', graph['nodes'][0]['status'])
        self.assertEqual('not_verified', graph['nodes'][1]['status'])
        self.assertTrue(graph['nodes'][1]['projection_only'])
        self.assertIsNone(graph['nodes'][1]['created_at'])
        self.assertFalse(any(n['kind'] in {'plan','trial','run'} for n in graph['nodes']))
        self.assertIn('sha256', detail(self.store, self.first['id'])['sources'][0])

    def test_rejects_cross_project_sources_fabricated_quotes_and_wrong_trigger_turn(self):
        foreign = self.store.create_project('different project')['id']
        sid = self.store.create_session(foreign, 'ept')['id']
        other = self.store.add_record(sid, {'kind':'result','title':'other','content':'its cause is uncertain'}, {})
        for mutation in ('foreign', 'quote', 'turn'):
            payload = self.payload()
            if mutation == 'foreign':payload['nodes'][0]['sources'][0]['id'] = other['id']
            if mutation == 'quote':payload['nodes'][0]['sources'][0]['quote'] = 'Invented answer'
            if mutation == 'turn':self.store.execute('UPDATE deliveries SET turn_id=? WHERE id=?', ('wrong-turn', self.delivery))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                save(self.store, self.pid, payload)
        self.assertIsNone(load(self.store, self.pid))

    def test_append_only_revisions_and_stale_write_rejected(self):
        p = self.payload()
        save(self.store, self.pid, p)
        with self.assertRaises(ValueError):save(self.store, self.pid, p)
        p['expected_revision'] = 1
        p['nodes'][0]['summary'] = 'More precise reading'
        save(self.store, self.pid, p)
        self.assertEqual(2, load(self.store, self.pid)['revision'])
        rows = self.store.rows("SELECT payload FROM events WHERE kind='research.story.saved' ORDER BY seq")
        self.assertEqual('Cause unknown', json.loads(rows[0]['payload'])['nodes'][0]['summary'])
        self.assertEqual(2, len(rows))

    def test_native_revision_links_are_explicit_and_input_attribution_is_derived(self):
        correction = self.record('Revised interpretation, further evidence needed.',
                                 research_context={'role':'correction','revises_record_ids':[self.first['id']],
                                                   'continues_record_id':self.first['id']})
        source = json.loads(correction['source'])['research_context']
        self.assertEqual(self.delivery, source['trigger_delivery_id'])
        self.assertEqual('user', source['trigger_origin'])
        graph = self.store.graph(self.pid)
        link = next(e for e in graph['edges'] if e['kind'] == 'claim_revision')
        self.assertFalse(link['primary'])
        self.assertTrue(link['story_visible'])
        self.assertEqual(self.first['id'], link['source'])
        root = next(n for n in graph['nodes'] if n['kind'] == 'project')
        self.assertTrue(root['projection_only'])
        self.assertEqual('Please check existing evidence.', detail(self.store, root['id'])['content'])
        self.assertTrue(any(e['source']==self.first['id'] and e['target']==correction['id'] and e['kind']=='research_step' for e in graph['edges']))
        self.assertEqual('recorded', next(n for n in graph['nodes'] if n['id']==correction['id'])['status'])
        with self.assertRaises(ValueError):self.record('Missing target', research_context={'role':'correction'})
        self.assertEqual('result', self.store.one('SELECT kind FROM records WHERE id=?',(correction['id'],))['kind'])

    def test_existing_input_provenance_distinguishes_automatic_continuation(self):
        self.store.execute("UPDATE research_input_origins SET origin='background' WHERE delivery_id=?", (self.delivery,))
        record = self.record('Automatically resumed analysis', research_context={'role':'investigation'})
        self.assertEqual('background',json.loads(record['source'])['research_context']['trigger_origin'])
        self.assertFalse(any(n['kind']=='project' for n in self.store.graph(self.pid)['nodes']))
        with self.assertRaises(ValueError):save(self.store, self.pid, self.payload())

    def test_no_semantics_inferred_from_chronological_parent(self):
        second = self.record('Independent analysis')
        graph = self.store.graph(self.pid)
        self.assertFalse(any(n.get('story_context') for n in graph['nodes']))
        self.assertEqual(['recorded_after'], [e['kind'] for e in graph['edges']])
        self.assertFalse(graph['edges'][0]['primary'])
        p = self.payload()
        p['edges'] = [{'source':self.first['id'],'target':p['nodes'][1]['id'],'kind':'research_step'},
                      {'source':p['nodes'][1]['id'],'target':self.first['id'],'kind':'research_step'}]
        with self.assertRaises(ValueError):save(self.store, self.pid, p)

    def test_story_api_serves_synthetic_details_without_starting_a_kernel(self):
        from lab.web import Application
        app = Application.__new__(Application)
        app.store = self.store
        app.kernel = None
        saved = app.route('POST', f'/api/projects/{self.pid}/research-story', {}, self.payload())
        self.assertEqual(1, saved['revision'])
        loaded = app.route('GET', f'/api/projects/{self.pid}/research-story', {}, {})
        self.assertEqual(1, loaded['story']['revision'])
        result = app.route('GET', f'/api/records/story:{self.pid}:question', {}, {})
        self.assertEqual('question', result['research_story']['kind'])
        self.assertEqual('not_verified', result['research_story']['status'])


if __name__ == '__main__':
    unittest.main()
