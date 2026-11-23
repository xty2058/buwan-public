"""Server-only Chat Completions wire adapter. No default supplier or credential discovery."""
import json
import math
import os
import socket
import threading
import time
import http.client
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

# 林屿 DeepSeek 专属提示词的已注册版本；v1.4、v1.2 保留用于回退。
LINYU_DEEPSEEK_PROMPTS = ('linyu-v1.8',)


class ModelError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)  # Never propagate a vendor response, URL, or key.


@dataclass(frozen=True)
class ModelSettings:
    allow_live: bool = False
    provider: str = ''
    model: str = ''
    base_url: str = ''
    key_file: str = field(default='', repr=False)
    approval_id: str = ''
    max_calls: object = 0
    max_output_tokens: int = 512
    timeout: float = 45
    input_price: str = ''
    output_price: str = ''
    currency: str = ''
    temperature: object = None
    prompt_version: str = ''

    @classmethod
    def from_env(cls):
        # A disabled adapter does not even inspect credential environment variables/files.
        flag=os.getenv('STEP5_ALLOW_LIVE_CALLS', '').lower()
        local=Path(__file__).resolve().parent.parent/'runtime/reply-config.json'
        if not local.is_file():
            local=local.with_name('qwen-config.json')
        if not flag and local.is_file():
            settings=cls(**json.loads(local.read_text(encoding='utf-8')))
            settings.validate()
            return settings
        if flag != 'true':
            return cls()
        settings = cls(True, os.getenv('STEP5_PROVIDER', ''), os.getenv('STEP5_MODEL', ''),
                       os.getenv('STEP5_BASE_URL', ''), os.getenv('STEP5_KEY_FILE', ''),
                       os.getenv('STEP5_APPROVAL_ID', ''), None if os.getenv('STEP5_MAX_CALLS')=='unlimited' else int(os.getenv('STEP5_MAX_CALLS', '0')),
                       int(os.getenv('STEP5_MAX_OUTPUT_TOKENS', '512')), float(os.getenv('STEP5_TIMEOUT', '45')),
                       os.getenv('STEP5_INPUT_PRICE_PER_MILLION', ''), os.getenv('STEP5_OUTPUT_PRICE_PER_MILLION', ''),
                       os.getenv('STEP5_CURRENCY', ''),
                       float(os.environ['STEP5_TEMPERATURE']) if os.getenv('STEP5_TEMPERATURE') else None,
                       os.getenv('STEP5_PROMPT_VERSION', ''))
        settings.validate()
        return settings

    def validate(self):
        if self.temperature is not None and (type(self.temperature) not in (int,float) or not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2):
            raise ValueError('Invalid temperature')
        if self.prompt_version and (self.prompt_version not in LINYU_DEEPSEEK_PROMPTS or self.provider != 'deepseek'):
            raise ValueError('Reply prompt requires DeepSeek with a registered Linyu prompt version')
        parsed = urlsplit(self.base_url)
        if not self.allow_live or not all((self.provider, self.model, self.approval_id, self.key_file)):
            raise ValueError('Explicit provider, model, credential file and approval ID are required')
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Live model base URL must be HTTPS with no credentials/query/fragment')
        if (self.max_calls is not None and (type(self.max_calls) is not int or not 1 <= self.max_calls <= 10000)) or not 1 <= self.max_output_tokens <= 4096 or not 1 <= self.timeout <= 120:
            raise ValueError('Invalid explicit call/output/time limits')
        if bool(self.input_price) != bool(self.output_price) or bool(self.input_price) != bool(self.currency):
            raise ValueError('Both prices and a currency are needed for cost estimates')
        for price in (self.input_price, self.output_price):
            if price:
                try:
                    value = Decimal(price)
                    if not value.is_finite() or value < 0:
                        raise ValueError('Invalid price')
                except InvalidOperation:
                    raise ValueError('Invalid price') from None


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.connection = None

    def attach(self, connection):
        with self.lock:
            self.connection = connection
            if self.event.is_set():
                connection.close()
                raise ModelError('cancelled')

    def cancel(self):
        self.event.set()
        with self.lock:
            conn = self.connection
            if conn:
                # Interrupt socket reads, including a provider that has stopped emitting tokens.
                try:
                    if conn.sock:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()


class CompatibleProvider:
    mode = 'live'

    def __init__(self, settings=None):
        self.settings = settings or ModelSettings()

    @property
    def configured(self):
        return self.settings.allow_live

    @property
    def label(self):
        return self.settings.model if self.configured else '未配置真实模型'

    def stream(self, messages, cancellation):
        settings = self.settings
        if not self.configured:
            raise ModelError('model_not_configured')
        # Only read the exact explicitly selected file at the moment of an authorized call.
        try:
            path = Path(settings.key_file).expanduser()
            if not path.is_file() or path.stat().st_size > 4096:
                raise OSError()
            key = path.read_text(encoding='utf-8').strip()
            if not key or any(ch.isspace() for ch in key):
                raise OSError()
        except (OSError, UnicodeError):
            raise ModelError('credential_unavailable') from None
        parsed = urlsplit(settings.base_url)
        conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=settings.timeout)
        cancellation.attach(conn)
        deadline = time.monotonic()+settings.timeout
        parameters={'model':settings.model,'messages':messages,'stream':True,'stream_options':{'include_usage':True}}
        if settings.temperature is not None:
            parameters['temperature']=settings.temperature
        if settings.provider=='deepseek':
            parameters.update({'max_tokens':settings.max_output_tokens,'thinking':{'type':'disabled'}})
            if messages and messages[0].get('content','').startswith('WEIWAN_JSON_DIALOGUE_V1\n'):
                parameters['response_format']={'type':'json_object'}
        elif settings.provider=='aliyun-qwen':
            parameters.update({'max_tokens':settings.max_output_tokens,'enable_thinking':False})
            if messages and messages[0].get('content','').startswith('WEIWAN_JSON_DIALOGUE_V1\n'):
                parameters.update({'response_format':{'type':'json_object'},'temperature':0.4})
        else:
            parameters['max_completion_tokens']=settings.max_output_tokens
        payload=json.dumps(parameters,ensure_ascii=False).encode()
        try:
            conn.request('POST', parsed.path.rstrip('/')+'/chat/completions', body=payload,
                         headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json',
                                  'Accept': 'text/event-stream'})
            response = conn.getresponse()
            if response.status != 200:
                # No implicit retries, redirects or fallback: each new billable attempt requires user action.
                raise ModelError('provider_http_'+str(response.status))
            if 'text/event-stream' not in response.getheader('Content-Type', ''):
                raise ModelError('provider_protocol')
            yield from parse_stream(response, cancellation, deadline)
        except ModelError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, ValueError, UnicodeError):
            raise ModelError('cancelled' if cancellation.event.is_set() else 'provider_connection') from None
        finally:
            key = ''
            conn.close()


def parse_stream(response, cancellation, deadline):
    """SSE event framing, usage-only chunks, explicit finish, and early EOF rejection."""
    parts = []
    finished = False
    seen_done = False
    while True:
        if cancellation.event.is_set():
            raise ModelError('cancelled')
        if time.monotonic() > deadline:
            raise ModelError('provider_timeout')
        raw = response.readline(262145)
        if len(raw) > 262144:
            raise ModelError('provider_protocol')
        if not raw:
            break
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            parts.append(line[5:].lstrip(' '))
        elif not line and parts:
            value = '\n'.join(parts)
            parts = []
            if value == '[DONE]':
                seen_done = True
                break
            try:
                item = json.loads(value)
                if item.get('error'):
                    raise ModelError('provider_error')
                if item.get('usage'):
                    usage = item['usage']
                    incoming, outgoing = usage.get('prompt_tokens'), usage.get('completion_tokens')
                    if type(incoming) is int and type(outgoing) is int and incoming >= 0 and outgoing >= 0:
                        record = {'input_tokens': incoming, 'output_tokens': outgoing}
                        # Prefix-cache hits (roadmap 1.15): DeepSeek reports prompt_cache_hit_tokens,
                        # OpenAI-compatible providers report prompt_tokens_details.cached_tokens.
                        # Recorded only when present and plausible; absence stays NULL, never 0.
                        details = usage.get('prompt_tokens_details')
                        cached = usage.get('prompt_cache_hit_tokens', details.get('cached_tokens') if isinstance(details, dict) else None)
                        if type(cached) is int and 0 <= cached <= incoming:
                            record['cached_input_tokens'] = cached
                        yield {'usage': record}
                choices = item.get('choices', [])
                if choices:
                    choice = choices[0]
                    delta = choice.get('delta') or {}
                    text = delta.get('content') or ''
                    if not isinstance(text, str) or delta.get('tool_calls') or delta.get('function_call'):
                        raise ModelError('provider_protocol')
                    if text:
                        if finished:
                            raise ModelError('provider_protocol')
                        yield {'delta': text}
                    if choice.get('finish_reason'):
                        reason = choice['finish_reason']
                        if reason != 'stop':
                            raise ModelError('provider_incomplete_'+str(reason)[:30] if reason in ('length','content_filter','tool_calls','function_call') else 'provider_incomplete')
                        finished = True
                        yield {'finish_reason': 'stop'}
            except (ValueError, TypeError, AttributeError, IndexError):
                raise ModelError('provider_protocol') from None
    if not seen_done or not finished:
        raise ModelError('provider_interrupted')
