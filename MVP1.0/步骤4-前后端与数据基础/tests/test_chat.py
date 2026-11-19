# -*- coding: utf-8 -*-
import copy
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.api import Application
from backend.auth import password_hash
from backend.config import Config
from backend.server import make_server
from backend.model import Cancellation, CompatibleProvider, ModelError, ModelSettings, parse_stream


class FakeProvider:
    mode='simulated'
    configured=True
    label='隔离测试服务 · 非真实模型'
    def __init__(self):
        self.settings=SimpleNamespace(approval_id='test-only',provider='fixture',model='fixture',max_calls=None,
                                      input_price='',output_price='',currency='')
        self.plans=[];self.inputs=[];self.lock=threading.Lock()
    def stream(self,messages,cancel):
        with self.lock:
            self.inputs.append(copy.deepcopy(messages))
            plan=self.plans.pop(0) if self.plans else ['模拟回复甲','模拟回复乙']
        for item in plan:
            if isinstance(item,threading.Event):item.wait(3)
            elif isinstance(item,Exception):raise item
            else:
                yield {'delta':item}
                time.sleep(.008)
        yield {'usage':{'input_tokens':100,'output_tokens':20}}
        yield {'finish_reason':'stop'}


class ChatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.encoded=password_hash('Test-only-credential-2026')
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.provider=FakeProvider()
        self.config=Config(db=self.root/'chat.sqlite3',log_dir=self.root/'logs',origin='http://test.local')
        self.app=Application(self.config,provider=self.provider)
        self.sessions={}
        with self.app.db.connect() as c:
            for user in ('a','b'):
                c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(user,'user_'+user,self.encoded,'PRIVATE_NAME_'+user,1,1))
                self.sessions[user]=self.app.auth.issue(c,user)
        _,conv=self.req('POST','/api/conversations',{'character_id':'linyu'});self.cid=conv['id']
    def tearDown(self):
        with self.app.chat.guard:
            controls=list(self.app.chat.controls.values())
        for control in controls:control.cancel()
        deadline=time.monotonic()+4
        while self.app.chat.controls and time.monotonic()<deadline:time.sleep(.01)
        for h in list(self.app.logger.handlers):h.close();self.app.logger.removeHandler(h)
        self.temp.cleanup()
    def req(self,method,path,body=None,user='a',app=None):
        token,csrf=self.sessions[user]
        headers={'Host':'test.local','Origin':'http://test.local','Content-Type':'application/json',
                 'Cookie':'weiwan_session='+token,'X-CSRF-Token':csrf}
        response=(app or self.app).handle(method,path,headers,json.dumps(body or {}).encode())
        return response,json.loads(response.body) if response.stream is None else response.stream
    def message(self,text='我喜欢无糖红茶',key=None):
        key=key or 'message_'+str(time.time_ns())
        r,data=self.req('POST','/api/conversations/'+self.cid+'/messages',{'content':text,'client_request_id':key})
        self.assertEqual(r.status,201,data);return data['id']
    def start(self,source=None,kind='reply',original=None,key=None):
        source=source or self.message()
        payload={'kind':kind,'user_message_id':source,'client_request_id':key or 'generation_'+str(time.time_ns())}
        if original:payload['original_reply_id']=original
        r,data=self.req('POST','/api/conversations/'+self.cid+'/generations',payload)
        self.assertEqual(r.status,202,data);return data
    def snap(self,rid):return self.app.chat.snapshot('a',self.cid,rid)
    def wait(self,rid,statuses=('adopted','awaiting_choice','failed','stopped','kept')):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            data=self.snap(rid)
            if data['request']['status'] in statuses:return data
            time.sleep(.005)
        self.fail('Generation did not reach expected state')
    def complete(self):
        started=self.start();return self.wait(started['request']['id'])
    def test_deepseek_v12_context_uses_saved_prompt_and_live_state(self):
        self.provider.settings.provider='deepseek'
        self.provider.settings.prompt_version='linyu-v1.2'
        source=self.message('今天下班真累。')
        self.wait(self.start(source)['request']['id'])
        prompt=(Path(__file__).resolve().parents[1]/'backend/prompts/linyu-v1.2.txt').read_text().strip()
        context=self.provider.inputs[-1]
        self.assertTrue(context[0]['content'].startswith(prompt))
        self.assertIn('当前关系',context[0]['content'])
        self.assertFalse(context[0]['content'].startswith('WEIWAN_JSON_DIALOGUE_V1'))
        self.assertEqual(context[-1]['content'],'今天下班真累。')
        source=self.message('你还记得我养过什么宠物吗？')
        self.wait(self.start(source)['request']['id'])
        self.assertTrue(self.provider.inputs[-1][0]['content'].startswith(prompt))
        self.assertIn('本轮仅核对用户事实',self.provider.inputs[-1][0]['content'])

    def test_deepseek_v14_context_uses_v14_prompt(self):
        self.provider.settings.provider='deepseek'
        self.provider.settings.prompt_version='linyu-v1.4'
        self.wait(self.start(self.message('啊啊啊！我面试通过了！！'))['request']['id'])
        prompts=Path(__file__).resolve().parents[1]/'backend/prompts'
        v14=(prompts/'linyu-v1.4.txt').read_text().strip()
        system=self.provider.inputs[-1][0]['content']
        self.assertTrue(system.startswith(v14))
        self.assertIn('【好消息与新进展的边界】',system)
        self.assertNotIn('【新进展的回应示范】',system)
        self.assertIn('当前关系',system)

    def test_deepseek_prompt_does_not_apply_to_other_characters(self):
        self.provider.settings.provider='deepseek'
        self.provider.settings.prompt_version='linyu-v1.2'
        _,conv=self.req('POST','/api/conversations',{'character_id':'luye'})
        self.cid=conv['id']
        self.wait(self.start(self.message('你好'))['request']['id'])
        system=self.provider.inputs[-1][0]['content']
        self.assertNotIn('你是林屿，25岁',system)

    def test_persona_context_and_profile_isolation(self):
        first=self.complete();self.assertEqual(first['request']['status'],'adopted')
        source=self.message('我刚才说喜欢喝什么？');second=self.start(source);self.wait(second['request']['id'])
        context=json.dumps(self.provider.inputs[-1],ensure_ascii=False)
        self.assertIn('林屿',context);self.assertIn('温柔',context);self.assertIn('我喜欢无糖红茶',context)
        self.assertIn('模拟回复甲',context);self.assertNotIn('PRIVATE_NAME',context)
        with self.app.db.connect() as c:
            row=c.execute('SELECT context_hash,context_message_ids FROM generation_requests WHERE id=?',(second['request']['id'],)).fetchone()
            self.assertEqual(len(row['context_hash']),64);self.assertIn(source,json.loads(row['context_message_ids']))
    def test_duplicate_generation_and_user_message(self):
        source=self.message(key='same_message_key')
        first=self.start(source,key='same_generation_key');second=self.start(source,key='same_generation_key')
        self.assertEqual(first['request']['id'],second['request']['id']);self.wait(first['request']['id'])
        self.assertEqual(len(self.provider.inputs),1)
        with self.app.db.connect() as c:self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND role='user'",(self.cid,)).fetchone()[0],1)
    def test_failure_retry_preserves_source_and_is_idempotent(self):
        self.provider.plans=[['已生成片段',ModelError('provider_interrupted')],['恢复后的完整回复']]
        started=self.start();rid=started['request']['id'];failed=self.wait(rid)
        self.assertEqual(failed['request']['status'],'failed');self.assertEqual(failed['message']['content'],'已生成片段')
        r,retry=self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/retry',{'attempt':1})
        self.assertEqual(r.status,202);self.wait(rid)
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/retry',{'attempt':1})
        self.assertEqual(self.snap(rid)['request']['attempt'],2);self.assertEqual(len(self.provider.inputs),2)
        with self.app.db.connect() as c:self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND role='user'",(self.cid,)).fetchone()[0],1)
    def test_stop_freezes_even_noncooperative_late_output(self):
        gate=threading.Event();self.provider.plans=[['第一段🙂',gate,'绝不可追加']]
        started=self.start();rid=started['request']['id']
        deadline=time.monotonic()+2
        while not self.snap(rid)['message']['content'] and time.monotonic()<deadline:time.sleep(.005)
        r,stopped=self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/stop',{'visible_chars':3})
        self.assertEqual(r.status,200);self.assertEqual(stopped['message']['content'],'第一段');gate.set();time.sleep(.06)
        self.assertEqual(self.snap(rid)['message']['content'],'第一段');self.assertEqual(self.snap(rid)['request']['status'],'stopped')
    def test_later_user_closes_incomplete_recovery(self):
        self.provider.plans=[[ModelError('provider_connection')]]
        first=self.start();self.wait(first['request']['id']);self.message('换个话题')
        r,_=self.req('POST','/api/conversations/'+self.cid+'/generations/'+first['request']['id']+'/retry',{'attempt':1})
        self.assertEqual(r.status,409)
    def test_candidate_keeps_original_until_adoption_then_invalidates_sources(self):
        first=self.complete();old=first['message']['id'];source=first['request']['user_message_id']
        with self.app.db.connect() as c:
            c.execute('INSERT INTO memories(id,conversation_id,kind,fact_scope,content,status,primary_source_id,confirmed_by,confirmed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('m',self.cid,'experience','real_user','测试事件','confirmed',old,'a',1,1,1))
            c.execute("UPDATE memories SET story_scene='书店' WHERE id='m'")
        candidate=self.start(source,'regenerate',old);rid=candidate['request']['id'];self.wait(rid)
        _,messages=self.req('GET','/api/conversations/'+self.cid+'/messages')
        self.assertIn(old,[x['id'] for x in messages['items']]);self.assertNotIn(candidate['message']['id'],[x['id'] for x in messages['items']])
        r,_=self.req('POST','/api/conversations/'+self.cid+'/messages',{'content':'不能越过未决候选','client_request_id':'blocked_message'})
        self.assertEqual(r.status,409)
        r,_=self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/choice',{'choice':'adopt'});self.assertEqual(r.status,200)
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/choice',{'choice':'adopt'})
        with self.app.db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM messages WHERE id=?',(old,)).fetchone()[0],'replaced')
            self.assertEqual(c.execute("SELECT status FROM memories WHERE id='m'").fetchone()[0],'invalidated')
    def test_candidate_cancel_ignores_late_result(self):
        first=self.complete();gate=threading.Event();self.provider.plans=[['未采用文字',gate,'迟到结果']]
        candidate=self.start(first['request']['user_message_id'],'regenerate',first['message']['id']);rid=candidate['request']['id']
        r,_=self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/choice',{'choice':'keep'});self.assertEqual(r.status,200)
        gate.set();time.sleep(.06);self.assertEqual(self.snap(rid)['request']['status'],'kept')
        with self.app.db.connect() as c:self.assertEqual(c.execute('SELECT status FROM messages WHERE id=?',(first['message']['id'],)).fetchone()[0],'completed')
    def test_old_failed_candidate_cannot_revive_after_newer_adoption(self):
        first=self.complete();source=first['request']['user_message_id'];original=first['message']['id']
        self.provider.plans=[[ModelError('provider_interrupted')]]
        old=self.start(source,'regenerate',original);self.wait(old['request']['id'])
        newer=self.start(source,'regenerate',original);self.wait(newer['request']['id'])
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+newer['request']['id']+'/choice',{'choice':'adopt'})
        r,data=self.req('POST','/api/conversations/'+self.cid+'/generations/'+old['request']['id']+'/retry',{'attempt':1})
        self.assertEqual(r.status,409);self.assertEqual(data['error']['code'],'original_replaced')

    def test_late_stop_cannot_revive_replaced_reply_or_pollute_context(self):
        self.provider.plans=[['OLD_REPLACED_MUST_NOT_RETURN'],['NEW_ADOPTED_VALID_REPLY'],['NEXT_REPLY']]
        first=self.complete();old=first['message']['id'];rid=first['request']['id']
        with self.app.db.connect() as c:
            c.execute('INSERT INTO memories(id,conversation_id,kind,fact_scope,content,status,primary_source_id,confirmed_by,confirmed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      ('late-stop-memory',self.cid,'user_fact','real_user','测试来源','confirmed',old,'a',1,1,1))
        candidate=self.start(first['request']['user_message_id'],'regenerate',old)
        chosen_id=candidate['request']['id'];self.wait(chosen_id)
        r,_=self.req('POST',f'/api/conversations/{self.cid}/generations/{chosen_id}/choice',{'choice':'adopt'})
        self.assertEqual(r.status,200)
        before=self.snap(rid);chosen_before=self.snap(chosen_id)
        with self.app.db.connect() as c:
            audit_before=dict(c.execute('SELECT * FROM model_calls WHERE generation_request_id=?',(rid,)).fetchone())
        self.assertEqual(before['message']['status'],'replaced')
        for visible in (8,0):
            r,stopped=self.req('POST',f'/api/conversations/{self.cid}/generations/{rid}/stop',{'visible_chars':visible})
            self.assertEqual(r.status,200)
            self.assertEqual(stopped,before)
            self.assertEqual(self.snap(chosen_id),chosen_before)
        with self.app.db.connect() as c:
            self.assertEqual(dict(c.execute('SELECT * FROM model_calls WHERE generation_request_id=?',(rid,)).fetchone()),audit_before)
            self.assertEqual(c.execute("SELECT status FROM memories WHERE id='late-stop-memory'").fetchone()[0],'invalidated')
        _,messages=self.req('GET',f'/api/conversations/{self.cid}/messages')
        self.assertNotIn(old,[m['id'] for m in messages['items']])
        self.assertIn(candidate['message']['id'],[m['id'] for m in messages['items']])
        self.assertNotIn('OLD_REPL',json.dumps(messages))
        following=self.start(self.message('接着聊刚才的话题'));self.wait(following['request']['id'])
        context=json.dumps(self.provider.inputs[-1])
        self.assertNotIn('OLD_REPL',context)
        self.assertIn('NEW_ADOPTED_VALID_REPLY',context)
        self.assertEqual(self.snap(chosen_id)['message']['status'],'completed')
        self.assertEqual(len(self.provider.inputs),3)

    def test_completed_buffer_stop_still_truncates_and_retries_same_turn(self):
        self.provider.plans=[['COMPLETED_BUFFER_TEXT'],['RETRY_VALID_REPLY']]
        first=self.complete();rid=first['request']['id']
        r,stopped=self.req('POST',f'/api/conversations/{self.cid}/generations/{rid}/stop',{'visible_chars':8})
        self.assertEqual(r.status,200)
        self.assertEqual(stopped['request']['status'],'stopped')
        self.assertEqual(stopped['message']['status'],'stopped')
        self.assertEqual(stopped['message']['content'],'COMPLETE')
        r,again=self.req('POST',f'/api/conversations/{self.cid}/generations/{rid}/stop',{'visible_chars':0})
        self.assertEqual(again,stopped)
        r,_=self.req('POST',f'/api/conversations/{self.cid}/generations/{rid}/retry',{'attempt':1})
        self.assertEqual(r.status,202);self.wait(rid)
        self.req('POST',f'/api/conversations/{self.cid}/generations/{rid}/retry',{'attempt':1})
        self.assertEqual(self.snap(rid)['request']['attempt'],2)
        self.assertEqual(len(self.provider.inputs),2)
        with self.app.db.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND role='user'",(self.cid,)).fetchone()[0],1)

    def test_completed_stop_after_later_user_has_no_effect(self):
        first=self.complete();rid=first['request']['id'];self.message('下一轮已经提交')
        r,stopped=self.req('POST',f'/api/conversations/{self.cid}/generations/{rid}/stop',{'visible_chars':0})
        self.assertEqual(r.status,200);self.assertEqual(stopped,first)
        self.assertEqual(len(self.provider.inputs),1)

    def test_failed_candidate_never_enters_future_context(self):
        first=self.complete();self.provider.plans=[['FAILED_CANDIDATE_CANARY',ModelError('provider_interrupted')]]
        candidate=self.start(first['request']['user_message_id'],'regenerate',first['message']['id']);self.wait(candidate['request']['id'])
        source=self.message('继续原来的话题');next_request=self.start(source);self.wait(next_request['request']['id'])
        self.assertNotIn('FAILED_CANDIDATE_CANARY',json.dumps(self.provider.inputs[-1]))
    def test_cross_account_all_generation_controls_and_unavailable_role(self):
        first=self.complete();rid=first['request']['id'];root='/api/conversations/'+self.cid+'/generations/'+rid
        for method,suffix,body in [('GET','',{}),('GET','/stream',{}),('POST','/stop',{}),('POST','/retry',{'attempt':1}),('POST','/choice',{'choice':'keep'})]:
            r,_=self.req(method,root+suffix,body,user='b');self.assertEqual(r.status,404)
        _,conv=self.req('POST','/api/conversations',{'character_id':'luye'});cid=conv['id']
        _,msg=self.req('POST','/api/conversations/'+cid+'/messages',{'content':'你好','client_request_id':'other_role_message'})
        with self.app.db.connect() as c:c.execute("UPDATE characters SET available=0 WHERE id='luye'")
        r,_=self.req('POST','/api/conversations/'+cid+'/generations',{'user_message_id':msg['id'],'client_request_id':'other_role_generation'})
        self.assertEqual(r.status,409)
    def test_subscribed_snapshots_do_not_trigger_model_requests(self):
        started=self.start();rid=started['request']['id']
        r,events=self.req('GET','/api/conversations/'+self.cid+'/generations/'+rid+'/stream')
        data=b''.join(events);self.assertIn(b'event: snapshot',data);self.assertEqual(len(self.provider.inputs),1)
    def test_actual_http_sse_flush_and_stop(self):
        gate=threading.Event();self.provider.plans=[['HTTP片段',gate,'不得继续']]
        started=self.start();rid=started['request']['id']
        server=make_server(self.app,host='127.0.0.1',port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            connection.request('GET','/api/conversations/'+self.cid+'/generations/'+rid+'/stream',
                               headers={'Host':'test.local','Cookie':'weiwan_session='+self.sessions['a'][0]})
            response=connection.getresponse()
            self.assertEqual(response.status,200);self.assertIn('text/event-stream',response.getheader('Content-Type'))
            self.assertEqual(response.readline(),b'event: snapshot\n')
            self.assertTrue(response.readline().startswith(b'data: '))
            self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/stop',{'visible_chars':0})
            gate.set();remaining=response.read();self.assertIn(b'stopped',remaining)
        finally:
            gate.set();connection.close();server.shutdown();server.server_close();thread.join()

    def test_logout_stops_active_request(self):
        gate=threading.Event();self.provider.plans=[['前段',gate,'不再追加']]
        first=self.start();rid=first['request']['id']
        self.req('POST','/api/auth/logout');gate.set();time.sleep(.04)
        self.assertEqual(self.snap(rid)['request']['status'],'stopped')
    def test_error_and_usage_audit_do_not_log_content(self):
        self.provider.plans=[['PRIVATE_TOKEN_TEXT',RuntimeError('SECRET_EXCEPTION_PAYLOAD')]]
        first=self.start();rid=first['request']['id'];self.wait(rid)
        log=(self.root/'logs/app.jsonl').read_text()
        self.assertNotIn('PRIVATE_TOKEN_TEXT',log);self.assertNotIn('SECRET_EXCEPTION_PAYLOAD',log)
        with self.app.db.connect() as c:
            row=c.execute('SELECT * FROM model_calls WHERE generation_request_id=?',(rid,)).fetchone()
            self.assertEqual(row['status'],'failed');self.assertIsNone(row['estimated_cost']);self.assertEqual(row['usage_complete'],0)
    def test_call_audit_records_latency_and_cache_hits(self):
        # Roadmap 1.8 / 1.15: completed and failed attempts both keep timing; cache hits are copied from usage.
        original=self.provider.stream
        def cached(messages,cancel):
            for event in original(messages,cancel):
                yield {'usage':dict(event['usage'],cached_input_tokens=60)} if 'usage' in event else event
        self.provider.stream=cached
        rid=self.start()['request']['id'];self.wait(rid)
        self.provider.stream=original
        self.provider.plans=[['半句',RuntimeError('upstream')]]
        failed=self.start()['request']['id'];self.wait(failed)
        with self.app.db.connect() as c:
            ok=c.execute('SELECT * FROM model_calls WHERE generation_request_id=?',(rid,)).fetchone()
            bad=c.execute('SELECT * FROM model_calls WHERE generation_request_id=?',(failed,)).fetchone()
        self.assertEqual(ok['status'],'completed');self.assertEqual(ok['cached_input_tokens'],60)
        self.assertIsNotNone(ok['first_token_ms']);self.assertGreaterEqual(ok['duration_ms'],ok['first_token_ms'])
        self.assertEqual(bad['status'],'failed');self.assertIsNone(bad['cached_input_tokens'])
        self.assertIsNotNone(bad['first_token_ms']);self.assertIsNotNone(bad['duration_ms'])
    def test_bound_version_used_in_model_context(self):
        definition=json.loads((Path(__file__).resolve().parents[1]/'backend/seed_characters.json').read_text())[0]
        definition['version']=2;definition['private']['character_card']='PRIVATE_NEW_VERSION_CANARY'
        self.app.service.publish(definition);self.complete()
        self.assertNotIn('PRIVATE_NEW_VERSION_CANARY',json.dumps(self.provider.inputs[-1]))
    def test_unconfigured_is_explicit_and_does_not_fake_reply(self):
        self.app.chat.provider=CompatibleProvider();source=self.message()
        r,_=self.req('POST','/api/conversations/'+self.cid+'/generations',{'user_message_id':source,'client_request_id':'unconfigured_test'})
        self.assertEqual(r.status,503);self.assertEqual(self.provider.inputs,[])


class AdapterTest(unittest.TestCase):
    def event(self,obj):return ('data: '+json.dumps(obj,ensure_ascii=False)+'\n\n').encode()
    def test_sse_delta_usage_and_explicit_end(self):
        data=self.event({'choices':[{'delta':{'content':'你好🙂'},'finish_reason':None}]})
        data+=self.event({'choices':[{'delta':{},'finish_reason':'stop'}]})
        data+=self.event({'choices':[],'usage':{'prompt_tokens':12,'completion_tokens':3}})+b'data: [DONE]\n\n'
        events=list(parse_stream(io.BytesIO(data),Cancellation(),time.monotonic()+2))
        self.assertEqual(events[0]['delta'],'你好🙂');self.assertEqual(events[-1]['usage']['input_tokens'],12)
    def test_prefix_cache_hits_are_reported_only_when_plausible(self):
        # Roadmap 1.15: DeepSeek and OpenAI-style cache fields; absent or impossible values stay unreported.
        def usage(extra):
            data=self.event({'choices':[{'delta':{'content':'好'},'finish_reason':'stop'}]})
            data+=self.event({'choices':[],'usage':dict({'prompt_tokens':100,'completion_tokens':3},**extra)})+b'data: [DONE]\n\n'
            return [e for e in parse_stream(io.BytesIO(data),Cancellation(),time.monotonic()+2) if 'usage' in e][0]['usage']
        self.assertEqual(usage({'prompt_cache_hit_tokens':64})['cached_input_tokens'],64)
        self.assertEqual(usage({'prompt_tokens_details':{'cached_tokens':32}})['cached_input_tokens'],32)
        self.assertNotIn('cached_input_tokens',usage({}))
        self.assertNotIn('cached_input_tokens',usage({'prompt_cache_hit_tokens':101}))
    def test_early_eof_malformed_and_token_cutoff_are_failures(self):
        examples=[b'data: bad-json\n\n',self.event({'choices':[{'delta':{'content':'未完成'},'finish_reason':None}]}),
                  self.event({'choices':[{'delta':{},'finish_reason':'length'}]})+b'data: [DONE]\n\n']
        for data in examples:
            with self.subTest(data=data):
                with self.assertRaises(ModelError):list(parse_stream(io.BytesIO(data),Cancellation(),time.monotonic()+2))
    def test_server_adapter_uses_only_explicit_file_and_qwen_parameters(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'test-only.key';path.write_text('TEST_CREDENTIAL_NOT_REAL')
            settings=ModelSettings(True,'aliyun-qwen','qwen-flash','https://dashscope.aliyuncs.com/compatible-mode/v1',str(path),'test',None)
            response=io.BytesIO(self.event({'choices':[{'delta':{'content':'答复'},'finish_reason':'stop'}]})+b'data: [DONE]\n\n')
            response.status=200;response.getheader=lambda name,default='':'text/event-stream'
            capture={}
            class Connection:
                sock=None
                def __init__(self,*a,**k):pass
                def request(self,method,url,body,headers):capture.update({'url':url,'body':json.loads(body),'headers':headers})
                def getresponse(self):return response
                def close(self):pass
            with patch('backend.model.http.client.HTTPSConnection',Connection):
                events=list(CompatibleProvider(settings).stream([{'role':'user','content':'测试'}],Cancellation()))
            self.assertTrue(events);self.assertEqual(capture['url'],'/compatible-mode/v1/chat/completions')
            self.assertEqual(capture['body']['model'],'qwen-flash');self.assertFalse(capture['body']['enable_thinking'])
            self.assertNotIn('TEST_CREDENTIAL_NOT_REAL',json.dumps(events))

if __name__=='__main__':unittest.main()
