# -*- coding: utf-8 -*-
import json,threading,time,uuid,unittest
import test_chat as helpers
from backend.relationship import command
class RelationshipTest(unittest.TestCase):
    setUpClass=classmethod(helpers.ChatTest.setUpClass.__func__)
    setUp=helpers.ChatTest.setUp;tearDown=helpers.ChatTest.tearDown
    req=helpers.ChatTest.req;message=helpers.ChatTest.message;start=helpers.ChatTest.start;snap=helpers.ChatTest.snap;wait=helpers.ChatTest.wait
    def setup_relation(self):
        token=self.sessions['a'][0];self.user=self.app.auth.authenticate(token)
        _,u=self.req('POST','/api/conversations/'+self.cid+'/uses',{});self.use=u['id']
    def read(self,use=None):
        _,d=self.req('GET','/api/conversations/'+self.cid+'/relationships'+('?use_id='+use if use else ''));return d
    def operation(self,action,**kw):
        body={'action':action,'client_request_id':'rel_'+uuid.uuid4().hex,'revision':self.read(self.use)['state']['revision'],'use_id':self.use,**kw}
        return self.req('POST','/api/conversations/'+self.cid+'/relationships',body)
    def say(self,text):
        r,d=self.req('POST','/api/conversations/'+self.cid+'/messages',{'content':text,'client_request_id':'said_'+uuid.uuid4().hex,'use_id':self.use});self.assertEqual(r.status,201,d);return d['id']
    def reply(self,source):
        r,d=self.req('POST','/api/conversations/'+self.cid+'/generations',{'user_message_id':source,'client_request_id':'reply_'+uuid.uuid4().hex,'use_id':self.use});self.assertEqual(r.status,202,d);return self.wait(d['request']['id'])
    def test_no_quantity_upgrade_and_ambiguous_is_not_consent(self):
        self.setup_relation()
        for text in ('你好','哈哈','随便','你觉得呢','可以','假如我们开始恋爱吧','她说我们开始恋爱吧','“我们开始恋爱吧”','不要保存，我们开始恋爱吧'):
            self.say(text);self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')
        self.assertEqual(self.read()['state']['familiarity'],'new');self.assertEqual(self.read()['events'],[])
    def test_valid_romance_keeps_ordinary_reply_across_turns_and_pace_revision(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True)
        self.provider.plans=[['BOOK_CANARY_ORDINARY 我推荐《小王子》，先读第一章。']]
        first=self.reply(self.say('推荐一本适合今天看的书'))
        # A pace edit changes the revision, but does not revoke this relation basis.
        self.operation('pace',pace='先听，不提供建议',store=True)
        question='刚才推荐的那本书，第一章讲什么？'
        later=self.reply(self.say(question))
        with self.app.db.connect() as c:
            ids=json.loads(c.execute('SELECT context_message_ids FROM generation_requests WHERE id=?',(later['request']['id'],)).fetchone()[0])
        self.assertIn(first['message']['id'],ids)
        actual=json.dumps(self.provider.inputs[-1],ensure_ascii=False)
        self.assertIn('BOOK_CANARY_ORDINARY',actual);self.assertIn(question,actual)
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'romantic')
        self.assertFalse(self.read(self.use)['state']['intimacy_paused'])

    def test_pre_fix_input_audit_keeps_unchanged_authority_but_not_revoked_history(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True)
        self.provider.plans=[['LEGACY_AUDIT_BOOK_CANARY 先看第一章。']]
        first=self.reply(self.say('推荐一本书'))
        with self.app.db.connect() as c:
            row=c.execute('SELECT effective_state FROM relationship_inputs WHERE generation_id=?',(first['request']['id'],)).fetchone()
            legacy=json.loads(row[0]);legacy.pop('relation_basis_id');legacy.pop('familiarity_basis_id')
            c.execute('UPDATE relationship_inputs SET effective_state=? WHERE generation_id=?',(json.dumps(legacy),first['request']['id']))
        self.reply(self.say('第一章是什么'))
        self.assertIn('LEGACY_AUDIT_BOOK_CANARY',json.dumps(self.provider.inputs[-1]))
        self.say('分手');self.reply(self.say('换个新话题'))
        self.assertNotIn('LEGACY_AUDIT_BOOK_CANARY',json.dumps(self.provider.inputs[-1]))

    def test_saved_romance_keeps_ordinary_reply_in_new_valid_use(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True)
        self.provider.plans=[['PERSISTENT_BOOK_CANARY 先看第一章。']]
        first=self.reply(self.say('今天读哪章'))
        self.req('POST',f'/api/conversations/{self.cid}/uses/{self.use}/close',{})
        _,new=self.req('POST',f'/api/conversations/{self.cid}/uses',{});self.use=new['id']
        later=self.reply(self.say('继续聊第一章'))
        self.assertIn('PERSISTENT_BOOK_CANARY',json.dumps(self.provider.inputs[-1]))
        with self.app.db.connect() as c:
            self.assertIn(first['message']['id'],json.loads(c.execute('SELECT context_message_ids FROM generation_requests WHERE id=?',(later['request']['id'],)).fetchone()[0]))

    def test_temporary_romance_keeps_current_reply_but_not_new_use_authority(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=False)
        self.provider.plans=[['TEMPORARY_BOOK_CANARY 先读第一章。']]
        first=self.reply(self.say('推荐哪章'))
        self.reply(self.say('第一章讲什么'))
        self.assertIn('TEMPORARY_BOOK_CANARY',json.dumps(self.provider.inputs[-1]))
        _,new=self.req('POST',f'/api/conversations/{self.cid}/uses',{});self.use=new['id']
        question='接着讨论刚才那本书'
        later=self.reply(self.say(question))
        self.assertNotIn('TEMPORARY_BOOK_CANARY',json.dumps(self.provider.inputs[-1]))
        self.assertIn(question,json.dumps(self.provider.inputs[-1],ensure_ascii=False))
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')

    def test_revocation_and_reacceptance_do_not_reauthorize_old_reply(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True)
        self.provider.plans=[['OLD_RELATION_BOOK_CANARY 这本书先看第一章。']]
        first=self.reply(self.say('推荐一本书'))
        self.say('分手');self.operation('set_relation',target='romantic',store=True)
        question='现在重新聊一本书'
        self.reply(self.say(question))
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'romantic')
        self.assertNotIn('OLD_RELATION_BOOK_CANARY',json.dumps(self.provider.inputs[-1]))
        self.assertIn(question,json.dumps(self.provider.inputs[-1],ensure_ascii=False))

    def test_legacy_friend_memory_cannot_authorize_rag_relationship(self):
        from test_memory import MemoryTest
        self.setup_relation();MemoryTest.setup_memory(self)
        m=MemoryTest.candidate(self,'我们是朋友',topic='关系',kind='user_fact')
        changed=self.app.memory.mutate('a',self.cid,m['id'],{'action':'confirm','revision':m['revision']})
        self.assertEqual(MemoryTest.ready(self,m['id'])['index_status'],'ready')
        request=self.reply(self.say('我们现在是什么关系？'))
        with self.app.db.connect() as c:
            selected=json.loads(c.execute('SELECT selected FROM memory_retrievals WHERE generation_id=?',(request['request']['id'],)).fetchone()[0])
        self.assertEqual(selected,[])
        self.assertNotIn('我们是朋友',json.dumps(self.provider.inputs[-1],ensure_ascii=False))
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')
        self.assertIn('当前关系类型：未约定',self.provider.inputs[-1][0]['content'])

    def test_ordinary_facts_including_real_friend_are_still_recalled(self):
        from test_memory import MemoryTest
        self.setup_relation();MemoryTest.setup_memory(self)
        for fact,query in [('我喜欢乌龙茶','推荐饮品'),('我喜欢篮球运动','聊篮球运动'),('我的现实朋友喜欢喝茶','聊朋友的茶')]:
            with self.subTest(fact=fact):
                for old in self.app.memory.list('a',self.cid)['items']:
                    self.app.memory.mutate('a',self.cid,old['id'],{'action':'delete','revision':old['revision']})
                m=MemoryTest.candidate(self,fact,topic='普通事实')
                self.app.memory.mutate('a',self.cid,m['id'],{'action':'confirm','revision':m['revision']})
                MemoryTest.ready(self,m['id'])
                request=self.reply(self.say(query))
                with self.app.db.connect() as c:
                    selected=json.loads(c.execute('SELECT selected FROM memory_retrievals WHERE generation_id=?',(request['request']['id'],)).fetchone()[0])
                self.assertIn(m['id'],[item['id'] for item in selected])
                self.assertIn(fact,self.provider.inputs[-1][0]['content'])

    def test_authority_vocabulary_is_shared_between_extraction_history_and_rag(self):
        from backend.relationship import AUTHORITY,RELATED
        self.setup_relation()
        authority=[{'id':uuid.uuid4().hex,'content':text,'kind':'user_fact'} for text in ('我们是朋友','我们做朋友','我们退回朋友','我们成为朋友','我们在一起','我们开始恋爱')]
        ordinary={'id':uuid.uuid4().hex,'content':'我和现实朋友讨论旧书','kind':'user_fact'}
        with self.app.db.connect() as c:
            self.assertEqual(self.app.relationship.extraction_sources(c,self.cid,authority+[ordinary]),[ordinary])
        self.assertEqual(self.app.relationship.filter_recall(authority+[ordinary]),[ordinary])
        self.assertTrue(all(AUTHORITY.search(m['content']) and RELATED.search(m['content']) for m in authority))

    def test_invalid_closed_or_other_conversation_use_returns_only_long_term_events(self):
        self.setup_relation();_,saved=self.operation('set_relation',target='friends',store=True)
        _,temporary=self.operation('set_relation',target='romantic',store=False)
        old=self.use
        self.assertIn(temporary['event_id'],[e['id'] for e in self.read(old)['events']])
        self.req('POST',f'/api/conversations/{self.cid}/uses/{old}/close',{})
        _,other=self.req('POST','/api/conversations',{'character_id':'luye'})
        _,other_use=self.req('POST',f"/api/conversations/{other['id']}/uses",{})
        for invalid in (old,uuid.uuid4().hex,other_use['id'],None):
            with self.subTest(use=invalid):
                data=self.read(invalid)
                self.assertIsNone(data['use_id'])
                self.assertEqual(data['state']['relationship_type'],'friends')
                self.assertEqual([e['id'] for e in data['events']],[saved['event_id']])
        _,history=self.req('GET',f'/api/conversations/{self.cid}/messages')
        self.assertTrue(any('仅本次' in m['content'] for m in history['items']))

    def test_open_use_of_another_login_does_not_expose_temporary_events(self):
        self.setup_relation();_,saved=self.operation('set_relation',target='friends',store=True)
        _,temporary=self.operation('set_relation',target='romantic',store=False)
        with self.app.db.connect() as c:token,_=self.app.auth.issue(c,'a')
        other=self.app.auth.authenticate(token)
        data=self.app.relationship.read(other,self.cid,self.use)
        self.assertIsNone(data['use_id'])
        self.assertEqual(data['state']['relationship_type'],'friends')
        self.assertEqual([e['id'] for e in data['events']],[saved['event_id']])
        self.assertIn(temporary['event_id'],[e['id'] for e in self.read(self.use)['events']])
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'romantic')

    def test_temporary_relation_refresh_new_use_relogin_and_model_filter(self):
        self.setup_relation();source=self.say('我们开始恋爱吧');self.assertEqual(self.read(self.use)['state']['relationship_type'],'romantic');self.assertEqual(self.read()['state']['relationship_type'],'unspecified')
        self.provider.plans=[['ROMANTIC_AUTH_CANARY 宝贝']];first=self.reply(source)
        _,again=self.req('POST','/api/conversations/'+self.cid+'/uses',{'use_id':self.use});self.assertEqual(again['temporary_relation'],'romantic')
        _,new=self.req('POST','/api/conversations/'+self.cid+'/uses',{});self.use=new['id'];self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')
        self.reply(self.say('接着聊书吧'))
        inputs=json.dumps(self.provider.inputs[-1],ensure_ascii=False);self.assertNotIn('ROMANTIC_AUTH_CANARY',inputs);self.assertNotIn('我们开始恋爱吧',inputs)
        self.assertIn('"relationship_type": "unspecified"',self.provider.inputs[-1][0]['content'])
    def test_persistent_relationship_and_revocation_without_saving(self):
        self.setup_relation();r,d=self.operation('set_relation',target='romantic',store=True);self.assertEqual(r.status,200,d)
        self.assertEqual(self.read()['state']['relationship_type'],'romantic')
        source=self.say('我们分手吧');self.assertEqual(self.read()['state']['relationship_type'],'unspecified');self.assertTrue(self.read()['state']['intimacy_paused'])
        with self.app.db.connect() as c:self.assertEqual(c.execute("SELECT count(*) FROM relationship_events WHERE kind='revocation'",()).fetchone()[0],0);self.assertEqual(c.execute('SELECT count(*) FROM relationship_revocations').fetchone()[0],1)
        self.reply(source);self.assertNotIn('用户明确同意romantic',json.dumps(self.provider.inputs[-1],ensure_ascii=False))
        self.assertEqual(self.read()['state']['familiarity'],'new')
    def test_retreat_friends_temporary_only_and_no_relationship_memory_revival(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True);self.say('退回朋友')
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'friends');self.assertEqual(self.read()['state']['relationship_type'],'unspecified')
        _,new=self.req('POST','/api/conversations/'+self.cid+'/uses',{});self.assertEqual(self.read(new['id'])['state']['relationship_type'],'unspecified')
        self.assertFalse(any(e['status']=='accepted' and e['target_type']=='romantic' for e in self.read()['events']))
    def test_explicit_storage_not_refusal_or_hypothesis(self):
        self.setup_relation();self.say('请记住，我们开始恋爱吧');self.assertEqual(self.read()['state']['relationship_type'],'romantic')
        self.say('结束恋爱');self.say('请不要记住，我们开始恋爱吧');self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')
        for text in ('假如请记住我们开始恋爱吧','她说请记住我们开始恋爱吧','请记住，假如我们开始恋爱吧'):
            self.assertIsNone(command(text))
    def test_slow_pace_refusal_pause_do_not_upgrade_or_end_relation(self):
        self.setup_relation();self.operation('set_relation',target='friends',store=True);self.say('我想慢一点')
        s=self.read(self.use)['state'];self.assertEqual(s['relationship_type'],'friends');self.assertEqual(s['pace'],'我想慢一点')
        self.say('我不愿意下次一起听歌');self.assertFalse(self.read()['state']['intimacy_paused'])
        self.say('不要亲我');self.assertTrue(self.read()['state']['intimacy_paused']);self.assertEqual(self.read()['state']['relationship_type'],'friends')
        self.operation('resume_intimacy');self.assertFalse(self.read()['state']['intimacy_paused'])
    def test_revocation_interrupts_live_stream_and_stale_decision(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True);gate=threading.Event();self.provider.plans=[['旧亲密',gate,'LATE_RELATION_CANARY']]
        source=self.say('现在聊什么');r,d=self.req('POST','/api/conversations/'+self.cid+'/generations',{'client_request_id':'active_rel_req','user_message_id':source,'use_id':self.use});rid=d['request']['id']
        deadline=time.monotonic()+2
        while not self.snap(rid)['message']['content'] and time.monotonic()<deadline:time.sleep(.01)
        self.say('分手');gate.set();time.sleep(.05);self.assertEqual(self.snap(rid)['request']['status'],'failed');self.assertNotIn('LATE_RELATION_CANARY',self.snap(rid)['message']['content'])
        r,d=self.req('POST','/api/conversations/'+self.cid+'/relationships',{'action':'set_relation','target':'romantic','store':True,'revision':0,'use_id':self.use,'client_request_id':'stale_rel_decision'});self.assertEqual(r.status,409)
    def test_idempotency_account_and_role_isolation(self):
        self.setup_relation();body={'action':'set_relation','target':'friends','store':True,'revision':0,'use_id':self.use,'client_request_id':'same_rel_decision'}
        r,a=self.req('POST','/api/conversations/'+self.cid+'/relationships',body);r,b=self.req('POST','/api/conversations/'+self.cid+'/relationships',body);self.assertEqual(a,b)
        r,d=self.req('GET','/api/conversations/'+self.cid+'/relationships',user='b');self.assertEqual(r.status,404)
        r,d=self.req('POST','/api/conversations/'+self.cid+'/relationships',body,user='b');self.assertEqual(r.status,404)
        r,d=self.req('POST','/api/conversations/'+self.cid+'/relationships',{**body,'use_id':{}});self.assertEqual(r.status,422)
        _,other=self.req('POST','/api/conversations',{'character_id':'luye'});_,read=self.req('GET','/api/conversations/'+other['id']+'/relationships');self.assertEqual(read['state']['relationship_type'],'unspecified')
    def test_familiarity_has_concrete_bilateral_evidence_and_independent_type(self):
        self.setup_relation();ids=[]
        self.provider.plans=[['我整理旧书时习惯留下书签'],['我也记得旧书书签的话题']]
        first=self.say('我喜欢旧书里的书签');ids+=[first,self.reply(first)['message']['id']]
        later=self.say('上次说的旧书书签，我带了一张');ids+=[later,self.reply(later)['message']['id']]
        r,d=self.operation('familiarity',target='familiar',store=True,source_ids=ids,confirm_evidence=True);self.assertEqual(r.status,200,d)
        self.assertEqual(self.read()['state']['familiarity'],'familiar');self.assertEqual(self.read()['state']['relationship_type'],'unspecified')
        r,d=self.operation('familiarity',target='close',store=True,source_ids=ids,confirm_evidence=True);self.assertEqual(r.status,422)
        r,d=self.operation('familiarity',target='close',store=True,source_ids=ids,confirm_evidence=True,allow_emotional_expression=True);self.assertEqual(r.status,200)
        self.operation('set_relation',target='romantic',store=True);self.say('分手');self.assertEqual(self.read()['state']['familiarity'],'close')
    def test_repeated_greetings_cannot_form_familiarity(self):
        self.setup_relation();ids=[]
        for _ in range(2):
            mid=self.say('你好');self.provider.plans=[['你好']];ids+=[mid,self.reply(mid)['message']['id']]
        r,d=self.operation('familiarity',target='familiar',store=True,source_ids=ids,confirm_evidence=True);self.assertEqual(r.status,422)
    def test_deleting_relation_basis_requires_explicit_state_impact(self):
        self.setup_relation();_,d=self.operation('set_relation',target='romantic',store=True);eid=d['event_id'];event=self.read()['events'][0]
        r,d=self.operation('delete_event',event_id=eid,event_revision=event['revision']);self.assertEqual(r.status,409)
        r,d=self.operation('delete_event',event_id=eid,event_revision=event['revision'],result_type='unspecified');self.assertEqual(r.status,200,d)
        self.assertEqual(self.read()['state']['relationship_type'],'unspecified')
        r,d=self.operation('save_event',event_id=eid);self.assertEqual(r.status,409)
    def test_closing_use_and_logout_do_not_extend_temporary_relation(self):
        self.setup_relation();self.say('我们开始恋爱吧');self.req('POST','/api/conversations/'+self.cid+'/uses/'+self.use+'/close',{})
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')
        r,d=self.operation('set_relation',target='romantic',store=False);self.assertEqual(r.status,409)
    def test_delete_temporary_event_clears_current_use(self):
        self.setup_relation();_,d=self.operation('set_relation',target='romantic',store=False);event=self.read(self.use)['events'][0]
        r,d=self.operation('delete_event',event_id=event['id'],event_revision=event['revision'],result_type='unspecified');self.assertEqual(r.status,200,d);self.assertEqual(self.read(self.use)['state']['relationship_type'],'unspecified')
    def test_new_device_use_does_not_get_unsaved_auth_but_does_get_revocation(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=False)
        with self.app.db.connect() as c:token,_=self.app.auth.issue(c,'a')
        other=self.app.auth.authenticate(token);use=self.app.service.open_use(other,self.cid)
        self.assertEqual(self.app.relationship.read(other,self.cid,use['id'])['state']['relationship_type'],'unspecified')
        self.operation('set_relation',target='romantic',store=True);self.assertEqual(self.app.relationship.read(other,self.cid,use['id'])['state']['relationship_type'],'romantic')
        self.say('分手');self.assertEqual(self.app.relationship.read(other,self.cid,use['id'])['state']['relationship_type'],'unspecified')
    def test_restart_keeps_revocation_and_fixed_version(self):
        from backend.api import Application
        self.setup_relation();self.operation('set_relation',target='romantic',store=True);self.say('分手')
        restarted=Application(self.config,provider=self.provider)
        self.assertEqual(restarted.relationship.read(self.user,self.cid)['state']['relationship_type'],'unspecified')
        with restarted.db.connect() as c:self.assertTrue(c.execute('SELECT character_version_id FROM conversations WHERE id=?',(self.cid,)).fetchone()[0])
    def test_friend_selection_retires_previous_persistent_romance(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True)
        self.operation('set_relation',target='friends',store=False)
        self.assertEqual(self.read(self.use)['state']['relationship_type'],'friends');self.assertEqual(self.read()['state']['relationship_type'],'unspecified')
    def test_imported_memory_event_and_delete_do_not_require_embedding_call(self):
        from test_memory import MemoryTest
        self.setup_relation()
        MemoryTest.setup_memory(self)
        # Explicit fixture extraction with two valid sources, followed by user confirmation.
        source=self.say('你说下次一起听歌吗');self.provider.plans=[['下次一起听歌好吗']];first=self.reply(source)
        with self.app.db.connect() as c:proof=[dict(c.execute('SELECT * FROM messages WHERE id=?',(first['message']['id'],)).fetchone())]
        m=MemoryTest.candidate(self,'好，下次一起听歌',topic='听歌约定',kind='promise',proof=proof)
        result=self.app.memory.mutate('a',self.cid,m['id'],{'action':'confirm','revision':m['revision']})
        MemoryTest.ready(self,m['id']);m['revision']=result['revision']
        before=len(self.embed.calls)
        r,d=self.operation('import_memory',memory_id=m['id'],memory_revision=m['revision']);self.assertEqual(r.status,200,d);eid=d['event_id']
        self.assertEqual(len(self.embed.calls),before)
        r,d=self.operation('import_memory',memory_id=m['id'],memory_revision=m['revision']);self.assertEqual(r.status,409)
        e=next(x for x in self.read()['events'] if x['id']==eid)
        r,d=self.operation('edit_event',event_id=eid,event_revision=e['revision'],content='我们已经听完歌');self.assertEqual(r.status,200,d)
        e=next(x for x in self.read()['events'] if x['id']==eid);self.assertEqual(e['status'],'pending_review')
    # Helpers used by MemoryTest.candidate against this same isolated Application.
    setup_memory=__import__('test_memory').MemoryTest.setup_memory
    def test_current_question_after_control_must_not_be_filtered_out(self):
        self.setup_relation();self.operation('set_relation',target='romantic',store=True)
        text='按刚才明确确认的关系，现在你会怎样称呼我？一句话即可。';mid=self.say(text);result=self.reply(mid)
        self.assertTrue(any(text in m['content'] for m in self.provider.inputs[-1] if m['role']=='user'))
        with self.app.db.connect() as c:self.assertIn(mid,json.loads(c.execute('SELECT context_message_ids FROM generation_requests WHERE id=?',(result['request']['id'],)).fetchone()[0]))
    def test_temporary_topic_not_saved_as_unbounded_pace(self):
        self.setup_relation();self.say('请记住，今天不想聊工作');data=self.read(self.use)
        self.assertIn('不想聊工作',data['state']['pace']);self.assertEqual(self.read()['state']['pace'],'')
        e=data['events'][0];r,d=self.operation('save_event',event_id=e['id']);self.assertEqual(r.status,422)
        r,d=self.operation('pace',pace='暂停这个话题',store=True);self.assertEqual(r.status,422)
    def test_save_current_pace_persists_without_changing_relation(self):
        self.setup_relation();self.say('我想慢一点');e=self.read(self.use)['events'][0];r,d=self.operation('save_event',event_id=e['id']);self.assertEqual(r.status,200,d)
        self.assertEqual(self.read()['state']['pace'],'我想慢一点');self.assertEqual(self.read()['state']['relationship_type'],'unspecified')
    def test_expired_imported_boundary_loses_current_effect(self):
        from test_memory import MemoryTest
        self.setup_relation();MemoryTest.setup_memory(self);m=MemoryTest.candidate(self,'不要叫我笨蛋',topic='禁用称呼',kind='boundary')
        changed=self.app.memory.mutate('a',self.cid,m['id'],{'action':'confirm','revision':m['revision']});MemoryTest.ready(self,m['id'])
        r,d=self.operation('import_memory',memory_id=m['id'],memory_revision=changed['revision']);self.assertEqual(r.status,200,d);self.assertEqual(self.read()['state']['pace'],'不要叫我笨蛋')
        with self.app.db.connect() as c:c.execute('UPDATE memories SET valid_until=1 WHERE id=?',(m['id'],))
        self.assertEqual(self.read()['state']['pace'],'');self.assertTrue(any(e['status']=='invalidated' for e in self.read()['events']))
    def test_deleting_event_removes_derived_model_echo_without_vector_dependency(self):
        self.setup_relation();_,d=self.operation('pace',pace='先听，不提供建议',store=True);eid=d['event_id'];self.provider.plans=[['EVENT_ERASE_CANARY 我会听']]
        first=self.reply(self.say('回应一句就好'))
        event=next(e for e in self.read()['events'] if e['id']==eid)
        r,d=self.operation('delete_event',event_id=eid,event_revision=event['revision']);self.assertEqual(r.status,200,d)
        self.reply(self.say('我们继续聊书'))
        self.assertNotIn('EVENT_ERASE_CANARY',json.dumps(self.provider.inputs[-1],ensure_ascii=False));self.assertEqual(self.read()['state']['pace'],'')
        with self.app.db.connect() as c:self.assertTrue(c.execute('SELECT 1 FROM memory_context_blocks WHERE message_id=?',(first['message']['id'],)).fetchone())
    def test_accepted_repair_requires_bilateral_sources_and_does_not_upgrade(self):
        self.setup_relation();self.provider.plans=[['对不起，我刚才理解错了，现在说清楚。']];first=self.reply(self.say('刚才有个误会'))
        content='我接受你的道歉，这次误会说开了';accepted=self.say(content)
        r,d=self.operation('repair_event',source_ids=[first['message']['id'],accepted],content=content,confirm_repair=True,store=True);self.assertEqual(r.status,200,d)
        self.assertEqual(self.read()['state']['relationship_type'],'unspecified');self.assertEqual(self.read()['state']['familiarity'],'new');self.assertEqual(self.read()['events'][0]['status'],'completed')
        r,d=self.operation('repair_event',source_ids=[first['message']['id'],accepted],content='我们计划以后和好',confirm_repair=True,store=True);self.assertEqual(r.status,422)
    def test_explicit_lowering_familiarity_retires_old_close_permission_without_save(self):
        self.test_familiarity_has_concrete_bilateral_evidence_and_independent_type()
        # The previous scenario leaves confirmed closeness but has ended romance.
        r,d=self.operation('familiarity',target='familiar',store=True);self.assertEqual(r.status,200,d)
        event=next(e for e in self.read()['events'] if e['id']==d['event_id']);self.assertGreaterEqual(len(event['sources']),4)
        r,d=self.operation('familiarity',target='new',store=False);self.assertEqual(r.status,200,d)
        self.assertEqual(self.read(self.use)['state']['familiarity'],'new');self.assertEqual(self.read()['state']['familiarity'],'new');self.assertTrue(self.read()['state']['intimacy_paused'])
        _,newuse=self.req('POST','/api/conversations/'+self.cid+'/uses',{});self.assertEqual(self.read(newuse['id'])['state']['familiarity'],'new')
