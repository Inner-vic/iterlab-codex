import unittest
from lab.transcript import hide_host_echoes
from lab.assistant_behavior import automatic_update_instructions

class VisibilityTests(unittest.TestCase):
    def test_exact_automatic_echo_hidden_but_human_input_and_originals_preserved(self):
        delivery={'id':'auto','turn_id':'t','text':'[角色交接事件 · 系统，非人类新授权]\n{"target":"executor"}',
                  'prefix':'[宿主持久角色合同 v1]\ncontract','suffix':'\npolicy','reason':None}
        class ReadOnlyStore:
            def rows(self,sql,args):return [delivery]
        def msg(id,text,turn='t',role='user',kind='message'):
            return dict(id=id,item_id=id,text=text,turn_id=turn,role=role,kind=kind)
        automatic=automatic_update_instructions()+'\n'+delivery['text']
        messages=[msg('native',automatic),msg('raw',delivery['prefix']+'\n'+automatic+'\npolicy'),
                  msg('delivery:auto',delivery['text']),msg('human','请继续检查'),
                  msg('quoted',automatic,'human-turn'),msg('assistant','已有结果',role='assistant'),
                  msg('note','正在交接',role='system',kind='role_handoff')]
        before=[dict(m) for m in messages]
        result=hide_host_echoes(ReadOnlyStore(),'s',messages)
        self.assertEqual([m['id'] for m in result],['human','quoted','assistant'])
        self.assertEqual(messages,before)

if __name__=='__main__':unittest.main()
