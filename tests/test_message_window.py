import unittest
from tests import test_company as fixtures
from lab.message_window import read


class MessageWindowTests(unittest.TestCase):
    setUp=fixtures.CompanyTests.setUp
    tearDown=fixtures.CompanyTests.tearDown

    def test_backward_keyset_pages_and_loaded_start_do_not_drop_or_duplicate(self):
        sid=self.session['id']
        for index in range(301):self.store.message(sid,'item-'+str(index),'assistant','agentMessage','answer '+str(index),'completed','turn-'+str(index//3))
        self.store.message(sid,'raw','tool','output','x'*1000000,'completed')
        before=self.store.one('SELECT COUNT(*) AS n,SUM(length(text)) AS bytes FROM messages')
        newest,window=read(self.store,sid,limit=120)
        self.assertTrue(window['has_more']);self.assertLessEqual(len(newest),122)
        self.assertEqual(int(newest[0]['item_id'][5:])%3,0) # Never split a native turn.
        older,previous=read(self.store,sid,before=window['start_position'])
        self.assertFalse({m['id'] for m in older}&{m['id'] for m in newest})
        loaded,_=read(self.store,sid,start=previous['start_position'])
        self.assertEqual([m['id'] for m in loaded],[m['id'] for m in older+newest])
        oldest,last=read(self.store,sid,before=previous['start_position'])
        self.assertFalse(last['has_more']);self.assertEqual(len(oldest+older+newest),301)
        self.assertEqual(before,self.store.one('SELECT COUNT(*) AS n,SUM(length(text)) AS bytes FROM messages'))
        self.assertEqual(self.store.one("SELECT length(text) AS n FROM messages WHERE item_id='raw'")['n'],1000000)

    def test_positions_cannot_address_another_session_and_invalid_cursor_rejected(self):
        sid=self.session['id'];other=self.store.create_session(self.project['id'],'ept')['id']
        self.store.message(other,'other','assistant','agentMessage','private','completed')
        self.assertEqual(read(self.store,sid,start=1)[0],[])
        for value in ['-1','1 or 1=1','0',str(2**64)]:
            with self.assertRaises(ValueError):read(self.store,sid,before=value)
