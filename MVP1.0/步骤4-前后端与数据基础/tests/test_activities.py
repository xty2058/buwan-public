"""Isolated SQLite integration tests; all model/vector outputs are simulated fixtures."""
import json
import threading
import time
import unittest
import uuid
from backend.activities import ARTICLE, Activities
from backend.model import ModelError
import test_chat as helpers
from test_memory import FakeEmbedding


class ActivityTest(unittest.TestCase):
    setUpClass=classmethod(helpers.ChatTest.setUpClass.__func__)
    setUp=helpers.ChatTest.setUp
    tearDown=helpers.ChatTest.tearDown
    req=helpers.ChatTest.req
    message=helpers.ChatTest.message
    start_generation=helpers.ChatTest.start
    snap=helpers.ChatTest.snap
    wait=helpers.ChatTest.wait

    def activity(self,cid=None,user='a',key=None):
        r,a=self.req('POST','/api/conversations/'+(cid or self.cid)+'/activities',{'client_request_id':key or uuid.uuid4().hex},user)
        self.assertEqual(r.status,200,a);return a
    def action(self,a,action,key=None):
        return self.req('POST','/api/conversations/'+a['conversation_id']+'/activities/'+a['id'],{'action':action,'revision':a['revision'],'client_request_id':key or uuid.uuid4().hex})
    def read(self,a,user='a'):
        return self.req('GET','/api/conversations/'+a['conversation_id']+'/activities/'+a['id'],user=user)
    def finish(self,a):
        while a['current_index']<a['total_paragraphs']-1:
            r,a=self.action(a,'next');self.assertEqual(r.status,200,a)
        r,a=self.action(a,'finish');self.assertEqual(r.status,200,a);return a
    def discuss(self,a,text='你怎么看这一段？',key=None,**extra):
        return self.req('POST','/api/conversations/'+self.cid+'/activities/'+a['id']+'/messages',{'content':text,'revision':a['revision'],'client_request_id':key or uuid.uuid4().hex,**extra})
    def memories(self):return self.app.memory.list('a',self.cid)['items']
    def change_memory(self,m,action,**extra):
        return self.req('POST','/api/conversations/'+self.cid+'/memories/'+m['id'],{'action':action,'revision':m['revision'],**extra})
    def ready(self,mid):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with self.app.db.connect() as c:m=dict(c.execute('SELECT * FROM memories WHERE id=?',(mid,)).fetchone())
            if m['index_status'] in ('ready','failed','unconfigured'):return m
            time.sleep(.01)
        self.fail('index failed to settle')
    def normal_reply(self,text):
        r=self.start_generation(self.message(text));done=self.wait(r['request']['id']);self.assertEqual(done['request']['status'],'adopted',done)
        return r['request']['id'],json.dumps(self.provider.inputs[-1],ensure_ascii=False)
    def open_use(self):
        r,u=self.req('POST','/api/conversations/'+self.cid+'/uses',{});self.assertEqual(r.status,200,u);return u['id']
    def promise(self,text='我们约定下次一起读一篇短文'):
        source=self.message(text);mid=uuid.uuid4().hex
        with self.app.db.connect() as c:
            c.execute("INSERT INTO memories(id,conversation_id,kind,fact_scope,content,status,primary_source_id,confirmed_by,confirmed_at,created_at,updated_at,topic_key) VALUES(?,?,'promise','real_user',?,'confirmed',?,'a',1,1,1,?)",(mid,self.cid,text,source,mid))
            c.execute('INSERT INTO memory_sources VALUES(?,?,?)',(mid,self.cid,source));c.execute('INSERT INTO memory_evidence VALUES(?,?,?)',(mid,source,text))
        return mid

    def test_cancel_idempotency_progress_resume_restart(self):
        _,ls=self.req('GET','/api/conversations/'+self.cid+'/activities');self.assertEqual(ls['items'],[]);self.assertNotIn('paragraphs',ls['article'])
        a=self.activity(key='same_start_key');self.assertEqual(self.activity(key='same_start_key')['id'],a['id']);self.assertEqual(self.activity()['id'],a['id'])
        r,b=self.action(a,'next',key='same_next_key');self.assertEqual(r.status,200,b)
        _,again=self.action(a,'next',key='same_next_key');self.assertEqual(again['current_index'],1)
        r,_=self.action(a,'next');self.assertEqual(r.status,409)
        r,_=self.action(b,'finish');self.assertEqual(r.status,409)
        _,p=self.action(b,'pause');self.assertEqual(p['status'],'paused')
        _,r=self.action(p,'resume');self.assertEqual(r['current_index'],1)
        self.app.activities=Activities(self.app.db,self.app.service,self.app.chat,self.app.memory,self.app.relationship)
        _,restored=self.read(r);self.assertEqual(restored['current_index'],1);self.assertEqual(restored['status'],'active')
        done=self.finish(restored);self.assertEqual(done['status'],'completed')
        with self.app.db.connect() as c:events=[x[0] for x in c.execute('SELECT event FROM activity_events WHERE activity_id=?',(a['id'],))]
        self.assertIn('resume',events);self.assertIn('opened',events);self.assertEqual(events.count('started'),1)

    def test_three_roles_two_accounts_isolation(self):
        ids=[]
        for user in ('a','b'):
            for role in ('linyu','luye','shenyan'):
                _,conv=self.req('POST','/api/conversations',{'character_id':role},user)
                a=self.activity(conv['id'],user);ids.append(a['id']);self.assertEqual(a['character_id'],role);self.assertEqual(a['current_index'],0)
                response,_=self.read(a,'b' if user=='a' else 'a');self.assertEqual(response.status,404)
                response,_=self.req('GET','/api/conversations/'+conv['id']+'/archive',user='b' if user=='a' else 'a');self.assertEqual(response.status,404)
        self.assertEqual(len(set(ids)),6)
        # An activity ID belonging to another role cannot be used under this conversation.
        r,_=self.req('GET','/api/conversations/'+self.cid+'/activities/'+ids[1]);self.assertEqual(r.status,404)

    def test_actual_model_input_opened_prefix_and_dedup(self):
        a=self.activity();_,reply=self.discuss(a,key='same_discuss_key');_,again=self.discuss(a,key='same_discuss_key')
        self.assertEqual(len(self.provider.inputs),1);self.assertEqual(reply['messages'],again['messages'])
        actual=json.dumps(self.provider.inputs[-1],ensure_ascii=False);self.assertIn('林屿',actual)
        self.assertIn(ARTICLE['paragraphs'][0],actual)
        for paragraph in ARTICLE['paragraphs'][1:]:self.assertNotIn(paragraph,actual)
        self.assertEqual(reply['current_index'],0)
        with self.app.db.connect() as c:call=dict(c.execute('SELECT * FROM activity_model_calls').fetchone())
        self.assertEqual(call['status'],'completed');self.assertEqual(call['input_tokens'],100)
        self.assertEqual(json.loads(call['context_snapshot']),self.provider.inputs[-1])

    def test_explicit_failed_reply_retry_reuses_user_message(self):
        self.provider.plans=[[ModelError('provider_interrupted')]];a=self.activity()
        _,failed=self.discuss(a,key='failed_request_key');self.assertEqual(failed['messages'][-1]['status'],'failed')
        _,replay=self.discuss(a,key='failed_request_key');self.assertEqual(len(self.provider.inputs),1)
        _,retried=self.discuss(failed,key='retry_request_key',retry_message_id=failed['messages'][-1]['id'])
        self.assertEqual(retried['messages'][-1]['status'],'completed')
        self.assertEqual(len([m for m in retried['messages'] if m['role']=='user']),1)
        self.assertEqual(sum(x['content']=='你怎么看这一段？' for x in self.provider.inputs[-1]),1)

    def test_old_failed_retry_is_rejected_after_a_later_turn(self):
        self.provider.plans=[[ModelError('provider_interrupted')]];a=self.activity()
        _,failed=self.discuss(a,text='最早失败的独立问题');old=failed['messages'][-1]['id']
        for i in range(8):
            _,failed=self.discuss(failed,text='之后的问题'+str(i))
        calls=len(self.provider.inputs)
        r,data=self.discuss(failed,text='最早失败的独立问题',retry_message_id=old)
        self.assertEqual(r.status,409,data);self.assertEqual(len(self.provider.inputs),calls)
        self.assertEqual(data['error']['code'],'activity_retry_invalid')

    def test_pause_discards_late_reply(self):
        a=self.activity();gate=threading.Event();self.provider.plans=[[gate,'迟到的私密回复']];result=[]
        worker=threading.Thread(target=lambda:result.append(self.discuss(a)));worker.start()
        deadline=time.monotonic()+2
        while not self.provider.inputs and time.monotonic()<deadline:time.sleep(.01)
        r,paused=self.action(a,'pause');self.assertEqual(r.status,200,paused);gate.set();worker.join(3)
        self.assertFalse(worker.is_alive());_,last=self.read(paused)
        self.assertNotEqual(last['messages'][-1]['status'],'completed');self.assertEqual(last['messages'][-1]['content'],'')

    def test_memory_deletion_cancels_active_activity_reply_and_old_retry(self):
        a=self.activity();_,a=self.action(a,'pause');m=self.memories()[0];_,a=self.action(a,'resume')
        gate=threading.Event();self.provider.plans=[[gate,'基于旧记录的迟到回复']];result=[]
        worker=threading.Thread(target=lambda:result.append(self.discuss(a)));worker.start()
        deadline=time.monotonic()+2
        while not self.provider.inputs and time.monotonic()<deadline:time.sleep(.01)
        r,data=self.change_memory(m,'delete');self.assertEqual(r.status,200,data);gate.set();worker.join(3)
        self.assertFalse(worker.is_alive());_,a=self.read(a)
        self.assertEqual(a['messages'][-1]['status'],'failed');self.assertEqual(a['messages'][-1]['content'],'')
        calls=len(self.provider.inputs)
        r,data=self.discuss(a,retry_message_id=a['messages'][-1]['id'])
        self.assertEqual(r.status,409,data);self.assertEqual(len(self.provider.inputs),calls)

    def test_closed_use_and_logout_cannot_commit_late_reply(self):
        a=self.activity();use=self.open_use();gate=threading.Event();self.provider.plans=[[gate,'迟到的回复']];result=[]
        worker=threading.Thread(target=lambda:result.append(self.discuss(a,use_id=use)));worker.start()
        deadline=time.monotonic()+2
        while not self.provider.inputs and time.monotonic()<deadline:time.sleep(.01)
        self.req('POST','/api/conversations/'+self.cid+'/uses/'+use+'/close',{});gate.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(result[0][1]['messages'][-1]['status'],'failed')
        self.assertEqual(result[0][1]['messages'][-1]['content'],'')
        a=result[0][1];use=self.open_use();gate=threading.Event();self.provider.plans=[[gate,'退出后的私密回复']];result=[]
        count=len(self.provider.inputs);worker=threading.Thread(target=lambda:result.append(self.discuss(a,use_id=use)));worker.start()
        deadline=time.monotonic()+2
        while len(self.provider.inputs)==count and time.monotonic()<deadline:time.sleep(.01)
        self.req('POST','/api/auth/logout',{});gate.set();worker.join(3)
        self.assertEqual(result[0][0].status,401);self.assertNotIn('messages',result[0][1])
        with self.app.db.connect() as c:
            last=dict(c.execute('SELECT * FROM activity_model_calls ORDER BY rowid DESC LIMIT 1').fetchone())
        self.assertEqual(last['status'],'failed')

    def test_candidate_confirm_long_term_recall_and_delete_no_revival(self):
        self.app.memory.embedding=FakeEmbedding();a=self.activity();_,paused=self.action(a,'pause');m=self.memories()[0]
        self.assertEqual(m['status'],'candidate');self.assertEqual(m['source_type'],'activity');self.assertIn('尚未完成',m['content'])
        self.assertEqual(self.app.memory.retrieve(self.cid,'共读')['items'],[])
        _,actual=self.normal_reply('我们刚才做了什么？');self.assertNotIn(ARTICLE['title'],actual)
        r,d=self.change_memory(m,'confirm');self.assertEqual(r.status,200,d);m=self.ready(m['id'])
        for i in range(45):self.message('独立寒暄'+str(i))
        rid,actual=self.normal_reply('我们共读过什么？');self.assertIn(ARTICLE['title'],actual)
        with self.app.db.connect() as c:
            context=json.loads(c.execute('SELECT context_message_ids FROM generation_requests WHERE id=?',(rid,)).fetchone()[0]);self.assertNotIn(m['primary_source_id'],context)
            recalled=json.loads(c.execute('SELECT selected FROM memory_retrievals WHERE generation_id=?',(rid,)).fetchone()[0]);self.assertEqual(recalled[0]['id'],m['id'])
        r,d=self.change_memory(m,'delete');self.assertEqual(r.status,200,d);self.assertEqual(self.app.memory.retrieve(self.cid,'共读')['items'],[])
        _,paused=self.read(paused);_,a=self.action(paused,'resume');self.finish(a);self.assertEqual(self.memories(),[])
        _,actual=self.normal_reply('我们共读过什么？');self.assertNotIn(ARTICLE['title'],actual)

    def test_correction_reconfirm_archive_and_rejection(self):
        self.app.memory.embedding=FakeEmbedding();a=self.finish(self.activity());m=self.memories()[0]
        self.change_memory(m,'confirm');m=self.ready(m['id']);correction='读完后我更在意纸条上的交流。'
        r,d=self.change_memory(m,'edit',content=correction);self.assertEqual(r.status,200,d)
        m=self.memories()[0];self.assertEqual(m['status'],'candidate');self.assertEqual(self.app.memory.retrieve(self.cid,'共读')['items'],[])
        r,d=self.change_memory(m,'confirm');self.assertEqual(r.status,200,d);m=self.ready(m['id'])
        _,archive=self.req('GET','/api/conversations/'+self.cid+'/archive');self.assertIn(correction,archive['experiences'][0]['content'])
        self.assertEqual(archive['state']['relationship_type'],'unspecified');self.assertEqual(archive['state']['familiarity'],'new')
        self.change_memory(m,'delete');_,archive=self.req('GET','/api/conversations/'+self.cid+'/archive');self.assertNotIn(correction,json.dumps(archive,ensure_ascii=False))
        b=self.activity();_,b=self.action(b,'pause');self.change_memory(self.memories()[0],'reject');_,b=self.read(b);_,b=self.action(b,'resume');self.finish(b);self.assertEqual(self.memories(),[])

    def test_activity_and_chat_share_approval_budget(self):
        self.provider.mode='live';self.provider.settings.max_calls=1
        a=self.activity();r,a=self.discuss(a);self.assertEqual(r.status,200,a)
        source=self.message('普通聊天')
        r,d=self.req('POST','/api/conversations/'+self.cid+'/generations',{'user_message_id':source,'client_request_id':uuid.uuid4().hex})
        self.assertEqual(r.status,409,d);self.assertEqual(d['error']['code'],'call_limit_reached')

    def test_imported_promise_completion_survives_relationship_reconcile(self):
        self.app.memory.embedding=FakeEmbedding();mid=self.promise();use=self.open_use()
        _,relations=self.req('GET','/api/conversations/'+self.cid+'/relationships?use_id='+use)
        r,data=self.req('POST','/api/conversations/'+self.cid+'/relationships',{'action':'import_memory','memory_id':mid,'memory_revision':1,'revision':relations['state']['revision'],'use_id':use,'client_request_id':uuid.uuid4().hex})
        self.assertEqual(r.status,200,data)
        r,data=self.req('POST','/api/conversations/'+self.cid+'/archive',{'action':'complete_promise','memory_id':mid,'revision':1,'client_request_id':uuid.uuid4().hex,'confirm_completed':True})
        self.assertEqual(r.status,200,data);m=self.ready(mid);self.assertEqual(m['status'],'confirmed')
        _,archive=self.req('GET','/api/conversations/'+self.cid+'/archive?use_id='+use)
        self.assertEqual(archive['promises'][0]['status'],'completed')
        self.assertEqual(len(archive['promises']),1)
        self.assertTrue(any(x['id']==mid for x in self.app.memory.retrieve(self.cid,'共读')['items']))
        _,actual=self.normal_reply('我们还有什么没完成的约定？')
        self.assertIn('已完成',actual)

    def test_promise_completion_and_withdrawal_change_authoritative_memory(self):
        self.app.memory.embedding=FakeEmbedding();mid=self.promise()
        _,before=self.req('GET','/api/conversations/'+self.cid+'/archive');self.assertEqual(before['promises'][0]['status'],'pending')
        body={'action':'complete_promise','memory_id':mid,'revision':1,'client_request_id':'complete_promise_key','confirm_completed':True}
        r,d=self.req('POST','/api/conversations/'+self.cid+'/archive',body);self.assertEqual(r.status,200,d);self.ready(mid)
        _,after=self.req('GET','/api/conversations/'+self.cid+'/archive');self.assertEqual(after['promises'][0]['status'],'completed')
        recalled=self.app.memory.retrieve(self.cid,'共读')['items'];self.assertEqual(recalled[0]['kind'],'experience');self.assertIn('已完成',recalled[0]['content'])
        r,d=self.req('POST','/api/conversations/'+self.cid+'/archive',body);self.assertEqual(r.status,200,d)
        self.change_memory(self.memories()[0],'delete');self.assertEqual(self.app.memory.retrieve(self.cid,'共读')['items'],[])
        mid=self.promise('我们约定下次一起画一张图')
        r,d=self.req('POST','/api/conversations/'+self.cid+'/archive',{'action':'withdraw_promise','memory_id':mid,'revision':1,'client_request_id':'withdraw_promise_key'});self.assertEqual(r.status,200,d)
        _,after=self.req('GET','/api/conversations/'+self.cid+'/archive');self.assertTrue(all(p['status']=='withdrawn' for p in after['promises']))
        self.assertEqual(self.app.memory.retrieve(self.cid,'约定')['items'],[])

if __name__=='__main__':unittest.main()
