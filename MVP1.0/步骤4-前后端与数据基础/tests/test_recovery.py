# -*- coding: utf-8 -*-
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_chat as helpers
from backend.api import Application
from backend.model import ModelError


class RecoveryTest(unittest.TestCase):
    setUpClass=classmethod(helpers.ChatTest.setUpClass.__func__)
    setUp=helpers.ChatTest.setUp
    tearDown=helpers.ChatTest.tearDown
    req=helpers.ChatTest.req
    message=helpers.ChatTest.message
    start=helpers.ChatTest.start
    snap=helpers.ChatTest.snap
    wait=helpers.ChatTest.wait
    complete=helpers.ChatTest.complete
    def recovery(self,query='',app=None,user='a'):
        r,data=self.req('GET','/api/conversations/'+self.cid+'/recovery'+query,user=user,app=app)
        self.assertEqual(r.status,200,data);return data
    def test_completed_restore_order_time_identity_and_no_model_call(self):
        first=self.complete();calls=len(self.provider.inputs)
        for _ in range(3):
            data=self.recovery()
            self.assertEqual(data['conversation_id'],self.cid);self.assertEqual(data['character_id'],'linyu')
            self.assertEqual(data['generation']['request']['id'],first['request']['id'])
            self.assertEqual(data['items'][-1]['content'],first['message']['content'])
            self.assertTrue(all(isinstance(m['created_at'],int) and isinstance(m['updated_at'],int) for m in data['items']))
            self.assertEqual([m['ordinal'] for m in data['items']],sorted(m['ordinal'] for m in data['items']))
        self.assertEqual(calls,len(self.provider.inputs))
    def test_saved_user_without_generation_is_explicit_and_idempotent(self):
        source=self.message('保存后关闭但还没开始回复','restore_saved_user')
        data=self.recovery('?client_request_id=restore_saved_user')
        self.assertEqual(data['submitted_message']['id'],source);self.assertTrue(data['reply_required'])
        self.assertIsNone(data['generation']['request']);self.assertEqual(len(self.provider.inputs),0)
        one=self.start(source,key='reply_'+source);two=self.start(source,key='reply_'+source)
        self.assertEqual(one['request']['id'],two['request']['id']);self.wait(one['request']['id'])
        data=self.recovery('?client_request_id=restore_saved_user');self.assertFalse(data['reply_required'])
        self.assertEqual(len([m for m in data['items'] if m['role']=='user']),1)
    def test_active_generation_restores_without_new_request(self):
        gate=threading.Event();self.provider.plans=[['已持久化片段',gate,'完整结尾']]
        first=self.start();rid=first['request']['id']
        try:
            limit=time.monotonic()+2
            while not self.snap(rid)['message']['content'] and time.monotonic()<limit:time.sleep(.005)
            for _ in range(3):
                data=self.recovery();self.assertEqual(data['generation']['request']['id'],rid)
                self.assertEqual(data['generation']['request']['status'],'generating')
                self.assertEqual(data['generation']['message']['content'],'已持久化片段')
            self.assertEqual(len(self.provider.inputs),1)
        finally:gate.set()
        self.wait(rid)
    def test_process_interruption_marks_partial_failed_and_retains_sources(self):
        gate=threading.Event();self.provider.plans=[['进程中断前片段',gate,'OLD_LATE_EVENT']]
        first=self.start();rid=first['request']['id']
        limit=time.monotonic()+2
        while not self.snap(rid)['message']['content'] and time.monotonic()<limit:time.sleep(.005)
        restarted=Application(self.config,provider=self.provider)
        data=self.recovery(app=restarted)
        self.assertEqual(data['generation']['request']['status'],'failed')
        self.assertEqual(data['generation']['request']['error_code'],'process_interrupted')
        self.assertEqual(data['generation']['message']['content'],'进程中断前片段')
        gate.set();time.sleep(.03)
        r,retried=self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/retry',{'attempt':1},app=restarted)
        self.assertEqual(r.status,202)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            data=self.recovery(app=restarted)
            if data['generation']['request']['status']=='adopted':break
            time.sleep(.01)
        self.assertEqual(data['generation']['request']['status'],'adopted')
        self.assertNotIn('OLD_LATE_EVENT',data['generation']['message']['content'])
        self.assertEqual(len([m for m in data['items'] if m['role']=='user']),1)
    def test_stopped_and_failed_survive_restore(self):
        self.provider.plans=[['失败片段',ModelError('provider_interrupted')]]
        first=self.start();rid=first['request']['id'];self.wait(rid)
        data=self.recovery();self.assertEqual(data['generation']['request']['status'],'failed')
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/retry',{'attempt':1});self.wait(rid)
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/stop',{'attempt':2,'visible_chars':2})
        data=self.recovery();self.assertEqual(data['generation']['request']['status'],'stopped')
        self.assertEqual(data['items'][-1]['content'],'模拟')
        before=json.dumps(data,ensure_ascii=False);again=self.recovery();self.assertEqual(before,json.dumps(again,ensure_ascii=False))
    def test_candidate_ready_kept_and_adopted_recover_only_valid_content(self):
        self.provider.plans=[['OLD_REPLACED_CANARY'],['VALID_NEW_REPLY']]
        first=self.complete();candidate=self.start(first['request']['user_message_id'],'regenerate',first['message']['id'],key='restore_candidate_key')
        rid=candidate['request']['id'];self.wait(rid)
        ready=self.recovery('?generation_key=restore_candidate_key')
        self.assertEqual(ready['generation']['request']['status'],'awaiting_choice')
        self.assertEqual(ready['requested_generation']['request']['id'],rid)
        self.assertIn('OLD_REPLACED_CANARY',[m['content'] for m in ready['items']])
        self.assertNotIn('VALID_NEW_REPLY',[m['content'] for m in ready['items']])
        restarted=Application(self.config,provider=self.provider);ready=self.recovery(app=restarted)
        self.assertEqual(ready['generation']['request']['status'],'awaiting_choice')
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/choice',{'choice':'adopt'},app=restarted)
        adopted=self.recovery(app=restarted)
        self.assertIn('VALID_NEW_REPLY',[m['content'] for m in adopted['items']])
        self.assertNotIn('OLD_REPLACED_CANARY',[m['content'] for m in adopted['items']])
        # Check the actual next provider input after restoration and explicit continued chat.
        _,source=self.req('POST','/api/conversations/'+self.cid+'/messages',{'content':'继续聊','client_request_id':'resume_after_adoption'},app=restarted)
        _,next_task=self.req('POST','/api/conversations/'+self.cid+'/generations',{'user_message_id':source['id'],'client_request_id':'generation_after_restore'},app=restarted)
        deadline=time.monotonic()+3
        while len(self.provider.inputs)<3 and time.monotonic()<deadline:time.sleep(.01)
        context=json.dumps(self.provider.inputs[-1])
        self.assertIn('VALID_NEW_REPLY',context);self.assertNotIn('OLD_REPLACED_CANARY',context)
        # The resumed Application owns this worker, so the original fixture's
        # tearDown cannot wait for it. Wait for persisted completion and worker
        # release before removing the shared temporary database.
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            _,finished=self.req('GET','/api/conversations/'+self.cid+'/generations/'+next_task['request']['id'],app=restarted)
            if finished['request']['status']=='adopted' and not restarted.chat.controls:break
            time.sleep(.01)
        self.assertEqual(finished['request']['status'],'adopted')
        self.assertFalse(restarted.chat.controls)
    def test_cancelled_and_failed_candidates_not_in_summary_or_context(self):
        self.provider.plans=[['ORIGINAL_VALID'],['CANDIDATE_NOT_ADOPTED']]
        first=self.complete();candidate=self.start(first['request']['user_message_id'],'regenerate',first['message']['id']);self.wait(candidate['request']['id'])
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+candidate['request']['id']+'/choice',{'choice':'keep'})
        data=self.recovery();self.assertEqual(data['generation']['request']['status'],'kept')
        self.assertNotIn('CANDIDATE_NOT_ADOPTED',json.dumps(data['items']))
        _,listed=self.req('GET','/api/conversations');self.assertEqual(listed['items'][0]['preview'],'ORIGINAL_VALID')
    def test_unknown_user_save_and_generation_lookup_are_scoped(self):
        source=self.message(key='recover_same_client_key');first=self.start(source,key='recover_generation_key');self.wait(first['request']['id'])
        found=self.recovery('?client_request_id=recover_same_client_key&generation_key=recover_generation_key')
        self.assertEqual(found['submitted_message']['id'],source);self.assertEqual(found['submitted_generation']['request']['id'],first['request']['id'])
        r,_=self.req('GET','/api/conversations/'+self.cid+'/recovery?client_request_id=recover_same_client_key',user='b');self.assertEqual(r.status,404)
        _,other=self.req('POST','/api/conversations',{'character_id':'luye'})
        _,data=self.req('GET','/api/conversations/'+other['id']+'/recovery?client_request_id=recover_same_client_key')
        self.assertIsNone(data['submitted_message']);self.assertIsNone(data['generation']['request'])
    def test_old_stop_attempt_does_not_modify_new_retry(self):
        self.provider.plans=[[ModelError('provider_connection')],['NEW_ATTEMPT_VALID']]
        first=self.start();rid=first['request']['id'];self.wait(rid)
        self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/retry',{'attempt':1});self.wait(rid)
        before=self.snap(rid);r,_=self.req('POST','/api/conversations/'+self.cid+'/generations/'+rid+'/stop',{'attempt':1,'visible_chars':0})
        self.assertEqual(r.status,409);self.assertEqual(before,self.snap(rid))
    def test_old_anchor_window_restores_reading_without_losing_latest_turn(self):
        ids=[]
        for i in range(160):ids.append(self.message('历史消息'+str(i),key='history_request_'+str(i)))
        data=self.recovery('?anchor='+ids[5])
        self.assertTrue(data['anchor_found']);self.assertTrue(data['has_newer'])
        self.assertIn(ids[5],[m['id'] for m in data['items']])
        self.assertEqual(data['last_user_message']['id'],ids[-1]);self.assertTrue(data['reply_required'])
        latest=self.recovery();self.assertEqual(latest['items'][-1]['id'],ids[-1]);self.assertFalse(latest['has_newer'])
    def test_timestamp_day_boundary_is_storage_not_a_model_memory_claim(self):
        with patch('backend.service.now',return_value=1794587400):source=self.message('跨日测试事实')
        data=self.recovery();self.assertEqual(next(m for m in data['items'] if m['id']==source)['created_at'],1794587400)
        self.assertEqual(self.recovery()['items'],data['items'])
    def test_bad_recovery_identifiers_rejected_without_private_data(self):
        for query in ('?anchor=bad','?client_request_id=x','?generation_key=%21'):
            r,data=self.req('GET','/api/conversations/'+self.cid+'/recovery'+query)
            self.assertEqual(r.status,422);self.assertNotIn('items',data)

if __name__=='__main__':unittest.main()
