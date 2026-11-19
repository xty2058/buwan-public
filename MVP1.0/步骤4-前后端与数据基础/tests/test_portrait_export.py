# -*- coding: utf-8 -*-
"""Roadmap 1.9 「Ta 心中的我」 grounding and 1.10 export."""
import json
import unittest
import test_memory as helpers
from backend.portrait import grounded


class GroundingTest(unittest.TestCase):
    def test_sentence_must_reuse_memory_wording(self):
        tea = {'content': '我喜欢乌龙茶'}
        self.assertTrue(grounded('你喜欢乌龙茶，我记着呢。', [tea]))
        self.assertFalse(grounded('你总在雨天来店里。', [tea]))
        self.assertFalse(grounded('你喜欢乌龙茶，也喜欢下雨。', [tea, {'content': '我每天跑步'}]))


class PortraitExportTest(unittest.TestCase):
    setUpClass = classmethod(helpers.MemoryTest.setUpClass.__func__)
    setUp = helpers.MemoryTest.setUp;tearDown = helpers.MemoryTest.tearDown
    req = helpers.MemoryTest.req;message = helpers.MemoryTest.message;start = helpers.MemoryTest.start
    snap = helpers.MemoryTest.snap;wait = helpers.MemoryTest.wait;complete = helpers.MemoryTest.complete
    setup_memory = helpers.MemoryTest.setup_memory;candidate = helpers.MemoryTest.candidate
    mutate = helpers.MemoryTest.mutate;ready = helpers.MemoryTest.ready;confirm = helpers.MemoryTest.confirm

    def raw(self, path, user='a'):
        token, csrf = self.sessions[user]
        return self.app.handle('GET', path, {'Host': 'test.local', 'Origin': 'http://test.local', 'Cookie': 'weiwan_session=' + token}, b'')

    def saved(self, text, topic):
        m = self.candidate(text, topic)
        return self.confirm(m) if m['status'] != 'confirmed' else self.ready(m['id'])

    def portrait_plan(self, sentences):
        self.provider.plans = [[json.dumps({'sentences': sentences}, ensure_ascii=False)]]

    def test_portrait_keeps_grounded_sentences_and_hides_them_after_deletion(self):
        self.setup_memory()
        tea = self.saved('我喜欢乌龙茶', '饮品偏好');sport = self.saved('我喜欢篮球', '运动')
        self.portrait_plan([{'text': '你喜欢乌龙茶，泡的时候我会留意。', 'memory_ids': ['M2']},
                            {'text': '你周末常来店里看书。', 'memory_ids': ['M1']},  # invented
                            {'text': '你喜欢篮球。', 'memory_ids': ['M9']}])          # unknown id
        r, d = self.req('POST', '/api/conversations/' + self.cid + '/portrait')
        self.assertEqual(r.status, 200, d)
        self.assertEqual([s['text'] for s in d['sentences']], ['你喜欢乌龙茶，泡的时候我会留意。'])
        self.assertEqual(d['dropped'], 2)
        prompt = self.provider.inputs[-1][0]['content']
        self.assertIn('我喜欢乌龙茶', prompt);self.assertNotIn(tea['id'], prompt)  # labels only, no internal ids
        self.mutate(self.ready(tea['id']), 'delete')
        _, d = self.req('GET', '/api/conversations/' + self.cid + '/portrait')
        self.assertEqual(d['sentences'], []);self.assertTrue(d['stale'])
        r, _ = self.req('GET', '/api/conversations/' + self.cid + '/portrait', user='b');self.assertEqual(r.status, 404)

    def test_portrait_failures_store_nothing(self):
        self.setup_memory()
        self.saved('我喜欢乌龙茶', '饮品偏好')
        r, d = self.req('POST', '/api/conversations/' + self.cid + '/portrait')
        self.assertEqual(d['error']['code'], 'portrait_not_enough')
        self.saved('我喜欢篮球', '运动')
        self.portrait_plan([{'text': '你一定很喜欢旅行。', 'memory_ids': ['M1']}])
        r, d = self.req('POST', '/api/conversations/' + self.cid + '/portrait')
        self.assertEqual(r.status, 422);self.assertEqual(d['error']['code'], 'portrait_ungrounded')
        self.provider.plans = [['不是JSON']]
        r, d = self.req('POST', '/api/conversations/' + self.cid + '/portrait')
        self.assertEqual(d['error']['code'], 'portrait_failed')
        _, d = self.req('GET', '/api/conversations/' + self.cid + '/portrait')
        self.assertEqual(d['sentences'], [])
        with self.app.db.connect() as c:
            audits = [r[0] for r in c.execute("SELECT status FROM memory_call_audit WHERE purpose='portrait' ORDER BY rowid")]
        self.assertEqual(audits, ['completed', 'failed'])

    def test_export_owner_only_without_deleted_text_or_private_card(self):
        self.setup_memory()
        source = self.message('我喜欢乌龙茶');reply = self.complete(source)['message']['id']
        self.saved('我喜欢乌龙茶', '饮品偏好')
        secret = self.message('这句我想删掉');self.req('POST', f'/api/conversations/{self.cid}/messages/{secret}/delete')
        r = self.raw(f'/api/conversations/{self.cid}/export')
        data = json.loads(r.body)
        self.assertEqual(r.status, 200);self.assertIn('attachment', r.headers['Content-Disposition'])
        self.assertEqual(data['character']['name'], '林屿')
        self.assertIn('我喜欢乌龙茶', [m['content'] for m in data['memories']])
        self.assertTrue(any(m.get('deleted') for m in data['messages']))
        raw = r.body.decode()
        self.assertNotIn('这句我想删掉', raw);self.assertNotIn('character_card', raw);self.assertNotIn('PRIVATE_NAME', raw)
        r = self.raw(f'/api/conversations/{self.cid}/export?format=md')
        md = r.body.decode()
        self.assertTrue(md.startswith('# 我与林屿的记录'));self.assertIn('（这条消息已删除）', md);self.assertNotIn('这句我想删掉', md)
        r = self.raw(f'/api/conversations/{self.cid}/export', user='b');self.assertEqual(r.status, 404)


if __name__ == '__main__':
    unittest.main()
