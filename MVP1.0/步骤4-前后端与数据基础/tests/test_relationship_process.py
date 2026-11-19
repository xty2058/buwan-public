import http.client,json,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
class RelationshipProcessTest(unittest.TestCase):
    def test_persistent_revocation_survives_real_process_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            origin='http://127.0.0.1:'+str(port);cookie=None;csrf=None;process=None
            def request(method,path,body=None):
                c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);headers={'Origin':origin,'Content-Type':'application/json'}
                if cookie:headers['Cookie']=cookie
                if csrf:headers['X-CSRF-Token']=csrf
                c.request(method,path,json.dumps(body or {}),headers);r=c.getresponse();status=r.status;data=json.loads(r.read());new_cookie=r.getheader('Set-Cookie');c.close();return status,data,new_cookie
            def boot():
                p=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('recovery_process_fixture.py')),directory,str(port)],env={**os.environ,'STEP5_ALLOW_LIVE_CALLS':'false'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                for _ in range(150):
                    if p.poll() is not None:raise AssertionError('process exited')
                    try:
                        if request('GET','/api/health')[0]==200:return p
                    except OSError:time.sleep(.02)
                p.kill();p.wait();raise AssertionError('readiness timeout')
            try:
                process=boot();_,login,new=request('POST','/api/auth/register',{'username':'relation_process','password':'Test-only-relation-process-2026','nickname':'process'});cookie=new.split(';')[0];csrf=login['csrf_token']
                _,conv,_=request('POST','/api/conversations',{'character_id':'linyu'});cid=conv['id'];base='/api/conversations/'+cid
                _,use,_=request('POST',base+'/uses',{})
                status,chosen,_=request('POST',base+'/relationships',{'action':'set_relation','target':'romantic','store':True,'revision':0,'use_id':use['id'],'client_request_id':'persistent_romance_process'});self.assertEqual(status,200)
                status,revoked,_=request('POST',base+'/relationships',{'action':'revoke','target':'unspecified','store':False,'revision':chosen['state']['revision'],'use_id':use['id'],'client_request_id':'persistent_revoke_process'});self.assertEqual(status,200)
                process.kill();process.wait(timeout=5);process=boot()
                _,newuse,_=request('POST',base+'/uses',{});_,restored,_=request('GET',base+'/relationships?use_id='+newuse['id'])
                self.assertEqual(restored['state']['relationship_type'],'unspecified');self.assertTrue(restored['state']['intimacy_paused']);self.assertFalse(any(e['kind']=='revocation' for e in restored['events']));self.assertTrue(any(e['status']=='invalidated' for e in restored['events']))
                # Replaying the old save operation cannot change current authoritative state.
                request('POST',base+'/relationships',{'action':'set_relation','target':'romantic','store':True,'revision':0,'use_id':use['id'],'client_request_id':'persistent_romance_process'})
                _,again,_=request('GET',base+'/relationships');self.assertEqual(again['state']['relationship_type'],'unspecified')
            finally:
                if process and process.poll() is None:process.kill();process.wait(timeout=5)
