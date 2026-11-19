"""Isolated model comparison: no conversation, memory or TTS writes."""
import json
import os
import time
import uuid
import math
import hashlib
import threading
import http.client
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from .errors import ApiError

DATA = json.loads(Path(__file__).with_name('evaluation_cases.json').read_text())
# Labels are placeholders; concrete model IDs and endpoints are supplied by the operator.
MODELS = [('deepseek', 'DeepSeek'), ('qwen', '通义千问'), ('doubao', '豆包'), ('kimi', 'Kimi'), ('glm', '智谱 GLM'), ('minimax', 'MiniMax')]


CONFIG_PATH = Path(__file__).resolve().parent.parent/'runtime/evaluation-config.json'
CONFIG_LOCK = threading.Lock()


def local_config():
    try: return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError): return {}


def config_value(model, field):
    return os.getenv('EVAL_'+model.upper()+'_'+field, '').strip() or str(local_config().get(model,{}).get(field,'')).strip()


def save_config(body):
    model=body.get('model')
    if model not in dict(MODELS): raise ApiError(400,'invalid_model','模型不存在')
    fields=('BASE_URL','MODEL','API_KEY','INPUT_PRICE_PER_MILLION','OUTPUT_PRICE_PER_MILLION','CACHED_INPUT_PRICE_PER_MILLION','CURRENCY')
    values={k:body.get(k,'') for k in fields}
    if any(not isinstance(v,str) or len(v)>4096 for v in values.values()):
        raise ApiError(400,'invalid_configuration','配置格式不正确')
    values={k:v.strip() for k,v in values.items()}
    parsed=urlsplit(values['BASE_URL'])
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ApiError(400,'invalid_configuration','请填写 HTTPS 兼容接口前缀地址')
    if not values['MODEL']: raise ApiError(400,'invalid_configuration','请填写模型 ID')
    if any(c.isspace() for c in values['API_KEY']):raise ApiError(400,'invalid_configuration','密钥不能包含空白字符')
    for field in fields[3:6]:
        if values[field]:
            try:
                price=Decimal(values[field])
                if not price.is_finite() or price<0:raise ValueError()
            except (ValueError,InvalidOperation):raise ApiError(400,'invalid_price','单价须为非负数字') from None
    with CONFIG_LOCK:
        saved=local_config()
        if not values['API_KEY']:values['API_KEY']=saved.get(model,{}).get('API_KEY','')
        if not values['API_KEY'] and not config_value(model,'API_KEY'):
            raise ApiError(400,'missing_key','首次配置请填写 API Key')
        saved[model]=values
        CONFIG_PATH.parent.mkdir(parents=True,exist_ok=True)
        temporary=CONFIG_PATH.with_name('evaluation-config-'+uuid.uuid4().hex+'.tmp')
        fd=os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w') as out:json.dump(saved,out,ensure_ascii=False)
            os.replace(temporary,CONFIG_PATH)
        finally:
            if temporary.exists():temporary.unlink()
    return {'saved':True,'model':model,'configured':all(settings(model).values())}


def settings(model):
    if model not in dict(MODELS):
        raise ApiError(400, 'invalid_model', '请选择列表中的回复模型')
    prefix = 'EVAL_'+model.upper()+'_'
    return {k: config_value(model,k) for k in ('BASE_URL', 'MODEL', 'API_KEY')}


def catalogue():
    return {**DATA, 'models': [{'id': id, 'label': label, 'configured': all(settings(id).values()), 'base_url':settings(id)['BASE_URL'], 'model':settings(id)['MODEL']} for id, label in MODELS]}


def prompt_card(version='baseline-v1'):
    if version == 'baseline-v1': return DATA['card']
    if version in ('linyu-v1.1', 'linyu-v1.2', 'linyu-v1.4', 'linyu-qwen-v1', 'linyu-qwen-v2', 'linyu-qwen-v3'): return (Path(__file__).parent/'prompts'/f'{version}.txt').read_text(encoding='utf-8').strip()
    raise ApiError(400,'invalid_prompt_version','提示词版本不存在')


def build_messages(body):
    if body.get('prompt_version') in ('linyu-qwen-v1', 'linyu-qwen-v2', 'linyu-qwen-v3') and body.get('model') != 'qwen':
        raise ApiError(400,'prompt_model_mismatch','千问专用提示词仅可用于通义千问；DeepSeek 请保留 v1.2')
    card=prompt_card(body.get('prompt_version','baseline-v1'))
    if body.get('case_id') == 'CUSTOM':
        content = body.get('input')
        if not isinstance(content, str) or not content.strip() or len(content) > 4000:
            raise ApiError(400, 'invalid_input', '请输入 1–4000 字的对话内容')
        return [{'role':'system','content':card}, {'role':'user','content':content.strip()}]
    case = next((c for c in DATA['cases'] if c['id'] == body.get('case_id')), None)
    if not case:
        raise ApiError(400, 'invalid_case', '测试题不存在')
    messages = [{'role': 'system', 'content': card}]
    if case['context']:
        messages.append({'role': 'system', 'content': '本题有效状态：\n'+case['context']})
    messages.extend(case['history'])
    if case['id'] == 'S02':
        history = body.get('s02_history')
        if not isinstance(history, list) or not history or len(history)>20 or any(not isinstance(m,dict) or m.get('role') not in ('user','assistant') or not isinstance(m.get('content'),str) or not m['content'].strip() or len(m['content'])>2000 for m in history) or history[-1]['role']!='assistant':
            raise ApiError(400, 'context_required', 'S02 请补充以林屿回复结尾的完整前文')
        messages.extend({'role':m['role'],'content':m['content']} for m in history)
    messages.append({'role':'user','content':case['input']})
    return messages


def estimate_cost(model, usage):
    """Snapshot operator-supplied prices; never invent a bill or missing usage."""
    prefix = 'EVAL_'+model.upper()+'_'
    result = {'status':'unknown', 'amount':None, 'currency':config_value(model,'CURRENCY') or 'CNY',
              'basis':'configured_price_estimate', 'prices_per_million':{},
              'reason':'未配置单价；费用未知，不代表免费'}
    try:
        prices = {name: Decimal(config_value(model,name)) for name in
                  ('INPUT_PRICE_PER_MILLION','OUTPUT_PRICE_PER_MILLION')}
        cached_price = config_value(model,'CACHED_INPUT_PRICE_PER_MILLION')
        if cached_price: prices['CACHED_INPUT_PRICE_PER_MILLION'] = Decimal(cached_price)
        if any(not v.is_finite() or v < 0 for v in prices.values()): raise ValueError()
    except (InvalidOperation, ValueError): return result
    result['prices_per_million'] = {k:str(v) for k,v in prices.items()}
    if not isinstance(usage,dict):
        result['reason']='服务商未返回 Token 用量，费用未知';return result
    incoming,outgoing=usage.get('prompt_tokens'),usage.get('completion_tokens')
    if any(type(v) is not int or v<0 for v in (incoming,outgoing)):
        result['reason']='输入或输出 Token 不完整，费用未知';return result
    cached=usage.get('prompt_cache_hit_tokens', (usage.get('prompt_tokens_details') or {}).get('cached_tokens',0))
    if type(cached) is not int or not 0<=cached<=incoming:
        result['reason']='缓存 Token 数据无效，费用未知';return result
    cache_rate=prices.get('CACHED_INPUT_PRICE_PER_MILLION',prices['INPUT_PRICE_PER_MILLION'])
    amount=((incoming-cached)*prices['INPUT_PRICE_PER_MILLION']+cached*cache_rate+outgoing*prices['OUTPUT_PRICE_PER_MILLION'])/Decimal(1000000)
    result.update(status='estimated',amount=str(amount),reason='按本次 Token 和配置单价估算，最终以服务商账单为准',
                  input_tokens=incoming,output_tokens=outgoing,cached_input_tokens=cached,
                  cache_discount_applied='CACHED_INPUT_PRICE_PER_MILLION' in prices)
    return result


def generate(body):
    config=settings(body.get('model'))
    messages=build_messages(body)
    temperature=body.get('temperature',0.7)
    tokens=body.get('max_tokens',512)
    if type(temperature) not in (int,float) or not math.isfinite(temperature) or not 0<=temperature<=2 or type(tokens) is not int or not 32<=tokens<=4096:
        raise ApiError(400,'invalid_parameters','温度需在 0–2，输出上限需在 32–4096')
    if not all(config.values()):
        raise ApiError(409,'model_not_configured','该模型尚未配置 API；请先填写服务端环境变量')
    parsed=urlsplit(config['BASE_URL'])
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ApiError(409,'invalid_configuration','模型地址需使用无凭证、查询参数的 HTTPS 地址')
    payload={'model':config['MODEL'],'messages':messages,'stream':False,'temperature':temperature,'max_tokens':tokens}
    if body['model']=='deepseek':
        payload['thinking']={'type':'disabled'}
    if body['model']=='qwen':
        payload['enable_thinking']=False
    started=time.monotonic()
    conn=http.client.HTTPSConnection(parsed.hostname,parsed.port or 443,timeout=60)
    try:
        conn.request('POST',parsed.path.rstrip('/')+'/chat/completions',json.dumps(payload,ensure_ascii=False).encode(),{'Authorization':'Bearer '+config['API_KEY'],'Content-Type':'application/json'})
        response=conn.getresponse()
        if response.status!=200:
            raise ApiError(502,'provider_error','模型服务返回 HTTP '+str(response.status)+'；未自动重试')
        raw=response.read(1048577)
        if len(raw)>1048576:raise ValueError()
        data=json.loads(raw)
        choice=data['choices'][0]
        text=choice['message']['content']
        if not isinstance(text,str) or not text.strip():raise ValueError()
        return {'id':uuid.uuid4().hex,'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'case_id':body['case_id'],'model_id':body['model'],'model':config['MODEL'],'returned_model':data.get('model'),'system_fingerprint':data.get('system_fingerprint'),'prompt_version':body.get('prompt_version','baseline-v1'),'prompt_sha256':hashlib.sha256(messages[0]['content'].encode()).hexdigest(),'temperature':temperature,'max_tokens':tokens,'thinking':payload.get('thinking'),'enable_thinking':payload.get('enable_thinking'),'messages':messages,'reply':text,'usage':data.get('usage'), 'cost':estimate_cost(body['model'],data.get('usage')), 'finish_reason':choice.get('finish_reason'),'latency_ms':round((time.monotonic()-started)*1000),'score':None,'note':'','blocker':False,'scope':'独立回复评测，不验证产品数据库状态变更'}
    except ApiError:raise
    except Exception:
        raise ApiError(502,'provider_failed','模型连接或响应失败；未自动重试') from None
    finally:conn.close()
