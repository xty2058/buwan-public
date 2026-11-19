# -*- coding: utf-8 -*-
import unittest
from backend.reply_checks import case_templates, check, pet_phrases, repeated, speaks_for_user, truncated


def rec(reply, case='C1', repeat='1', finish='stop'):
    return {'reply': reply, 'case_id': case, 'repeat': repeat, 'finish_reason': finish}


class ReplyChecksTest(unittest.TestCase):
    def test_truncation_uses_finish_reason_and_dangling_clause(self):
        self.assertEqual(truncated(rec('好啊', finish='length')), 'finish_reason=length')
        self.assertEqual(truncated(rec('我刚整理完旧书，然后')), 'dangling_end')
        self.assertIsNone(truncated(rec('嗯。')))
        self.assertIsNone(truncated(rec('好')))  # short chat replies without punctuation are normal

    def test_repetition_inside_one_reply(self):
        self.assertTrue(repeated(rec('今天雨下得挺大。今天雨下得挺大。')))
        self.assertIsNone(repeated(rec('今天雨下得挺大。你那边呢？')))

    def test_speaking_for_user(self):
        self.assertTrue(speaks_for_user(rec('（你点点头，接过书）那就这本吧。')))
        self.assertTrue(speaks_for_user(rec('你笑了，说好啊。')))
        self.assertIsNone(speaks_for_user(rec('你说，我听着。')))

    def test_pet_phrase_needs_distinct_cases(self):
        same_case = [rec('雨声刚好当背景音，挺好。', 'S01', str(i)) for i in range(3)]
        self.assertEqual(pet_phrases(same_case), [])
        spread = [rec('雨声刚好当背景音，挺好。', c) for c in ('A', 'B', 'C')]
        self.assertTrue(any('雨声刚好当背景音' in p['phrase'] for p in pet_phrases(spread)))

    def test_case_template_catches_repeated_opener(self):
        rows = [rec('在整理旧书。你那边雨大吗？', 'S01', '1'), rec('在擦书架。你那边雨大吗？', 'S01', '2'),
                rec('刚开门，人不多。', 'S01', '3'), rec('雨声挺好听的。', 'S01', '4'), rec('在看书。', 'S01', '5')]
        found = case_templates(rows)
        self.assertTrue(any(t['phrase'] == '你那边雨大吗' and t['repeats'] == 2 for t in found))
        self.assertEqual(check(rows)['counts']['case_templates'], 1)


if __name__ == '__main__':
    unittest.main()
