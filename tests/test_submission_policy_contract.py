"""No unsupported backend override, and old work packages must be re-prepared."""
import unittest
from types import SimpleNamespace
from lab import submission_policy as policy

class StorageContractTests(unittest.TestCase):
    def conf(self,value):return SimpleNamespace(get_store_type=lambda:value)
    def test_sdk_bos_is_not_treated_as_server_enum_or_effective_backend(self):
        value=policy.read(self.conf('bos'))
        self.assertEqual(value['schema'],2)
        self.assertNotIn('storage_type',value)
        self.assertFalse(value['override_sent'])
        self.assertFalse(value['effective_backend_verified'])
        self.assertEqual(value['server_selection'],'platform_default')
    def test_other_sdk_value_does_not_invent_another_server_mapping(self):
        value=policy.read(self.conf('minio'))
        self.assertEqual(value['configured_sdk_store_type'],'minio')
        self.assertNotIn('storage_type',value)
        with self.assertRaises(ValueError):policy.read(self.conf(None))
    def test_legacy_claim_and_configuration_drift_are_rejected_before_write(self):
        current=policy.read(self.conf('bos'))
        for frozen in ({'schema':1,'backend':'bos','storage_type':3},policy.read(self.conf('minio'))):
            with self.assertRaises(ValueError):policy.require(frozen,current)
        self.assertNotEqual(policy.execution_digest('component',{},current),
            policy.execution_digest('component',{}, {'schema':1,'backend':'bos','storage_type':3}))
