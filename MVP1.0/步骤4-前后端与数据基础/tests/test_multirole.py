# -*- coding: utf-8 -*-
import copy,json,time,uuid,unittest
import test_chat as helpers
from test_memory import FakeEmbedding
from backend.model import ModelError
class MultiRoleTest(unittest.TestCase):
    setUpClass=classmethod(helpers.ChatTest.setUpClass.__func__)
    setUp=helpers.ChatTest.setUp;tearDown=helpers.ChatTest.tearDown;req=helpers.ChatTest.req
    def convo(self,role,user='a'):
        _,d=self.req('POST','/api/conversations',{'character_id':role},user=user);return d['id']
    def talk(self,cid,text,key=None):
        r,m=self.req('POST','/api/conversations/'+cid+'/messages',{'content':text,'client_request_id':key or 'msg_'+uuid.uuid4().hex});self.assertEqual(r.status,201,m)
        r,g=self.req('POST','/api/conversations/'+cid+'/generations',{'user_message_id':m['id'],'client_request_id':'gen_'+uuid.uuid4().hex});self.assertEqual(r.status,202,g)
        until=time.monotonic()+4
        while time.monotonic()<until:
            d=self.app.chat.snapshot('a',cid,g['request']['id'])
            if d['request']['status'] in ('adopted','failed','awaiting_choice','stopped'):return d
            time.sleep(.005)
        self.fail('generation timeout')
    def test_three_bound_cards_histories_and_scoped_memory_relation_inputs(self):
        self.app.memory.embedding=FakeEmbedding();roles=['linyu','luye','shenyan'];names=['林屿','陆野','沈砚'];jobs=['独立书店','独立乐队','建筑设计师'];cids=[];sources=[];memories=[]
        for role in roles:
            cid=self.convo(role);cids.append(cid)
            reply=self.talk(cid,'ROLE_PRIVATE_'+role);self.assertEqual(reply['request']['status'],'adopted');sources.append(reply['request']['user_message_id'])
            mid=uuid.uuid4().hex;memories.append(mid)
            with self.app.db.connect() as c:
                c.execute("INSERT INTO memories(id,conversation_id,kind,fact_scope,content,status,primary_source_id,confirmed_by,confirmed_at,created_at,updated_at) VALUES(?,?,'user_fact','real_user',?,'confirmed',?,'a',1,1,1)",(mid,cid,'MEMORY_PRIVATE_'+role,sources[-1]));c.execute('INSERT INTO memory_sources VALUES(?,?,?)',(mid,cid,sources[-1]))
            self.app.memory.index(mid,1)
        token=self.sessions['a'][0];user=self.app.auth.authenticate(token)
        use=self.app.service.open_use(user,cids[1])['id'];self.app.relationship.mutate(user,cids[1],{'action':'set_relation','target':'friends','store':True,'use_id':use,'revision':0,'client_request_id':'role_private_relation'})
        for i,(role,cid) in enumerate(zip(roles,cids)):
            g=self.talk(cid,'根据有效信息，继续聊一句');actual=self.provider.inputs[-1];system=actual[0]['content'];raw=json.dumps(actual,ensure_ascii=False)
            self.assertIn(names[i],system);self.assertIn(jobs[i],system)
            for j,other in enumerate(roles):
                if j==i:continue
                self.assertNotIn(names[j],system);self.assertNotIn('ROLE_PRIVATE_'+other,raw);self.assertNotIn('MEMORY_PRIVATE_'+other,raw)
            self.assertIn('MEMORY_PRIVATE_'+role,raw)
            self.assertIn('当前关系类型：'+('朋友' if i==1 else '未约定'),system)
            _,recovery=self.req('GET','/api/conversations/'+cid+'/recovery');self.assertEqual(recovery['character_id'],role);self.assertEqual(recovery['generation']['request']['id'],g['request']['id'])
            for other_cid,source,mid in zip(cids,sources,memories):
                if other_cid==cid:continue
                r,_=self.req('POST','/api/conversations/'+cid+'/generations',{'user_message_id':source,'client_request_id':'cross_'+uuid.uuid4().hex});self.assertEqual(r.status,404)
                r,_=self.req('POST','/api/conversations/'+cid+'/memories/'+mid,{'action':'delete','revision':1});self.assertEqual(r.status,404)
            r,_=self.req('GET','/api/conversations/'+cid+'/recovery',user='b');self.assertEqual(r.status,404)
    def test_new_published_version_cannot_change_existing_role_name_card_or_opening(self):
        cid=self.convo('luye')
        with self.app.db.connect() as c:
            old=dict(c.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone());version=c.execute('SELECT * FROM character_versions WHERE id=?',(old['character_version_id'],)).fetchone();public=json.loads(version['public_profile']);private=json.loads(version['private_definition'])
        private['character_card']+='\nNEW_VERSION_ONLY_CANARY';public['name']='陆野新版本';public['opening']='NEW_OPENING_ONLY_CANARY'
        new_id=self.app.service.publish({'slug':'luye','version':2,'public':public,'private':private})
        self.talk(cid,'你叫什么，做什么工作');system=self.provider.inputs[-1][0]['content'];self.assertNotIn('NEW_VERSION_ONLY_CANARY',system);self.assertNotIn('陆野新版本',system)
        other=self.convo('luye','b')
        with self.app.db.connect() as c:
            self.assertEqual(c.execute('SELECT character_version_id FROM conversations WHERE id=?',(cid,)).fetchone()[0],old['character_version_id']);self.assertEqual(c.execute('SELECT character_version_id FROM conversations WHERE id=?',(other,)).fetchone()[0],new_id)
            self.assertNotIn('NEW_OPENING_ONLY_CANARY',[r[0] for r in c.execute('SELECT content FROM messages WHERE conversation_id=?',(cid,))])
    def test_luye_failed_retry_and_shenyan_candidate_adoption(self):
        cid=self.convo('luye');self.provider.plans=[['LUYE_PARTIAL',ModelError('provider_interrupted')]];g=self.talk(cid,'失败后仍保持当前角色');self.assertEqual(g['request']['status'],'failed')
        r,retry=self.req('POST','/api/conversations/'+cid+'/generations/'+g['request']['id']+'/retry',{'attempt':1});self.assertEqual(r.status,202)
        until=time.monotonic()+3
        while time.monotonic()<until:
            current=self.app.chat.snapshot('a',cid,g['request']['id'])
            if current['request']['status']=='adopted':break
            time.sleep(.01)
        self.assertEqual(current['request']['status'],'adopted')
        cid=self.convo('shenyan');g=self.talk(cid,'先聊一句');self.provider.plans=[['SHENYAN_VALID_CANDIDATE']]
        r,candidate=self.req('POST','/api/conversations/'+cid+'/generations',{'user_message_id':g['request']['user_message_id'],'kind':'regenerate','original_reply_id':g['message']['id'],'client_request_id':'shenyan_candidate_test'});self.assertEqual(r.status,202)
        until=time.monotonic()+3
        while time.monotonic()<until:
            current=self.app.chat.snapshot('a',cid,candidate['request']['id'])
            if current['request']['status']=='awaiting_choice':break
            time.sleep(.01)
        self.req('POST','/api/conversations/'+cid+'/generations/'+candidate['request']['id']+'/choice',{'choice':'adopt'})
        self.talk(cid,'继续');raw=json.dumps(self.provider.inputs[-1],ensure_ascii=False);self.assertIn('SHENYAN_VALID_CANDIDATE',raw);self.assertIn('沈砚',raw);self.assertNotIn('LUYE_PARTIAL',raw)
    def test_all_roles_restore_saved_user_gap_without_starting_model(self):
        for role in ('linyu','luye','shenyan'):
            cid=self.convo(role);_,m=self.req('POST','/api/conversations/'+cid+'/messages',{'content':'仅保存尚未生成','client_request_id':'same_role_scoped_save_key'});before=len(self.provider.inputs)
            _,d=self.req('GET','/api/conversations/'+cid+'/recovery?client_request_id=same_role_scoped_save_key');self.assertTrue(d['reply_required']);self.assertEqual(d['submitted_message']['id'],m['id']);self.assertEqual(len(self.provider.inputs),before)
    def test_fixed_identity_anchor_preserves_history_but_does_not_adopt_wrong_age(self):
        self.provider.plans=[['我错误地说成了26岁']];cid=self.convo('linyu');first=self.talk(cid,'简单自我介绍')
        self.talk(cid,'核对自己的固定年龄，不修改人设')
        system=self.provider.inputs[-1][0]['content'];self.assertIn('"固定年龄": 25',system);self.assertIn('不按当前日期推算',system)
        self.assertIn('我错误地说成了26岁',json.dumps(self.provider.inputs[-1],ensure_ascii=False))
        with self.app.db.connect() as c:self.assertEqual(c.execute('SELECT content FROM messages WHERE id=?',(first['message']['id'],)).fetchone()[0],'我错误地说成了26岁')
