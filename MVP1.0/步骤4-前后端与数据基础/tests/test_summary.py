# -*- coding: utf-8 -*-
import json,time,unittest,uuid
import test_memory as memory_helpers
from test_chat import FakeProvider
from backend import summary as S

class SummaryTest(unittest.TestCase):
 setUpClass=classmethod(memory_helpers.MemoryTest.setUpClass.__func__)
 setUp=memory_helpers.MemoryTest.setUp;tearDown=memory_helpers.MemoryTest.tearDown
 req=memory_helpers.MemoryTest.req;message=memory_helpers.MemoryTest.message;start=memory_helpers.MemoryTest.start;snap=memory_helpers.MemoryTest.snap;wait=memory_helpers.MemoryTest.wait
 complete=memory_helpers.MemoryTest.complete;setup_memory=memory_helpers.MemoryTest.setup_memory;candidate=memory_helpers.MemoryTest.candidate;confirm=memory_helpers.MemoryTest.confirm;mutate=memory_helpers.MemoryTest.mutate;ready=memory_helpers.MemoryTest.ready
 def add(self,role,content):
  with self.app.db.connect() as c:
   mid=uuid.uuid4().hex;o=c.execute('SELECT coalesce(max(ordinal),0)+1 FROM messages WHERE conversation_id=?',(self.cid,)).fetchone()[0]
   c.execute("INSERT INTO messages(id,conversation_id,ordinal,role,content,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(mid,self.cid,o,role,content,'saved' if role=='user' else 'completed',1,1))
  return mid
 def build(self):
  """Early talk about a job decision, then enough filler to push it out of the 40-message window."""
  self.setup_memory();self.sm=self.app.memory.summaries;self.llm=FakeProvider();self.sm.provider=self.llm
  self.u1=self.add('user','我在纠结要不要换工作，还没决定');self.a1=self.add('assistant','慢慢想，不急')
  self.u2=self.add('user','我们约好周六一起读完《小王子》第三章');self.a2=self.add('assistant','好，周六一起读完第三章')
  self.u3=self.add('user','我最喜欢的饮品是乌龙茶');self.a3=self.add('assistant','记下了')
  for i in range(22):self.add('user','闲聊%d'%i);self.add('assistant','嗯%d'%i)
 def plan(self,items):self.llm.plans.append([json.dumps({'items':items},ensure_ascii=False)])
 def run_once(self):
  self.assertTrue(self.sm.schedule(self.cid));deadline=time.monotonic()+3
  while self.cid in self.sm.working and time.monotonic()<deadline:time.sleep(.01)
  with self.app.db.connect() as c:return self.sm.active(c,self.cid)
 def read(self):
  with self.app.db.connect() as c:
   h=[dict(r) for r in c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY ordinal DESC LIMIT 40',(self.cid,))]
   return self.sm.read(c,self.cid,h)
 def good(self):
  return [{'type':'open_topic','text':'用户在考虑是否换工作，尚未决定','source_ids':[self.u1]},
          {'type':'promise','text':'双方约好周六读完《小王子》第三章','source_ids':[self.u2,self.a2]},
          {'type':'fact','text':'用户最喜欢乌龙茶','source_ids':[self.u3]}]
 def test_out_of_window_messages_become_cited_summary(self):
  self.build();self.plan(self.good());row=self.run_once()
  self.assertIsNotNone(row);items=json.loads(row['items'])
  self.assertEqual(len(items),3);self.assertTrue(all(i['source_ids'] for i in items))
  sent=json.loads(self.llm.inputs[0][1]['content'])
  self.assertIn(self.u1,[m['id'] for m in sent['messages']])
  self.assertNotIn('闲聊21',json.dumps(sent,ensure_ascii=False))   # still in window, not summarized
  self.assertEqual([i['type'] for i in self.read()],['open_topic','promise','fact'])
 def test_fabricated_or_unsupported_items_are_dropped(self):
  self.build()
  self.plan([{'type':'fact','text':'用户住在上海','source_ids':['invented-id']},
             {'type':'fact','text':'用户已经辞职','source_ids':[self.a1]},           # assistant text is not a user fact
             {'type':'promise','text':'约好周六读书','source_ids':[self.u2]},          # one side only
             {'type':'open_topic','text':'换工作未定','source_ids':[self.u1]}])
  items=json.loads(self.run_once()['items'])
  self.assertEqual([i['text'] for i in items],['换工作未定'])
 def test_deleting_memory_removes_summary_items_from_same_source(self):
  self.build();self.plan(self.good());self.run_once()
  with self.app.db.connect() as c:src=dict(c.execute('SELECT * FROM messages WHERE id=?',(self.u3,)).fetchone())
  m=self.confirm(self.candidate('我最喜欢的饮品是乌龙茶',source=self.u3))
  self.mutate(m,'delete')
  texts=json.dumps(self.read(),ensure_ascii=False)
  self.assertNotIn('乌龙',texts);self.assertIn('换工作',texts)
  with self.app.db.connect() as c:
   rows=c.execute('SELECT status,reason FROM conversation_summaries WHERE conversation_id=? ORDER BY version',(self.cid,)).fetchall()
  self.assertEqual([tuple(r) for r in rows],[('invalidated','incremental'),('active','source_removed')])
 def test_correction_removes_old_value_even_when_cited_elsewhere(self):
  self.build()
  good=self.good();good[2]['source_ids']=[self.a3]+[self.u3]
  good.append({'type':'open_topic','text':'用户想找乌龙茶的新店','source_ids':[self.u1]})  # mentions old value via another source
  self.plan(good);self.run_once()
  m=self.confirm(self.candidate('我最喜欢的饮品是乌龙茶',source=self.u3))
  self.mutate(m,'edit',content='我最喜欢的饮品是红茶');self.ready(m['id'])
  texts=json.dumps(self.read(),ensure_ascii=False)
  self.assertNotIn('乌龙',texts);self.assertIn('读完',texts)
 def test_removed_item_does_not_return_on_next_increment(self):
  self.build();self.plan(self.good());self.run_once()
  m=self.confirm(self.candidate('我最喜欢的饮品是乌龙茶',source=self.u3));self.mutate(m,'delete')
  for i in range(10):self.add('user','新话题%d'%i);self.add('assistant','好%d'%i)
  # Even if the model replays the deleted item with its original source, validation drops it.
  self.plan(self.good());row=self.run_once()
  self.assertNotIn('乌龙',row['items'])
  sent=json.loads(self.llm.inputs[-1][1]['content'])
  self.assertNotIn('乌龙',json.dumps(sent,ensure_ascii=False))
 def test_change_during_generation_discards_result_and_keeps_old(self):
  self.build();self.plan(self.good());first=self.run_once()
  for i in range(10):self.add('user','新话题%d'%i);self.add('assistant','好%d'%i)
  import threading
  gate=threading.Event();self.llm.plans.append([gate,json.dumps({'items':[]})])
  self.assertTrue(self.sm.schedule(self.cid));time.sleep(.05)
  with self.app.db.connect() as c:self.app.memory.bump(c,self.cid)
  gate.set();deadline=time.monotonic()+3
  while self.cid in self.sm.working and time.monotonic()<deadline:time.sleep(.01)
  with self.app.db.connect() as c:
   self.assertEqual(self.sm.active(c,self.cid)['id'],first['id'])
   self.assertEqual(c.execute('SELECT last_error FROM summary_state WHERE conversation_id=?',(self.cid,)).fetchone()[0],'summary_stale')
 def test_protocol_failure_keeps_previous_summary(self):
  self.build();self.plan(self.good());first=self.run_once()
  for i in range(10):self.add('user','新话题%d'%i);self.add('assistant','好%d'%i)
  self.llm.plans.append(['不是JSON']);self.run_once()
  with self.app.db.connect() as c:self.assertEqual(self.sm.active(c,self.cid)['id'],first['id'])
 def test_too_few_messages_do_not_call_model(self):
  self.setup_memory();sm=self.app.memory.summaries;sm.provider=FakeProvider()
  for i in range(20):self.add('user','x');self.add('assistant','y')
  self.assertFalse(sm.schedule(self.cid));self.assertEqual(sm.provider.inputs,[])
 def test_summary_reaches_reply_prompt_and_not_after_delete(self):
  self.build();self.plan(self.good());self.run_once()
  self.complete(self.message('上次我们聊到哪了',uuid.uuid4().hex))
  system=self.provider.inputs[-1][0]['content']
  self.assertIn('较早对话摘要',system);self.assertIn('换工作',system)
  m=self.confirm(self.candidate('我最喜欢的饮品是乌龙茶',source=self.u3));self.mutate(m,'delete')
  self.complete(self.message('继续刚才的话题',uuid.uuid4().hex))
  self.assertNotIn('乌龙',json.dumps(self.provider.inputs[-1],ensure_ascii=False))
 def test_other_conversation_is_isolated(self):
  self.build();self.plan(self.good());self.run_once()
  _,other=self.req('POST','/api/conversations',{'character_id':'linyu'},user='b')
  with self.app.db.connect() as c:self.assertEqual(self.sm.read(c,other['id'],[]),[])

if __name__=='__main__':unittest.main()
