"""Pure identity/date checks: no store, SDK, transport, credentials or fixtures."""
import unittest

from lab.lpai_identity import (MARKER_PREFIX, platform_identity, reconciliation_evidence,
                               task_observation, timestamp)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.row={'component_uid':'component-1'}
        self.marker=MARKER_PREFIX+'original-request'
        self.echo='fixed bounded duration\n['+self.marker+']'
        self.frozen={'task_name':'task-name','component':{'namespace':'fixture-namespace'},
                     'task_information':{'expected_duration_desc':self.echo}}
        self.identity={'namespace':'fixture-namespace','requested_at':'2026-10-30T03:00:00+08:00',
                       'submission_marker':self.marker,'platform_identity':None}
        self.item={'uid':'task-uid','namespace':'fixture-namespace','project_uid':'component-1',
                   'name':'task-name','created_at':'2026-10-29T19:00:00Z','expected_duration_desc':self.echo}

    def check(self,item=None,identity=None,known=False):
        return reconciliation_evidence(self.row,self.frozen,self.identity if identity is None else identity,
                                       self.item if item is None else item,'task-uid',known)

    def test_offset_instants_equal_and_naive_invalid(self):
        self.assertEqual(timestamp('2026-10-30T03:00:00+08:00'),timestamp({'time':'2026-10-29T19:00:00Z'}))
        self.assertEqual(timestamp('2026-10-29T14:00:00-05:00'),timestamp('2026-10-29T19:00:00Z'))
        for value in (None,0,True,[],{},'2026-10-30','2026-10-30T03:00:00',
                      '0001-01-01T00:00:00Z','2026-13-30T03:00:00Z',{'seconds':123}):
            with self.subTest(value=value):self.assertIsNone(timestamp(value))

    def test_timestamp_before_original_request_rejected_across_offsets(self):
        with self.assertRaisesRegex(ValueError,'实际原请求'):
            self.check(item={**self.item,'created_at':'2026-10-29T13:59:59-05:00'})

    def test_echo_required_for_unknown_uid_even_with_exact_name_and_time(self):
        self.assertEqual(self.check(),(None,'original_request_echo'))
        for name in ('task-name','task-name-colliding-postfix'):
            with self.subTest(name=name):
                missing,origin=self.check(item={**self.item,'name':name,'expected_duration_desc':None})
                self.assertEqual(missing,'original_submission_marker_not_echoed');self.assertIsNone(origin)
        for echo in ('['+self.marker+']','unrelated '+self.echo,self.echo+' extra','[iterlab-submission:another]'):
            missing,origin=self.check(item={**self.item,'expected_duration_desc':echo})
            self.assertEqual(missing,'original_submission_marker_not_echoed');self.assertIsNone(origin)

    def test_locked_uid_is_origin_evidence_but_does_not_replace_missing_time(self):
        self.assertEqual(self.check(item={**self.item,'expected_duration_desc':None},known=True),
                         (None,'original_uid_receipt'))
        missing,origin=self.check(identity={**self.identity,'requested_at':None},known=True)
        self.assertEqual(missing,'missing_original_requested_at');self.assertIsNone(origin)
        missing,origin=self.check(identity={**self.identity,'requested_at':'2026-10-30T03:00:00'},known=True)
        self.assertEqual(missing,'unconfirmed_original_requested_at');self.assertIsNone(origin)

    def test_known_identity_still_checks_explicit_scope_component_name_and_date(self):
        for field,value in (('namespace','wrong-namespace'),('project_uid','wrong-component'),
                            ('name','wrong-name'),('uid','wrong-uid')):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):self.check(item={**self.item,field:value},known=True)
        for field in ('namespace','project_uid','uid'):
            missing,origin=self.check(item={**self.item,field:None},known=True)
            self.assertEqual(missing,'missing_'+field);self.assertIsNone(origin)
        missing,origin=self.check(item={**self.item,'created_at':None},known=True)
        self.assertEqual(missing,'unconfirmed_creation_time');self.assertIsNone(origin)

    def test_public_platform_identity_must_match_when_original_is_available(self):
        original={'platform':'lpai','account_uid':'fixture-account','tenant_uid':'fixture-tenant'}
        identity={**self.identity,'platform_identity':original}
        self.assertEqual(self.check(identity=identity,item={**self.item,'platform_identity':original}),
                         (None,'original_request_echo'))
        missing,origin=self.check(identity=identity)
        self.assertEqual(missing,'missing_platform_identity');self.assertIsNone(origin)
        with self.assertRaisesRegex(ValueError,'账号身份'):
            self.check(identity=identity,item={**self.item,'platform_identity':{**original,'account_uid':'other-account'}})
        missing,origin=self.check(item={**self.item,'platform_identity':original})
        self.assertEqual(missing,'unconfirmed_original_platform_identity');self.assertIsNone(origin)
        self.assertEqual(self.check(item={**self.item,'platform_identity':original},known=True),
                         (None,'original_uid_receipt'))

    def test_platform_identity_allowlist_rejects_unknown_or_credential_shapes(self):
        self.assertIsNone(platform_identity(None))
        self.assertEqual(platform_identity({'platform':'lpai','user_uid':'public-user'}),
                         {'platform':'lpai','user_uid':'public-user'})
        for value in ('account',{}, {'account_uid':0},{'account_uid':''},{'token':'synthetic-not-a-credential'},
                      {'account_uid':'a','unexpected_field':'b'}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):platform_identity(value)

    def test_observation_uses_explicit_namespace_and_metadata_time_without_request_fallback(self):
        raw={'metadata':{'uid':'task-uid','name':'task-name','creationTimestamp':'2026-10-29T19:00:00Z'},
             'project_uid':'component-1','status':{'phase':'Succeeded'},
             'task_information':{'expected_duration_desc':self.echo}}
        observed=task_observation(raw)
        self.assertIsNone(observed['namespace'])
        self.assertEqual(observed['uid'],'task-uid');self.assertEqual(observed['phase'],'Succeeded')
        self.assertEqual(observed['created_at'],'2026-10-29T19:00:00Z')
        self.assertEqual(observed['expected_duration_desc'],self.echo)
        observed=task_observation({**raw,'metadata':{**raw['metadata'],'namespace':'fixture-namespace'}})
        self.assertEqual(observed['namespace'],'fixture-namespace')
        self.assertEqual(self.check(item=observed),(None,'original_request_echo'))

    def test_observation_rejects_conflicting_metadata_time_and_echo(self):
        for field in ('uid','name','namespace'):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError,'冲突'):
                    task_observation({field:'top','metadata':{field:'different'}})
        equal={'create_timestamp':{'time':'2026-10-30T03:00:00+08:00'},'created_at':'2026-10-29T19:00:00Z'}
        self.assertEqual(task_observation(equal)['created_at'],equal['create_timestamp'])
        for time in ('2026-10-29T19:00:01Z','2026-10-29T19:00:00',{},0):
            with self.subTest(time=time):
                with self.assertRaisesRegex(ValueError,'创建时间'):
                    task_observation({**equal,'created_at':time})
        with self.assertRaisesRegex(ValueError,'回显'):
            task_observation({'expected_duration_desc':self.echo,'task_information':{'expected_duration_desc':'different'}})

    def test_unsupported_observed_platform_identity_cannot_be_ignored(self):
        observed=task_observation({'uid':'task-uid','name':'task-name','namespace':'fixture-namespace',
            'project_uid':'component-1','create_timestamp':{'time':'2026-10-29T19:00:00Z'},
            'expected_duration_desc':self.echo,'platform_identity':{'unsupported':'identity'}})
        self.assertTrue(observed['platform_identity_unconfirmed'])
        missing,origin=self.check(item=observed)
        self.assertEqual(missing,'unsupported_platform_identity');self.assertIsNone(origin)


if __name__=='__main__':unittest.main()
