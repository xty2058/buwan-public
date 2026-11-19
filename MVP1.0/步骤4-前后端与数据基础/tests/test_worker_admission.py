"""Worker preflight and in-retrieval races. All vectors/models are fixtures, no live IO."""
import threading,time,unittest
from backend.model import Cancellation
import test_generation_source as helpers
import test_memory as memory_helpers

class WorkerAdmissionTest(unittest.TestCase):
    setUpClass=classmethod(helpers.GenerationSourceTest.setUpClass.__func__)
    setUp=helpers.GenerationSourceTest.setUp;tearDown=helpers.GenerationSourceTest.tearDown
    req=helpers.GenerationSourceTest.req;message=helpers.GenerationSourceTest.message
    setup_relation=helpers.GenerationSourceTest.setup_relation;operation=helpers.GenerationSourceTest.operation
    read=helpers.GenerationSourceTest.read;say=helpers.GenerationSourceTest.say
    wait=helpers.GenerationSourceTest.wait;snap=helpers.GenerationSourceTest.snap
    new_use=helpers.GenerationSourceTest.new_use
    setup_memory=memory_helpers.MemoryTest.setup_memory
    def ready_memory(self,text='我喜欢乌龙茶',topic='饮品偏好'):
        m=memory_helpers.MemoryTest.candidate(self,text,topic=topic)
        self.app.memory.mutate('a',self.cid,m['id'],{'action':'confirm','revision':m['revision']})
        m=memory_helpers.MemoryTest.ready(self,m['id']);self.assertEqual(m['index_status'],'ready');return m
    def queue(self,source):
        self.launch=self.app.chat.launch;self.app.chat.launch=lambda rid:None
        r,d=self.req('POST',f'/api/conversations/{self.cid}/generations',{'user_message_id':source,'client_request_id':'queued_ready_source','use_id':self.use})
        self.assertEqual(r.status,202,d);self.app.chat.launch=self.launch
        return d['request']['id']
    def counters(self):
        with self.app.db.connect() as c:
            retrieve=c.execute("SELECT count(*) FROM memory_call_audit WHERE purpose='retrieve'").fetchone()[0]
            calls=c.execute('SELECT count(*) FROM model_calls').fetchone()[0]
        return len(self.provider.inputs),len(self.embed.calls),retrieve,calls
    def finish(self):
        deadline=time.monotonic()+3
        while self.app.chat.controls and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(self.app.chat.controls)
    def test_expired_use_with_ready_memory_never_reaches_query_embedding(self):
        self.setup_relation();self.ready_memory();rid=self.queue(self.say('我们开始恋爱吧'));self.new_use()
        before=self.counters();self.launch(rid);result=self.wait(rid);self.finish()
        self.assertEqual(result['request']['status'],'failed');self.assertEqual(result['request']['error_code'],'source_unavailable')
        self.assertEqual(self.counters(),before)
        with self.app.db.connect() as c:self.assertEqual(c.execute('SELECT status FROM model_calls WHERE generation_request_id=?',(rid,)).fetchone()[0],'failed')
    def test_revoked_source_with_ready_memory_never_queries(self):
        self.setup_relation();self.ready_memory();rid=self.queue(self.say('我们开始恋爱吧'))
        self.operation('revoke',target='unspecified',store=False);before=self.counters()
        self.app.chat.run(rid,Cancellation(),1)
        self.assertEqual(self.counters(),before);self.assertEqual(self.snap(rid)['request']['status'],'failed')
    def test_deleted_source_does_not_query_other_ready_memory(self):
        self.setup_relation();self.ready_memory('我喜欢篮球运动','运动兴趣');source_memory=self.ready_memory()
        rid=self.queue(source_memory['primary_source_id'])
        self.app.memory.mutate('a',self.cid,source_memory['id'],{'action':'delete','revision':source_memory['revision']})
        before=self.counters();self.app.chat.run(rid,Cancellation(),1)
        self.assertEqual(self.counters(),before);self.assertEqual(self.snap(rid)['request']['status'],'failed')
    def test_expired_login_is_checked_before_embedding(self):
        self.setup_relation();self.ready_memory();rid=self.queue(self.say('我们开始恋爱吧'))
        with self.app.db.connect() as c:c.execute('UPDATE auth_sessions SET expires_at=1 WHERE id=?',(self.user['session_id'],))
        before=self.counters();self.launch(rid);result=self.wait(rid);self.finish()
        self.assertEqual(result['request']['error_code'],'authentication_expired');self.assertEqual(self.counters(),before)
    def test_wrong_engine_attempt_and_cancelled_control_do_not_query(self):
        self.setup_relation();self.ready_memory();rid=self.queue(self.say('聊一杯茶'));before=self.counters()
        with self.app.db.connect() as c:c.execute("UPDATE generation_requests SET engine_id='stale-engine' WHERE id=?",(rid,))
        self.app.chat.run(rid,Cancellation(),1);self.assertEqual(self.counters(),before)
        with self.app.db.connect() as c:c.execute('UPDATE generation_requests SET engine_id=? WHERE id=?',(self.app.chat.engine_id,rid))
        self.app.chat.run(rid,Cancellation(),2);self.assertEqual(self.counters(),before)
        control=Cancellation();control.cancel();self.app.chat.run(rid,control,1);self.assertEqual(self.counters(),before)
    def test_legal_sources_with_ready_memory_query_and_generate(self):
        # Separate fixtures avoid conflating superseded relation bases across cases.
        self.setup_relation();self.ready_memory();source=self.say('我们开始恋爱吧')
        rid=self.queue(source);before=self.counters();self.launch(rid);result=self.wait(rid);self.finish()
        self.assertEqual(result['request']['status'],'adopted')
        after=self.counters();self.assertEqual(after[:3],(before[0]+1,before[1]+1,before[2]+1));self.assertEqual(after[3],before[3])
    def test_saved_source_across_use_with_memory_still_queries(self):
        self.setup_relation();self.ready_memory();source=self.say('请记住，我们开始恋爱吧');self.new_use()
        rid=self.queue(source);before=self.counters();self.launch(rid);self.assertEqual(self.wait(rid)['request']['status'],'adopted');self.finish()
        after=self.counters();self.assertEqual(after[:3],(before[0]+1,before[1]+1,before[2]+1))
    def test_ordinary_new_source_with_memory_still_queries(self):
        self.setup_relation();self.ready_memory();source=self.say('推荐符合偏好的茶')
        rid=self.queue(source);before=self.counters();self.launch(rid);self.assertEqual(self.wait(rid)['request']['status'],'adopted');self.finish()
        after=self.counters();self.assertEqual(after[:3],(before[0]+1,before[1]+1,before[2]+1))
        self.assertIn('我喜欢乌龙茶',self.provider.inputs[-1][0]['content'])
    def during_retrieval(self,change):
        self.setup_relation();m=self.ready_memory();rid=self.queue(self.say('我们开始恋爱吧'))
        gate=threading.Event();self.embed.gate=gate;before=self.counters();self.launch(rid)
        try:
            deadline=time.monotonic()+2
            while len(self.embed.calls)==before[1] and time.monotonic()<deadline:time.sleep(.005)
            self.assertEqual(len(self.embed.calls),before[1]+1)
            change(m)
        finally:gate.set()
        self.wait(rid);self.finish();after=self.counters()
        self.assertEqual(after[0],before[0]);self.assertEqual(after[1],before[1]+1);self.assertEqual(after[2],before[2]+1)
        self.assertEqual(self.snap(rid)['request']['status'],'failed')
    def test_source_expires_during_legitimate_query_no_chat_follows(self):
        self.during_retrieval(lambda m:self.new_use())
        self.assertEqual(self.snap(next(iter(self.generation_ids())))['request']['error_code'],'source_unavailable')
    def generation_ids(self):
        with self.app.db.connect() as c:return [r[0] for r in c.execute('SELECT id FROM generation_requests')]
    def test_memory_changes_during_legitimate_query_no_chat_follows(self):
        self.during_retrieval(lambda m:self.app.memory.mutate('a',self.cid,m['id'],{'action':'delete','revision':m['revision']}))

if __name__=='__main__':unittest.main()
