# -*- coding: utf-8 -*-
import json,threading,time,unittest
from backend.model import ModelError
from backend.memory import direct_rule_candidates
import test_memory as memory_helpers
from test_chat import FakeProvider

class MemoryPhase2Test(unittest.TestCase):
 setUpClass=classmethod(memory_helpers.MemoryTest.setUpClass.__func__)
 setUp=memory_helpers.MemoryTest.setUp;tearDown=memory_helpers.MemoryTest.tearDown
 req=memory_helpers.MemoryTest.req;message=memory_helpers.MemoryTest.message;start=memory_helpers.MemoryTest.start;snap=memory_helpers.MemoryTest.snap;wait=memory_helpers.MemoryTest.wait
 complete=memory_helpers.MemoryTest.complete;setup_memory=memory_helpers.MemoryTest.setup_memory;candidate=memory_helpers.MemoryTest.candidate;confirm=memory_helpers.MemoryTest.confirm;mutate=memory_helpers.MemoryTest.mutate;ready=memory_helpers.MemoryTest.ready
 def enable_filter(self):
  self.mem.retrieval_policy={'threshold':.3,'top_k':2,'pool_k':8,'scope_filter':True,'semantic_filter':True};self.filter=FakeProvider();self.mem.reranker=self.filter
 def test_delete_unrelated_memory_preserves_prior_explicit_correction(self):
  tea=self.confirm(self.candidate());sport=self.confirm(self.candidate('我喜欢慢跑',topic='运动偏好'))
  self.mutate(tea,'edit',content='我现在更喜欢红茶');tea=self.ready(tea['id'])
  self.mutate(sport,'delete')
  rows=self.mem.list('a',self.cid)['items'];self.assertTrue(any(x['id']==tea['id'] and x['status']=='confirmed' for x in rows))
 def test_ordinary_questions_are_not_boundary_or_fact_candidates(self):
  for text in ['门口的灯光选亮一点还是暗一点？','换个话题，说说你的桌子。']:
   self.assertIsNone(self.candidate(text,kind='boundary',topic='伪边界'));self.assertIsNone(self.candidate(text,kind='user_fact',topic='伪事实'))
 def test_invented_permanent_boundary_deadline_is_removed(self):
  m=self.candidate('我不喜欢被叫小朋友',kind='boundary',topic='称呼边界')
  with self.app.db.connect() as c:c.execute('UPDATE memories SET expires_note=? WHERE id=?',('模型虚构待核对期限',m['id']))
  saved=self.confirm(m);self.assertEqual(saved['status'],'confirmed');self.assertIsNone(saved['expires_note'])
 def test_omitted_temporary_qualifier_still_needs_deadline(self):
  source=self.message('今天暂时不聊工作','temporary_original');m=self.candidate('不聊工作',kind='boundary',topic='工作边界',source=source)
  self.assertTrue(m['expires_note']);response,data=self.mutate(m,'confirm');self.assertEqual(response.status,422)
 def test_direct_rules_do_not_guess_hypotheses_or_questions(self):
  for text in ['如果我喜欢羽毛球','朋友说我喜欢羽毛球','我喜欢什么？','说说你喜欢的运动']:
   self.assertEqual(direct_rule_candidates([{'id':'x','role':'user','content':text}]),[])
  rows=direct_rule_candidates([{'id':'x','role':'user','content':'我不喜欢咖啡。'}]);self.assertEqual(rows[0]['content'],'我不喜欢咖啡')
 def test_semantic_filter_rejects_foreign_ids(self):
  self.confirm(self.candidate());self.enable_filter();self.filter.plans=[[json.dumps({'ids':['foreign_id']})]]
  with self.assertRaises(ModelError) as e:self.mem.retrieve(self.cid,'饮品')
  self.assertEqual(e.exception.code,'memory_filter_protocol')
  with self.app.db.connect() as c:self.assertEqual(c.execute("SELECT status FROM memory_call_audit WHERE purpose='rerank' ORDER BY rowid DESC LIMIT 1").fetchone()[0],'failed')
 def test_empty_semantic_selection_does_not_fall_back_to_cosine(self):
  self.confirm(self.candidate());self.enable_filter();self.filter.plans=[['{"ids":[]}']];self.assertEqual(self.mem.retrieve(self.cid,'生日')['items'],[])
 def test_real_scope_does_not_query_story_memory(self):
  self.confirm(self.candidate('我扮演一个喜欢咖啡的人',scope='story'));self.enable_filter();before=len(self.embed.calls)
  self.assertEqual(self.mem.retrieve(self.cid,'现实里我的饮品是什么')['items'],[]);self.assertEqual(len(self.embed.calls),before)
 def test_wrong_story_scene_excluded_before_network(self):
  m=self.confirm(self.candidate('我扮演一个喜欢咖啡的人',scope='story'));self.enable_filter()
  with self.app.db.connect() as c:c.execute('UPDATE memories SET story_scene=? WHERE id=?',('另一个故事',m['id']))
  before=len(self.embed.calls);self.assertEqual(self.mem.retrieve(self.cid,'故事里喜欢什么')['items'],[]);self.assertEqual(len(self.embed.calls),before)
 def test_stop_during_query_does_not_start_filter_or_chat(self):
  self.confirm(self.candidate());self.enable_filter();gate=threading.Event();self.embed.gate=gate;before=len(self.embed.calls);r=self.start(self.message('我爱喝什么茶','stop_before_filter'));rid=r['request']['id']
  for _ in range(200):
   if len(self.embed.calls)>before:break
   time.sleep(.01)
  self.req('POST',f'/api/conversations/{self.cid}/generations/{rid}/stop',{'attempt':1,'visible_chars':0});gate.set()
  for _ in range(300):
   if not self.app.chat.controls:break
   time.sleep(.01)
  self.assertEqual(self.filter.inputs,[]);self.assertEqual(self.provider.inputs,[]);self.assertEqual(self.snap(rid)['request']['status'],'stopped')
 def test_failed_extraction_explicit_retry_then_rules_pending(self):
  self.setup_memory();source=self.message('我喜欢羽毛球','protocol_retry_fact');reply=self.complete(source)['message']['id'];extractor=FakeProvider();extractor.plans=[['not-json'],['{"memories":[]}']];self.mem.extractor=extractor
  self.mem.schedule('a',self.cid,reply)
  for _ in range(300):
   with self.app.db.connect() as c:job=dict(c.execute('SELECT * FROM memory_jobs WHERE reply_id=?',(reply,)).fetchone())
   if job['status']=='failed':break
   time.sleep(.01)
  self.assertEqual(job['error_code'],'memory_extract_protocol');self.assertEqual(len(extractor.inputs),1);self.mem.schedule('a',self.cid,reply,retry=True)
  for _ in range(300):
   items=self.mem.list('a',self.cid)['items']
   if items:break
   time.sleep(.01)
  self.assertEqual(items[0]['status'],'candidate');self.assertEqual(len(extractor.inputs),2)
 def test_explicit_story_source_cannot_be_written_as_real_user_fact(self):
  m=self.candidate('故事里我喜欢咖啡',scope='real_user');self.assertEqual(m['fact_scope'],'story');self.assertTrue(m['story_scene'])
 def test_question_with_positive_assistant_is_not_user_acceptance(self):
  text='故事里我们下次一起给小诗选配图，你愿意吗？';source=self.message(text,'question_not_acceptance');self.provider.plans=[['我同意下次一起给小诗选配图。']];reply=self.complete(source)['message']['id']
  with self.app.db.connect() as c:proof=[dict(c.execute('SELECT * FROM messages WHERE id=?',(reply,)).fetchone())]
  self.assertIsNone(self.candidate(text,kind='promise',scope='story',source=source,proof=proof))
 def test_assistant_refusal_is_not_two_party_promise_evidence(self):
  source=self.message('好，故事里我们约定下次一起给小诗配图','not_two_party');self.provider.plans=[['我不同意下次一起给小诗配图。']];reply=self.complete(source)['message']['id']
  with self.app.db.connect() as c:proof=[dict(c.execute('SELECT * FROM messages WHERE id=?',(reply,)).fetchone())]
  self.assertIsNone(self.candidate('故事里我们约定下次一起给小诗配图',kind='promise',scope='story',source=source,proof=proof))
 def test_expiring_memory_stops_late_stream_content(self):
  from unittest.mock import patch
  self.setup_memory();m=self.candidate('今天暂时不聊工作',kind='boundary',topic='工作边界');until=int(time.time())+3600
  self.mutate(m,'edit',content=m['content'],valid_until=until);self.ready(m['id'])
  gate=threading.Event();self.provider.plans=[['有效期内片段',gate,'LATE_EXPIRED_CANARY']]
  r=self.start(self.message('现在有什么话题限制','expiry_stream'));rid=r['request']['id']
  for _ in range(200):
   if self.snap(rid)['message']['content']:break
   time.sleep(.01)
  with patch('backend.memory.now',return_value=until+1),patch('backend.chat.now',return_value=until+1):
   gate.set()
   for _ in range(300):
    if not self.app.chat.controls:break
    time.sleep(.01)
  after=self.snap(rid);self.assertEqual(after['request']['status'],'failed');self.assertEqual(after['request']['error_code'],'memory_changed');self.assertNotIn('LATE_EXPIRED_CANARY',after['message']['content'])
