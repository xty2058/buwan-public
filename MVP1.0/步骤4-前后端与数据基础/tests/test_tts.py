import base64
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.api import Application
from backend.config import Config
from backend.errors import ApiError
from backend.tts import TTS, DoubaoProvider, TTSSettings, TTSError, VOICES, parse_audio
from backend.model import CompatibleProvider, ModelSettings


class FakeTTS:
    configured = True
    mode = 'simulated'
    name = 'fixture-tts'
    def __init__(self, calls=100, chars=100000):
        self.settings = SimpleNamespace(approval_id='tts-test-only', max_calls=calls, max_chars=chars)
        self.inputs = []
        self.gate = None
        self.fail = False
    def synthesize(self, text, voice, request_id):
        self.inputs.append((text, voice, request_id))
        if self.gate:
            self.gate.wait(3)
        if self.fail:
            raise TTSError('tts_provider_failed')
        return b'ID3simulated-not-real-audio', len(text)


class TTSTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.provider = FakeTTS()
        self.app = Application(Config(db=root/'test.sqlite3', log_dir=root/'logs'),
                               provider=CompatibleProvider(ModelSettings()), tts_provider=self.provider)
        self.tts = self.app.tts
        with self.app.db.connect() as c:
            for user in ('a', 'b'):
                c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', (user, 'user_'+user, 'unused', user, 1, 1))
        self.user = {'id': 'a', 'session_id': None}
        self.cid, _ = self.app.service.create_conversation('a', {'character_id': 'linyu'})
        self.mid = self.message(self.cid)
    def test_usage_metrics_are_persisted_and_cache_does_not_call_again(self):
        self.provider.price_per_10000 = 3.5
        self.tts.request(self.user, self.cid, self.mid)
        for worker in list(self.tts.workers):
            worker.join(3)
        result = self.tts.status(self.user, self.cid, self.mid)
        self.assertEqual(result['status'], 'ready')
        self.assertIsInstance(result['elapsed_seconds'], float)
        self.assertEqual(result['estimated_cny'], round(len('好，今天不问。')*3.5/10000,8))
        self.assertFalse(result['billing_verified'])
        self.tts.request(self.user, self.cid, self.mid)
        self.assertEqual(len(self.provider.inputs), 1)

    def tearDown(self):
        if self.provider.gate:
            self.provider.gate.set()
        for worker in list(self.tts.workers):
            worker.join(3)
        for h in list(self.app.logger.handlers):
            h.close()
            self.app.logger.removeHandler(h)
        self.tmp.cleanup()
    def message(self, cid, text='好，今天不问。', status='completed'):
        import uuid
        mid = uuid.uuid4().hex
        with self.app.db.connect() as c:
            ordinal = c.execute('SELECT coalesce(max(ordinal),0)+1 FROM messages WHERE conversation_id=?', (cid,)).fetchone()[0]
            c.execute('INSERT INTO messages(id,conversation_id,ordinal,role,content,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (mid,cid,ordinal,'assistant',text,status,1,1))
        return mid
    def wait(self, cid=None, mid=None):
        cid, mid = cid or self.cid, mid or self.mid
        until = time.monotonic()+4
        while time.monotonic() < until:
            state = self.tts.status(self.user, cid, mid)
            if state['status'] != 'pending':
                return state
            time.sleep(.005)
        self.fail('TTS did not finish')
    def test_unconfigured_is_explicit_and_never_reads_credentials(self):
        tts = TTS(self.app.db, self.app.service, DoubaoProvider(TTSSettings()), self.app.chat)
        self.assertFalse(tts.capabilities()['tts_configured'])
        with self.assertRaises(ApiError) as caught:
            tts.request(self.user, self.cid, self.mid)
        self.assertEqual(caught.exception.code, 'tts_unconfigured')
        self.assertEqual(self.provider.inputs, [])
    def test_replay_uses_cached_audio_and_records_actual_billable_chars(self):
        self.tts.request(self.user, self.cid, self.mid)
        ready = self.wait()
        self.assertEqual(ready['status'], 'ready')
        result = self.tts.request(self.user, self.cid, self.mid)
        self.assertTrue(result['cached'])
        self.assertEqual(len(self.provider.inputs), 1)
        self.assertEqual(self.tts.audio(self.user, self.cid, self.mid)[1], 'audio/mpeg')
        with self.app.db.connect() as c:
            call = c.execute('SELECT * FROM tts_calls').fetchone()
            self.assertEqual(call['status'], 'completed')
            self.assertEqual(call['text_chars'], len('好，今天不问。'))
            self.assertEqual(call['billable_chars'], call['text_chars'])
            self.assertEqual(c.execute('SELECT count(*) FROM tts_reuse').fetchone()[0], 2)
    def test_concurrent_clicks_and_cross_process_instance_use_one_call(self):
        self.provider.gate = threading.Event()
        second = TTS(self.app.db, self.app.service, self.provider, self.app.chat)
        first = self.tts.request(self.user, self.cid, self.mid)
        result = second.request(self.user, self.cid, self.mid)
        self.assertEqual(first['id'], result['id'])
        self.provider.gate.set()
        self.wait()
        self.assertEqual(len(self.provider.inputs), 1)
    def test_failure_has_no_automatic_retry_and_stale_retry_is_idempotent(self):
        self.provider.fail = True
        self.tts.request(self.user, self.cid, self.mid)
        failed = self.wait()
        self.assertEqual(failed['status'], 'failed')
        self.tts.request(self.user, self.cid, self.mid)
        self.assertEqual(len(self.provider.inputs), 1)
        self.provider.fail = False
        retried = self.tts.request(self.user, self.cid, self.mid, {'retry':True,'attempt':1})
        self.wait()
        self.assertEqual(retried['attempt'], 2)
        self.tts.request(self.user, self.cid, self.mid, {'retry':True,'attempt':1})
        self.assertEqual(len(self.provider.inputs), 2)
    def test_failed_late_duplicate_retry_does_not_resubmit(self):
        self.provider.fail = True
        self.tts.request(self.user, self.cid, self.mid)
        self.wait()
        self.tts.request(self.user, self.cid, self.mid, {'retry':True,'attempt':1})
        self.wait()
        self.tts.request(self.user, self.cid, self.mid, {'retry':True,'attempt':1})
        self.assertEqual(len(self.provider.inputs), 2)
    def test_replaced_or_blocked_message_cannot_play_cached_audio(self):
        self.tts.request(self.user, self.cid, self.mid)
        self.wait()
        with self.app.db.connect() as c:
            c.execute("UPDATE messages SET status='replaced' WHERE id=?", (self.mid,))
        with self.assertRaises(ApiError):
            self.tts.audio(self.user, self.cid, self.mid)
        with self.app.db.connect() as c:
            c.execute("UPDATE messages SET status='completed' WHERE id=?", (self.mid,))
            c.execute('INSERT INTO memory_context_blocks VALUES(?,?,?)', (self.cid,self.mid,'deleted'))
        with self.assertRaises(ApiError):
            self.tts.audio(self.user, self.cid, self.mid)
    def test_deleted_during_synthesis_does_not_publish_audio(self):
        self.provider.gate = threading.Event()
        self.tts.request(self.user, self.cid, self.mid)
        with self.app.db.connect() as c:
            c.execute("UPDATE messages SET status='replaced' WHERE id=?", (self.mid,))
        self.provider.gate.set()
        for worker in list(self.tts.workers):
            worker.join(3)
        with self.app.db.connect() as c:
            row = c.execute('SELECT status,audio FROM tts_audio').fetchone()
            self.assertEqual(row['status'], 'failed')
            self.assertIsNone(row['audio'])
    def test_accounts_and_roles_are_isolated(self):
        with self.assertRaises(ApiError) as caught:
            self.tts.request({'id':'b'}, self.cid, self.mid)
        self.assertEqual(caught.exception.status, 404)
        for role in ('linyu','luye','shenyan'):
            cid, _ = self.app.service.create_conversation('a', {'character_id':role})
            mid = self.message(cid)
            self.tts.request(self.user, cid, mid)
            self.wait(cid, mid)
            self.assertEqual(self.provider.inputs[-1][1], VOICES[role]['id'])
        self.assertEqual(len(set(item[1] for item in self.provider.inputs)), 3)
    def test_incomplete_reply_cannot_synthesize(self):
        for status in ('candidate','stopped','streaming','failed','replaced'):
            with self.assertRaises(ApiError):
                self.tts.request(self.user, self.cid, self.message(self.cid,status=status))
        self.assertEqual(self.provider.inputs, [])
    def test_budget_is_reserved_before_provider_call_and_cache_remains_playable(self):
        self.provider.settings.max_calls = 1
        self.tts.request(self.user, self.cid, self.mid)
        self.wait()
        with self.assertRaises(ApiError) as caught:
            self.tts.request(self.user, self.cid, self.message(self.cid))
        self.assertEqual(caught.exception.code, 'tts_budget_exhausted')
        self.assertEqual(self.tts.request(self.user, self.cid, self.mid)['status'], 'ready')
    def test_character_budget_never_sends_over_limit(self):
        self.provider.settings.max_chars = 1
        with self.assertRaises(ApiError):
            self.tts.request(self.user, self.cid, self.mid)
        self.assertEqual(self.provider.inputs, [])
    def test_stale_pending_recovers_to_explicit_retry(self):
        self.provider.gate = threading.Event()
        self.tts.request(self.user, self.cid, self.mid)
        with self.app.db.connect() as c:
            c.execute('UPDATE tts_audio SET updated_at=1')
        self.assertEqual(self.tts.status(self.user,self.cid,self.mid)['error_code'], 'tts_interrupted')
        self.provider.gate.set()
    def test_credentials_are_not_in_capabilities(self):
        caps = json.dumps(self.tts.capabilities())
        self.assertNotIn('key_file', caps)
        self.assertNotIn('approval_id', caps)

    def test_messages_flag_voice_blocked_for_invalidated_source(self):
        import uuid
        with self.app.db.connect() as c:
            uid = uuid.uuid4().hex
            ordinal = c.execute('SELECT coalesce(max(ordinal),0)+1 FROM messages WHERE conversation_id=?', (self.cid,)).fetchone()[0]
            c.execute("INSERT INTO messages(id,conversation_id,ordinal,role,content,status,reply_to_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (uid, self.cid, ordinal, 'user', '我喜欢乌龙茶', 'saved', None, 1, 1))
            mid = uuid.uuid4().hex
            c.execute("INSERT INTO messages(id,conversation_id,ordinal,role,content,status,reply_to_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, self.cid, ordinal + 1, 'assistant', '乌龙茶挺好，柜台可以给你倒一杯。', 'completed', uid, 1, 1))
            c.execute('INSERT INTO memory_context_blocks VALUES(?,?,?)', (self.cid, mid, 'corrected'))
        items = self.app.service.messages('a', self.cid)['items']
        flagged = {m['id']: m.get('voice_blocked') for m in items}
        self.assertTrue(flagged.get(mid))
        self.assertFalse(flagged.get(self.mid))
        with self.app.db.connect() as c:
            with self.assertRaises(ApiError) as caught:
                self.tts.source(c, self.user, self.cid, mid)
            self.assertEqual(caught.exception.status, 409)


class ParserTest(unittest.TestCase):
    def stream(self, parts):
        return io.BytesIO(('\n'.join(json.dumps(x) for x in parts)+'\n').encode())
    def test_complete_audio_and_usage(self):
        audio, billable = parse_audio(self.stream([{'code':0,'data':base64.b64encode(b'ID3').decode()}, {'code':0,'data':base64.b64encode(b'audio').decode()}, {'code':20000000,'usage':{'text_words':18}}]))
        self.assertEqual(audio,b'ID3audio')
        self.assertEqual(billable,18)
    def test_partial_error_and_corrupt_audio_are_not_success(self):
        for parts in ([{'code':0,'data':base64.b64encode(b'ID3').decode()}], [{'code':400,'message':'private vendor details'}], [{'code':0,'data':'not-base64!'},{'code':20000000}], [{'code':0,'data':base64.b64encode(b'html-error').decode()},{'code':20000000}]):
            with self.assertRaises(TTSError):
                parse_audio(self.stream(parts))
    def test_provider_wire_uses_full_exact_text_and_fixed_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp)/'key'
            key.write_text('fixture-only')
            response = self.stream([{'code':0,'data':base64.b64encode(b'ID3audio').decode()},{'code':20000000,'usage':{'text_words':7}}])
            response.status = 200
            with patch('backend.tts.http.client.HTTPSConnection') as cls:
                cls.return_value.getresponse.return_value = response
                provider = DoubaoProvider(TTSSettings(True,str(key),'test',1,100))
                provider.synthesize('先别急，好吗？',VOICES['linyu']['id'],'request-id')
                args = cls.return_value.request.call_args.args
                self.assertEqual(json.loads(args[2])['req_params']['text'],'先别急，好吗？')
                self.assertEqual(args[3]['X-Api-Resource-Id'],'seed-tts-2.0')
                self.assertEqual(args[3]['X-Control-Require-Usage-Tokens-Return'],'*')

import test_relationship as relationship_helpers


class TTSRelationshipTest(unittest.TestCase):
    setUpClass = classmethod(relationship_helpers.RelationshipTest.setUpClass.__func__)
    req = relationship_helpers.RelationshipTest.req
    setup_relation = relationship_helpers.RelationshipTest.setup_relation
    operation = relationship_helpers.RelationshipTest.operation
    read = relationship_helpers.RelationshipTest.read
    say = relationship_helpers.RelationshipTest.say
    reply = relationship_helpers.RelationshipTest.reply
    wait = relationship_helpers.RelationshipTest.wait
    snap = relationship_helpers.RelationshipTest.snap

    def setUp(self):
        relationship_helpers.RelationshipTest.setUp(self)
        self.voice_provider = FakeTTS()
        self.app.tts = TTS(self.app.db,self.app.service,self.voice_provider,self.app.chat)
        self.setup_relation()

    def tearDown(self):
        for worker in list(self.app.tts.workers):
            worker.join(3)
        relationship_helpers.RelationshipTest.tearDown(self)

    def test_revoked_relation_invalidates_ordinary_reply_audio_and_reaccept_does_not_revive(self):
        self.operation('set_relation',target='romantic',store=True)
        first = self.reply(self.say('推荐一本书'))
        mid = first['message']['id']
        self.app.tts.request(self.user,self.cid,mid,{'use_id':self.use})
        for worker in list(self.app.tts.workers):
            worker.join(3)
        self.assertEqual(self.app.tts.status(self.user,self.cid,mid,self.use)['status'],'ready')
        self.say('分手')
        with self.assertRaises(ApiError):
            self.app.tts.audio(self.user,self.cid,mid,self.use)
        self.operation('set_relation',target='romantic',store=True)
        with self.assertRaises(ApiError):
            self.app.tts.audio(self.user,self.cid,mid,self.use)

    def test_new_use_does_not_inherit_temporary_relationship_audio_authority(self):
        self.operation('set_relation',target='romantic',store=False)
        first = self.reply(self.say('推荐一本书'))
        mid = first['message']['id']
        self.app.tts.request(self.user,self.cid,mid,{'use_id':self.use})
        for worker in list(self.app.tts.workers):
            worker.join(3)
        _, new = self.req('POST','/api/conversations/'+self.cid+'/uses',{})
        with self.assertRaises(ApiError):
            self.app.tts.audio(self.user,self.cid,mid,new['id'])


if __name__ == '__main__':
    unittest.main()
