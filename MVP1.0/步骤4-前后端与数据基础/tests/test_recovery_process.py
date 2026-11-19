# -*- coding: utf-8 -*-
import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

class ProcessRecoveryTest(unittest.TestCase):
    def test_killed_generation_restart_candidate_choice_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            origin='http://127.0.0.1:'+str(port);cookie=None;csrf=None;process=None
            fixture=Path(__file__).with_name('recovery_process_fixture.py')
            def request(method,path,body=None):
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                headers={'Origin':origin,'Content-Type':'application/json'}
                if cookie:headers['Cookie']=cookie
                if csrf:headers['X-CSRF-Token']=csrf
                connection.request(method,path,json.dumps(body or {}),headers)
                response=connection.getresponse();data=json.loads(response.read());status=response.status;new_cookie=response.getheader('Set-Cookie');connection.close()
                return status,data,new_cookie
            def boot(block=False):
                command=[sys.executable,'-B',str(fixture),directory,str(port)]+(['block'] if block else [])
                child=subprocess.Popen(command,env={**os.environ,'STEP5_ALLOW_LIVE_CALLS':'false'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                for _ in range(150):
                    if child.poll() is not None:raise AssertionError('Fixture exited before readiness')
                    try:
                        if request('GET','/api/health')[0]==200:return child
                    except OSError:time.sleep(.02)
                child.kill();child.wait();raise AssertionError('Fixture readiness timeout')
            def wait_status(cid,expected):
                for _ in range(150):
                    status,data,_=request('GET','/api/conversations/'+cid+'/recovery')
                    if data.get('generation',{}).get('request',{}).get('status') in expected:return data
                    time.sleep(.01)
                raise AssertionError('Generation status did not settle')
            try:
                process=boot(True)
                status,login,new_cookie=request('POST','/api/auth/register',{'username':'process_user','password':'Test-only-crash-2026!','nickname':'进程验收'})
                self.assertEqual(status,201);cookie=new_cookie.split(';')[0];csrf=login['csrf_token']
                _,conv,_=request('POST','/api/conversations',{'character_id':'linyu'});cid=conv['id']
                _,source,_=request('POST','/api/conversations/'+cid+'/messages',{'content':'进程恢复原消息','client_request_id':'stable_process_message'})
                _,task,_=request('POST','/api/conversations/'+cid+'/generations',{'user_message_id':source['id'],'client_request_id':'stable_process_generation'})
                data=wait_status(cid,('generating',))
                for _ in range(100):
                    _,data,_=request('GET','/api/conversations/'+cid+'/recovery')
                    if data['generation']['message']['content']=='PERSISTED_BEFORE_CRASH':break
                    time.sleep(.01)
                self.assertEqual(data['generation']['message']['content'],'PERSISTED_BEFORE_CRASH')
                process.kill();process.wait(timeout=5);process=boot(False)
                _,restored,_=request('GET','/api/conversations/'+cid+'/recovery?client_request_id=stable_process_message')
                self.assertEqual(restored['generation']['request']['status'],'failed')
                self.assertEqual(restored['generation']['request']['error_code'],'process_interrupted')
                self.assertEqual(restored['generation']['message']['content'],'PERSISTED_BEFORE_CRASH')
                self.assertEqual(restored['submitted_message']['id'],source['id'])
                rid=task['request']['id']
                request('POST','/api/conversations/'+cid+'/generations/'+rid+'/retry',{'attempt':1})
                settled=wait_status(cid,('adopted',));original=settled['generation']['message']['id']
                self.assertEqual(settled['generation']['message']['content'],'VALID_AFTER_RESTART')
                _,candidate,_=request('POST','/api/conversations/'+cid+'/generations',{'kind':'regenerate','user_message_id':source['id'],'original_reply_id':original,'client_request_id':'candidate_process_restore'})
                wait_status(cid,('awaiting_choice',));candidate_id=candidate['request']['id']
                process.kill();process.wait(timeout=5);process=boot(False)
                _,ready,_=request('GET','/api/conversations/'+cid+'/recovery')
                self.assertEqual(ready['generation']['request']['status'],'awaiting_choice')
                self.assertEqual(ready['generation']['message']['content'],'NEW_CANDIDATE_AFTER_RESTART')
                self.assertEqual(len([m for m in ready['items'] if m['role']=='user']),1)
                self.assertEqual(len([m for m in ready['items'] if m['content']=='VALID_AFTER_RESTART']),1)
                request('POST','/api/conversations/'+cid+'/generations/'+candidate_id+'/choice',{'choice':'adopt'})
                _,chosen,_=request('GET','/api/conversations/'+cid+'/recovery')
                self.assertNotIn(original,[m['id'] for m in chosen['items']])
                self.assertIn('NEW_CANDIDATE_AFTER_RESTART',[m['content'] for m in chosen['items']])
                _,next_source,_=request('POST','/api/conversations/'+cid+'/messages',{'content':'恢复后继续','client_request_id':'next_process_message'})
                _,next_task,_=request('POST','/api/conversations/'+cid+'/generations',{'user_message_id':next_source['id'],'client_request_id':'next_process_generation'})
                wait_status(cid,('adopted',))
                c=sqlite3.connect(str(Path(directory)/'process.sqlite3'))
                source_ids=json.loads(c.execute('SELECT context_message_ids FROM generation_requests WHERE id=?',(next_task['request']['id'],)).fetchone()[0])
                self.assertNotIn(original,source_ids);self.assertIn(candidate['message']['id'],source_ids)
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[]);c.close()
            finally:
                if process and process.poll() is None:process.kill();process.wait(timeout=5)

if __name__=='__main__':unittest.main()
