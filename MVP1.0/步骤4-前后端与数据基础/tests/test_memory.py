# -*- coding: utf-8 -*-
import json,threading,time,unittest,uuid
import test_chat as helpers
from backend.model import ModelError
from backend.memory import Memory,explicit_storage,supported_assertion

class FakeEmbedding:
    configured=True;mode='simulated';model='test-semantic-fixture';dimensions=3
    def __init__(self):self.calls=[];self.gate=None;self.fail=False
    def embed(self,texts):
        self.calls+=texts
        if self.gate:self.gate.wait(3)
        if self.fail:raise ModelError('embedding_fixture_failure')
        def vector(t):
            if any(x in t for x in ('茶','饮品','咖啡')):return [1.,0.,.01]
            if any(x in t for x in ('篮球','运动')):return [0.,1.,.01]
            return [0.,0.,1.]
        return [vector(t) for t in texts],10

class MemoryTest(unittest.TestCase):
    setUpClass=classmethod(helpers.ChatTest.setUpClass.__func__)
    setUp=helpers.ChatTest.setUp;tearDown=helpers.ChatTest.tearDown
    req=helpers.ChatTest.req;message=helpers.ChatTest.message;start=helpers.ChatTest.start;snap=helpers.ChatTest.snap;wait=helpers.ChatTest.wait;unused_complete=helpers.ChatTest.complete
    def complete(self,source=None):
        r=self.start(source);return self.wait(r['request']['id'])
    def setup_memory(self):
        self.embed=FakeEmbedding();self.mem=self.app.memory;self.mem.embedding=self.embed
    def candidate(self,text='我喜欢乌龙茶',topic='饮品偏好',kind='user_fact',scope='real_user',source=None,proof=None):
        if not hasattr(self,'mem'):self.setup_memory()
        source=source or self.message(text,'memory_'+uuid.uuid4().hex)
        with self.app.db.connect() as c:
            s=dict(c.execute('SELECT * FROM messages WHERE id=?',(source,)).fetchone())
            sources=[s]+(proof or [])
            jid=uuid.uuid4().hex
            # A test-only effective assistant reply, distinct from speculative/candidate output.
            reply_id=uuid.uuid4().hex;ordinal=c.execute('SELECT max(ordinal)+1 FROM messages WHERE conversation_id=?',(self.cid,)).fetchone()[0]
            c.execute("INSERT INTO messages(id,conversation_id,ordinal,role,content,status,reply_to_id,created_at,updated_at) VALUES(?,?,?,?,?,'completed',?,?,?)",(reply_id,self.cid,ordinal,'assistant','明白',source,1,1))
            base=self.mem.epoch(c,self.cid);c.execute('INSERT INTO memory_jobs VALUES(?,?,?,?,?,?,?,?)',(jid,self.cid,reply_id,base,'running',None,1,1))
            job=dict(c.execute('SELECT * FROM memory_jobs WHERE id=?',(jid,)).fetchone())
        item={'content':text,'topic_key':topic,'kind':kind,'fact_scope':scope,'evidence':[{'message_id':r['id'],'quote':r['content']} for r in sources]}
        self.mem.commit_candidates(job,[item],sources)
        with self.app.db.connect() as c:
            row=c.execute('SELECT * FROM memories WHERE conversation_id=? AND content=? ORDER BY rowid DESC LIMIT 1',(self.cid,text)).fetchone()
        return dict(row) if row else None
    def mutate(self,m,action,**kw):return self.req('POST','/api/conversations/'+self.cid+'/memories/'+m['id'],{'revision':m['revision'],'action':action,**kw})
    def ready(self,mid):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with self.app.db.connect() as c:m=dict(c.execute('SELECT * FROM memories WHERE id=?',(mid,)).fetchone())
            if m['index_status'] in ('ready','failed','unconfigured'):return m
            time.sleep(.01)
        self.fail('index not finished')
    def confirm(self,m):
        r,d=self.mutate(m,'confirm',replace_conflict=True);self.assertEqual(r.status,200,d);return self.ready(m['id'])
    def test_candidates_consent_sources_and_true_extraction_protocol(self):
        self.setup_memory();source=self.message('我不喜欢咖啡','negative_tea_key');first=self.complete(source)
        extractor=helpers.FakeProvider();extractor.plans=[[json.dumps({'memories':[{'kind':'user_fact','fact_scope':'real_user','topic_key':'饮品偏好','content':'我不喜欢咖啡','evidence':[{'message_id':source,'quote':'我不喜欢咖啡'}]}]},ensure_ascii=False)]]
        self.mem.extractor=extractor;self.mem.schedule('a',self.cid,first['message']['id'])
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            data=self.mem.list('a',self.cid)
            if data['items']:break
            time.sleep(.01)
        self.assertEqual(data['items'][0]['status'],'candidate');self.assertEqual(data['items'][0]['content'],'我不喜欢咖啡')
        self.assertEqual(data['items'][0]['sources'][0]['message_id'],source)
        self.assertEqual(self.mem.retrieve(self.cid,'咖啡')['items'],[])
        self.confirm(data['items'][0]);self.assertEqual(len(self.mem.retrieve(self.cid,'饮品')['items']),1)
    def test_hypothesis_roleplay_quotation_and_positive_flip_rejected(self):
        for text in ('假如我喜欢咖啡','下面我扮演一个喜欢咖啡的人','朋友说我喜欢咖啡'):
            self.assertIsNone(self.candidate(text))
        self.setup_memory()
        item={'kind':'user_fact','fact_scope':'real_user','topic_key':'偏好','content':'我喜欢咖啡','evidence':[{'message_id':'x','quote':'我不喜欢咖啡'}]}
        self.assertIsNone(self.mem.validate_candidate(item,[{'id':'x','role':'user','content':'我不喜欢咖啡','status':'saved'}]))
        story=self.candidate('我扮演一个喜欢咖啡的人',scope='story');self.assertEqual(story['fact_scope'],'story');self.assertTrue(story['story_scene'])
    def test_denied_positive_substrings_never_become_effective(self):
        cases=(
            ('请记住：我没说我喜欢咖啡，我不喜欢咖啡。','我喜欢咖啡'),
            ('请保存：不是说我喜欢咖啡，我不喜欢咖啡。','我喜欢咖啡'),
            ('请记住：别把我喜欢咖啡当真，我不喜欢咖啡。','我喜欢咖啡'),
            ('请记住：“我喜欢咖啡”不是事实。','我喜欢咖啡'),
            ('请记住：我不喜欢咖啡。','喜欢咖啡'),
            ('请记住：我喜欢咖啡，我不喜欢咖啡。','我喜欢咖啡'),
        )
        for quote,wrong in cases:
            with self.subTest(quote=quote):
                source=self.message(quote,'denial_'+uuid.uuid4().hex)
                self.assertIsNone(self.candidate(wrong,source=source))
        self.assertEqual(self.mem.list('a',self.cid)['items'],[])
        self.assertEqual(self.mem.retrieve(self.cid,'咖啡')['items'],[])
        self.assertEqual(self.embed.calls,[])

    def test_storage_consent_binds_to_specific_supported_statement(self):
        source=self.message('请记住：我喜欢乌龙茶。我喜欢咖啡。','separate_consent')
        unrelated=self.candidate('我喜欢咖啡',source=source)
        self.assertEqual(unrelated['status'],'candidate')
        self.assertEqual(self.mem.retrieve(self.cid,'咖啡')['items'],[])
        self.assertEqual(self.embed.calls,[])
        # The negative statement is independently asserted, not part of the preceding denial.
        source=self.message('请记住：我没说我喜欢咖啡，我不喜欢咖啡。','supported_negative')
        negative=self.candidate('我不喜欢咖啡',source=source)
        self.assertIsNotNone(negative)
        self.assertEqual(negative['status'],'candidate')
        self.assertEqual(self.confirm(negative)['status'],'confirmed')

    def test_explicit_positive_and_negative_storage_remain_supported(self):
        for text in ('请记住：我喜欢乌龙茶。','请保存：我不喜欢咖啡。'):
            with self.subTest(text=text):
                # Separate topic names do not bypass the normal drink conflict policy:
                # remove the preceding test record before asserting the next consent case.
                for previous in self.mem.list('a',self.cid)['items'] if hasattr(self,'mem') else []:
                    self.mutate(previous,'delete')
                source=self.message(text,'consent_'+uuid.uuid4().hex)
                content=text.split('：')[1].rstrip('。')
                m=self.candidate(content,source=source)
                self.assertEqual(m['status'],'confirmed')
                self.assertEqual(self.ready(m['id'])['index_status'],'ready')
                self.assertEqual([x['content'] for x in self.mem.retrieve(self.cid,'咖啡饮品')['items']],[content])

    def test_negated_storage_commands_never_auto_confirm_or_embed(self):
        # The fact remains affirmative. Only the storage request is refused.
        for refusal in ('不用','不要','不必','别','无需','不需要','不想','不希望','不允许','不能','禁止','请勿','勿','不准','用不着'):
            for command in ('帮我记住','帮我保存','请保存'):
                text=refusal+command+'：我喜欢乌龙茶。'
                with self.subTest(text=text):
                    self.assertTrue(supported_assertion('我喜欢乌龙茶',text))
                    self.assertFalse(explicit_storage('我喜欢乌龙茶',text))
                    source=self.message(text,'refusal_'+uuid.uuid4().hex)
                    m=self.candidate('我喜欢乌龙茶',source=source)
                    self.assertIsNotNone(m)
                    self.assertEqual(m['status'],'candidate')
        with self.app.db.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM memories WHERE conversation_id=? AND status='confirmed'",(self.cid,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)
        self.assertEqual(self.mem.retrieve(self.cid,'乌龙茶')['items'],[])
        self.assertEqual(self.embed.calls,[])

    def test_storage_refusal_with_fillers_and_competing_commands_stays_pending(self):
        for text in (
            '请不要再帮我保存：我喜欢乌龙茶。',
            '不必麻烦你替我记住：我喜欢乌龙茶。',
            '不用，帮我记住：我喜欢乌龙茶。',
            '请记住：我喜欢乌龙茶，但不要保存。',
            '不要帮我保存：我喜欢乌龙茶。请记住：我喜欢乌龙茶。',
        ):
            with self.subTest(text=text):
                self.assertFalse(explicit_storage('我喜欢乌龙茶',text))
                source=self.message(text,'refusal_scope_'+uuid.uuid4().hex)
                m=self.candidate('我喜欢乌龙茶',source=source)
                self.assertEqual(m['status'],'candidate')
        self.assertEqual(self.mem.retrieve(self.cid,'乌龙茶')['items'],[])
        self.assertEqual(self.embed.calls,[])

    def test_quoted_reported_hypothetical_storage_is_not_authorization(self):
        for text in (
            '“请记住：我喜欢乌龙茶。”',
            '「帮我保存：我喜欢乌龙茶。」',
            '\"帮我记住：我喜欢乌龙茶。\"',
            '他说：请保存：我喜欢乌龙茶。',
            '如果我说请记住：我喜欢乌龙茶。',
            '假如以后要保存，请记住：我喜欢乌龙茶。',
            '故事里如果我说，请记住：我喜欢乌龙茶。',
        ):
            with self.subTest(text=text):
                self.assertFalse(explicit_storage('我喜欢乌龙茶',text))
                source=self.message(text,'quoted_storage_'+uuid.uuid4().hex)
                m=self.candidate('我喜欢乌龙茶',source=source)
                self.assertTrue(m is None or m['status']=='candidate')
        self.assertEqual(self.mem.retrieve(self.cid,'乌龙茶')['items'],[])
        self.assertEqual(self.embed.calls,[])

    def test_explicit_storage_command_supports_negative_facts_and_polite_requests(self):
        for command in ('请记住','请保存','帮我记住','帮我保存','请帮我记住','麻烦你帮我保存'):
            for fact in ('我喜欢乌龙茶','我不喜欢咖啡'):
                text=command+'：'+fact+'。'
                with self.subTest(text=text):
                    for previous in self.mem.list('a',self.cid)['items'] if hasattr(self,'mem') else []:
                        self.mutate(previous,'delete')
                    self.assertTrue(explicit_storage(fact,text))
                    source=self.message(text,'allowed_storage_'+uuid.uuid4().hex)
                    m=self.candidate(fact,source=source)
                    self.assertEqual(m['status'],'confirmed')
                    self.assertEqual(self.ready(m['id'])['index_status'],'ready')
                    self.assertEqual([x['content'] for x in self.mem.retrieve(self.cid,'饮品')['items']],[fact])

    def test_refusal_in_another_evidence_message_prevents_auto_confirmation(self):
        source=self.message('请记住：我喜欢乌龙茶。','older_storage_request')
        refused=self.message('不要帮我保存：我喜欢乌龙茶。','newer_storage_refusal')
        with self.app.db.connect() as c:
            proof=[dict(c.execute('SELECT * FROM messages WHERE id=?',(refused,)).fetchone())]
        m=self.candidate('我喜欢乌龙茶',source=source,proof=proof)
        self.assertEqual(m['status'],'candidate')
        self.assertEqual(self.mem.retrieve(self.cid,'乌龙茶')['items'],[])
        self.assertEqual(self.embed.calls,[])

    def test_temporary_edit_without_deadline_rejected_without_side_effects(self):
        m=self.candidate('今天暂时不聊工作',kind='boundary',topic='工作边界')
        r,d=self.mutate(m,'edit',content=m['content'])
        self.assertEqual(r.status,422);self.assertEqual(d['error']['code'],'memory_time_unresolved')
        with self.app.db.connect() as c:
            row=dict(c.execute('SELECT * FROM memories WHERE id=?',(m['id'],)).fetchone())
            self.assertEqual(row,m)
            self.assertEqual(c.execute('SELECT count(*) FROM memory_vectors WHERE memory_id=?',(m['id'],)).fetchone()[0],0)
        self.assertEqual(self.mem.retrieve(self.cid,'工作')['items'],[])
        self.assertEqual(self.embed.calls,[])

    def test_temporary_deadline_cannot_be_cleared_and_expiry_is_respected(self):
        from unittest.mock import patch
        m=self.candidate('今天暂时不聊工作',kind='boundary',topic='工作边界')
        deadline=int(time.time())+3600
        r,d=self.mutate(m,'edit',content=m['content'],valid_until=deadline)
        self.assertEqual(r.status,200);saved=self.ready(m['id'])
        self.assertEqual(saved['status'],'confirmed');self.assertEqual(saved['valid_until'],deadline)
        self.assertEqual(len(self.mem.retrieve(self.cid,'工作')['items']),1)
        r,d=self.mutate(saved,'edit',content=saved['content'],valid_until=None)
        self.assertEqual(r.status,422);self.assertEqual(d['error']['code'],'memory_time_unresolved')
        with self.app.db.connect() as c:
            self.assertEqual(dict(c.execute('SELECT * FROM memories WHERE id=?',(m['id'],)).fetchone()),saved)
        with patch('backend.memory.now',return_value=deadline+1):
            self.assertEqual(self.mem.retrieve(self.cid,'工作')['items'],[])

    def test_permanent_boundary_edited_to_temporary_requires_deadline(self):
        m=self.confirm(self.candidate('不要叫我笨蛋',kind='boundary',topic='称呼边界'))
        r,d=self.mutate(m,'edit',content='今天不要叫我笨蛋')
        self.assertEqual(r.status,422);self.assertEqual(d['error']['code'],'memory_time_unresolved')
        with self.app.db.connect() as c:
            self.assertEqual(dict(c.execute('SELECT * FROM memories WHERE id=?',(m['id'],)).fetchone()),m)
        r,d=self.mutate(m,'edit',content='今天不要叫我笨蛋',valid_until=int(time.time())+3600)
        self.assertEqual(r.status,200)
        self.assertEqual(self.ready(m['id'])['status'],'confirmed')

    def test_dedup_merges_sources_and_conflict_requires_explicit_replace(self):
        first=self.confirm(self.candidate());self.candidate()
        with self.app.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM memories WHERE conversation_id=?',(self.cid,)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM memory_sources WHERE memory_id=?',(first['id'],)).fetchone()[0],2)
        new=self.candidate('我现在更喜欢红茶');self.assertEqual(new['status'],'conflicted')
        r,d=self.mutate(new,'confirm');self.assertEqual(r.status,409)
        chosen=self.confirm(new);self.assertEqual(chosen['status'],'confirmed')
        result=self.mem.retrieve(self.cid,'饮品');self.assertEqual([x['content'] for x in result['items']],['我现在更喜欢红茶'])
    def test_rag_long_memory_proven_outside_actual_recent_context(self):
        first=self.confirm(self.candidate())
        with self.app.db.connect() as c:
            for i in range(60):
                n=c.execute('SELECT max(ordinal)+1 FROM messages WHERE conversation_id=?',(self.cid,)).fetchone()[0]
                c.execute('INSERT INTO messages(id,conversation_id,ordinal,role,content,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,self.cid,n,'user','中间无关话题'+str(i),'saved',2,2))
        request=self.complete(self.message('给我推荐符合偏好的茶','query_long_memory'))
        with self.app.db.connect() as c:
            ids=json.loads(c.execute('SELECT context_message_ids FROM generation_requests WHERE id=?',(request['request']['id'],)).fetchone()[0]);recall=json.loads(c.execute('SELECT selected FROM memory_retrievals WHERE generation_id=?',(request['request']['id'],)).fetchone()[0])
        self.assertNotIn(first['primary_source_id'],ids);self.assertEqual(recall[0]['id'],first['id']);self.assertIn('我喜欢乌龙茶',self.provider.inputs[-1][0]['content']);self.assertNotIn('我喜欢乌龙茶',''.join(x['content'] for x in self.provider.inputs[-1][1:]))
        self.assertEqual(self.mem.retrieve(self.cid,'聊篮球运动')['items'],[])
    def test_edit_delete_propagates_history_rag_and_reextract(self):
        first=self.confirm(self.candidate());oldreply=self.complete(self.message('你知道我爱喝什么茶吗','memory_echo_query'))
        # Model output that copied a recalled fact must also be excluded after correction/deletion.
        r,d=self.mutate(first,'edit',content='我现在更喜欢红茶');self.assertEqual(r.status,200);edited=self.ready(first['id'])
        nextreply=self.complete(self.message('我的饮品偏好是什么','query_after_correction'))
        actual=json.dumps(self.provider.inputs[-1],ensure_ascii=False);self.assertNotIn('乌龙茶',actual);self.assertIn('红茶',actual)
        r,d=self.mutate(edited,'delete');self.assertEqual(r.status,200)
        self.assertEqual(self.mem.retrieve(self.cid,'茶')['items'],[])
        self.complete(self.message('推荐一杯茶','query_after_deletion'))
        actual=json.dumps(self.provider.inputs[-1],ensure_ascii=False);self.assertNotIn('乌龙茶',actual);self.assertNotIn('红茶',actual)
        self.candidate('我喜欢乌龙茶',source=first['primary_source_id'])
        self.assertEqual(self.mem.list('a',self.cid)['items'],[])
        new=self.candidate('我喜欢乌龙茶');self.assertEqual(new['status'],'candidate')
    def test_rejected_source_is_not_asked_again_new_source_allowed(self):
        first=self.candidate();r,d=self.mutate(first,'reject');self.assertEqual(r.status,200)
        self.candidate(source=first['primary_source_id']);self.assertEqual(self.mem.list('a',self.cid)['items'],[])
        new=self.candidate();self.assertEqual(new['status'],'candidate')
    def test_index_delete_interleave_stale_vector_cannot_return(self):
        self.setup_memory();self.embed.gate=threading.Event();m=self.candidate();r,d=self.mutate(m,'confirm');self.assertEqual(r.status,200)
        r,d=self.req('POST','/api/conversations/'+self.cid+'/generations',{'client_request_id':'blocked_index_req','user_message_id':m['primary_source_id']});self.assertEqual(r.status,409)
        m['revision']+=1;r,d=self.mutate(m,'delete');self.assertEqual(r.status,200);self.embed.gate.set();time.sleep(.08)
        with self.app.db.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM memory_vectors WHERE memory_id=?',(m['id'],)).fetchone()[0],0)
    def test_stale_extraction_and_cross_account_direct_access(self):
        m=self.candidate()
        r,d=self.req('POST','/api/conversations/'+self.cid+'/memories/'+m['id'],{'action':'delete','revision':1},user='b');self.assertEqual(r.status,404)
        r,d=self.req('GET','/api/conversations/'+self.cid+'/memories',user='b');self.assertEqual(r.status,404)
        with self.app.db.connect() as c:
            job=dict(c.execute('SELECT * FROM memory_jobs LIMIT 1').fetchone());sources=[dict(c.execute('SELECT * FROM messages WHERE id=?',(m['primary_source_id'],)).fetchone())]
        self.mutate(m,'delete')
        with self.assertRaises(ModelError):self.mem.commit_candidates(job,[],sources)
        r,d=self.mutate(m,'confirm');self.assertEqual(r.status,409)
    def test_other_role_retrieval_empty_and_source_authorization(self):
        self.confirm(self.candidate());_,other=self.req('POST','/api/conversations',{'character_id':'luye'})
        self.assertEqual(self.mem.retrieve(other['id'],'茶')['items'],[])
        self.assertEqual(self.mem.list('a',other['id'])['items'],[])
    def test_events_require_both_sides_and_cannot_edit_plan_into_completed(self):
        self.assertIsNone(self.candidate('好，下次一起听歌',kind='promise'))
        source=self.message('你说下次一起听歌吗','event_source_key');self.provider.plans=[['下次一起听歌好吗']];first=self.complete(source)
        with self.app.db.connect() as c:proof=[dict(c.execute('SELECT * FROM messages WHERE id=?',(first['message']['id'],)).fetchone())]
        m=self.candidate('好，下次一起听歌',kind='promise',proof=proof);self.assertIsNotNone(m)
        self.assertIsNone(self.candidate('我们下次一起听歌',kind='experience',scope='story',proof=proof))
        m=self.confirm(m);r,d=self.mutate(m,'edit',content='我们已经一起听完歌曲');self.assertEqual(r.status,200)
        m['revision']+=1;r,d=self.mutate(m,'confirm');self.assertEqual(r.status,422)
    def test_index_failure_is_visible_and_retry_replaces_revision(self):
        self.setup_memory();self.embed.fail=True;m=self.confirm(self.candidate());self.assertEqual(m['index_status'],'failed')
        self.embed.fail=False;r,d=self.mutate(m,'reindex');self.assertEqual(r.status,200);self.assertEqual(self.ready(m['id'])['index_status'],'ready')
    def test_mutation_cancels_active_generation_and_late_output(self):
        m=self.confirm(self.candidate());gate=threading.Event();self.provider.plans=[['旧事实片段',gate,'迟到泄漏']]
        first=self.start(self.message('偏好是什么茶','active_delete_key'));rid=first['request']['id']
        deadline=time.monotonic()+2
        while not self.snap(rid)['message']['content'] and time.monotonic()<deadline:time.sleep(.01)
        self.mutate(m,'delete');before=self.snap(rid);gate.set();time.sleep(.05);after=self.snap(rid)
        self.assertEqual(after['request']['status'],'failed');self.assertEqual(after['request']['error_code'],'memory_changed');self.assertEqual(before['message']['content'],after['message']['content'])
    def test_only_adopted_complete_sources_and_expiry(self):
        self.setup_memory();m=self.candidate('今天暂时不聊工作',kind='boundary',topic='话题边界')
        r,d=self.mutate(m,'confirm');self.assertEqual(r.status,422)
        r,d=self.mutate(m,'edit',content=m['content'],valid_until=int(time.time())+3600);self.assertEqual(r.status,200)
        updated=self.ready(m['id']);self.assertEqual(updated['status'],'confirmed')
        with self.app.db.connect() as c:c.execute('UPDATE memories SET valid_until=1 WHERE id=?',(m['id'],))
        self.assertEqual(self.mem.retrieve(self.cid,'工作')['items'],[])
    def test_explicit_storage_consent_and_completed_shared_event(self):
        m=self.candidate('请记住，我喜欢乌龙茶');self.assertEqual(m['status'],'confirmed');self.assertEqual(self.ready(m['id'])['index_status'],'ready')
        source=self.message('我们给歌起名吧','shared_event_prepare');self.provider.plans=[['那把歌名确定为晚风，你觉得呢？']];first=self.complete(source)
        with self.app.db.connect() as c:proof=[dict(c.execute('SELECT * FROM messages WHERE id=?',(first['message']['id'],)).fetchone())]
        event=self.candidate('我们刚才一起确定歌名就叫晚风',kind='experience',scope='story',topic='共同歌名',proof=proof)
        self.assertIsNotNone(event);self.assertEqual(self.confirm(event)['status'],'confirmed')
    def test_source_replaced_or_stopped_cannot_keep_memory_effective(self):
        self.setup_memory();source=self.message('请下次一起听歌','source_invalidation');self.provider.plans=[['下次一起听歌好吗']];first=self.complete(source)
        with self.app.db.connect() as c:proof=[dict(c.execute('SELECT * FROM messages WHERE id=?',(first['message']['id'],)).fetchone())]
        m=self.confirm(self.candidate('好，下次一起听歌',kind='promise',topic='听歌约定',proof=proof))
        # This fixture's assistant was an older turn, so mark its source replaced in the same operation used by adoption.
        with self.app.db.connect() as c:
            c.execute("UPDATE messages SET status='replaced' WHERE id=?",(first['message']['id'],));self.mem.invalidate_source(c,self.cid,first['message']['id'])
        self.assertEqual(self.mem.list('a',self.cid)['items'],[]);self.assertEqual(self.mem.retrieve(self.cid,'听歌')['items'],[])
    def test_two_conflicts_cannot_both_become_effective(self):
        self.confirm(self.candidate());red=self.candidate('我喜欢红茶');green=self.candidate('我喜欢绿茶');self.confirm(red)
        r,d=self.mutate(green,'confirm',replace_conflict=True);self.assertEqual(r.status,409)
        r,d=self.mutate(green,'edit',content='我喜欢绿茶');self.assertEqual(r.status,409)
        self.assertEqual([x['content'] for x in self.mem.retrieve(self.cid,'茶')['items']],['我喜欢红茶'])
    def test_memory_change_invalidates_undecided_candidate(self):
        m=self.confirm(self.candidate());first=self.complete(self.message('饮品是什么茶','undecided_memory_query'))
        candidate=self.start(first['request']['user_message_id'],'regenerate',first['message']['id']);self.wait(candidate['request']['id'])
        self.mutate(m,'delete');snapshot=self.snap(candidate['request']['id']);self.assertEqual(snapshot['request']['status'],'kept');self.assertEqual(snapshot['message']['status'],'cancelled')
        r,d=self.req('POST','/api/conversations/'+self.cid+'/generations/'+candidate['request']['id']+'/choice',{'choice':'adopt'});self.assertEqual(r.status,409)
    def test_restart_keeps_deletion_blocks_and_does_not_restart_paid_jobs(self):
        from backend.api import Application
        m=self.confirm(self.candidate());self.mutate(m,'delete')
        restarted=Application(self.config,provider=self.provider,embedding_provider=self.embed)
        self.assertEqual(restarted.memory.list('a',self.cid)['items'],[])
        self.assertEqual(restarted.memory.retrieve(self.cid,'茶')['items'],[])
        with restarted.db.connect() as c:
            self.assertTrue(c.execute('SELECT 1 FROM memory_context_blocks WHERE message_id=?',(m['primary_source_id'],)).fetchone())
        self.assertEqual(len(self.provider.inputs),0)
    def test_embedding_dimension_change_requires_reindex_before_chat(self):
        m=self.confirm(self.candidate())
        calls=len(self.embed.calls)
        self.embed.dimensions=1024
        listed=self.mem.list('a',self.cid)['items'][0]
        self.assertEqual(listed['index_status'],'failed')
        r,d=self.req('POST','/api/conversations/'+self.cid+'/generations',{'kind':'reply','user_message_id':self.message('茶是什么'),'client_request_id':'wrong_dim_generation'})
        self.assertEqual(r.status,409,d)
        self.assertEqual(d['error']['code'],'memory_processing')
        self.assertEqual(len(self.embed.calls),calls)
        self.assertEqual(self.mem.retrieve(self.cid,'茶')['items'],[])

    def test_embedding_budget_failure_does_not_mark_index_ready(self):
        self.setup_memory()
        def exhausted(texts): raise ModelError('embedding_budget_exhausted')
        self.embed.embed=exhausted
        m=self.confirm(self.candidate())
        self.assertEqual(m['index_status'],'failed')
        item=self.mem.list('a',self.cid)['items'][0]
        self.assertEqual(item['index_error_code'],'embedding_budget_exhausted')
        self.assertIn('额度已用完',item['index_error_message'])
        with self.app.db.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM memory_vectors').fetchone()[0],0)
            audit=c.execute("SELECT status,error_code FROM memory_call_audit WHERE purpose='index'").fetchone()
        self.assertEqual(tuple(audit),('failed','embedding_budget_exhausted'))
    def test_expired_boundary_cannot_return_through_recent_retrieval_reply(self):
        from unittest.mock import patch
        self.setup_memory()
        m=self.candidate('今天暂时不聊工作',kind='boundary',topic='工作边界')
        until=int(time.time())+3600
        self.mutate(m,'edit',content=m['content'],valid_until=until)
        m=self.ready(m['id'])
        self.provider.plans=[['今天暂时不聊工作。']]
        previous=self.complete(self.message('现在有什么临时话题限制？','before_expiry_boundary'))
        with patch('backend.memory.now',return_value=until+1):
            current=self.complete(self.message('现在还有有效限制吗？','after_expiry_boundary'))
        actual=json.dumps(self.provider.inputs[-1],ensure_ascii=False)
        self.assertNotIn('今天暂时不聊工作',actual)
        with self.app.db.connect() as c:
            ids=json.loads(c.execute('SELECT context_message_ids FROM generation_requests WHERE id=?',(current['request']['id'],)).fetchone()[0])
        self.assertNotIn(previous['message']['id'],ids)
    def test_expired_sources_cannot_commit_late_extraction_as_new_memory(self):
        from unittest.mock import patch
        self.setup_memory()
        source=self.message('今天暂时不聊工作。我喜欢乌龙茶','expired_mixed_source')
        m=self.candidate('今天暂时不聊工作',kind='boundary',topic='工作边界',source=source)
        until=int(time.time())+3600
        with self.app.db.connect() as c:c.execute('UPDATE memories SET valid_until=?,expires_note=NULL WHERE id=?',(until,m['id']))
        self.confirm(m)
        with self.app.db.connect() as c:
            captured=[dict(c.execute('SELECT * FROM messages WHERE id=?',(source,)).fetchone())]
            job=dict(c.execute('SELECT * FROM memory_jobs WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1',(self.cid,)).fetchone())
            job['base_revision']=self.mem.epoch(c,self.cid)
        with patch('backend.memory.now',return_value=until+1):
            self.mem.commit_candidates(job,[{'content':'我喜欢乌龙茶','topic_key':'饮品偏好','kind':'user_fact','fact_scope':'real_user','evidence':[{'message_id':source,'quote':'我喜欢乌龙茶'}]}],captured)
            with self.app.db.connect() as c:self.assertEqual(self.mem.filter_context(c,self.cid,captured),[])
        with self.app.db.connect() as c:
            self.assertFalse(c.execute("SELECT 1 FROM memories WHERE content='我喜欢乌龙茶'").fetchone())
            self.assertEqual(c.execute('SELECT content FROM messages WHERE id=?',(source,)).fetchone()[0],'今天暂时不聊工作。我喜欢乌龙茶')
            self.assertFalse(c.execute('SELECT 1 FROM memory_context_blocks WHERE message_id=?',(source,)).fetchone())
    def test_memory_lookup_excludes_old_generated_prose_but_keeps_user_and_recall(self):
        self.confirm(self.candidate())
        self.provider.plans=[['HISTORY_ASSISTANT_CANARY 我闻到你上次喝的茶。']]
        self.complete(self.message('聊聊窗边的灯','non_lookup_scene'))
        self.complete(self.message('你还记得我平时爱喝哪一种茶吗？','authoritative_lookup'))
        actual=self.provider.inputs[-1]
        self.assertNotIn('HISTORY_ASSISTANT_CANARY',json.dumps(actual,ensure_ascii=False))
        self.assertIn('我喜欢乌龙茶',actual[0]['content'])
        self.assertEqual([m['role'] for m in actual[1:]],['user'])
        self.assertEqual(actual[-1]['content'],'你还记得我平时爱喝哪一种茶吗？')

    def test_preference_statement_does_not_switch_ordinary_dialogue_to_lookup(self):
        self.setup_memory();self.provider.plans=[['ORDINARY_DIALOGUE_CANARY']]
        self.complete(self.message('聊聊窗边的灯','ordinary_scene_one'))
        self.complete(self.message('我说一下我的饮品偏好：我喜欢红茶。','ordinary_preference_statement'))
        self.assertIn('ORDINARY_DIALOGUE_CANARY',json.dumps(self.provider.inputs[-1]))

    def test_plain_storage_command_and_refusal_quote_boundaries(self):
        self.assertTrue(explicit_storage('我喜欢红茶','记住，我喜欢红茶'))
        for text in ('不要记住，我喜欢红茶','他说记住，我喜欢红茶','假如我说记住，我喜欢红茶','“记住，我喜欢红茶”'):
            self.assertFalse(explicit_storage('我喜欢红茶',text),text)

    def test_linyu_memory_across_login_restart_correction_and_forgetting(self):
        from backend.api import Application
        self.setup_memory()
        source=self.message('记住，我喜欢乌龙茶','plain_storage')
        saved=self.candidate('我喜欢乌龙茶',source=source)
        self.assertEqual(saved['status'],'confirmed')
        saved=self.ready(saved['id'])
        # A new authenticated session and application instance use the same durable relationship.
        restarted=Application(self.config,provider=self.provider,embedding_provider=self.embed)
        with self.app.db.connect() as c:
            self.app.auth._revoke(c,self.sessions['a'][0])
            self.sessions['a']=restarted.auth.issue(c,'a')
        response,conversation=self.req('POST','/api/conversations',{'character_id':'linyu'},app=restarted)
        self.assertEqual(response.status,200)
        self.assertEqual(conversation['id'],self.cid)
        self.assertEqual(restarted.memory.retrieve(self.cid,'饮品')['items'][0]['content'],'我喜欢乌龙茶')
        response,_=self.mutate(saved,'edit',content='我喜欢红茶')
        self.assertEqual(response.status,200)
        edited=self.ready(saved['id'])
        recalled=restarted.memory.retrieve(self.cid,'饮品')['items']
        self.assertEqual([m['content'] for m in recalled],['我喜欢红茶'])
        response,_=self.mutate(edited,'delete')
        self.assertEqual(response.status,200)
        self.assertEqual(restarted.memory.retrieve(self.cid,'饮品')['items'],[])
        self.candidate('我喜欢乌龙茶',source=source)
        self.assertEqual(restarted.memory.list('a',self.cid)['items'],[])
        other,_=restarted.service.create_conversation('b',{'character_id':'linyu'})
        self.assertEqual(restarted.memory.retrieve(other,'饮品')['items'],[])
