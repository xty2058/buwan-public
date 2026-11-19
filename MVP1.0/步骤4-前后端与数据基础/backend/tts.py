"""Opt-in Doubao TTS. Never synthesize at page load; no automatic paid retries.

Protocol source: https://docs.volcengine.com/docs/doubao-voice/unidirectional-streaming-text-to-speech-http?lang=zh
Voice source: https://docs.volcengine.com/docs/doubao-voice/tone-list-3?lang=zh
"""
import base64
import hashlib
import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from .errors import ApiError
from .service import uid, now

VOICES = {
    'linyu': {'id': 'ICL_uranus_zh_male_gaolengzongcai_tob', 'label': '高冷总裁 2.0', 'review_status': 'user_selected'},
    'luye': {'id': 'zh_male_yangguangqingnian_uranus_bigtts', 'label': '阳光青年 2.0', 'review_status': 'pending'},
    'shenyan': {'id': 'zh_male_gaolengchenwen_uranus_bigtts', 'label': '高冷沉稳 2.0', 'review_status': 'pending'},
}
ERRORS = {
    'tts_unconfigured': '角色语音尚未配置，文字聊天仍可使用。',
    'tts_budget_exhausted': '本轮语音合成额度已用完，可继续播放已有音频。',
    'tts_provider_failed': '语音合成失败，可点击重试。文字聊天仍可使用。',
    'tts_provider_timeout': '语音合成超时，可点击重试。',
    'tts_interrupted': '上次语音合成已中断，可点击重试。',
    'tts_source_unavailable': '这条回复已更新或失效，请使用当前有效回复。',
    'tts_invalid_audio': '语音返回不完整，可点击重试。',
}


@dataclass(frozen=True)
class TTSSettings:
    allow_live: bool = False
    key_file: str = field(default='', repr=False)
    approval_id: str = ''
    max_calls: int = 0
    max_chars: int = 0
    timeout: float = 45
    provider: str = "minimax"

    @classmethod
    def from_config(cls):
        # This file contains a credential path, never a credential value.
        path = Path(__file__).resolve().parent.parent / 'runtime/tts-config.json'
        if not path.is_file():
            return cls()
        try:
            settings = cls(**json.loads(path.read_text(encoding='utf-8')))
            settings.validate()
            return settings
        except (ValueError, TypeError, OSError):
            return cls()

    def validate(self):
        if self.provider not in ("minimax", "doubao"):
            raise ValueError("Unknown TTS provider")
        if not self.allow_live:
            return
        if (not self.key_file or not Path(self.key_file).is_absolute() or not self.approval_id
                or type(self.max_calls) is not int or not 1 <= self.max_calls <= 10000
                or type(self.max_chars) is not int or not 1 <= self.max_chars <= 10000000
                or not 1 <= self.timeout <= 90):
            raise ValueError('TTS requires credential file, approval ID and explicit call/character limits')


class TTSError(Exception):
    def __init__(self, code, details=None):
        self.details = details or {}
        self.code = code
        super().__init__(code)


def parse_audio(response, timeout=45):
    """Accept NDJSON chunks only after a complete terminal event.

    A transport EOF before the terminal code is not a playable partial success.
    """
    chunks, billable, finished, size = [], None, False, 0
    deadline = time.monotonic()+timeout
    while True:
        if time.monotonic() > deadline:
            raise TTSError('tts_provider_timeout')
        raw = response.readline(4 * 1024 * 1024 + 1)
        if not raw:
            break
        if len(raw) > 4 * 1024 * 1024:
            raise TTSError('tts_invalid_audio')
        if not raw.strip():
            continue
        try:
            part = json.loads(raw)
            code = part.get('code')
            if code not in (0, 20000000):
                raise TTSError('tts_provider_failed', {'provider_code':code,'provider_message':str(part.get('message',''))[:1000]})
            usage = part.get('usage') or {}
            if type(usage.get('text_words')) is int and usage['text_words'] >= 0:
                billable = usage['text_words']
            if part.get('data'):
                chunk = base64.b64decode(part['data'], validate=True)
                size += len(chunk)
                if size > 12 * 1024 * 1024:
                    raise TTSError('tts_invalid_audio')
                chunks.append(chunk)
            if code == 20000000:
                finished = True
                break
        except (ValueError, TypeError, AttributeError):
            raise TTSError('tts_invalid_audio')
    if not finished or not chunks:
        raise TTSError('tts_invalid_audio')
    audio = b''.join(chunks)
    if not (audio.startswith(b'ID3') or (len(audio) > 1 and audio[0] == 255 and audio[1] & 224 == 224)):
        raise TTSError('tts_invalid_audio')
    return audio, billable


class DoubaoProvider:
    mode = 'live'
    name = 'doubao-seed-tts-2.0'

    def __init__(self, settings=None):
        self.settings = settings or TTSSettings.from_config()
        self.settings.validate()
        self.configured = bool(self.settings.allow_live and self.settings.key_file and Path(self.settings.key_file).is_file())

    def synthesize(self, text, voice, request_id):
        if not self.configured:
            raise TTSError('tts_unconfigured')
        conn = None
        timer = None
        try:
            key = Path(self.settings.key_file).read_text(encoding='utf-8').strip()
            if not key or '\n' in key or '\r' in key:
                raise TTSError('tts_unconfigured')
            body = json.dumps({'req_params': {'text': text, 'speaker': voice,
                              'audio_params': {'format': 'mp3', 'sample_rate': 24000}}}, ensure_ascii=False).encode()
            conn = http.client.HTTPSConnection('openspeech.bytedance.com', timeout=self.settings.timeout)
            def expire():
                try:
                    if conn.sock:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
            timer = threading.Timer(self.settings.timeout, expire)
            timer.daemon = True
            timer.start()
            conn.request('POST', '/api/v3/tts/unidirectional', body, {
                'Content-Type': 'application/json', 'X-Api-Key': key,
                'X-Api-Resource-Id': 'seed-tts-2.0', 'X-Api-Request-Id': request_id,
                'X-Control-Require-Usage-Tokens-Return': '*',
            })
            response = conn.getresponse()
            if response.status != 200:
                raw = response.read(8192).decode('utf-8',errors='replace').replace(key,'[REDACTED]')
                raise TTSError('tts_provider_failed', {'http_status':response.status,
                    'request_id':request_id,'provider_log_id':response.getheader('X-Tt-Logid'),
                    'response_body':raw})
            return parse_audio(response, self.settings.timeout)
        except (TimeoutError, socket.timeout):
            raise TTSError('tts_provider_timeout')
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
            raise TTSError('tts_transport_failed', {'exception_type':type(exc).__name__,'request_id':request_id})
        finally:
            if timer:
                timer.cancel()
            if conn:
                conn.close()


class MiniMaxProvider:
    mode = 'live'
    name = 'minimax-speech-2.8-hd'
    price_per_10000 = 3.5
    voices = {'linyu': {'id': 'Chinese_casual_guide_nv1', 'label': '亲和向导', 'review_status': 'user_selected'}}

    def __init__(self, settings=None):
        self.settings = settings or TTSSettings.from_config()
        self.settings.validate()
        self.configured = bool(self.settings.allow_live and self.settings.key_file and Path(self.settings.key_file).is_file())

    def synthesize(self, text, voice, request_id):
        if not self.configured:
            raise TTSError('tts_unconfigured')
        if voice != 'Chinese_casual_guide_nv1':
            raise TTSError('tts_unconfigured')
        conn = None
        try:
            key = Path(self.settings.key_file).read_text().strip()
            if not key or '\n' in key or '\r' in key:
                raise TTSError('tts_unconfigured')
            body = {'model': 'speech-2.8-hd', 'text': text, 'stream': False, 'output_format': 'hex',
                    'voice_setting': {'voice_id': voice, 'speed': 1, 'vol': 1, 'pitch': 0},
                    'audio_setting': {'sample_rate': 24000, 'bitrate': 64000, 'format': 'mp3', 'channel': 1}}
            conn = http.client.HTTPSConnection('api.minimax.cn', timeout=self.settings.timeout)
            conn.request('POST', '/v1/t2a_v2', json.dumps(body),
                         {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
            response = conn.getresponse()
            raw = response.read(16*1024*1024+1)
            if len(raw) > 16*1024*1024:
                raise TTSError('tts_invalid_audio')
            try:
                data = json.loads(raw)
            except ValueError:
                raise TTSError('tts_provider_failed', {'http_status': response.status, 'request_id': request_id,
                    'response_body': raw[:8192].decode(errors='replace').replace(key, '[REDACTED]')})
            details = {'http_status': response.status, 'request_id': request_id, 'trace_id': data.get('trace_id'),
                       'base_resp': data.get('base_resp')}
            details = json.loads(json.dumps(details).replace(key, '[REDACTED]'))
            if response.status != 200 or data.get('base_resp', {}).get('status_code') != 0:
                raise TTSError('tts_provider_failed', details)
            audio = bytes.fromhex(data.get('data', {}).get('audio', ''))
            if not (audio.startswith(b'ID3') or (len(audio)>1 and audio[0]==255 and audio[1]&224==224)):
                raise TTSError('tts_invalid_audio', details)
            usage = data.get('extra_info', {}).get('usage_characters')
            return audio, usage if type(usage) is int and usage >= 0 else None
        except (TimeoutError, socket.timeout):
            raise TTSError('tts_provider_timeout', {'request_id': request_id})
        except (OSError, http.client.HTTPException, UnicodeError, ValueError, TypeError, AttributeError) as exc:
            raise TTSError('tts_provider_failed', {'request_id': request_id, 'exception_type': type(exc).__name__})
        finally:
            if conn:
                conn.close()


def configured_provider():
    settings = TTSSettings.from_config()
    return MiniMaxProvider(settings) if settings.provider == 'minimax' else DoubaoProvider(settings)


class TTS:
    def __init__(self, db, service, provider=None, chat=None):
        self.db, self.service, self.chat = db, service, chat
        self.provider = provider or configured_provider()
        self.voices = getattr(self.provider, "voices", VOICES)
        self.workers = set()
        self.guard = threading.Lock()

    def capabilities(self):
        return {'tts_configured': self.provider.configured, 'tts_provider': self.provider.name,
                'tts_voices': self.voices}

    def _relationship_blocks_voice(self, c, user, cid, message, use_id=None):
        if not (self.chat and self.chat.relationship):
            return False
        rel = self.chat.relationship
        record = c.execute('SELECT i.* FROM relationship_inputs i JOIN generation_requests g ON g.id=i.generation_id WHERE g.result_message_id=?', (message['id'],)).fetchone()
        if not record:
            return False
        old = json.loads(record['effective_state'])
        use = rel.use(c, cid, user.get('session_id'), use_id)
        state = rel.effective(c, cid, use)
        current = c.execute('SELECT * FROM relationship_states WHERE conversation_id=?', (cid,)).fetchone()
        for dimension, oldkey, value, basiskey in [('relation', 'relationship_type', 'romantic', 'basis_event_id'), ('familiarity', 'familiarity', 'close', 'familiarity_basis_id')]:
            basis = use['temporary_'+dimension+'_event_id'] if use and use['temporary_'+dimension] else current[basiskey]
            if old.get(oldkey) == value and (state[oldkey] != value or state['intimacy_paused'] or not rel.authority_valid(c, cid, record, old, state, use, basis, dimension)):
                return True
        return False

    def _source_unavailable(self, c, user, cid, message, use_id=None):
        if not message or not message['content'].strip():
            return True
        if c.execute('SELECT 1 FROM memory_context_blocks WHERE conversation_id=? AND message_id=?', (cid, message['id'])).fetchone():
            return True
        if self.chat and self.chat.memory and not self.chat.memory.filter_context(c, cid, [message]):
            return True
        if self.chat and message['reply_to_id'] and not self.chat.source_available(c, cid, message['reply_to_id'], user.get('session_id'), use_id):
            return True
        return self._relationship_blocks_voice(c, user, cid, message, use_id)

    def voice_blocked_map(self, c, user, cid, messages, use_id=None):
        """Batch check for a conversation page: message_id -> True when the reply can no longer be voiced."""
        if self.chat and self.chat.memory:
            blocked = self.chat.memory.blocked_context_ids(c, cid)
        else:
            blocked = {r[0] for r in c.execute('SELECT message_id FROM memory_context_blocks WHERE conversation_id=?', (cid,))}
        result = {}
        for m in messages:
            if m.get('role') != 'assistant' or m.get('status') != 'completed' or not m.get('reply_to_id'):
                continue
            if not (m.get('content') or '').strip() or m['id'] in blocked or self._source_unavailable(c, user, cid, m, use_id):
                result[m['id']] = True
        return result

    def source(self, c, user, cid, mid, use_id=None):
        conv = self.service.own(c, cid, user['id'])
        message = c.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND role='assistant' AND status='completed'", (mid, cid)).fetchone()
        if self._source_unavailable(c, user, cid, message, use_id):
            raise ApiError(409, 'tts_source_unavailable', ERRORS['tts_source_unavailable'])
        voice = self.voices.get(conv['character_id'])
        if not voice:
            raise ApiError(422, 'tts_role_unavailable', '该角色暂未配置音色')
        fingerprint = hashlib.sha256(json.dumps([message['content'], voice['id'], self.provider.name, 'mp3', 24000], ensure_ascii=False).encode()).hexdigest()
        return dict(message), voice, fingerprint

    def public(self, row, cid, mid, use_id=None, cached=False):
        from urllib.parse import urlencode
        suffix = '?' + urlencode({'use_id': use_id}) if use_id else ''
        with self.db.connect() as c:
            metric = c.execute('SELECT * FROM tts_metrics WHERE audio_id=? AND attempt=?', (row['id'], row['attempt'])).fetchone()
        metrics = dict(metric) if metric else {}
        return {'elapsed_seconds': metrics.get('elapsed_seconds'), 'estimated_cny': metrics.get('estimated_cny'),
                'billing_verified': False, 'provider': metrics.get('provider', self.provider.name),
                'error_details': json.loads(metrics.get('error_details') or '{}'),
                'id': row['id'], 'status': row['status'], 'attempt': row['attempt'], 'message_id': mid,
                'voice_id': row['voice_id'], 'cached': cached, 'error_code': row['error_code'],
                'error_message': ERRORS.get(row['error_code'], '语音合成失败，可重试。') if row['error_code'] else None,
                'audio_url': '/api/conversations/'+cid+'/messages/'+mid+'/audio/content'+suffix if row['status'] == 'ready' else None}

    def find(self, c, cid, mid, fingerprint):
        row = c.execute('SELECT * FROM tts_audio WHERE conversation_id=? AND message_id=? AND fingerprint=?', (cid, mid, fingerprint)).fetchone()
        if row and row['status'] == 'pending' and row['updated_at'] < now()-120:
            c.execute("UPDATE tts_audio SET status='failed',error_code='tts_interrupted',updated_at=? WHERE id=?", (now(), row['id']))
            c.execute("UPDATE tts_calls SET status='failed',error_code='tts_interrupted',ended_at=? WHERE audio_id=? AND attempt=? AND status='started'", (now(), row['id'], row['attempt']))
            row = c.execute('SELECT * FROM tts_audio WHERE id=?', (row['id'],)).fetchone()
        return row

    def request(self, user, cid, mid, body=None):
        body = body or {}
        use_id = body.get('use_id')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            message, voice, fingerprint = self.source(c, user, cid, mid, use_id)
            row = self.find(c, cid, mid, fingerprint)
            if row and row['status'] != 'failed':
                c.execute('INSERT INTO tts_reuse VALUES(?,?,?,?)', (uid(), row['id'], 'cache' if row['status'] == 'ready' else 'inflight', now()))
                return self.public(row, cid, mid, use_id, row['status'] == 'ready')
            if row and (body.get('retry') is not True or body.get('attempt') != row['attempt']):
                return self.public(row, cid, mid, use_id)
            if not self.provider.configured:
                raise ApiError(503, 'tts_unconfigured', ERRORS['tts_unconfigured'])
            if len(message['content']) > 3000:
                raise ApiError(422, 'tts_text_too_long', '这条回复过长，暂不支持语音播放')
            settings = self.provider.settings
            totals = c.execute('SELECT count(*),coalesce(sum(text_chars),0) FROM tts_calls WHERE approval_id=? AND mode=?', (settings.approval_id, self.provider.mode)).fetchone()
            if totals[0] >= settings.max_calls or totals[1]+len(message['content']) > settings.max_chars:
                raise ApiError(429, 'tts_budget_exhausted', ERRORS['tts_budget_exhausted'])
            audio_id, attempt = (row['id'], row['attempt']+1) if row else (uid(), 1)
            if row:
                c.execute("UPDATE tts_audio SET status='pending',attempt=?,error_code=NULL,updated_at=? WHERE id=?", (attempt, now(), audio_id))
            else:
                c.execute('INSERT INTO tts_audio VALUES(?,?,?,?,?,?,?,?,?,?,?)', (audio_id, cid, mid, fingerprint, voice['id'], 'pending', attempt, None, None, now(), now()))
            call_id = uid()
            c.execute('INSERT INTO tts_calls VALUES(?,?,?,?,?,?,?,?,?,?,?)', (call_id, audio_id, attempt, settings.approval_id, self.provider.mode, len(message['content']), None, 'started', None, now(), None))
            row = c.execute('SELECT * FROM tts_audio WHERE id=?', (audio_id,)).fetchone()
        worker = threading.Thread(target=self.run, args=(dict(user), cid, mid, use_id, fingerprint, voice['id'], message['content'], audio_id, attempt, call_id), daemon=True)
        with self.guard:
            self.workers.add(worker)
        worker.start()
        return self.public(row, cid, mid, use_id)

    def run(self, user, cid, mid, use_id, fingerprint, voice, content, audio_id, attempt, call_id):
        billable = None
        started = time.monotonic()
        error_details = {}
        try:
            with self.db.connect() as c:
                self.source(c, user, cid, mid, use_id)
            audio, billable = self.provider.synthesize(content, voice, call_id)
            with self.db.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                _, _, latest = self.source(c, user, cid, mid, use_id)
                if latest != fingerprint:
                    raise TTSError('tts_source_unavailable')
                price = getattr(self.provider, 'price_per_10000', 3 if self.provider.name == 'doubao-seed-tts-2.0' else None)
                cost = round(billable*price/10000, 8) if type(billable) is int and price is not None else None
                c.execute('INSERT OR REPLACE INTO tts_metrics VALUES(?,?,?,?,?,?)',
                          (audio_id, attempt, self.provider.name, round(time.monotonic()-started, 3), cost, '{}'))
                c.execute("UPDATE tts_audio SET status='ready',audio=?,error_code=NULL,updated_at=? WHERE id=? AND attempt=? AND status='pending'", (audio, now(), audio_id, attempt))
                c.execute("UPDATE tts_calls SET status='completed',billable_chars=?,ended_at=? WHERE id=?", (billable, now(), call_id))
        except Exception as exc:
            error_details = getattr(exc, 'details', {})
            code = exc.code if isinstance(exc, (TTSError, ApiError)) and exc.code in ERRORS else 'tts_provider_failed'
            with self.db.connect() as c:
                c.execute("UPDATE tts_audio SET status='failed',audio=NULL,error_code=?,updated_at=? WHERE id=? AND attempt=? AND status='pending'", (code, now(), audio_id, attempt))
                c.execute("UPDATE tts_calls SET status='failed',billable_chars=?,error_code=?,ended_at=? WHERE id=?", (billable, code, now(), call_id))
        finally:
            with self.db.connect() as c:
                price = getattr(self.provider, 'price_per_10000', 3 if self.provider.name == 'doubao-seed-tts-2.0' else None)
                cost = round(billable*price/10000, 8) if type(billable) is int and price is not None else None
                c.execute('INSERT OR REPLACE INTO tts_metrics VALUES(?,?,?,?,?,?)',
                          (audio_id, attempt, self.provider.name, round(time.monotonic()-started, 3), cost, json.dumps(error_details, ensure_ascii=False)))
            with self.guard:
                self.workers.discard(threading.current_thread())

    def status(self, user, cid, mid, use_id=None):
        with self.db.connect() as c:
            _, voice, fingerprint = self.source(c, user, cid, mid, use_id)
            row = self.find(c, cid, mid, fingerprint)
            if not row:
                return {'id': None, 'status': 'idle', 'attempt': 0, 'message_id': mid, 'voice_id': voice['id'], 'audio_url': None, 'cached': False, 'error_code': None, 'error_message': None}
            return self.public(row, cid, mid, use_id)

    def audio(self, user, cid, mid, use_id=None):
        with self.db.connect() as c:
            _, _, fingerprint = self.source(c, user, cid, mid, use_id)
            row = self.find(c, cid, mid, fingerprint)
            if not row or row['status'] != 'ready' or not row['audio']:
                raise ApiError(409, 'tts_not_ready', '语音尚未生成完成')
            c.execute('INSERT INTO tts_reuse VALUES(?,?,?,?)', (uid(), row['id'], 'delivery', now()))
            return bytes(row['audio']), 'audio/mpeg'
