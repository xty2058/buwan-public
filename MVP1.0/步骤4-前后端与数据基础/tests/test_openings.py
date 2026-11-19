# -*- coding: utf-8 -*-
"""Roadmap 1.5: several openings for the default scene, one per new conversation."""
import copy
import json
import unittest
from pathlib import Path
try:
    import test_chat as base
except ImportError:
    from tests import test_chat as base

CANDIDATE = Path(__file__).resolve().parents[3] / 'docs/quality-candidate/linyu-v6-openings-candidate.json'


class OpeningsTest(unittest.TestCase):
    setUpClass = classmethod(base.ChatTest.setUpClass.__func__)
    setUp, tearDown, req = base.ChatTest.setUp, base.ChatTest.tearDown, base.ChatTest.req

    def first_message(self, cid):
        with self.app.db.connect() as c:
            return dict(c.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY ordinal LIMIT 1', (cid,)).fetchone())

    def test_candidate_publishes_and_new_conversation_uses_one_opening(self):
        definition = json.loads(CANDIDATE.read_text(encoding='utf-8'))
        self.app.service.publish(definition)
        texts = {o['text'] for o in definition['public']['openings']}
        old = self.first_message(self.cid)  # created in setUp on v1
        self.assertNotIn(old['content'], texts)
        _, conv = self.req('POST', '/api/conversations', {'character_id': 'linyu'}, user='b')
        first = self.first_message(conv['id'])
        self.assertEqual(first['role'], 'assistant');self.assertIn(first['content'], texts)
        for text in texts:  # house rules for the candidate lines
            self.assertNotRegex(text, r'[（(]|雨还大|天气|[？?]$')

    def test_adjusted_scene_ignores_openings(self):
        self.app.service.publish(json.loads(CANDIDATE.read_text(encoding='utf-8')))
        _, conv = self.req('POST', '/api/conversations', {'character_id': 'linyu', 'initial_scene': '周末午后，你路过书店门口。'}, user='b')
        first = self.first_message(conv['id'])
        self.assertEqual(first['role'], 'system');self.assertTrue(first['content'].startswith('已采用你调整的初始背景'))

    def test_invalid_openings_are_rejected(self):
        definition = json.loads(CANDIDATE.read_text(encoding='utf-8'))
        for bad in ([{'style': '只有一句', 'text': '你好'}], [{'text': '缺风格'}, {'style': 'x', 'text': 'y'}], '一句话', [{'style': 'a', 'text': ''}, {'style': 'b', 'text': 'y'}]):
            d = copy.deepcopy(definition);d['public']['openings'] = bad;d['version'] = 9
            with self.assertRaises(ValueError):self.app.service.publish(d)


if __name__ == '__main__':
    unittest.main()
