# -*- coding: utf-8 -*-
"""语音输入 (roadmap 2.6): one short recording in, text for the input box out.

The server never sends the text as a message: the client puts it in the input
box and the user decides. Audio is held in memory for the one provider call
and is not written to disk or the database; only the call is audited
(purpose 'asr', with seconds of audio as its input size).

The browser converts every recording to 16 kHz mono 16-bit WAV before upload,
so this endpoint accepts exactly one format and can check it byte by byte.

Provider: Alibaba Cloud Model Studio `qwen3-asr-flash` through its
OpenAI-compatible endpoint (non-streaming, audio as a base64 data URI),
per https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference .
Like every live provider here it stays off until `runtime/asr-config.json`
names a credential file, an approval ID and a call limit.

Record-then-fill-the-box flow follows NextChat's speech input
(https://github.com/ChatGPTNextWeb/NextChat, MIT, behaviour only); no code copied.
"""
import base64
import http.client
import json
import re
import struct
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ApiError
from .model import ModelError, ModelSettings
from .suggest import RateLimit

MAX_SECONDS = 30
MIN_SECONDS = 0.3
SAMPLE_RATE = 16000
MAX_BYTES = 44 + MAX_SECONDS * SAMPLE_RATE * 2 + 4096
MAX_TEXT = 500
PER_MINUTE = 10
CONFIG = Path(__file__).resolve().parent.parent / 'runtime/asr-config.json'


AUDIO_ROUTE = re.compile(r'/api/conversations/[a-f0-9]{32}/transcriptions')


def body_limit(method, path, content_type):
    """JSON stays tiny everywhere; only a WAV posted to the voice-input route may be larger."""
    if method == 'POST' and AUDIO_ROUTE.fullmatch(urlsplit(path).path) and (content_type or '').lower() == 'audio/wav':
        return MAX_BYTES
    return 16384


def wav_seconds(raw):
    """Duration of a 16 kHz mono 16-bit PCM WAV, or None if it is anything else."""
    if len(raw) < 44 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        return None
    position, fmt, data = 12, None, None
    while position + 8 <= len(raw):
        tag, size = raw[position:position + 4], struct.unpack('<I', raw[position + 4:position + 8])[0]
        body = raw[position + 8:position + 8 + size]
        if tag == b'fmt ' and len(body) >= 16:
            fmt = struct.unpack('<HHIIHH', body[:16])
        elif tag == b'data':
            data = len(body)
            break
        position += 8 + size + (size & 1)
    if not fmt or data is None or (fmt[0], fmt[1], fmt[2], fmt[5]) != (1, 1, SAMPLE_RATE, 16):
        return None
    return data / (SAMPLE_RATE * 2)


class ASRProvider:
    mode = 'live'

    def __init__(self, settings=None):
        self.settings = settings or ModelSettings()

    @classmethod
    def from_config(cls):
        # The file holds a credential path, never a credential value.
        if not CONFIG.is_file():
            return cls()
        try:
            settings = ModelSettings(**json.loads(CONFIG.read_text(encoding='utf-8')))
            settings.validate()
            return cls(settings)
        except (ValueError, TypeError, OSError):
            return cls()

    @property
    def configured(self):
        return self.settings.allow_live

    def transcribe(self, wav):
        settings = self.settings
        if not self.configured:
            raise ModelError('asr_unconfigured')
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
        payload = json.dumps({'model': settings.model, 'stream': False, 'asr_options': {'language': 'zh', 'enable_itn': True},
                              'messages': [{'role': 'user', 'content': [{'type': 'input_audio', 'input_audio': {
                                  'data': 'data:audio/wav;base64,' + base64.b64encode(wav).decode()}}]}]}).encode()
        conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=settings.timeout)
        try:
            conn.request('POST', parsed.path.rstrip('/') + '/chat/completions', body=payload,
                         headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
            response = conn.getresponse()
            if response.status != 200:
                # No retry or fallback: another billable attempt needs the user to record again.
                raise ModelError('provider_http_' + str(response.status))
            data = json.loads(response.read(262144).decode('utf-8'))
            text = data['choices'][0]['message']['content']
            if not isinstance(text, str):
                raise ValueError()
            usage = data.get('usage') or {}
            return text, {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'seconds') if type(usage.get(k)) is int}
        except ModelError:
            raise
        except TimeoutError:
            raise ModelError('provider_timeout') from None
        except (OSError, http.client.HTTPException, ValueError, UnicodeError, KeyError, IndexError, TypeError):
            raise ModelError('provider_connection') from None
        finally:
            key = ''
            conn.close()


class Transcriptions:
    def __init__(self, db, service, memory, provider=None):
        self.db, self.service, self.memory = db, service, memory
        # None: read runtime/asr-config.json; False: off (isolated tests); otherwise the given provider.
        self.provider = ASRProvider.from_config() if provider is None else (provider or ASRProvider())
        self.limit = RateLimit(PER_MINUTE)

    def capabilities(self):
        return {'voice_input': bool(self.provider.configured), 'voice_input_max_seconds': MAX_SECONDS}

    def transcribe(self, user, cid, wav):
        if not self.provider.configured:
            raise ApiError(503, 'asr_unconfigured', '语音输入尚未开通，可以直接打字')
        seconds = wav_seconds(wav)
        if seconds is None:
            raise ApiError(422, 'invalid_audio', '录音格式不正确，请重新录一次')
        if seconds < MIN_SECONDS:
            raise ApiError(422, 'audio_too_short', '说话时间太短，没有录到')
        if seconds > MAX_SECONDS + 0.5:
            raise ApiError(413, 'audio_too_long', '一次最多说 ' + str(MAX_SECONDS) + ' 秒')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.service.own(c, cid, user['id'])
            self.limit.check(user['id'])
            limit = self.provider.settings.max_calls
            if limit is not None and c.execute("SELECT count(*) FROM memory_call_audit WHERE purpose='asr'").fetchone()[0] >= limit:
                raise ApiError(429, 'asr_budget_exhausted', '语音输入的额度用完了，可以直接打字')
            call = self.memory.audit(c, cid, 'asr', self.provider)
        try:
            text, usage = self.provider.transcribe(wav)
        except ModelError as error:
            with self.db.connect() as c:
                self.memory.audit(c, cid, 'asr', self.provider, 'failed', error=error.code, call=call)
            raise ApiError(502, 'asr_failed', '没有识别出来，可以再说一次或直接打字') from None
        with self.db.connect() as c:
            # Audio seconds are the billed input; the provider's token counts are kept as given.
            self.memory.audit(c, cid, 'asr', self.provider, 'completed',
                              {'input_tokens': usage.get('prompt_tokens'), 'output_tokens': usage.get('completion_tokens')}, call=call)
        text = ' '.join(text.split())[:MAX_TEXT]
        if not text:
            raise ApiError(422, 'audio_empty', '没有听到说话声，可以再说一次')
        return {'text': text, 'seconds': round(seconds, 1)}
