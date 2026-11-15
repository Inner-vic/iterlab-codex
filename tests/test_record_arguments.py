import copy,unittest
from lab.kernel import RECORD_TOOL
from lab.record_arguments import normalize
from lab.plan_envelope import decode_plan_record
from tests.test_plan_envelope import proposal,envelope

class RecordArgumentsTests(unittest.TestCase):
    def test_only_optional_top_level_null_placeholders_are_removed(self):
        value={'kind':'review','title':'Review','summary':'unchanged','content':'null',
            'status':'completed','evidence':[],'stop_reason':'null','round_outcome':None}
        before=copy.deepcopy(value);result,ignored=normalize(value,RECORD_TOOL['inputSchema'])
        self.assertEqual(set(ignored),{'stop_reason','round_outcome'})
        self.assertEqual(result['content'],'null');self.assertEqual(value,before)
        self.assertNotIn('stop_reason',result)

    def test_real_stop_and_unknown_fields_are_not_erased(self):
        value={'stop_reason':'user_stop','round_outcome':'stop_requested','unknown':'null'}
        self.assertEqual(normalize(value,RECORD_TOOL['inputSchema']),(value,[]))

    def test_pinned_outer_defaults_decode_without_changing_inner_contract(self):
        record=proposal();value=envelope(record)
        value.update(stop_reason='null',round_outcome=None,run_ids=[],cloud_run_ids=[])
        clean,_=normalize(value,RECORD_TOOL['inputSchema'])
        self.assertEqual(decode_plan_record(clean,RECORD_TOOL['inputSchema']),record)
        record['scope_proposal']['compute']['gpu_count']='null';value=envelope(record)
        with self.assertRaises(ValueError):decode_plan_record(value,RECORD_TOOL['inputSchema'])
