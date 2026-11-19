import json,unittest
from pathlib import Path
import test_foundation as base
from backend.auth import password_hash

class RoleReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.encoded=password_hash(base.PASSWORD)
    setUp=base.FoundationTest.setUp
    tearDown=base.FoundationTest.tearDown
    req=base.FoundationTest.req
    conv=base.FoundationTest.conv
    def definition(self,role='linyu'):
        root=Path(__file__).resolve().parent/'fixtures'/'role_versions'
        return json.loads((root/(role+'-v2.json')).read_text())
    def test_release_keeps_old_binding_and_new_users_get_v2(self):
        old=self.conv();self.assertEqual(old['version'],1)
        for role in ['linyu','luye','shenyan']:self.app.service.publish(self.definition(role))
        same=self.conv();new=self.conv(user='b');self.assertEqual(same['id'],old['id']);self.assertEqual(same['version'],1);self.assertEqual(new['version'],2)
        with self.assertRaises(ValueError):self.app.service.publish(self.definition())
        self.assertEqual(self.app.service.characters()[0]['version'],2)
    def test_character_art_comes_from_the_spec_and_other_files_are_blocked(self):
        # Character art is served from the 不晚 visual spec (/buwan/, see test_visual_spec); the old PNG route is gone.
        for role in ['linyu','luye','shenyan']:
            r=self.app.handle('GET','/buwan/assets/characters/'+role+'.jpg',{'Host':'test.local'})
            self.assertEqual(r.status,200);self.assertEqual(r.headers['Content-Type'],'image/jpeg');self.assertTrue(r.body.startswith(b'\xff\xd8'))
        for path in ['/assets/characters/linyu-v2.png','/assets/characters/../../.env','/buwan/assets/characters/../../README.md','/buwan/assets/characters/unknown.jpg','/assets/characters/%2e%2e/backend/config.py']:
            r=self.app.handle('GET',path,{'Host':'test.local'});self.assertEqual(r.status,404)
    def test_private_speech_examples_stay_private_and_identities_fixed(self):
        for role in ['linyu','luye','shenyan']:
            d=self.definition(role);self.app.service.publish(d)
            public=self.app.service.character(role);self.assertEqual(public['age'],d['public']['age']);self.assertNotIn('dialogue_examples',public);self.assertNotIn('private',public)
            self.assertTrue(d['private']['dialogue_style']);self.assertEqual(len(d['private']['dialogue_examples']),5)
    def test_structured_reply_hides_protocol_and_preserves_usage(self):
        from types import SimpleNamespace
        from backend.model import Cancellation
        self.app.chat.provider=SimpleNamespace(stream=lambda m,c:iter([{'delta':'{"reply":"'}, {'delta':'好，先不分析。"}'},{'finish_reason':'stop'},{'usage':{'input_tokens':20,'output_tokens':10}}]))
        messages=[{'role':'system','content':'WEIWAN_JSON_DIALOGUE_V1\nJSON'}]
        events=list(self.app.chat.dialogue_events(messages,Cancellation()))
        self.assertEqual(''.join(x.get('delta','') for x in events),'好，先不分析。');self.assertIn({'usage':{'input_tokens':20,'output_tokens':10}},events);self.assertEqual(events[-1],{'finish_reason':'stop'});self.assertEqual(events[:3],[{},{},{}])
    def test_structured_invalid_or_extra_fields_fail_without_leaking_json(self):
        from types import SimpleNamespace
        from backend.model import Cancellation,ModelError
        for text in ['not json','{"reply":"hi","extra":1}','{"reply":12}','{"reply":" "}']:
            self.app.chat.provider=SimpleNamespace(stream=lambda m,c:iter([{'delta':text},{'finish_reason':'stop'}]))
            emitted=[]
            with self.assertRaises(ModelError) as error:
                for x in self.app.chat.dialogue_events([{'role':'system','content':'WEIWAN_JSON_DIALOGUE_V1\nJSON'}],Cancellation()):emitted.append(x)
            self.assertEqual(error.exception.code,'provider_dialogue_protocol');self.assertFalse(any('delta' in x for x in emitted))
    def test_nonstructured_old_versions_keep_streaming(self):
        from types import SimpleNamespace
        from backend.model import Cancellation
        planned=[{'delta':'原'}, {'delta':'回复'},{'finish_reason':'stop'}]
        self.app.chat.provider=SimpleNamespace(stream=lambda m,c:iter(planned))
        self.assertEqual(list(self.app.chat.dialogue_events([{'role':'system','content':'old version'}],Cancellation())),planned)
    def test_failed_structured_protocol_keeps_actual_usage_in_audit(self):
        import time
        from test_chat import FakeProvider
        root=Path(__file__).resolve().parent/'fixtures'/'role_versions'
        self.app.service.publish(json.loads((root/'linyu-v4.json').read_text()))
        provider=FakeProvider();provider.plans=[['1234']];self.app.chat.provider=provider
        cid=self.conv()['id'];_,use=self.req('POST',f'/api/conversations/{cid}/uses',{})
        _,message=self.req('POST',f'/api/conversations/{cid}/messages',{'content':'你好','client_request_id':'structured_usage_source','use_id':use['id']})
        response,g=self.req('POST',f'/api/conversations/{cid}/generations',{'user_message_id':message['id'],'client_request_id':'structured_usage_generation','use_id':use['id']});self.assertEqual(response.status,202)
        for _ in range(100):
            _,g=self.req('GET',f'/api/conversations/{cid}/generations/'+g['request']['id']+'?use_id='+use['id'])
            if g['request']['status']=='failed' and not self.app.chat.controls:break
            time.sleep(.01)
        self.assertEqual(g['request']['error_code'],'provider_dialogue_protocol');self.assertEqual(g['message']['content'],'')
        with self.db.connect() as c:r=c.execute('SELECT * FROM model_calls WHERE generation_request_id=?',(g['request']['id'],)).fetchone()
        self.assertEqual((r['status'],r['input_tokens'],r['output_tokens'],r['usage_complete']),('failed',100,20,1))
