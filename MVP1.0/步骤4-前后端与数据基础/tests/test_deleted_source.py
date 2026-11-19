import unittest
import test_memory as helpers
from backend.model import ModelError


class DeletedSourceTest(unittest.TestCase):
    setUpClass=classmethod(helpers.MemoryTest.setUpClass.__func__)
    setUp=helpers.MemoryTest.setUp;tearDown=helpers.MemoryTest.tearDown
    req=helpers.MemoryTest.req;message=helpers.MemoryTest.message;start=helpers.MemoryTest.start
    snap=helpers.MemoryTest.snap;wait=helpers.MemoryTest.wait;complete=helpers.MemoryTest.complete
    setup_memory=helpers.MemoryTest.setup_memory;candidate=helpers.MemoryTest.candidate
    mutate=helpers.MemoryTest.mutate;ready=helpers.MemoryTest.ready

    def test_deleted_or_corrected_source_cannot_regenerate_but_new_message_can(self):
        for action in ('delete','edit'):
            with self.subTest(action=action):
                self.setup_memory()
                sid=self.message('我喜欢乌龙茶')
                reply=self.complete(sid)
                memory=self.candidate(source=sid)
                r,d=self.mutate(memory,action,**({'content':'现在更喜欢红茶'} if action=='edit' else {}))
                self.assertEqual(r.status,200)
                if action=='edit':self.ready(memory['id'])
                before=len(self.provider.inputs)
                r,d=self.req('POST',f'/api/conversations/{self.cid}/generations',{'kind':'regenerate','user_message_id':sid,'original_reply_id':reply['message']['id'],'client_request_id':'blocked_'+action})
                self.assertEqual(r.status,409);self.assertEqual(d['error']['code'],'source_unavailable')
                self.assertEqual(len(self.provider.inputs),before)
                self.assertFalse(self.snap(reply['request']['id'])['source_available'])
                newer=self.complete(self.message('换个普通话题继续'))
                self.assertEqual(newer['request']['status'],'adopted')
                self.assertTrue(newer['source_available'])
                with self.app.db.connect() as c:
                    self.assertEqual(c.execute('SELECT content FROM messages WHERE id=?',(sid,)).fetchone()[0],'我喜欢乌龙茶')

    def test_failed_deleted_source_cannot_retry_or_reserve_paid_call(self):
        self.setup_memory();sid=self.message('我喜欢乌龙茶')
        self.provider.plans=[[ModelError('provider_connection')]]
        reply=self.complete(sid);self.assertEqual(reply['request']['status'],'failed')
        memory=self.candidate(source=sid);self.mutate(memory,'delete')
        before=len(self.provider.inputs)
        with self.app.db.connect() as c:count=c.execute('SELECT count(*) FROM model_calls').fetchone()[0]
        r,d=self.req('POST',f'/api/conversations/{self.cid}/generations/{reply["request"]["id"]}/retry',{'attempt':1})
        self.assertEqual(r.status,409);self.assertEqual(d['error']['code'],'source_unavailable')
        self.assertEqual(len(self.provider.inputs),before)
        with self.app.db.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM model_calls').fetchone()[0],count)


if __name__=='__main__':unittest.main()
