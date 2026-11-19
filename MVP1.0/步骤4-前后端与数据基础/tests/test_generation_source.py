"""Effective source admission: actual API/SQL with FakeProvider, never live calls."""
import unittest,uuid
import test_relationship as helpers
from backend.model import ModelError

class GenerationSourceTest(unittest.TestCase):
    setUpClass=classmethod(helpers.RelationshipTest.setUpClass.__func__)
    setUp=helpers.RelationshipTest.setUp;tearDown=helpers.RelationshipTest.tearDown
    req=helpers.RelationshipTest.req;message=helpers.RelationshipTest.message
    setup_relation=helpers.RelationshipTest.setup_relation;operation=helpers.RelationshipTest.operation
    read=helpers.RelationshipTest.read;say=helpers.RelationshipTest.say;reply=helpers.RelationshipTest.reply
    wait=helpers.RelationshipTest.wait;snap=helpers.RelationshipTest.snap
    def new_use(self):
        self.req('POST',f'/api/conversations/{self.cid}/uses/{self.use}/close',{})
        r,d=self.req('POST',f'/api/conversations/{self.cid}/uses',{});self.assertEqual(r.status,200);self.use=d['id']
    def counts(self):
        with self.app.db.connect() as c:
            return tuple(c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('generation_requests','model_calls'))+(len(self.provider.inputs),)
    def regenerate(self,source,reply):
        return self.req('POST',f'/api/conversations/{self.cid}/generations',{'kind':'regenerate','user_message_id':source,'original_reply_id':reply['message']['id'],'client_request_id':'candidate_'+uuid.uuid4().hex,'use_id':self.use})
    def assert_denied(self,response,before):
        r,d=response;self.assertEqual(r.status,409,d);self.assertEqual(d['error']['code'],'source_unavailable');self.assertEqual(self.counts(),before)
    def current_snapshot(self,rid):
        r,d=self.req('GET',f'/api/conversations/{self.cid}/generations/{rid}?use_id={self.use}');self.assertEqual(r.status,200);return d
    def test_expired_temporary_source_regeneration_and_snapshot_agree(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');first=self.reply(source);self.new_use()
        before=self.counts();self.assert_denied(self.regenerate(source,first),before)
        self.assertFalse(self.current_snapshot(first['request']['id'])['source_available'])
        r,recovered=self.req('GET',f'/api/conversations/{self.cid}/recovery?use_id={self.use}')
        self.assertFalse(recovered['generation']['source_available']);self.assertFalse(recovered['last_user_source_available'])
        self.assertIn(source,[m['id'] for m in recovered['items']]);self.assertEqual(self.counts(),before)
        # Read/replay old results is allowed and must never create another model call.
        r,_=self.req('POST',f'/api/conversations/{self.cid}/generations',{'user_message_id':source,'client_request_id':self.original_key(first),'use_id':self.use})
        self.assertEqual(r.status,202);self.assertEqual(self.counts(),before)
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')
        self.assertEqual(self.reply(self.say('聊一本新书吧'))['request']['status'],'adopted')
    def original_key(self,reply):
        with self.app.db.connect() as c:return c.execute('SELECT client_request_id FROM generation_requests WHERE id=?',(reply['request']['id'],)).fetchone()[0]
    def test_failed_temporary_source_retry_rejected_across_use_and_relogin(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');self.provider.plans=[[ModelError('provider_connection')]]
        first=self.reply(source);self.assertEqual(first['request']['status'],'failed');self.new_use();before=self.counts()
        self.assert_denied(self.req('POST',f"/api/conversations/{self.cid}/generations/{first['request']['id']}/retry",{'attempt':1,'use_id':self.use}),before)
        self.req('POST','/api/auth/logout')
        with self.app.db.connect() as c:self.sessions['a']=self.app.auth.issue(c,'a')
        _,opened=self.req('POST',f'/api/conversations/{self.cid}/uses',{});self.use=opened['id']
        self.assert_denied(self.req('POST',f"/api/conversations/{self.cid}/generations/{first['request']['id']}/retry",{'attempt':1,'use_id':self.use}),before)
        self.assertFalse(self.current_snapshot(first['request']['id'])['source_available'])
        self.assertEqual(self.reply(self.say('重新聊普通话题'))['request']['status'],'adopted')
    def test_same_use_temporary_authority_allows_legitimate_regeneration(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');first=self.reply(source)
        self.assertTrue(self.current_snapshot(first['request']['id'])['source_available'])
        r,d=self.regenerate(source,first);self.assertEqual(r.status,202,d);self.wait(d['request']['id'])
        self.assertTrue(any(m['role']=='user' and '我们开始恋爱吧' in m['content'] for m in self.provider.inputs[-1]))
    def test_saved_effective_authority_can_be_current_input_in_new_use(self):
        self.setup_relation();source=self.say('请记住，我们开始恋爱吧');first=self.reply(source);self.new_use()
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'romantic')
        self.assertTrue(self.current_snapshot(first['request']['id'])['source_available'])
        r,d=self.regenerate(source,first);self.assertEqual(r.status,202,d);self.wait(d['request']['id'])
        self.assertTrue(any(m['role']=='user' and '我们开始恋爱吧' in m['content'] for m in self.provider.inputs[-1]))
    def test_same_use_failed_temporary_source_can_retry(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');self.provider.plans=[[ModelError('provider_connection')],['合法重试回复']]
        first=self.reply(source);self.assertEqual(first['request']['status'],'failed')
        r,d=self.req('POST',f"/api/conversations/{self.cid}/generations/{first['request']['id']}/retry",{'attempt':1,'use_id':self.use})
        self.assertEqual(r.status,202,d);result=self.wait(first['request']['id'])
        self.assertEqual(result['request']['attempt'],2);self.assertEqual(result['request']['status'],'adopted')
        self.assertTrue(any(m['role']=='user' and '我们开始恋爱吧' in m['content'] for m in self.provider.inputs[-1]))
        self.assertEqual(len(self.provider.inputs),2)
    def test_saved_failed_source_can_retry_in_new_valid_use(self):
        self.setup_relation();source=self.say('请记住，我们开始恋爱吧');self.provider.plans=[[ModelError('provider_connection')],['保存授权下的合法回复']]
        first=self.reply(source);self.new_use()
        r,d=self.req('POST',f"/api/conversations/{self.cid}/generations/{first['request']['id']}/retry",{'attempt':1,'use_id':self.use})
        self.assertEqual(r.status,202,d);result=self.wait(first['request']['id'])
        self.assertEqual(result['request']['status'],'adopted');self.assertTrue(result['source_available'])
        self.assertTrue(any(m['role']=='user' and '我们开始恋爱吧' in m['content'] for m in self.provider.inputs[-1]))
        self.assertEqual(len(self.provider.inputs),2)
    def test_ordinary_old_question_is_not_blocked_by_new_use(self):
        self.setup_relation();source=self.say('推荐一本书');first=self.reply(source);self.new_use()
        r,d=self.regenerate(source,first);self.assertEqual(r.status,202,d);self.wait(d['request']['id'])
        self.assertTrue(any(m['role']=='user' and '推荐一本书' in m['content'] for m in self.provider.inputs[-1]))
    def test_unstarted_expired_source_cannot_start_and_recovery_has_no_resume(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');self.new_use();before=self.counts()
        self.assert_denied(self.req('POST',f'/api/conversations/{self.cid}/generations',{'user_message_id':source,'client_request_id':'unstarted_old_source','use_id':self.use}),before)
        _,d=self.req('GET',f'/api/conversations/{self.cid}/recovery?use_id={self.use}')
        self.assertFalse(d['reply_required']);self.assertFalse(d['last_user_source_available']);self.assertEqual(self.counts(),before)
    def test_revocation_and_new_authority_do_not_reauthorize_old_source(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');first=self.reply(source)
        self.operation('revoke',target='unspecified',store=False)
        before=self.counts();self.assert_denied(self.regenerate(source,first),before)
        self.operation('set_relation',target='romantic',store=True)
        self.assert_denied(self.regenerate(source,first),before)
        self.assertFalse(self.current_snapshot(first['request']['id'])['source_available'])
    def test_last_defense_stops_queued_request_when_use_expires_before_worker(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');launch=self.app.chat.launch;self.app.chat.launch=lambda rid:None
        r,d=self.req('POST',f'/api/conversations/{self.cid}/generations',{'user_message_id':source,'client_request_id':'queued_then_expired','use_id':self.use})
        self.assertEqual(r.status,202);self.new_use();self.app.chat.launch=launch;launch(d['request']['id'])
        result=self.wait(d['request']['id']);self.assertEqual(result['request']['status'],'failed');self.assertEqual(result['request']['error_code'],'source_unavailable')
        self.assertEqual(len(self.provider.inputs),0)

if __name__=='__main__':unittest.main()
