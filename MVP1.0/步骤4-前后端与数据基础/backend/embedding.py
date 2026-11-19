"""Explicit embedding adapter with a durable, process-shared request allowance."""
import json
import math
import os
import http.client
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from .model import ModelError


ERROR_TEXT = {
    'embedding_budget_exhausted': '本轮向量模型调用额度已用完，未继续请求；等待下一轮授权。',
    'embedding_budget_unavailable': '向量模型调用计数不可用，已暂停请求，请检查本机配置。',
    'embedding_config_invalid': '向量模型配置不正确，请检查地址、模型与维度。',
    'embedding_not_configured': '向量模型尚未配置并授权。',
    'embedding_auth': '向量模型密钥无效或无访问权限，请在本机核对。',
    'embedding_payment': '向量模型账户额度或余额不足，请核对百炼账户。',
    'embedding_rate_limit': '向量模型请求限流，请稍后手动重试。',
    'embedding_connection': '向量模型连接失败或超时，请稍后手动重试。',
    'credential_unavailable': '向量模型凭证文件不可用，请在本机核对配置。',
    'process_interrupted': '上次索引被服务重启中断，请重试索引。',
    'embedding_protocol': '向量模型返回的数据不符合配置，未保存无效向量。',
}


def failure_text(code):
    return ERROR_TEXT.get(code, '向量索引未完成，请核对配置后重试。')


class RequestBudget:
    """Reserve BEFORE network I/O. Failed/unknown outcomes also consume allowance."""
    def __init__(self, settings):
        self.path = Path(settings['budget_db']).expanduser()
        self.approval = settings['approval_id']
        self.limit = -1 if settings['max_requests'] is None and settings.get('unlimited_authorized') is True else settings['max_requests']

    def connect(self):
        c = sqlite3.connect(str(self.path), timeout=10)
        c.execute('CREATE TABLE IF NOT EXISTS budgets (approval TEXT PRIMARY KEY, request_limit INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, approval TEXT NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL, started REAL NOT NULL, ended REAL, status TEXT NOT NULL, code TEXT, tokens INTEGER, vendor_id TEXT)')
        return c

    def reserve(self, model, dimensions):
        try:
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT OR IGNORE INTO budgets VALUES (?,?)', (self.approval, self.limit))
                if c.execute('SELECT request_limit FROM budgets WHERE approval=?', (self.approval,)).fetchone()[0] != self.limit:
                    raise ModelError('embedding_budget_unavailable')
                used = c.execute('SELECT count(*) FROM calls WHERE approval=?', (self.approval,)).fetchone()[0]
                if self.limit != -1 and used >= self.limit:
                    raise ModelError('embedding_budget_exhausted')
                call = uuid.uuid4().hex
                c.execute('INSERT INTO calls(id,approval,model,dimensions,started,status) VALUES(?,?,?,?,?,?)',
                          (call, self.approval, model, dimensions, time.time(), 'reserved'))
            self.path.chmod(0o600)
            return call
        except (OSError, sqlite3.Error):
            raise ModelError('embedding_budget_unavailable') from None

    def finish(self, call, code=None, tokens=None, vendor_id=None):
        try:
            with self.connect() as c:
                c.execute('UPDATE calls SET ended=?,status=?,code=?,tokens=?,vendor_id=? WHERE id=?',
                          (time.time(), 'failed' if code else 'completed', code, tokens, vendor_id, call))
        except (OSError, sqlite3.Error):
            # Reservation remains consumed even if final audit cannot be updated.
            raise ModelError('embedding_budget_unavailable') from None


class EmbeddingProvider:
    mode = 'live'
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.model = self.settings.get('model', '')
        self.dimensions = self.settings.get('dimensions', 1024)

    @classmethod
    def from_config(cls):
        if os.getenv('STEP5_ALLOW_LIVE_CALLS', '').lower() == 'false':
            return cls()
        path = Path(__file__).resolve().parent.parent / 'runtime/embedding-config.json'
        return cls(json.loads(path.read_text(encoding='utf-8'))) if path.is_file() else cls()

    @property
    def configured(self):
        return self.settings.get('allow_live') is True and bool(self.model and self.settings.get('approval_id'))

    def embed(self, texts):
        if not self.configured:
            raise ModelError('embedding_not_configured')
        s = self.settings
        p = urlsplit(s.get('base_url', ''))
        if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment
                or type(self.dimensions) is not int or self.dimensions < 1
                or not ((type(s.get('max_requests')) is int and 1 <= s['max_requests'] <= 20)
                        or (s.get('max_requests') is None and s.get('unlimited_authorized') is True))
                or not s.get('budget_db')):
            raise ModelError('embedding_config_invalid')
        if (not isinstance(texts, list) or not 1 <= len(texts) <= 10
                or any(not isinstance(t, str) or not 1 <= len(t) <= 4000 for t in texts)):
            raise ModelError('embedding_input_invalid')
        key = ''
        conn = None
        call = None
        budget = RequestBudget(s)
        try:
            path = Path(s['key_file']).expanduser()
            if path.stat().st_size > 4096:
                raise ModelError('credential_unavailable')
            key = path.read_text(encoding='utf-8').strip()
            if not key or any(x.isspace() for x in key):
                raise ModelError('credential_unavailable')
            conn = http.client.HTTPSConnection(p.hostname, p.port or 443, timeout=30)
            call = budget.reserve(self.model, self.dimensions)
            conn.request('POST', p.path.rstrip('/') + '/embeddings',
                         json.dumps({'model': self.model, 'input': texts, 'dimensions': self.dimensions,
                                     'encoding_format': 'float'}).encode(),
                         {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
            r = conn.getresponse()
            if r.status != 200:
                code = {401: 'embedding_auth', 403: 'embedding_auth', 402: 'embedding_payment',
                        429: 'embedding_rate_limit'}.get(r.status, 'embedding_http_' + str(r.status))
                raise ModelError(code)
            data = json.loads(r.read(2000000))
            items = sorted(data['data'], key=lambda x: x['index'])
            vectors = [x['embedding'] for x in items]
            if (len(items) != len(texts) or any(type(x['index']) is not int for x in items)
                    or [x['index'] for x in items] != list(range(len(texts)))
                    or any(not valid_vector(v, self.dimensions) for v in vectors)
                    or data.get('model', self.model) != self.model):
                raise ModelError('embedding_protocol')
            usage = data.get('usage', {}).get('total_tokens')
            usage = usage if type(usage) is int and usage >= 0 else None
            vendor_id = data.get('id')
            vendor_id = vendor_id if isinstance(vendor_id, str) and len(vendor_id) <= 128 else None
            budget.finish(call, tokens=usage, vendor_id=vendor_id)
            call = None
            return vectors, usage
        except ModelError as exc:
            if call:
                budget.finish(call, code=exc.code)
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException):
            if call:
                budget.finish(call, code='embedding_connection')
            raise ModelError('embedding_connection') from None
        finally:
            key = ''
            if conn:
                conn.close()


def valid_vector(v, dimensions):
    return isinstance(v, list) and len(v) == dimensions and all(type(x) in (int, float) and math.isfinite(x) for x in v) and 0 < sum(x*x for x in v) < float('inf')


def cosine(a, b):
    if len(a) != len(b):
        return -1
    return sum(x*y for x, y in zip(a, b)) / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(x*x for x in b)))
