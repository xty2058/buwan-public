import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.model import ModelSettings,CompatibleProvider,Cancellation

class DeepSeekAdapterTests(unittest.TestCase):
    def test_wire_temperature_non_thinking_and_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key';key.write_text('test-only')
            settings=ModelSettings(allow_live=True,provider='deepseek',model='test-model',base_url='https://example.test/v1',key_file=str(key),approval_id='test',max_calls=1,temperature=.7,prompt_version='linyu-v1.2')
            settings.validate()
            with patch('backend.model.http.client.HTTPSConnection') as connection:
                response=connection.return_value.getresponse.return_value
                response.status=200;response.getheader.return_value='text/event-stream'
                chunks=[{'choices':[{'delta':{'content':'你好'},'finish_reason':None}]},{'choices':[{'delta':{},'finish_reason':'stop'}]},{'usage':{'prompt_tokens':10,'completion_tokens':2},'choices':[]}]
                stream=io.BytesIO((''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode())
                response.readline.side_effect=stream.readline
                events=list(CompatibleProvider(settings).stream([{'role':'system','content':'hello'}],Cancellation()))
                payload=json.loads(connection.return_value.request.call_args.kwargs['body'])
                self.assertEqual(payload['temperature'],.7)
                self.assertEqual(payload['thinking'],{'type':'disabled'})
                self.assertEqual(payload['max_tokens'],512)
                self.assertNotIn('max_completion_tokens',payload)
                self.assertIn({'delta':'你好'},events)
                self.assertIn({'usage':{'input_tokens':10,'output_tokens':2}},events)

    def test_registered_linyu_prompts_validate(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key';key.write_text('test-only')
            for version in ('linyu-v1.2','linyu-v1.4'):
                ModelSettings(allow_live=True,provider='deepseek',model='m',base_url='https://example.test/v1',key_file=str(key),approval_id='t',max_calls=1,temperature=.7,prompt_version=version).validate()

    def test_invalid_temperature_and_wrong_prompt_provider(self):
        for extra in [{'temperature':float('nan')},{'temperature':3},{'provider':'aliyun-qwen','prompt_version':'linyu-v1.2'},{'provider':'aliyun-qwen','prompt_version':'linyu-v1.4'},{'provider':'deepseek','prompt_version':'linyu-v1.3'}]:
            with self.assertRaises(ValueError):ModelSettings(**extra).validate()
