# -*- coding: utf-8 -*-
"""Recall inspector (roadmap 1.13) and latency view (1.8): owner-only, deletion-respecting, read-only."""
import json
import time
import unittest
try:
    import test_chat as base  # unittest discover -s tests
except ImportError:
    from tests import test_chat as base  # python -m unittest tests.test_inspection


class InspectionTest(unittest.TestCase):
    # Reuse ChatTest's fixtures without subclassing it (subclassing would re-run all chat tests).
    setUpClass = classmethod(base.ChatTest.setUpClass.__func__)
    setUp, tearDown = base.ChatTest.setUp, base.ChatTest.tearDown
    req, message, start = base.ChatTest.req, base.ChatTest.message, base.ChatTest.start
    snap, wait = base.ChatTest.snap, base.ChatTest.wait

    def turn_with_recall(self):
        source = self.message('我喜欢无糖红茶')
        rid = self.start(source)['request']['id'];self.wait(rid)
        mid = 'f' * 32
        with self.app.db.connect() as c:
            c.execute('''INSERT INTO memories(id,conversation_id,kind,fact_scope,content,status,primary_source_id,confirmed_by,confirmed_at,created_at,updated_at)
                         VALUES(?,?,'user_fact','real_user','我喜欢无糖红茶','confirmed',?,'a',1,1,1)''', (mid, self.cid, source))
            c.execute('INSERT INTO memory_evidence VALUES(?,?,?)', (mid, source, '我喜欢无糖红茶'))
            selected = [{'id': mid, 'revision': 1, 'score': .91, 'content': '我喜欢无糖红茶', 'kind': 'user_fact',
                         'fact_scope': 'real_user', 'source_time': int(time.time()) - 3 * 86400}]
            c.execute('INSERT OR REPLACE INTO memory_retrievals VALUES(?,?,?,?,?,?)',
                      (rid, self.cid, 1, json.dumps(selected, ensure_ascii=False), 'test', int(time.time())))
        return rid, mid

    def test_turn_shows_recalled_memory_with_original_words_and_latency(self):
        rid, _ = self.turn_with_recall()
        r, data = self.req('GET', '/api/conversations/' + self.cid + '/recalls')
        self.assertEqual(r.status, 200, data)
        turn = next(t for t in data['items'] if t['generation_id'] == rid)
        self.assertEqual(turn['user_text'], '我喜欢无糖红茶')
        self.assertEqual(turn['recalled'][0]['content'], '我喜欢无糖红茶')
        self.assertEqual(turn['recalled'][0]['quotes'], ['我喜欢无糖红茶'])
        self.assertIsNotNone(turn['call']['duration_ms'])

    def test_deleted_memory_is_masked_not_resurfaced(self):
        rid, mid = self.turn_with_recall()
        with self.app.db.connect() as c:
            c.execute("UPDATE memories SET status='deleted' WHERE id=?", (mid,))
        _, data = self.req('GET', '/api/conversations/' + self.cid + '/recalls')
        memory = next(t for t in data['items'] if t['generation_id'] == rid)['recalled'][0]
        self.assertEqual(memory['status_now'], 'deleted')
        self.assertIsNone(memory['content']);self.assertEqual(memory['quotes'], [])
        self.assertNotIn('无糖红茶', json.dumps(memory, ensure_ascii=False))

    def test_other_users_cannot_read_recalls_or_metrics(self):
        self.turn_with_recall()
        r, _ = self.req('GET', '/api/conversations/' + self.cid + '/recalls', user='b')
        self.assertEqual(r.status, 404)
        _, mine = self.req('GET', '/api/inspection/metrics')
        _, theirs = self.req('GET', '/api/inspection/metrics', user='b')
        self.assertEqual(mine['items'][0]['calls'], 1);self.assertEqual(theirs['items'], [])

    def test_inspector_requires_login(self):
        response = self.app.handle('GET', '/api/inspection/metrics', {'Host': 'test.local'}, b'')
        self.assertEqual(response.status, 401)
        page = self.app.handle('GET', '/inspect', {'Host': 'test.local'}, b'')
        self.assertEqual(page.status, 200)


if __name__ == '__main__':
    unittest.main()
