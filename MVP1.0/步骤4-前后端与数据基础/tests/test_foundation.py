import http.client
import json
import sqlite3
import os
import socket
import subprocess
import sys
import time
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from backend.api import Application
from backend.model import CompatibleProvider
from backend.auth import digest, password_hash, verify
from backend.config import Config
from backend.server import make_server

PASSWORD='Local-test-only-2026!'

class FoundationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.encoded=password_hash(PASSWORD)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.config=Config(db=self.root/'test.sqlite3',log_dir=self.root/'logs',origin='http://test.local')
        self.app=Application(self.config, provider=CompatibleProvider());self.db=self.app.db;self.sessions={}
        for user in ('a','b'):
            with self.db.connect() as c:
                c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(user,'user_'+user,self.encoded,'昵称'+user,1,1))
                self.sessions[user]=self.app.auth.issue(c,user)
    def tearDown(self):
        for h in list(self.app.logger.handlers):h.close();self.app.logger.removeHandler(h)
        self.temp.cleanup()
    def req(self,method,path,data=None,user='a',headers=None,app=None):
        h={'Host':'test.local','Origin':'http://test.local','Content-Type':'application/json'}
        if user:
            token,csrf=self.sessions[user];h.update({'Cookie':'weiwan_session='+token,'X-CSRF-Token':csrf})
        h.update(headers or {});r=(app or self.app).handle(method,path,h,json.dumps(data if data is not None else {}).encode())
        return r,json.loads(r.body)
    def conv(self,role='linyu',user='a'):
        r,d=self.req('POST','/api/conversations',{'character_id':role},user);self.assertIn(r.status,(200,201));return d
    def msg(self,cid,text='PRIVATE_CHAT_CANARY',key='request_test_123',user='a'):
        return self.req('POST','/api/conversations/'+cid+'/messages',{'content':text,'client_request_id':key},user)
    def memory(self,cid,source,user='a'):
        with self.db.connect() as c:
            c.execute('INSERT INTO memories(id,conversation_id,kind,fact_scope,content,status,primary_source_id,confirmed_by,confirmed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('mem-'+cid,cid,'user_fact','real_user','红茶','confirmed',source,user,1,1,1))
    def test_register_login_hash_cookie(self):
        r,d=self.req('POST','/api/auth/register',{'username':'NEW_USER','password':PASSWORD,'nickname':'旅人'},None)
        self.assertEqual(r.status,201);self.assertEqual(d['user']['username'],'new_user')
        self.assertIn('HttpOnly',r.headers['Set-Cookie']);self.assertIn('SameSite=Lax',r.headers['Set-Cookie'])
        token=r.headers['Set-Cookie'].split(';')[0].split('=',1)[1]
        with self.db.connect() as c:
            p=c.execute("SELECT password_hash FROM users WHERE username='new_user'").fetchone()[0]
            t=c.execute('SELECT token_hash FROM auth_sessions WHERE user_id=?',(d['user']['id'],)).fetchone()[0]
        self.assertTrue(verify(PASSWORD,p));self.assertNotEqual(p,PASSWORD);self.assertEqual(t,digest(token));self.assertNotEqual(t,token)
        r,_=self.req('POST','/api/auth/login',{'username':'NEW_USER','password':PASSWORD},None);self.assertEqual(r.status,200)
        r,_=self.req('POST','/api/auth/login',{'username':'new_user','password':PASSWORD+'wrong'},None);self.assertEqual(r.status,401)
    def test_validation_and_duplicate_username(self):
        for p in ({'username':'ab','password':PASSWORD,'nickname':'客人'},{'username':'okay','password':'short','nickname':'客人'},{'username':'okay','password':PASSWORD,'nickname':''}):
            r,_=self.req('POST','/api/auth/register',p,None);self.assertEqual(r.status,422)
        r,_=self.req('POST','/api/auth/register',{'username':'USER_A','password':PASSWORD,'nickname':'客人'},None);self.assertEqual(r.status,409)
    def test_login_accepts_existing_short_credential_without_relaxing_registration(self):
        with self.db.connect() as c:
            c.execute("UPDATE users SET password_hash=? WHERE username='user_a'",(password_hash('888888'),))
        r,d=self.req('POST','/api/auth/login',{'username':'user_a','password':'888888'},None)
        self.assertEqual(r.status,200);self.assertEqual(d['user']['username'],'user_a')
        r,_=self.req('POST','/api/auth/login',{'username':'user_a','password':'888889'},None);self.assertEqual(r.status,401)
        r,_=self.req('POST','/api/auth/register',{'username':'new_short','password':'888888','nickname':'客人'},None);self.assertEqual(r.status,422)
    def test_persistent_auth_throttle(self):
        for _ in range(8):self.app.auth.throttle('limited','test-ip')
        restarted=Application(self.config, provider=CompatibleProvider())
        with self.assertRaises(Exception) as caught:restarted.auth.throttle('limited','test-ip')
        self.assertEqual(caught.exception.status,429)
    def test_csrf_origin_host(self):
        for h,status in [({'X-CSRF-Token':''},403),({'Origin':'http://evil.local'},403),({'Host':'evil.local'},421),({'Sec-Fetch-Site':'cross-site'},403)]:
            r,_=self.req('PATCH','/api/profile',{'nickname':'新昵称'},headers=h);self.assertEqual(r.status,status)
        r,_=self.req('PATCH','/api/profile',{'nickname':'新昵称'});self.assertEqual(r.status,200)
    def test_logout_and_expiry_and_rotation(self):
        cid=self.conv()['id'];_,use=self.req('POST','/api/conversations/'+cid+'/uses')
        r,_=self.req('POST','/api/auth/logout');self.assertEqual(r.status,200)
        r,_=self.req('GET','/api/conversations');self.assertEqual(r.status,401)
        with self.db.connect() as c:
            self.assertIsNotNone(c.execute('SELECT closed_at FROM conversation_uses WHERE id=?',(use['id'],)).fetchone()[0])
            c.execute('UPDATE auth_sessions SET expires_at=1 WHERE token_hash=?',(digest(self.sessions['b'][0]),))
        _,d=self.req('GET','/api/auth/session',user='b');self.assertIsNone(d['user'])
        r,_=self.req('POST','/api/auth/login',{'username':'user_b','password':PASSWORD},user='b');self.assertEqual(r.status,200)
        r,_=self.req('GET','/api/conversations',user='b');self.assertEqual(r.status,401)
    def test_public_role_whitelist(self):
        r,d=self.req('GET','/api/characters',user=None);self.assertEqual(r.status,200)
        self.assertEqual([x['name'] for x in d['items']],['林屿','陆野','沈砚'])
        for secret in ('private_definition','shared_rules','character_card'):self.assertNotIn(secret,r.body.decode())
        _,d=self.req('GET','/api/health',user=None);self.assertFalse(d['capabilities']['chat_generation'])
    def test_published_version_and_conversation_binding(self):
        old=self.conv();self.assertEqual(old['id'],self.conv()['id'])
        definition=json.loads((Path(__file__).resolve().parents[1]/'backend/seed_characters.json').read_text())[0]
        definition['version']=2;definition['public']['bio']='新版介绍';self.app.service.publish(definition)
        self.assertEqual(self.conv()['version'],1);self.assertTrue(self.conv()['has_newer_version']);self.assertEqual(self.conv(user='b')['version'],2)
        with self.db.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('UPDATE character_versions SET version=99 WHERE id=?',(old['character_version_id'],))
            with self.assertRaises(sqlite3.IntegrityError):c.execute("UPDATE conversations SET character_id='luye' WHERE id=?",(old['id'],))
    def test_parallel_idempotency(self):
        with ThreadPoolExecutor(max_workers=4) as pool:created=list(pool.map(lambda _:self.app.service.create_conversation('a',{'character_id':'linyu'})[0],range(4)))
        self.assertEqual(len(set(created)),1);cid=created[0]
        with ThreadPoolExecutor(max_workers=4) as pool:saved=list(pool.map(lambda _:self.app.service.save_message('a',cid,{'content':'并发测试','client_request_id':'parallel_test'})[0]['id'],range(4)))
        self.assertEqual(len(set(saved)),1)
        r,_=self.msg(cid,'不同内容','parallel_test');self.assertEqual(r.status,409)
    def test_cross_account_and_role_access(self):
        first=self.conv()['id'];second=self.conv('luye')['id'];_,msg=self.msg(first);self.memory(first,msg['id'])
        for suffix in ('','/messages','/memories','/relationships'):
            r,_=self.req('GET','/api/conversations/'+first+suffix,user='b');self.assertEqual(r.status,404)
        r,_=self.msg(first,user='b');self.assertEqual(r.status,404)
        r,_=self.req('POST','/api/conversations/'+first+'/uses',user='b');self.assertEqual(r.status,404)
        _,d=self.req('GET','/api/conversations/'+second+'/memories');self.assertEqual(d['items'],[])
        _,d=self.req('GET','/api/conversations/'+second+'/messages');self.assertNotIn('PRIVATE_CHAT_CANARY',json.dumps(d))
        _,d=self.req('GET','/api/conversations',user='b');self.assertEqual(d['items'],[])
    def test_composite_source_and_confirmation_constraints(self):
        first=self.conv()['id'];second=self.conv('luye')['id'];_,msg=self.msg(first)
        with self.assertRaises(sqlite3.IntegrityError):self.memory(second,msg['id'])
        with self.assertRaises(sqlite3.IntegrityError):self.memory(first,msg['id'],user='b')
        with self.db.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('INSERT INTO relationship_events(id,conversation_id,kind,content,status,primary_source_id,occurred_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',('bad',second,'experience','不能串用','completed',msg['id'],1,1,1))
    def test_adjusted_scene_does_not_invent_encounter(self):
        r,d=self.req('POST','/api/conversations',{'character_id':'linyu','initial_scene':'在公共阅读活动第一次见面。'});self.assertEqual(r.status,201)
        _,messages=self.req('GET','/api/conversations/'+d['id']+'/messages')
        self.assertEqual(messages['items'][0]['role'],'system');self.assertNotIn('外面雨',messages['items'][0]['content'])
    def test_restart_durability(self):
        cid=self.conv()['id'];_,msg=self.msg(cid);self.memory(cid,msg['id'])
        with self.db.connect() as c:
            c.execute('INSERT INTO relationship_events(id,conversation_id,kind,content,status,primary_source_id,consent_message_id,storage_consent_message_id,confirmed_by,long_term,occurred_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('event',cid,'relationship','朋友','accepted',msg['id'],msg['id'],msg['id'],'a',1,1,1,1))
            c.execute("UPDATE relationship_states SET relationship_type='friends',familiarity='familiar',basis_event_id='event' WHERE conversation_id=?",(cid,))
        restarted=Application(self.config, provider=CompatibleProvider())
        _,d=self.req('GET','/api/conversations/'+cid+'/messages',app=restarted);self.assertEqual(d['items'][-1]['content'],'PRIVATE_CHAT_CANARY')
        _,d=self.req('GET','/api/conversations/'+cid+'/memories',app=restarted);self.assertEqual(d['items'][0]['content'],'红茶')
        _,d=self.req('GET','/api/conversations/'+cid+'/relationships',app=restarted);self.assertEqual(d['state']['relationship_type'],'friends');self.assertEqual(len(d['events']),1)
        with restarted.db.connect() as c:self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_use_resume_close_and_owner(self):
        cid=self.conv()['id'];_,first=self.req('POST','/api/conversations/'+cid+'/uses')
        _,resumed=self.req('POST','/api/conversations/'+cid+'/uses',{'use_id':first['id']});self.assertEqual(first['id'],resumed['id'])
        self.req('POST','/api/conversations/'+cid+'/uses/'+first['id']+'/close')
        r,_=self.req('POST','/api/conversations/'+cid+'/uses',{'use_id':first['id']});self.assertEqual(r.status,409)
        with self.db.connect() as c:
            other=c.execute("SELECT id FROM auth_sessions WHERE user_id='b'").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):c.execute('INSERT INTO conversation_uses(id,conversation_id,auth_session_id,opened_at,last_seen_at) VALUES(?,?,?,?,?)',('bad',cid,other,1,1))
    def test_use_drops_stale_temporary_relation_after_revocation(self):
        cid=self.conv()['id'];_,use=self.req('POST','/api/conversations/'+cid+'/uses')
        with self.db.connect() as c:
            c.execute("UPDATE conversation_uses SET temporary_relation='romantic' WHERE id=?",(use['id'],))
            c.execute('UPDATE relationship_states SET revocation_revision=1 WHERE conversation_id=?',(cid,))
        r,resumed=self.req('POST','/api/conversations/'+cid+'/uses',{'use_id':use['id']})
        self.assertEqual(r.status,200);self.assertIsNone(resumed['temporary_relation']);self.assertEqual(resumed['based_on_revocation_revision'],1)

    def test_pagination_and_invalid_json_body_limits(self):
        cid=self.conv()['id']
        for i in range(3):self.msg(cid,'消息'+str(i),'request_'+str(i))
        _,d=self.req('GET','/api/conversations/'+cid+'/messages?limit=2');self.assertEqual([x['ordinal'] for x in d['items']],[3,4]);self.assertTrue(d['has_more'])
        r,_=self.req('GET','/api/conversations/'+cid+'/messages?before=bad');self.assertEqual(r.status,422)
        r,_=self.req('POST','/api/conversations',data=['array']);self.assertEqual(r.status,400)
        r,_=self.req('POST','/api/conversations',data={'big':'x'*17000});self.assertEqual(r.status,413)
    def test_cursor_sqlite_integer_bounds(self):
        cid=self.conv()['id']
        self.msg(cid)
        for cursor in ('-1','0','bad',str(2**63),'9'*1000,'9'*5000):
            with self.subTest(cursor_length=len(cursor),prefix=cursor[:20]):
                r,d=self.req('GET',f'/api/conversations/{cid}/messages?before={cursor}')
                self.assertEqual(r.status,422)
                self.assertEqual(d['error']['code'],'invalid_cursor')
        for cursor in ('1',str(2**63-1)):
            r,d=self.req('GET',f'/api/conversations/{cid}/messages?before={cursor}')
            self.assertEqual(r.status,200)
        _,latest=self.req('GET',f'/api/conversations/{cid}/messages?limit=1')
        _,earlier=self.req('GET',f"/api/conversations/{cid}/messages?before={latest['next_before']}&limit=1")
        self.assertEqual([m['ordinal'] for m in latest['items']],[2])
        self.assertEqual([m['ordinal'] for m in earlier['items']],[1])

    def test_log_redaction_and_error_correlation(self):
        cid=self.conv()['id'];self.msg(cid)
        self.req('POST','/api/auth/login',{'username':'user_a','password':'PRIVATE_PASSWORD_CANARY'},None)
        self.req('GET','/api/health?secret=PRIVATE_QUERY_CANARY')
        original=self.app.service.profile
        def broken(*args):raise RuntimeError('PRIVATE_EXCEPTION_CANARY')
        self.app.service.profile=broken
        r,d=self.req('PATCH','/api/profile',{'nickname':'PRIVATE_NICKNAME_CANARY'});self.assertEqual(r.status,500);self.assertEqual(d['request_id'],r.headers['X-Request-ID'])
        self.app.service.profile=original
        self.req('POST','/api/client-errors',{'code':'window_error','component':'chat','message':'PRIVATE_FRONTEND_CANARY'})
        log=(self.config.log_dir/'app.jsonl').read_text()
        for secret in ('PRIVATE_CHAT_CANARY','PRIVATE_PASSWORD_CANARY','PRIVATE_QUERY_CANARY','PRIVATE_EXCEPTION_CANARY','PRIVATE_NICKNAME_CANARY','PRIVATE_FRONTEND_CANARY',*self.sessions['a']):self.assertNotIn(secret,log)
        records=[json.loads(line) for line in log.splitlines()];error=next(x for x in records if x['event']=='server_error');self.assertEqual(error['exception_type'],'RuntimeError');self.assertTrue(error['frames'])
    def test_migrations_repeat_and_checksum(self):
        self.db.migrate();self.app.service.seed()
        with self.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM character_versions').fetchone()[0],3)
            c.execute("UPDATE schema_migrations SET checksum='wrong'")
        with self.assertRaises(RuntimeError):self.db.migrate()
    def test_real_process_restart(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        origin='http://127.0.0.1:'+str(port)
        env={**os.environ,'APP_HOST':'127.0.0.1','APP_PORT':str(port),'APP_ORIGIN':origin,
             'APP_DB':str(self.config.db),'APP_LOG_DIR':str(self.config.log_dir),'APP_COOKIE_SECURE':'false','STEP5_ALLOW_LIVE_CALLS':'false'}
        root=Path(__file__).resolve().parents[1]
        def start():
            process=subprocess.Popen([sys.executable,'-B','-m','backend.server'],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for _ in range(100):
                if process.poll() is not None:raise AssertionError('Development process did not start')
                try:
                    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=1);connection.request('GET','/api/health')
                    response=connection.getresponse();response.read();connection.close()
                    if response.status==200:return process
                except OSError:time.sleep(.03)
            process.terminate();process.wait(timeout=5);raise AssertionError('Process readiness timeout')
        def request(method,path,body=None,cookie=None,csrf=None):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            headers={'Origin':origin,'Content-Type':'application/json'}
            if cookie:headers['Cookie']=cookie
            if csrf:headers['X-CSRF-Token']=csrf
            connection.request(method,path,body=json.dumps(body or {}),headers=headers)
            response=connection.getresponse();data=json.loads(response.read());saved_cookie=response.getheader('Set-Cookie');status=response.status;connection.close()
            return status,data,saved_cookie
        process=start()
        try:
            status,login,cookie=request('POST','/api/auth/login',{'username':'user_a','password':PASSWORD})
            self.assertEqual(status,200);cookie=cookie.split(';')[0];csrf=login['csrf_token']
            _,conv,_=request('POST','/api/conversations',{'character_id':'linyu'},cookie,csrf)
            status,_,_=request('POST','/api/conversations/'+conv['id']+'/messages',{'content':'process restart survives','client_request_id':'restart_request'},cookie,csrf)
            self.assertEqual(status,201)
            process.terminate();process.wait(timeout=5);process=start()
            status,data,_=request('GET','/api/conversations/'+conv['id']+'/messages',cookie=cookie)
            self.assertEqual(status,200);self.assertEqual(data['items'][-1]['content'],'process restart survives')
        finally:
            process.terminate();process.wait(timeout=5)

    def test_real_http_and_static_boundary(self):
        server=make_server(self.app,host='127.0.0.1',port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for path,status in [('/',200),('/app.js',200),('/style.css',200),('/api/health',200),('/runtime/test.sqlite3',404)]:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);conn.request('GET',path,headers={'Host':'test.local'})
                r=conn.getresponse();r.read();self.assertEqual(r.status,status);self.assertIsNotNone(r.getheader('Content-Security-Policy'));conn.close()
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
