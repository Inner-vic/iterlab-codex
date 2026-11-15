import json,unittest
from lab.roles import HANDOFF_RECORD_TITLE,decode_handoff_record,handoff_retry_template


class PinnedTransportTests(unittest.TestCase):
    def test_failed_explicit_handoff_receives_exact_transport_retry_not_authority(self):
        body=json.dumps({'schema':'iterlab-role-handoff/v1','target':'executor','plan_id':'original-plan',
            'record_id':'original-trial','verdict':'handoff','reason':'continue original approved trial'})
        original={'kind':'decision','title':HANDOFF_RECORD_TITLE,'summary':'existing handoff',
                  'content':body,'status':'recorded','evidence':[],'trigger_record_ids':['original-trial']}
        with self.assertRaises(ValueError):decode_handoff_record(original)
        result=handoff_retry_template(original)
        retry=result['transport_retry']
        self.assertEqual(retry['content'],body);self.assertEqual(retry['status'],'proposed')
        self.assertNotIn('trigger_record_ids',retry)
        self.assertEqual(decode_handoff_record(retry)['record_id'],'original-trial')

    def test_ordinary_text_or_invalid_inner_contract_never_produces_control_retry(self):
        self.assertIsNone(handoff_retry_template({'title':'ordinary','content':'{}','summary':'note'}))
        self.assertIsNone(handoff_retry_template({'title':HANDOFF_RECORD_TITLE,'content':'{"schema":"fake"}','summary':'note'}))

    def test_transport_guidance_explains_registered_file_abi_without_changing_authority(self):
        from lab.host_preparation import native_tool_instructions
        text=native_tool_instructions()
        self.assertIn('iterlab-experiment/v1',text)
        self.assertIn('patch_file',text);self.assertIn('不要提供output_contract',text)
        self.assertIn('不增加预算',text)
