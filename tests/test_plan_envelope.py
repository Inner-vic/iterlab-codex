"""Pinned native schema compatibility, no native inference or computation."""
import copy,json,unittest
from pathlib import Path
from lab.kernel import RECORD_TOOL
from lab.plan_envelope import TITLE,SCHEMA,decode_plan_record,retry_template
from tests import test_roles as fixtures

def proposal():
    return {'kind':'plan','title':'GPU execution proposal','summary':'pending review','content':'Readable research reasoning.',
        'status':'proposed','evidence':['run-config.json'],
        'scope_proposal':{'objective':'explicit goal','evaluation':'fixed validation','data_boundary':'public TRAIN only',
            'allowed_changes':'evidence-based iteration','frozen_files':['run-config.json'],'component_config':'run-config.json',
            'compute':{'gpu_count':1,'workflow':'batch'},'budget':{'additional_runs':12,'single_attempt_seconds':1800}}}

def envelope(record):
    return {'kind':'plan','title':TITLE,'summary':'explicit legacy recording','content':json.dumps({'schema':SCHEMA,'record':record}),
        'status':'proposed','evidence':[]}

class EnvelopeTests(unittest.TestCase):
    def decode(self,value):return decode_plan_record(value,RECORD_TOOL['inputSchema'])
    def test_full_gpu_metadata_uses_same_current_record_schema(self):
        record=proposal();self.assertEqual(self.decode(envelope(record)),record)
    def test_ordinary_prose_and_lookalike_documents_are_not_control(self):
        record=proposal();record['content']=json.dumps({'schema':SCHEMA,'record':proposal()})
        self.assertIsNone(self.decode(record))
    def test_unknown_fields_invalid_types_and_other_actions_are_rejected(self):
        for mutate in [lambda r:r.update(kind='review'),lambda r:r.update(approval=True),
            lambda r:r['scope_proposal']['compute'].update(gpu_count=True),
            lambda r:r['scope_proposal']['compute'].update(gpu_count=129),
            lambda r:r['scope_proposal']['budget'].update(single_attempt_seconds=-1),
            lambda r:r['scope_proposal'].update(platform_action='submit'),lambda r:r.update(evidence='../file')]:
            value=proposal();mutate(value)
            with self.subTest(value=value),self.assertRaises(ValueError):self.decode(envelope(value))
    def test_duplicate_json_nonfinite_and_mixed_outer_fields_fail_closed(self):
        for text in ['{"schema":"wrong","schema":"'+SCHEMA+'","record":{}}',
            '{"schema":"'+SCHEMA+'","record":{"value":NaN}}','[]']:
            value=envelope(proposal());value['content']=text
            with self.assertRaises(ValueError):self.decode(value)
        value=envelope(proposal());value['plan_id']='cannot mix controls'
        with self.assertRaises(ValueError):self.decode(value)
    def test_future_schema_constraints_cannot_be_silently_skipped(self):
        schema=copy.deepcopy(RECORD_TOOL['inputSchema'])
        schema['properties']['scope_proposal']['properties']['component_config']['pattern']='^approved/'
        with self.assertRaisesRegex(ValueError,'更新兼容校验器'):
            decode_plan_record(envelope(proposal()),schema)

    def test_actual_paired_evaluation_schema_is_accepted_and_duplicates_refused(self):
        from tests.test_comparison import specification
        record=proposal();record['evaluation_contract']=specification(target=True)
        self.assertEqual(self.decode(envelope(record)),record)
        record['evaluation_contract']['pairs']=[42,42]
        with self.assertRaisesRegex(ValueError,'数组元素重复.*evaluation_contract.pairs'):
            self.decode(envelope(record))

    def test_contract_reference_error_explains_inheritance_without_accepting_it(self):
        record=proposal();record['evaluation_contract']={'path':'research-contracts/original.json','sha256':'a'*64,'version':1}
        with self.assertRaisesRegex(ValueError,'省略evaluation_contract'):
            self.decode(envelope(record))
        record.pop('evaluation_contract');record.pop('scope_proposal')
        self.assertEqual(self.decode(envelope(record)),record)

    def test_unique_items_preserves_json_types_and_numeric_equality(self):
        from lab.plan_envelope import validate
        schema={'type':'array','uniqueItems':True}
        validate([True,1,'1',None],schema)
        for duplicate in ([1,1.0],[{'a':1,'b':[True]},{'b':[True],'a':1.0}]):
            with self.assertRaisesRegex(ValueError,'数组元素重复'):validate(duplicate,schema)

    def test_mixed_outer_fields_name_the_required_correction_without_decoding(self):
        value=envelope(proposal());value.update(plan_id='old-plan',trigger_record_ids=['feedback'])
        with self.assertRaisesRegex(ValueError,'content.record.*plan_id,trigger_record_ids'):
            self.decode(value)

    def test_retry_hint_never_changes_inner_content_or_accepts_original_request(self):
        value=envelope(proposal());value.update(plan_id='conflicting-outer',previous_plan_id='outer')
        before=copy.deepcopy(value)
        hint=retry_template(value,RECORD_TOOL['inputSchema'])
        self.assertEqual(value,before)
        self.assertEqual(hint['retry_arguments']['content'],value['content'])
        self.assertEqual(set(hint['retry_arguments']),{'kind','title','summary','content','status','evidence'})
        self.assertEqual(self.decode(hint['retry_arguments']),proposal())
        with self.assertRaises(ValueError):self.decode(value)
        value['approve_resources']=True
        self.assertIsNone(retry_template(value,RECORD_TOOL['inputSchema']))
        value.pop('approve_resources');value['content']='{"schema":"wrong"}'
        self.assertIsNone(retry_template(value,RECORD_TOOL['inputSchema']))

    def test_outer_failure_also_reports_invalid_inner_without_recording_or_normalizing(self):
        record=proposal();record['stop_reason']=None
        value=envelope(record);value['plan_id']='conflicting-outer';before=copy.deepcopy(value)
        with self.assertRaisesRegex(ValueError,'外层.*内层同时需要修正.*record.stop_reason'):
            self.decode(value)
        self.assertEqual(value,before)
        self.assertIsNone(retry_template(value,RECORD_TOOL['inputSchema']))

class EnvelopeIntegrationTests(unittest.TestCase):
    setUp=fixtures.RoleTests.setUp
    tearDown=fixtures.RoleTests.tearDown
    record=fixtures.RoleTests.record
    activate=fixtures.RoleTests.activate
    handoff=fixtures.RoleTests.handoff
    def packet(self,record,call):
        return {'id':91,'method':'item/tool/call','params':{'threadId':'native-thread','turnId':self.turn,
            'callId':call,'tool':'research_record','arguments':envelope(record)}}
    def test_invalid_outer_request_needs_explicit_native_retry_and_grants_nothing(self):
        self.kernel.load_thread(self.sid);self.activate()
        workspace=Path(self.store.session(self.sid)['workspace'])
        (workspace/'run-config.json').write_text('{"isolated":true}')
        review=self.record('review',plan_id=self.plan['id'])
        record={**proposal(),'previous_plan_id':self.plan['id'],'review_record_id':review['id'],'revision_scope':'incremental'}
        packet=self.packet(record,'format-error');packet['params']['arguments']['plan_id']='outer-conflict'
        records=self.store.rows('SELECT * FROM records');grants=self.store.rows('SELECT * FROM research_grants')
        self.kernel.on_request(packet,self.kernel.rpc)
        response=self.kernel.rpc.replies[-1][1]
        self.assertFalse(response['success'])
        hint=json.loads(response['contentItems'][1]['text'])
        self.assertEqual(self.store.rows('SELECT * FROM records'),records)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        retry=self.packet(proposal(),'explicit-native-retry')
        retry['params']['arguments']=hint['retry_arguments']
        self.kernel.on_request(retry,self.kernel.rpc)
        response=self.kernel.rpc.replies[-1][1]
        self.assertTrue(response['success'],response)
        self.assertEqual(len(self.store.rows('SELECT * FROM records')),len(records)+1)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),grants)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))
    def test_old_schema_saves_readable_immutable_candidate_without_any_grant(self):
        self.kernel.load_thread(self.sid);self.activate()
        review=self.record('review',plan_id=self.plan['id'])
        workspace=Path(self.store.session(self.sid)['workspace']);(workspace/'run-config.json').write_text('{}')
        record={**proposal(),'previous_plan_id':self.plan['id'],'review_record_id':review['id'],'revision_scope':'incremental'}
        before=self.store.rows('SELECT * FROM research_grants');original=self.store.rows('SELECT * FROM records')
        packet=self.packet(record,'legacy-plan')
        self.kernel.on_request(packet,self.kernel.rpc)
        self.assertTrue(self.kernel.rpc.replies[-1][1]['success'],self.kernel.rpc.replies[-1])
        receipt=json.loads(self.kernel.rpc.replies[-1][1]['contentItems'][0]['text'])
        self.assertEqual(receipt['scope_proposal_saved']['compute'],record['scope_proposal']['compute'])
        self.assertEqual(receipt['scope_proposal_saved']['component_config'],'run-config.json')
        saved=self.store.one('SELECT * FROM records ORDER BY rowid DESC LIMIT 1')
        self.assertEqual(saved['title'],record['title']);self.assertEqual(saved['content'],record['content'])
        source=json.loads(saved['source'])
        self.assertEqual(source['scope_proposal'],record['scope_proposal'])
        snapshot=self.r.snapshot(self.sid,saved['id'])
        self.assertEqual(snapshot['scope_proposal'],record['scope_proposal'])
        self.assertNotIn('source',snapshot)
        self.assertIn('不代表本方案已获执行授权',snapshot['authorization_context']['meaning'])
        self.assertEqual(source['record_transport'],SCHEMA);self.assertIsNone(saved['version'])
        self.kernel.on_request(packet,self.kernel.rpc)
        self.assertEqual(len(self.store.rows('SELECT * FROM records')),len(original)+1)
        for old in original:self.assertEqual(self.store.one('SELECT * FROM records WHERE id=?',(old['id'],)),old)
        self.assertEqual(self.store.rows('SELECT * FROM research_grants'),before)
        self.assertFalse(self.store.rows('SELECT * FROM research_runs'))
    def test_executor_cannot_disguise_plan_creation_as_legacy_record(self):
        self.kernel.load_thread(self.sid);self.activate();self.activate(self.handoff('executor',stage='prepare'))
        before=self.store.rows('SELECT * FROM records')
        self.kernel.on_request(self.packet(proposal(),'forbidden-plan'),self.kernel.rpc)
        self.assertFalse(self.kernel.rpc.replies[-1][1]['success'])
        self.assertEqual(self.store.rows('SELECT * FROM records'),before)
