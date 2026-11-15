import json
import unittest
from unittest.mock import patch
from tests import test_company as fixtures
from lab.component_resources import packages


class ComponentResourceTests(unittest.TestCase):
    setUp=fixtures.CompanyTests.setUp
    tearDown=fixtures.CompanyTests.tearDown

    def directory(self,subject='account-a',count=2):
        config=self.company.config()
        return {'queues':[{'key':'q'+str(index),'queue_code':'queue-'+str(index),'name':subject+' resource '+str(index),
                           'resources':[{'label':'GPU','available':index,'unit':'card'}]} for index in range(count)],
                'source':'official_account_resources','live':True,'digest':'a'*64,
                'account_scope':{'subject':subject,'namespace':config['namespace'],'connection_revision':config['revision']}}

    def test_official_package_projection_does_not_invent_free_gpus(self):
        result=packages([{'Id':8,'Cpu':8,'Mem':64,'Gpu':1,'GpuType':'A800','LimitSum':800,'Token':'secret'}])
        self.assertEqual(result[0]['gpu_count'],1)
        self.assertNotIn('LimitSum',result[0]);self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('free_gpus',result[0])
        shared=packages([{'Id':55,'Cpu':0,'Mem':0,'Gpu':-1,'GpuType':'L20X-SHARE'}])[0]
        self.assertFalse(shared['selectable']);self.assertIsNone(shared['gpu_count'])

    def test_selection_is_account_scoped_and_never_allocation_or_submit(self):
        sid=self.session['id'];before=self.store.rows('SELECT * FROM company_operations')
        directory=self.company.resource_directory
        with patch.object(directory,'catalog',return_value=self.directory()),patch.object(self.company.training,'sdk') as sdk:
            selected=directory.select_queue(sid,'q0',self.directory()['account_scope'])
            self.assertEqual(selected['account_scope']['subject'],'account-a');sdk.assert_not_called()
            self.assertEqual(directory.preference(sid)['platform_queue_code'],'queue-0')
            other=self.store.create_session(self.project['id'],'ept')
            self.assertEqual(directory.preference(other['id'])['queue_key'],'q0')
            directory.require_component(sid,{'namespace':'vc-chassis','queue_code':'queue-0'})
            with self.assertRaisesRegex(ValueError,'不符'):directory.require_component(sid,{'namespace':'vc-chassis','queue_code':'other'})
        # Same local key and same namespace in another account cannot revive A's selection.
        with patch.object(directory,'catalog',return_value=self.directory('account-b',5)):
            self.assertIsNone(directory.preference(sid))
            with self.assertRaisesRegex(ValueError,'账号或连接已变化'):directory.select_queue(sid,'q0',self.directory('account-a')['account_scope'])
        with patch.object(directory,'catalog',return_value={**self.directory(),'live':False}):
            self.assertIsNone(directory.preference(sid))
            with self.assertRaisesRegex(ValueError,'尚未读回'):directory.select_queue(sid,'q0')
        project=self.store.create_project('other');session=self.store.create_session(project['id'],'ept')
        self.assertIsNone(directory.preference(session['id']))
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),before)

    def test_account_queue_view_never_falls_back_to_global_inventory(self):
        directory=self.company.resource_directory
        with patch.object(self.company,'_run') as cli,patch.object(self.company.training,'sdk') as sdk:
            first=directory.catalog();second=directory.catalog()
            self.assertEqual(first['queues'],[]);self.assertEqual(second['queues'],[]);self.assertIsNone(first['occupancy'])
            self.assertFalse(first['live']);self.assertNotIn('packages',first);self.assertNotIn('clusters',first)
            directory.catalog(refresh=True);cli.assert_not_called()
            self.assertEqual([call.args[0] for call in sdk.call_args_list],['account_resources','account_resources'])
        with self.assertRaisesRegex(ValueError,'队列'):directory.catalog('cluster-a')
        self.assertEqual(self.store.rows('SELECT * FROM company_operations'),[])

    def test_old_screenshots_and_unscoped_choices_never_become_current_resources(self):
        directory=self.company.resource_directory;sid=self.session['id']
        self.store.execute('CREATE TABLE component_queue_observations(namespace TEXT,config_revision INTEGER,content TEXT,observed_at TEXT)')
        snapshot=json.dumps({'queues':[{'name':'old personal resource'}],'source':'user_platform_screenshot'})
        self.store.execute('INSERT INTO component_queue_observations VALUES(?,?,?,?)',('vc-chassis',self.company.config()['revision'],snapshot,'old'))
        self.store.execute('INSERT INTO component_resource_preferences VALUES(?,?,?)',(self.project['id'],json.dumps({'queue_key':'old-personal-key','queue_name':'old personal resource'}),'old'))
        self.assertEqual(directory.catalog()['queues'],[])
        self.assertIsNone(directory.preference(sid));self.assertFalse(hasattr(directory,'observe'))
        with self.assertRaises(ValueError):directory.select_queue(sid,'old-personal-key')
        with self.assertRaises(ValueError):directory.select(sid,'cluster',8,1)
        # Historic evidence stays intact; only its use as an active choice is removed.
        self.assertEqual(self.store.one('SELECT content FROM component_queue_observations')['content'],snapshot)

    def test_connection_change_and_removed_permission_invalidate_active_choice(self):
        directory=self.company.resource_directory;original=self.directory();sid=self.session['id']
        with patch.object(directory,'catalog',return_value=original):directory.select_queue(sid,'q0',original['account_scope'])
        with patch.object(directory,'catalog',return_value={**original,'queues':[]}):self.assertIsNone(directory.preference(sid))
        self.company.configure({**self.company.config(),'namespace':'other-workspace'})
        with patch.object(directory,'catalog',return_value=original):self.assertIsNone(directory.preference(sid))

    def test_live_directory_cache_is_invalidated_when_official_login_file_changes(self):
        directory=self.company.resource_directory
        from pathlib import Path
        auth=self.root/'.lpai';auth.mkdir();file=auth/'conf.json';file.write_text('{}')
        config=self.company.config()
        result={'subject':'a'*64,'namespace':config['namespace'],'queues':self.directory()['queues']}
        with patch('lab.component_resources.Path.home',return_value=self.root),patch.object(self.company.training,'sdk',return_value=result) as sdk:
            first=directory.catalog();self.assertTrue(first['live']);self.assertEqual(len(first['queues']),2)
            directory.catalog();self.assertEqual(sdk.call_count,1)
            file.write_text('{"changed":true}')
            sdk.return_value={**result,'subject':'b'*64}
            second=directory.catalog();self.assertEqual(sdk.call_count,2)
            self.assertNotEqual(first['account_scope'],second['account_scope'])
