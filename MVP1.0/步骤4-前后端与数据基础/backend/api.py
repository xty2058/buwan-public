import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .auth import Auth, digest, public_user
from .db import Database
from .errors import ApiError
from .logs import create_logger, emit
from .service import Service
from .chat import Chat, SUPPORTED_ROLES
from .recovery import Recovery
from .relationship import Relationships
from .memory import Memory
from .summary import Summaries
from .embedding import EmbeddingProvider
from .model import CompatibleProvider, ModelSettings
from .activities import Activities
from .tts import TTS
from . import limits
from . import evaluation
from .inspection import Inspection
from .portrait import Portraits
from . import export as exporter

ROOT = Path(__file__).resolve().parent.parent
CAPABILITIES = {'accounts': True, 'message_storage': True, 'chat_generation': False,
                'memory_write': False, 'relationship_write': False}


# Files of the visual spec the product loads directly (fixed whitelist, no path input reaches the disk).
# Where the spec lives: APP_BUWAN_DIR if set; else a `buwan-ds` folder next to backend/ (the installed
# local service, filled from design/buwan-ds by scripts/redeploy_local.py); else the repository itself.
def _buwan_root():
    if os.getenv('APP_BUWAN_DIR'):
        return Path(os.environ['APP_BUWAN_DIR'])
    deployed = ROOT / 'buwan-ds'
    return deployed if (deployed / 'styles' / 'tokens.css').is_file() else ROOT.parent.parent / 'design' / 'buwan-ds'


BUWAN_ROOT = _buwan_root()
BUWAN_FILES = {'/buwan/styles/tokens.css': 'text/css; charset=utf-8', '/buwan/assets/icons.svg': 'image/svg+xml',
               '/buwan/assets/characters/linyu.jpg': 'image/jpeg', '/buwan/assets/characters/luye.jpg': 'image/jpeg',
               '/buwan/assets/characters/shenyan.jpg': 'image/jpeg'}


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)
    stream: object = None


def json_response(data, status=200, headers=None):
    return Response(status, json.dumps(data, ensure_ascii=False).encode('utf-8'),
                    {'Content-Type': 'application/json; charset=utf-8', **(headers or {})})


class Application:
    def __init__(self, config, provider=None, memory_provider=None, embedding_provider=None, tts_provider=None):
        self.config = config
        self.db = Database(config.db)
        self.db.migrate()
        self.service = Service(self.db, config.open_characters)
        self.service.seed()
        self.auth = Auth(self.db, config)
        self.logger = create_logger(config.log_dir)
        self.chat = Chat(self.db, self.service, provider or CompatibleProvider(ModelSettings.from_env()), self.logger)
        self.chat.daily_limits = (config.daily_reply_limit, config.daily_total_limit)
        self.chat.auth_check = self.auth.authenticate
        self.recovery = Recovery(self.db,self.service,self.chat)
        memory_settings=self.chat.provider.settings
        memory_config=Path(__file__).resolve().parent.parent/'runtime/memory-config.json'
        if provider is None and self.chat.provider.mode=='live' and self.chat.provider.configured and memory_config.is_file():
            memory_settings=ModelSettings(**json.loads(memory_config.read_text(encoding='utf-8')))
            memory_settings.validate()
        extractor=memory_provider if memory_provider is not None else (CompatibleProvider(replace(memory_settings,max_output_tokens=2048)) if self.chat.provider.mode=='live' and self.chat.provider.configured else None)
        self.memory=Memory(self.db,self.service,self.chat,extractor,embedding_provider if embedding_provider is not None else (EmbeddingProvider.from_config() if self.chat.provider.mode=='live' else EmbeddingProvider()),self.logger)
        self.chat.memory=self.memory
        self.relationship=Relationships(self.db,self.service,self.memory,self.chat)
        self.inspection=Inspection(self.db,self.service)
        self.portraits=Portraits(self.db,self.service,self.memory,self.chat.provider)
        self.service.relationship=self.relationship
        self.chat.relationship=self.relationship
        self.memory.relationship=self.relationship
        self.memory.summaries=Summaries(self.db,self.memory,self.memory.extractor,self.logger)
        self.chat.summaries=self.memory.summaries
        self.activities=Activities(self.db,self.service,self.chat,self.memory,self.relationship)
        self.tts=TTS(self.db,self.service,provider=tts_provider,chat=self.chat)
        self.service.voice=self.tts
        self.report_lock = threading.Lock()
        self.report_counts = {}

    def capabilities(self):
        return {**CAPABILITIES, **self.chat.capabilities(), **self.memory.capabilities(),
                'relationship_write': True, 'activities': True, 'relationship_archive': True,
                **self.tts.capabilities(),
                'invite_required': self.config.invite_required,
                'guest_trial': self.guest_allowed(),
                **({'chat_roles': [r for r in self.chat.capabilities().get('chat_roles', []) if r in self.config.open_characters]}
                   if self.config.open_characters else {})}

    def guest_allowed(self):
        # A throwaway account would bypass the invite gate, so invite-only deployments have no guest trial.
        return self.config.guest_trial and not self.config.invite_required

    def client_address(self, headers, address):
        # Behind the deployment proxy every request arrives from the proxy; the proxy appends the
        # real client as the last X-Forwarded-For entry. Only trusted when explicitly configured.
        if self.config.trust_proxy:
            forwarded = [x.strip() for x in headers.get('x-forwarded-for', '').split(',') if x.strip()]
            if forwarded:
                return forwarded[-1][:64]
        return address

    def handle(self, method, target, headers=None, raw=b'', address='127.0.0.1'):
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        address = self.client_address(headers, address)
        request_id, started = uuid.uuid4().hex, time.monotonic()
        route = self.route_label(method, urlsplit(target).path)
        try:
            if headers.get('host') != urlsplit(self.config.origin).netloc:
                raise ApiError(421, 'invalid_host', '请使用配置的本地开发地址访问')
            if headers.get('sec-fetch-site') == 'cross-site':
                raise ApiError(403, 'cross_site_rejected', '不接受跨站请求')
            if method in ('POST', 'PATCH', 'DELETE'):
                if headers.get('origin') != self.config.origin:
                    raise ApiError(403, 'origin_rejected', '请求来源不正确')
                if not headers.get('content-type', '').lower().startswith('application/json'):
                    raise ApiError(415, 'json_required', '请使用 JSON 请求')
                if len(raw) > 16384:
                    raise ApiError(413, 'body_too_large', '请求内容过长')
                try:
                    body = json.loads(raw.decode('utf-8') or '{}')
                except (ValueError, UnicodeError):
                    raise ApiError(400, 'invalid_json', '请求内容格式不正确')
                if not isinstance(body, dict):
                    raise ApiError(400, 'invalid_json_object', '请求应为 JSON 对象')
            else:
                body = {}
            response = self.dispatch(method, target, headers, body, address, request_id)
        except ApiError as exc:
            response = json_response({'error': {'code': exc.code, 'message': exc.message}, 'request_id': request_id}, exc.status)
            emit(self.logger, 'request_rejected', logging.WARNING, request_id=request_id, method=method,
                 route=route, status=exc.status, code=exc.code)
        except Exception as exc:
            # Capture stack locations without raw exception strings or source lines.
            frames = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                      for f in traceback.extract_tb(exc.__traceback__)[-8:]]
            emit(self.logger, 'server_error', logging.ERROR, request_id=request_id, method=method, route=route,
                 status=500, code='internal_error', exception_type=type(exc).__name__, frames=frames)
            response = json_response({'error': {'code': 'internal_error', 'message': '服务暂时出错，请稍后重试'}, 'request_id': request_id}, 500)
        response.headers.update({'X-Request-ID': request_id, 'Cache-Control': 'no-store',
                                 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        emit(self.logger, 'request_completed', request_id=request_id, method=method, route=route,
             status=response.status, duration_ms=round((time.monotonic()-started)*1000))
        return response

    @staticmethod
    def route_label(method, path):
        if path in ('/', '/index.html', '/app.js', '/style.css', '/recovery-cache.js', '/mobile-layout.js', '/assets/favicon.svg', '/api/health', '/api/auth/session',
                    '/api/auth/register', '/api/auth/login', '/api/auth/logout', '/api/profile',
                    '/api/characters', '/api/conversations', '/api/client-errors', '/api/inspection/metrics', '/inspect', '/inspect.js', '/inspect.css'):
            return path
        if path in BUWAN_FILES:
            return '/buwan/:file'
        for pattern, label in [(r'/api/characters/[^/]+', '/api/characters/:id'),
                               (r'/api/conversations/[^/]+/activities(?:/[^/]+(?:/messages)?)?', '/api/conversations/:id/activities'),
                               (r'/api/conversations/[^/]+/archive', '/api/conversations/:id/archive'),
                               (r'/api/conversations/[^/]+/messages/[^/]+/audio(?:/content)?', '/api/conversations/:id/messages/:id/audio'),
                               (r'/api/conversations/[^/]+(?:/(messages|memories|relationships|uses|generations|recovery|recalls|portrait|export))?', '/api/conversations/:id'),
                               (r'/api/conversations/[^/]+/generations/[^/]+(?:/(stream|stop|retry|choice))?', '/api/conversations/:id/generations/:id'),
                               (r'/api/conversations/[^/]+/memories/[^/]+', '/api/conversations/:id/memories/:id'),
                               (r'/api/conversations/[^/]+/memories/[^/]+/history', '/api/conversations/:id/memories/:id/history'),
                               (r'/api/conversations/[^/]+/messages/[^/]+/(?:delete|recalled)', '/api/conversations/:id/messages/:id/action'),
                               (r'/api/conversations/[^/]+/uses/[^/]+/close', '/api/conversations/:id/uses/:id/close')]:
            if re.fullmatch(pattern, path):
                return label
        return 'unmatched'

    def dispatch(self, method, target, headers, body, address, request_id):
        parsed = urlsplit(target)
        path, query = parsed.path, parse_qs(parsed.query)
        if method == 'GET' and path in BUWAN_FILES:
            # 不晚 visual spec (design/buwan-ds) is the single source of truth for tokens, icons and
            # character art: the product serves those files as they are instead of keeping copies.
            asset = BUWAN_ROOT / path[len('/buwan/'):]
            if asset.is_file():
                return Response(200, asset.read_bytes(), {'Content-Type': BUWAN_FILES[path], 'Cache-Control': 'public, max-age=300'})
        internal = ('/inspect', '/inspect.js', '/inspect.css', '/evaluation', '/evaluation.js', '/evaluation.css',
                    '/api/evaluation', '/api/evaluation/results', '/api/evaluation/reply')
        if not self.config.internal_tools and (path in internal or path.startswith('/api/inspection/')):
            raise ApiError(404, 'not_found', '页面不存在')
        if method == 'GET' and path in ('/inspect', '/inspect.js', '/inspect.css'):
            # Internal recall inspector (roadmap 1.13). The page is static; its data APIs require login.
            name = 'inspect.html' if path == '/inspect' else path[1:]
            mime = {'inspect.html':'text/html', 'inspect.js':'text/javascript', 'inspect.css':'text/css'}[name]
            return Response(200, (ROOT/'frontend'/name).read_bytes(), {'Content-Type':mime+'; charset=utf-8'})
        if method == 'GET' and path in ('/evaluation', '/evaluation.js', '/evaluation.css'):
            name = 'evaluation.html' if path == '/evaluation' else path[1:]
            mime = {'evaluation.html':'text/html', 'evaluation.js':'text/javascript', 'evaluation.css':'text/css'}[name]
            return Response(200, (ROOT/'frontend'/name).read_bytes(), {'Content-Type':mime+'; charset=utf-8'})
        if method == 'GET' and path == '/api/evaluation/results':
            result_dir=ROOT.parent.parent/'MVP1.1/评测结果'
            combined={'batches':[], 'records':[]}
            for name in ('deepseek-suite.json', 'deepseek-prompt-v1.1.json', 'deepseek-prompt-v1.2.json', 'qwen-plus-prompt-v1.2.json', 'qwen-plus-isolated-v1.json', 'qwen-plus-isolated-v2.json', 'qwen-plus-isolated-v3.json'):
                result_file=result_dir/name
                if result_file.exists():
                    saved=json.loads(result_file.read_text())
                    for key in combined: combined[key].extend(saved.get(key, []))
            return json_response(combined)
        if method == 'GET' and path == '/api/evaluation':
            return json_response(evaluation.catalogue())
        if method == 'GET' and path == '/assets/favicon.svg':
            asset = ROOT/'frontend'/'assets'/'favicon.svg'
            if asset.is_file():
                return Response(200, asset.read_bytes(), {'Content-Type': 'image/svg+xml', 'Cache-Control': 'public, max-age=3600'})
        if method == 'GET' and path in ('/', '/index.html', '/app.js', '/style.css', '/recovery-cache.js', '/mobile-layout.js'):
            name = 'index.html' if path == '/' else path[1:]
            mime = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css', 'recovery-cache.js': 'text/javascript', 'mobile-layout.js':'text/javascript'}[name]
            return Response(200, (ROOT/'frontend'/name).read_bytes(), {'Content-Type': mime+'; charset=utf-8'})
        if method == 'GET' and path == '/api/health':
            with self.db.connect() as c:
                c.execute('SELECT 1').fetchone()
                migration = c.execute('SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1').fetchone()[0]
            return json_response({'status': 'ok', 'schema_version': migration, 'capabilities': self.capabilities()})
        if method == 'GET' and path == '/api/characters':
            return json_response({'items': self.service.characters()})
        match = re.fullmatch(r'/api/characters/([^/]+)', path)
        if method == 'GET' and match:
            return json_response(self.service.character(match[1]))
        if method == 'POST' and path == '/api/evaluation/reply':
            # Local tool by design (Origin-checked, no login). It makes paid calls, so hosted
            # deployments set APP_INTERNAL_TOOLS=false and this route answers 404 above.
            return json_response(evaluation.generate(body))
        token = self.auth.token_from_cookie(headers.get('cookie'))
        if method == 'GET' and path == '/api/auth/session':
            try:
                user = self.auth.authenticate(token)
            except ApiError:
                return json_response({'user': None, 'csrf_token': None, 'capabilities': self.capabilities()}, headers={'Set-Cookie': self.auth.cookie()})
            with self.db.connect() as c:
                per_user = self.chat.daily_limits[0]
                left = limits.remaining(c, user['id'], per_user)
            return json_response({'user': public_user(user), 'csrf_token': digest('csrf:'+token), 'capabilities': self.capabilities(),
                                  'daily_replies_left': left, 'daily_reply_limit': per_user or None})
        if method == 'POST' and path == '/api/auth/guest':
            if not self.guest_allowed():
                raise ApiError(403, 'guest_disabled', '当前为邀请制，请用邀请码注册')
            try:
                user = self.auth.authenticate(token)
                return json_response({'user': public_user(user), 'csrf_token': digest('csrf:'+token)})
            except ApiError:
                pass
            user, new_token, csrf = self.auth.register(
                {'username': 'trial_'+uuid.uuid4().hex[:24], 'password': uuid.uuid4().hex+uuid.uuid4().hex,
                 'nickname': '试用者'}, address, token)
            return json_response({'user': user, 'csrf_token': csrf}, 201,
                                 {'Set-Cookie': self.auth.cookie(new_token)})
        if method == 'POST' and path in ('/api/auth/register', '/api/auth/login'):
            fn = self.auth.register if path.endswith('register') else self.auth.login
            user, new_token, csrf = fn(body, address, token)
            return json_response({'user': user, 'csrf_token': csrf}, 201 if path.endswith('register') else 200,
                                 {'Set-Cookie': self.auth.cookie(new_token)})
        if not path.startswith('/api/'):
            raise ApiError(404, 'not_found', '页面不存在')
        user = self.auth.authenticate(token)
        if method in ('POST', 'PATCH', 'DELETE'):
            self.auth.check_csrf(user, headers.get('x-csrf-token'))
        if method == 'POST' and path == '/api/auth/logout':
            self.chat.cancel_session(user['session_id'])
            self.auth.logout(token)
            return json_response({'logged_out': True}, headers={'Set-Cookie': self.auth.cookie()})
        if method == 'PATCH' and path == '/api/profile':
            return json_response(self.service.profile(user['id'], body))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/activities(?:/([a-f0-9]{32})(?:/(messages))?)?', path)
        if match:
            cid, aid, resource = match.groups()
            if method == 'GET' and not aid:
                return json_response(self.activities.list(user,cid))
            if method == 'POST' and not aid:
                return json_response(self.activities.start(user,cid,body))
            if method == 'GET' and aid and not resource:
                return json_response(self.activities.read(user,cid,aid))
            if method == 'POST' and aid and not resource:
                return json_response(self.activities.mutate(user,cid,aid,body))
            if method == 'POST' and aid and resource == 'messages':
                return json_response(self.activities.discuss(user,cid,aid,body))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/archive', path)
        if method == 'GET' and match:
            return json_response(self.activities.archive(user,match[1],query.get('use_id',[None])[0]))
        if method == 'POST' and match:
            return json_response(self.activities.archive_mutate(user,match[1],body))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/messages/([a-f0-9]{32})/audio(?:/(content))?', path)
        if match:
            cid, mid, resource = match.groups()
            if method == 'POST' and not resource:
                return json_response(self.tts.request(user,cid,mid,body),202)
            if method == 'GET' and not resource:
                return json_response(self.tts.status(user,cid,mid,query.get('use_id',[None])[0]))
            if method == 'GET' and resource == 'content':
                audio, mime = self.tts.audio(user,cid,mid,query.get('use_id',[None])[0])
                return Response(200,audio,{'Content-Type':mime})
        if method == 'GET' and path == '/api/inspection/metrics':
            return json_response(self.inspection.metrics(user['id']))
        if path == '/api/conversations':
            if method == 'GET':
                return json_response({'items': self.service.conversations(user['id'])})
            if method == 'POST':
                cid, created = self.service.create_conversation(user['id'], body)
                return json_response(self.service.conversation(user['id'], cid), 201 if created else 200)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/messages/([a-f0-9]{32})/(delete|recalled)', path)
        if match:
            cid, mid, resource = match.groups()
            if method == 'POST' and resource == 'delete':
                return json_response(self.memory.delete_message(user['id'], cid, mid))
            if method == 'GET' and resource == 'recalled':
                return json_response(self.inspection.recalled_for_message(user['id'], cid, mid))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/memories/([a-f0-9]{32})/history', path)
        if method == 'GET' and match:
            return json_response(self.memory.history(user['id'], match[1], match[2]))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/portrait', path)
        if match:
            # Roadmap 1.9: read is free; generating is one explicit, user-triggered model call.
            if method == 'GET':
                return json_response(self.portraits.read(user['id'], match[1]))
            if method == 'POST':
                return json_response(self.portraits.generate(user['id'], match[1]))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/export', path)
        if method == 'GET' and match:
            # Roadmap 1.10: the owner's own chat, memories and relationship record.
            data = exporter.collect(self.db, self.service, self.relationship, user, match[1])
            name = 'weiwan-' + time.strftime('%Y%m%d')
            if query.get('format', ['json'])[0] == 'md':
                return Response(200, exporter.markdown(data).encode(), {'Content-Type': 'text/markdown; charset=utf-8', 'Content-Disposition': 'attachment; filename="' + name + '.md"', 'Cache-Control': 'no-store'})
            return Response(200, json.dumps(data, ensure_ascii=False, indent=2).encode(), {'Content-Type': 'application/json; charset=utf-8', 'Content-Disposition': 'attachment; filename="' + name + '.json"', 'Cache-Control': 'no-store'})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/recalls', path)
        if method == 'GET' and match:
            try:
                limit = int(query.get('limit', ['50'])[0])
            except ValueError:
                raise ApiError(422, 'invalid_cursor', '分页参数不正确')
            return json_response(self.inspection.recalls(user['id'], match[1], limit))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})(?:/(messages|memories|relationships|uses|recovery))?', path)
        if match:
            cid, resource = match.groups()
            if method == 'GET':
                if not resource:
                    return json_response(self.service.conversation(user['id'], cid))
                if resource == 'recovery':
                    return json_response(self.recovery.read(user['id'],cid,query.get('anchor',[None])[0],query.get('client_request_id',[None])[0],query.get('generation_key',[None])[0],session_id=user['session_id'],use_id=query.get('use_id',[None])[0]))
                if resource == 'messages':
                    try:
                        before = int(query['before'][0]) if 'before' in query else None
                        limit = int(query.get('limit', ['100'])[0])
                    except ValueError:
                        raise ApiError(422, 'invalid_cursor', '分页参数不正确')
                    return json_response(self.service.messages(user['id'], cid, before, limit))
                if resource == 'memories':
                    return json_response(self.memory.list(user['id'], cid))
                if resource == 'relationships':
                    return json_response(self.relationship.read(user,cid,query.get('use_id',[None])[0]))
            if method == 'POST' and resource == 'messages':
                result, created = self.service.save_message(user['id'], cid, body,user['session_id'])
                self.memory.stop_controls(cid)
                result['relationship_state']=self.relationship.read(user,cid,body.get('use_id'))['state']
                role=self.service.conversation(user['id'],cid)['character_id']
                result['generation_available']=self.chat.provider.configured and role in SUPPORTED_ROLES and self.service.character(role)['available']
                return json_response(result, 201 if created else 200)
            if method=='POST' and resource=='relationships':
                return json_response(self.relationship.mutate(user,cid,body))
            if method == 'POST' and resource == 'uses':
                return json_response(self.service.open_use(user, cid, body.get('use_id')))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/memories/(extract|[a-f0-9]{32})',path)
        if method=='POST' and match:
            cid,mid=match.groups()
            if mid=='extract':return json_response(self.memory.schedule(user['id'],cid,retry=True),202)
            return json_response(self.memory.mutate(user['id'],cid,mid,body))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/uses/([a-f0-9]{32})/close', path)
        if method == 'POST' and match:
            return json_response(self.service.close_use(user, match[1], match[2]))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/generations(?:/([a-f0-9]{32})(?:/(stream|stop|retry|choice))?)?', path)
        if match:
            cid, rid, operation = match.groups()
            if method == 'POST' and not rid:
                return json_response(self.chat.create(user, cid, body), 202)
            if method == 'GET' and not operation:
                return json_response(self.chat.snapshot(user['id'], cid, rid, session_id=user['session_id'], use_id=query.get('use_id',[None])[0]))
            if rid and method == 'GET' and operation == 'stream':
                self.chat.snapshot(user['id'], cid, rid, session_id=user['session_id'], use_id=query.get('use_id',[None])[0])
                return Response(200, b'', {'Content-Type': 'text/event-stream; charset=utf-8', 'X-Accel-Buffering': 'no'}, self.chat.events(user, cid, rid, token, query.get('use_id',[None])[0]))
            if rid and method == 'POST' and operation == 'stop':
                return json_response(self.chat.stop(user,cid,rid,body))
            if rid and method == 'POST' and operation == 'retry':
                return json_response(self.chat.retry(user,cid,rid,body), 202)
            if rid and method == 'POST' and operation == 'choice':
                return json_response(self.chat.choose(user,cid,rid,body))
        if method == 'POST' and path == '/api/client-errors':
            code, component = body.get('code'), body.get('component')
            if code not in ('window_error', 'unhandled_rejection', 'api_failure') or component not in ('discover', 'detail', 'messages', 'chat', 'memory', 'relationship', 'me', 'auth', 'boot'):
                raise ApiError(422, 'invalid_error_report', '错误报告格式不正确')
            with self.report_lock:
                minute = int(time.time())//60
                self.report_counts = {k: v for k, v in self.report_counts.items() if k[1] == minute}
                key = (user['id'], minute)
                count = self.report_counts.get(key, 0)
                if count >= 20:
                    raise ApiError(429, 'report_rate_limited', '错误报告频率过高')
                self.report_counts[key] = count+1
            emit(self.logger, 'client_error', logging.ERROR, request_id=request_id, code=code, component=component)
            return json_response({'received': True})
        raise ApiError(404, 'not_found', '接口不存在')
