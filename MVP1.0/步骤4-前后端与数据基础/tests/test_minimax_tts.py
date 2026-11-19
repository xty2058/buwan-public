import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from backend.tts import MiniMaxProvider,TTSSettings,TTSError
class MiniMaxTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.key=Path(self.tmp.name)/'key';self.key.write_text('test-secret')
  self.provider=MiniMaxProvider(TTSSettings(True,str(self.key),'offline',2,100))
 def tearDown(self):self.tmp.cleanup()
 def response(self,data,status=200):
  conn=MagicMock();conn.getresponse.return_value.status=status;conn.getresponse.return_value.read.return_value=json.dumps(data).encode();return conn
 def test_request_voice_usage_and_single_call(self):
  conn=self.response({'base_resp':{'status_code':0},'data':{'audio':b'ID3fixture'.hex()},'extra_info':{'usage_characters':119}})
  with patch('backend.tts.http.client.HTTPSConnection',return_value=conn):
   audio,usage=self.provider.synthesize('你好','Chinese_casual_guide_nv1','request')
  self.assertEqual(usage,119);self.assertEqual(audio,b'ID3fixture');self.assertEqual(conn.request.call_count,1)
  body=json.loads(conn.request.call_args.args[2]);self.assertEqual(body['model'],'speech-2.8-hd');self.assertEqual(body['voice_setting']['voice_id'],'Chinese_casual_guide_nv1')
 def test_specific_error_redaction_no_retry(self):
  conn=self.response({'base_resp':{'status_code':1008,'status_msg':'insufficient balance test-secret'},'trace_id':'trace'},200)
  with patch('backend.tts.http.client.HTTPSConnection',return_value=conn):
   with self.assertRaises(TTSError) as e:self.provider.synthesize('你好','Chinese_casual_guide_nv1','request')
  self.assertEqual(e.exception.details['base_resp']['status_code'],1008);self.assertNotIn('test-secret',str(e.exception.details));self.assertEqual(conn.request.call_count,1)
 def test_disabled_never_connects(self):
  with patch('backend.tts.http.client.HTTPSConnection') as network:
   with self.assertRaises(TTSError):MiniMaxProvider(TTSSettings()).synthesize('你好','Chinese_casual_guide_nv1','request')
   network.assert_not_called()
 def test_bad_audio_rejected(self):
  conn=self.response({'base_resp':{'status_code':0},'data':{'audio':b'not audio'.hex()}})
  with patch('backend.tts.http.client.HTTPSConnection',return_value=conn):
   with self.assertRaises(TTSError) as e:self.provider.synthesize('你好','Chinese_casual_guide_nv1','request')
  self.assertEqual(e.exception.code,'tts_invalid_audio')
