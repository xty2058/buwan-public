import json
import unittest
from unittest.mock import patch
from backend import evaluation as ev
from backend.errors import ApiError

class EvaluationTests(unittest.TestCase):
    def test_prompt_version_changes_only_role_card(self):
        history=[{'role':'user','content':'今天下班真的累死我了。'},
                 {'role':'assistant','content':'先坐会儿吧。想说说今天的事，还是安静待一会儿？'}]
        for case in ev.DATA['cases']:
            body={'case_id':case['id'],'s02_history':history}
            before=ev.build_messages(body)
            after=ev.build_messages({**body,'prompt_version':'linyu-v1.1'})
            self.assertNotEqual(before[0],after[0])
            self.assertEqual(before[1:],after[1:])
        deleted=json.dumps(ev.build_messages({'case_id':'M03','prompt_version':'linyu-v1.1'}),ensure_ascii=False)
        self.assertNotIn('小白',deleted)
        with self.assertRaises(ApiError):
            ev.build_messages({'case_id':'C01','prompt_version':'../other'})

    def test_prompt_v12_changes_only_role_card(self):
        history=[{'role':'user','content':'今天下班真的累死我了。'},
                 {'role':'assistant','content':'先坐会儿吧。想说说今天的事，还是安静待一会儿？'}]
        for case in ev.DATA['cases']:
            body={'case_id':case['id'],'s02_history':history}
            before=ev.build_messages(body)
            after=ev.build_messages({**body,'prompt_version':'linyu-v1.2'})
            self.assertNotEqual(before[0],after[0])
            self.assertEqual(before[1:],after[1:])
        deleted=json.dumps(ev.build_messages({'case_id':'M03','prompt_version':'linyu-v1.2'}),ensure_ascii=False)
        self.assertNotIn('小白',deleted)
        with self.assertRaises(ApiError):
            ev.build_messages({'case_id':'C01','prompt_version':'../other'})

    def test_qwen_prompt_isolation(self):
        for model in ('deepseek','kimi','qwen'):
            body={'model':model,'case_id':'M02','prompt_version':'linyu-qwen-v1'}
            if model != 'qwen':
                with self.assertRaises(ApiError): ev.build_messages(body)
            else:
                old=ev.build_messages({**body,'prompt_version':'linyu-v1.2'})
                new=ev.build_messages(body)
                self.assertNotEqual(old[0],new[0])
                self.assertEqual(old[1:],new[1:])
        self.assertEqual(ev.build_messages({'model':'deepseek','case_id':'C01','prompt_version':'linyu-v1.2'})[0]['content'],ev.prompt_card('linyu-v1.2'))

    def test_qwen_v2_prompt_isolation(self):
        for model in ('deepseek','kimi','qwen'):
            body={'model':model,'case_id':'M02','prompt_version':'linyu-qwen-v2'}
            if model != 'qwen':
                with self.assertRaises(ApiError): ev.build_messages(body)
            else:
                old=ev.build_messages({**body,'prompt_version':'linyu-v1.2'})
                new=ev.build_messages(body)
                self.assertNotEqual(old[0],new[0])
                self.assertEqual(old[1:],new[1:])
        self.assertEqual(ev.build_messages({'model':'deepseek','case_id':'C01','prompt_version':'linyu-v1.2'})[0]['content'],ev.prompt_card('linyu-v1.2'))

    def test_qwen_v3_prompt_isolation(self):
        for model in ('deepseek','kimi','qwen'):
            body={'model':model,'case_id':'M02','prompt_version':'linyu-qwen-v3'}
            if model != 'qwen':
                with self.assertRaises(ApiError): ev.build_messages(body)
            else:
                old=ev.build_messages({**body,'prompt_version':'linyu-v1.2'})
                new=ev.build_messages(body)
                self.assertNotEqual(old[0],new[0])
                self.assertEqual(old[1:],new[1:])
        self.assertEqual(ev.build_messages({'model':'deepseek','case_id':'C01','prompt_version':'linyu-v1.2'})[0]['content'],ev.prompt_card('linyu-v1.2'))

    def test_v14_prompt_registered(self):
        from pathlib import Path
        text=(Path(ev.__file__).parent/'prompts/linyu-v1.4.txt').read_text(encoding='utf-8').strip()
        self.assertEqual(ev.prompt_card('linyu-v1.4'),text)
        old=ev.build_messages({'model':'deepseek','case_id':'E02','prompt_version':'linyu-v1.2'})
        new=ev.build_messages({'model':'deepseek','case_id':'E02','prompt_version':'linyu-v1.4'})
        self.assertEqual(new[0]['content'],text)
        self.assertEqual(old[1:],new[1:])
        with self.assertRaises(ApiError): ev.prompt_card('linyu-v1.3')

    def test_dataset_protocol(self):
        self.assertEqual(len(ev.DATA['cases']),30)
        self.assertEqual(sum(c['repeats'] for c in ev.DATA['cases']),94)
        self.assertEqual(sum(v[1] for v in ev.DATA['dimensions'].values()),100)
    def test_deleted_memory_not_sent(self):
        text=json.dumps(ev.build_messages({'case_id':'M03'}),ensure_ascii=False)
        for forbidden in ('小白','猫','去年'):self.assertNotIn(forbidden,text)
    def test_context_and_no_reference_leak(self):
        messages=ev.build_messages({'case_id':'X01'})
        self.assertEqual([m['role'] for m in messages],['system','user','assistant','user','user'])
        self.assertNotIn('参考',messages[0]['content'])
        self.assertIn('无糖红茶',json.dumps(ev.build_messages({'case_id':'M01'}),ensure_ascii=False))
    def test_s02_requires_fixed_context(self):
        with self.assertRaises(ApiError):ev.build_messages({'case_id':'S02'})
        history=[{'role':'user','content':'今天想坐一会儿。'},{'role':'assistant','content':'好，我去把灯打开。'}]
        self.assertEqual(ev.build_messages({'case_id':'S02','s02_history':history})[-2],history[-1])
    def test_unconfigured_never_calls(self):
        with patch.dict('os.environ',{},clear=True),patch.object(ev.http.client,'HTTPSConnection') as connect:
            with self.assertRaises(ApiError) as e:ev.generate({'model':'qwen','case_id':'C01'})
            self.assertEqual(e.exception.code,'model_not_configured');connect.assert_not_called()
    def test_mocked_provider_records_exact_parameters(self):
        env={'EVAL_QWEN_BASE_URL':'https://example.test/v1','EVAL_QWEN_MODEL':'test-model','EVAL_QWEN_API_KEY':'secret'}
        with patch.dict('os.environ',env,clear=True),patch.object(ev.http.client,'HTTPSConnection') as connect:
            response=connect.return_value.getresponse.return_value;response.status=200
            response.read.return_value=json.dumps({'choices':[{'message':{'content':'我还是喜欢雨天。'},'finish_reason':'stop'}],'usage':{'total_tokens':12}}).encode()
            result=ev.generate({'model':'qwen','case_id':'C03','temperature':0.5,'max_tokens':256})
            payload=json.loads(connect.return_value.request.call_args.args[2])
            self.assertEqual(payload['temperature'],0.5);self.assertEqual(payload['model'],'test-model')
            self.assertIs(payload['enable_thinking'],False)
            self.assertIs(result['enable_thinking'],False)
            self.assertEqual(result['usage']['total_tokens'],12);self.assertNotIn('secret',json.dumps(result))
            connect.return_value.close.assert_called_once()
    def test_catalogue_never_exposes_credentials(self):
        with patch.dict('os.environ',{'EVAL_QWEN_API_KEY':'SECRET'}):self.assertNotIn('SECRET',json.dumps(ev.catalogue()))
    def test_invalid_models_parameters(self):
        for body in ({'model':'bad','case_id':'C01'},{'model':'qwen','case_id':'missing'},{'model':'qwen','case_id':'C01','temperature':float('nan')},{'model':'qwen','case_id':'C01','max_tokens':9999}):
            with self.assertRaises(ApiError):ev.generate(body)

if __name__=='__main__':unittest.main()

class EvaluationRouteTests(unittest.TestCase):
    def test_local_evaluation_without_login_still_checks_origin(self):
        import tempfile
        from pathlib import Path
        from backend.api import Application
        from backend.config import Config
        from backend.model import CompatibleProvider
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            app=Application(Config(db=root/'db.sqlite3',log_dir=root/'logs',origin='http://test.local'),provider=CompatibleProvider(),memory_provider=False)
            headers={'Host':'test.local','Origin':'http://test.local','Content-Type':'application/json'}
            try:
                for path in ('/evaluation','/evaluation.js','/evaluation.css','/api/evaluation'):
                    self.assertEqual(app.handle('GET',path,headers).status,200)
                body=json.dumps({'model':'qwen','case_id':'C01'}).encode()
                with patch.object(ev,'generate',return_value={'reply':'fixture'}):
                    self.assertEqual(app.handle('POST','/api/evaluation/reply',headers,body).status,200)
                    self.assertEqual(app.handle('POST','/api/evaluation/reply',{**headers,'Origin':'https://other.test'},body).status,403)
                with app.db.connect() as c:
                    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',('evaluation-user','evaluation-user','unused','测试',1,1))
                    token,csrf=app.auth.issue(c,'evaluation-user')
                headers['Cookie']='weiwan_session='+token
                with patch.object(ev,'generate',return_value={'reply':'fixture'}):
                    self.assertEqual(app.handle('POST','/api/evaluation/reply',headers,body).status,200)
                headers['X-CSRF-Token']=csrf
                with patch.dict('os.environ',{},clear=True),patch.object(ev.http.client,'HTTPSConnection') as connect:
                    self.assertEqual(app.handle('POST','/api/evaluation/reply',headers,body).status,409)
                    connect.assert_not_called()
            finally:
                for handler in list(app.logger.handlers):handler.close();app.logger.removeHandler(handler)

class CostAndConfigTests(unittest.TestCase):
    def test_cost_with_cache_and_missing_usage(self):
        env={'EVAL_DEEPSEEK_INPUT_PRICE_PER_MILLION':'2','EVAL_DEEPSEEK_OUTPUT_PRICE_PER_MILLION':'3','EVAL_DEEPSEEK_CACHED_INPUT_PRICE_PER_MILLION':'0.2'}
        with patch.object(ev,'local_config',return_value={}),patch.dict('os.environ',env,clear=True):
            result=ev.estimate_cost('deepseek',{'prompt_tokens':1000,'completion_tokens':100,'prompt_cache_hit_tokens':500})
            self.assertEqual(result['amount'],'0.0014')
            self.assertEqual(result['status'],'estimated')
            self.assertEqual(ev.estimate_cost('deepseek',None)['status'],'unknown')
        with patch.object(ev,'local_config',return_value={}),patch.dict('os.environ',{},clear=True):
            self.assertIsNone(ev.estimate_cost('deepseek',{'prompt_tokens':1,'completion_tokens':1})['amount'])
    def test_local_key_write_permissions_and_no_leak(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp,patch.object(ev,'CONFIG_PATH',Path(temp)/'config.json'),patch.dict('os.environ',{},clear=True):
            result=ev.save_config({'model':'deepseek','BASE_URL':'https://example.test/v1','MODEL':'sample','API_KEY':'test-secret'})
            self.assertTrue(result['configured'])
            self.assertEqual(ev.CONFIG_PATH.stat().st_mode & 0o777,0o600)
            self.assertNotIn('test-secret',json.dumps(ev.catalogue()))
            ev.save_config({'model':'deepseek','BASE_URL':'https://example.test/v1','MODEL':'sample','API_KEY':''})
            self.assertEqual(ev.settings('deepseek')['API_KEY'],'test-secret')
