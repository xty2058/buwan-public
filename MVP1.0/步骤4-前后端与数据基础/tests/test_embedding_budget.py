"""No network/real credentials: bounded requests, failure accounting and restart checks."""
import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from backend.embedding import EmbeddingProvider, RequestBudget, valid_vector
from backend.model import ModelError


class EmbeddingBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root/'key'; self.key.write_text('fixture-not-a-real-key')
        self.settings = dict(allow_live=True, model='text-embedding-v4', dimensions=2,
                             approval_id='bounded-fixture', base_url='https://example.test/compatible-mode/v1',
                             key_file=str(self.key), budget_db=str(self.root/'budget.sqlite3'), max_requests=2)

    def tearDown(self): self.temp.cleanup()

    def response(self, data=None, status=200):
        class Response:
            def read(inner, n):
                return json.dumps(data if data is not None else {'model':'text-embedding-v4','data':[{'index':0,'embedding':[.3,.4]}],'usage':{'total_tokens':3},'id':'vendor-fixture'}).encode()
        r=Response(); r.status=status; return r

    def rows(self):
        with sqlite3.connect(self.settings['budget_db']) as c:
            c.row_factory=sqlite3.Row
            return [dict(r) for r in c.execute('SELECT * FROM calls')]

    def test_failed_requests_consume_budget_and_third_is_blocked(self):
        with patch('backend.embedding.http.client.HTTPSConnection') as factory:
            factory.return_value.getresponse.return_value=self.response(status=429)
            for _ in range(2):
                with self.assertRaises(ModelError) as e: EmbeddingProvider(self.settings).embed(['test'])
                self.assertEqual(e.exception.code,'embedding_rate_limit')
            with self.assertRaises(ModelError) as e: EmbeddingProvider(self.settings).embed(['test'])
            self.assertEqual(e.exception.code,'embedding_budget_exhausted')
            self.assertEqual(factory.return_value.request.call_count,2)
        self.assertEqual([r['status'] for r in self.rows()],['failed','failed'])

    def test_process_restart_uses_same_allowance(self):
        RequestBudget(self.settings).reserve('text-embedding-v4',2)
        code="from backend.embedding import RequestBudget; import json,sys; RequestBudget(json.loads(sys.argv[1])).reserve('text-embedding-v4',2)"
        subprocess.run([sys.executable,'-B','-c',code,json.dumps(self.settings)],check=True,cwd=Path(__file__).resolve().parents[1])
        with self.assertRaises(ModelError) as e: RequestBudget(self.settings).reserve('text-embedding-v4',2)
        self.assertEqual(e.exception.code,'embedding_budget_exhausted')
        self.assertEqual(len(self.rows()),2)

    def test_concurrent_reservations_cannot_exceed_limit(self):
        self.settings['max_requests']=5
        def reserve(_):
            try: return RequestBudget(self.settings).reserve('text-embedding-v4',2)
            except ModelError as e: return e.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(reserve,range(16)))
        self.assertEqual(len(self.rows()),5)
        self.assertEqual(results.count('embedding_budget_exhausted'),11)

    def test_changed_limit_does_not_silently_expand_existing_approval(self):
        RequestBudget(self.settings).reserve('text-embedding-v4',2)
        self.settings['max_requests']=20
        with self.assertRaises(ModelError) as e: RequestBudget(self.settings).reserve('text-embedding-v4',2)
        self.assertEqual(e.exception.code,'embedding_budget_unavailable')

    def test_timeout_is_counted_without_automatic_retry(self):
        with patch('backend.embedding.http.client.HTTPSConnection') as factory:
            factory.return_value.getresponse.side_effect=TimeoutError()
            with self.assertRaises(ModelError) as e: EmbeddingProvider(self.settings).embed(['test'])
            self.assertEqual(e.exception.code,'embedding_connection')
            self.assertEqual(factory.return_value.request.call_count,1)
        self.assertEqual(self.rows()[0]['code'],'embedding_connection')

    def test_auth_payment_and_permission_errors_are_sanitized(self):
        self.settings['max_requests']=5
        with patch('backend.embedding.http.client.HTTPSConnection') as factory:
            for status,expected in [(401,'embedding_auth'),(403,'embedding_auth'),(402,'embedding_payment')]:
                factory.return_value.getresponse.return_value=self.response(status=status)
                with self.assertRaises(ModelError) as e: EmbeddingProvider(self.settings).embed(['test'])
                self.assertEqual(e.exception.code,expected)

    def test_wrong_dimension_count_nonfinite_and_model_rejected(self):
        self.settings['max_requests']=5
        cases=[{'data':[{'index':0,'embedding':[1.]}]},
               {'data':[]}, {'data':[{'index':0,'embedding':[float('nan'),1.]}]},
               {'model':'wrong','data':[{'index':0,'embedding':[1.,1.]}]}]
        with patch('backend.embedding.http.client.HTTPSConnection') as factory:
            for data in cases:
                factory.return_value.getresponse.return_value=self.response(data)
                with self.assertRaises(ModelError) as e: EmbeddingProvider(self.settings).embed(['test'])
                self.assertEqual(e.exception.code,'embedding_protocol')
        self.assertTrue(all(r['status']=='failed' for r in self.rows()))

    def test_success_audit_contains_usage_no_text_or_secret(self):
        with patch('backend.embedding.http.client.HTTPSConnection') as factory:
            factory.return_value.getresponse.return_value=self.response()
            vectors,usage=EmbeddingProvider(self.settings).embed(['PRIVATE_TEST_TEXT'])
        row=self.rows()[0]
        self.assertEqual((row['status'],row['tokens'],row['vendor_id']),('completed',3,'vendor-fixture'))
        self.assertNotIn('PRIVATE_TEST_TEXT',json.dumps(row))
        self.assertNotIn('fixture-not-a-real-key',json.dumps(row))
        self.assertEqual(usage,3); self.assertEqual(len(vectors),1)

    def test_invalid_input_or_unbounded_config_makes_no_request(self):
        with patch('backend.embedding.http.client.HTTPSConnection') as factory:
            for texts in ([],['x']*11,['x'*4001],None):
                with self.assertRaises(ModelError): EmbeddingProvider(self.settings).embed(texts)
            self.settings['max_requests']=None
            with self.assertRaises(ModelError): EmbeddingProvider(self.settings).embed(['test'])
            factory.assert_not_called()
        self.assertFalse(Path(self.settings['budget_db']).exists())

    def test_finite_norm_required(self):
        self.assertFalse(valid_vector([1e308,1e308],2))
        self.assertFalse(valid_vector([0.,0.],2))




class ExplicitUnlimitedBudgetTest(unittest.TestCase):
    setUp=EmbeddingBudgetTest.setUp
    tearDown=EmbeddingBudgetTest.tearDown
    def test_unlimited_requires_flag_and_records_calls_beyond_twenty(self):
        settings=dict(self.settings,max_requests=None,unlimited_authorized=True,approval_id='explicit-unlimited-test')
        for _ in range(22):RequestBudget(settings).reserve('text-embedding-v4',2)
        with sqlite3.connect(settings['budget_db']) as c:
            self.assertEqual(c.execute('SELECT request_limit FROM budgets').fetchone()[0],-1)
            self.assertEqual(c.execute('SELECT count(*) FROM calls').fetchone()[0],22)
    def test_cannot_bypass_bounded_ledger_by_only_setting_unlimited_flag(self):
        RequestBudget(self.settings).reserve('text-embedding-v4',2)
        settings=dict(self.settings,max_requests=None,unlimited_authorized=True)
        with self.assertRaises(ModelError) as e:RequestBudget(settings).reserve('text-embedding-v4',2)
        self.assertEqual(e.exception.code,'embedding_budget_unavailable')

if __name__=='__main__': unittest.main()
