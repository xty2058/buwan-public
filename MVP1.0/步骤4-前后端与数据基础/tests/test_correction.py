# -*- coding: utf-8 -*-
"""Roadmap 1.4 related-memory suppression, 1.14 memory history and message deletion, 1.7 recalled-for-message."""
import json
import unittest
import test_memory as helpers


class CorrectionTest(unittest.TestCase):
    setUpClass = classmethod(helpers.MemoryTest.setUpClass.__func__)
    setUp = helpers.MemoryTest.setUp;tearDown = helpers.MemoryTest.tearDown
    req = helpers.MemoryTest.req;message = helpers.MemoryTest.message;start = helpers.MemoryTest.start
    snap = helpers.MemoryTest.snap;wait = helpers.MemoryTest.wait;complete = helpers.MemoryTest.complete
    setup_memory = helpers.MemoryTest.setup_memory;candidate = helpers.MemoryTest.candidate
    mutate = helpers.MemoryTest.mutate;ready = helpers.MemoryTest.ready;confirm = helpers.MemoryTest.confirm

    def saved(self, text, topic):
        m = self.candidate(text, topic)
        return self.confirm(m) if m['status'] != 'confirmed' else self.ready(m['id'])

    def recalled(self, query='茶饮品'):
        return [x['content'] for x in self.mem.retrieve(self.cid, query)['items']]

    def test_correction_suppresses_related_memory_until_released(self):
        self.setup_memory()
        tea = self.saved('我喜欢乌龙茶', '饮品偏好')
        habit = self.saved('我每天下午喝茶', '喝茶习惯')
        sport = self.saved('我喜欢篮球', '运动')
        self.assertIn('我每天下午喝茶', self.recalled())
        r, d = self.mutate(tea, 'edit', content='我现在只喝红茶');self.assertEqual(r.status, 200, d);self.ready(tea['id'])
        with self.app.db.connect() as c:
            held = [dict(x) for x in c.execute('SELECT * FROM memory_recall_suppressions')]
        self.assertEqual([h['memory_id'] for h in held], [habit['id']])  # 篮球 is unrelated
        self.assertEqual(held[0]['cause_memory_id'], tea['id'])
        self.assertNotIn('我每天下午喝茶', self.recalled());self.assertIn('我现在只喝红茶', self.recalled())
        _, listing = self.req('GET', '/api/conversations/' + self.cid + '/memories')
        item = next(x for x in listing['items'] if x['id'] == habit['id'])
        self.assertEqual(item['recall_suppressed']['cause_content'], '我现在只喝红茶')
        self.assertEqual(item['status'], 'confirmed')  # soft: not deleted
        r, d = self.mutate(habit, 'release_suppression');self.assertEqual(r.status, 200, d)
        self.assertIn('我每天下午喝茶', self.recalled())
        r, _ = self.mutate(habit, 'release_suppression');self.assertEqual(r.status, 404)

    def test_history_lists_previous_versions(self):
        self.setup_memory()
        tea = self.saved('我喜欢乌龙茶', '饮品偏好')
        self.mutate(tea, 'edit', content='我现在只喝红茶')
        r, d = self.req('GET', '/api/conversations/' + self.cid + '/memories/' + tea['id'] + '/history')
        self.assertEqual(r.status, 200, d)
        self.assertIn('我喜欢乌龙茶', [x['content'] for x in d['items']])
        r, _ = self.req('GET', '/api/conversations/' + self.cid + '/memories/' + tea['id'] + '/history', user='b')
        self.assertEqual(r.status, 404)

    def test_deleted_message_is_hidden_and_leaves_context_and_memories(self):
        self.setup_memory()
        source = self.message('我喜欢乌龙茶');reply = self.complete(source)['message']['id']
        tea = self.saved('我喜欢乌龙茶', '饮品偏好')
        with self.app.db.connect() as c:
            primary = c.execute('SELECT primary_source_id FROM memories WHERE id=?', (tea['id'],)).fetchone()[0]
        r, d = self.req('POST', f'/api/conversations/{self.cid}/messages/{reply}/delete');self.assertEqual(r.status, 200, d)
        _, page = self.req('GET', f'/api/conversations/{self.cid}/messages')
        gone = next(m for m in page['items'] if m['id'] == reply)
        self.assertTrue(gone['deleted']);self.assertEqual(gone['content'], '')
        self.complete(self.message('换个话题'))
        context = json.dumps(self.provider.inputs[-1], ensure_ascii=False)
        self.assertNotIn(gone['id'], context)
        with self.app.db.connect() as c:
            original = c.execute('SELECT content FROM messages WHERE id=?', (reply,)).fetchone()[0]
        self.assertNotIn(original, [m['content'] for m in self.provider.inputs[-1]])
        # Deleting the memory's own source message invalidates the memory.
        r, _ = self.req('POST', f'/api/conversations/{self.cid}/messages/{primary}/delete');self.assertEqual(r.status, 200)
        with self.app.db.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM memories WHERE id=?', (tea['id'],)).fetchone()[0], 'invalidated')
        r, _ = self.req('POST', f'/api/conversations/{self.cid}/messages/{reply}/delete', user='b');self.assertEqual(r.status, 404)

    def test_recalled_for_message_lists_live_memories(self):
        self.setup_memory()
        tea = self.saved('我喜欢乌龙茶', '饮品偏好')
        reply = self.complete(self.message('推荐一款茶饮品'))
        _, d = self.req('GET', f"/api/conversations/{self.cid}/messages/{reply['message']['id']}/recalled")
        self.assertEqual([x['id'] for x in d['items']], [tea['id']])
        self.mutate(self.ready(tea['id']), 'delete')
        _, d = self.req('GET', f"/api/conversations/{self.cid}/messages/{reply['message']['id']}/recalled")
        self.assertEqual(d['items'], [])


if __name__ == '__main__':
    unittest.main()
