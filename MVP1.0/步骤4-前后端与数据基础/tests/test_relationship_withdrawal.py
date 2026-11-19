"""Step 10 end-to-end finding: explicit withdrawal plus refusal to store the event."""
import json
import unittest
import test_relationship as helpers
from backend.relationship import command


class WithdrawalTest(unittest.TestCase):
    setUpClass = classmethod(helpers.RelationshipTest.setUpClass.__func__)
    setUp = helpers.RelationshipTest.setUp
    tearDown = helpers.RelationshipTest.tearDown
    req = helpers.RelationshipTest.req
    setup_relation = helpers.RelationshipTest.setup_relation
    operation = helpers.RelationshipTest.operation
    read = helpers.RelationshipTest.read
    say = helpers.RelationshipTest.say
    reply = helpers.RelationshipTest.reply
    wait = helpers.RelationshipTest.wait
    snap = helpers.RelationshipTest.snap

    def test_explicit_withdrawal_with_storage_refusal_is_persistent_and_filters_old_authority(self):
        for text, target in [('分手，不保存结束事件', 'unspecified'),
                             ('我们结束恋爱吧，不用保存这件事', 'unspecified'),
                             ('我们退回朋友，不保存新的朋友关系', 'friends')]:
            with self.subTest(text=text):
                self.setup_relation()
                self.operation('set_relation', target='romantic', store=True)
                self.provider.plans = [['OLD_AUTHORITY_REPLY 此时的普通对话']]
                self.reply(self.say('先聊一句普通话题'))
                self.say(text)
                self.assertEqual(self.read(self.use)['state']['relationship_type'], target)
                self.assertEqual(self.read(self.use)['state']['persistent_type'], 'unspecified')
                self.reply(self.say('换一个普通话题'))
                actual = json.dumps(self.provider.inputs[-1], ensure_ascii=False)
                self.assertNotIn('OLD_AUTHORITY_REPLY', actual)
                self.req('POST', f'/api/conversations/{self.cid}/uses/{self.use}/close', {})
                _, use = self.req('POST', f'/api/conversations/{self.cid}/uses', {})
                self.use = use['id']
                state = self.read(self.use)
                self.assertEqual(state['state']['relationship_type'], 'unspecified')
                self.assertFalse(any(e.get('long_term') and '结束' in e['content'] for e in state['events']))

    def test_negative_hypothetical_quoted_and_affirmative_clauses_do_not_gain_consent(self):
        for text in ['不要分手，不保存结束事件', '假如我们分手，不保存结束事件',
                     '她说分手，不保存结束事件', '“分手”，不保存结束事件',
                     '分手吗，不保存结束事件', '分手，不保存并继续恋爱',
                     '我们开始恋爱吧，不保存这件事', '我们成为朋友，不保存新关系']:
            with self.subTest(text=text):
                self.assertIsNone(command(text))
        self.assertEqual(command('分手'), ('revoke', 'unspecified'))
        self.assertEqual(command('我们开始恋爱吧'), ('relation', 'romantic'))


if __name__ == '__main__':
    unittest.main()
