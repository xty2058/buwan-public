import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.embedding import EmbeddingProvider,cosine
from backend.model import ModelError
class EmbeddingTest(unittest.TestCase):
    def test_disabled_never_reads_key_or_connects(self):
        with patch('backend.embedding.http.client.HTTPSConnection') as connection,patch('pathlib.Path.read_text',side_effect=AssertionError('credential read')):
            with self.assertRaises(ModelError):EmbeddingProvider().embed(['test'])
            connection.assert_not_called()
    def test_explicit_wire_and_finite_dimensions(self):
        with tempfile.TemporaryDirectory() as d:
            key=Path(d)/'dummy';key.write_text('dummy-test-not-a-live-key')
            settings={'allow_live':True,'model':'text-embedding-v4','dimensions':2,'approval_id':'test','base_url':'https://example.test/compatible-mode/v1','key_file':str(key),'max_requests':20,'budget_db':str(Path(d)/'budget.sqlite3')}
            class Response:
                status=200
                def read(self,n):return json.dumps({'data':[{'index':0,'embedding':[.3,.4]}],'usage':{'total_tokens':3}}).encode()
            with patch('backend.embedding.http.client.HTTPSConnection') as factory:
                conn=factory.return_value;conn.getresponse.return_value=Response()
                provider=EmbeddingProvider(settings);vectors,usage=provider.embed(['test'])
                args=conn.request.call_args.args;self.assertEqual(args[:2],('POST','/compatible-mode/v1/embeddings'));self.assertEqual(json.loads(args[2])['model'],'text-embedding-v4');self.assertEqual(usage,3);self.assertEqual(vectors,[[.3,.4]])
                settings['dimensions']=3
                with self.assertRaises(ModelError) as error:EmbeddingProvider(settings).embed(['test'])
                self.assertEqual(error.exception.code,'embedding_protocol')
    def test_cosine_retrieval_score(self):
        self.assertAlmostEqual(cosine([1.,0.],[1.,0.]),1);self.assertEqual(cosine([1.,0.],[0.,1.]),0)
