import hashlib
import hmac
import re
import secrets
import time
import uuid
from http.cookies import SimpleCookie, CookieError
from .errors import ApiError

ITERATIONS = 600_000
COOKIE = 'weiwan_session'


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def password_hash(password):
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS).hex()
    return 'pbkdf2_sha256${}${}${}'.format(ITERATIONS, salt, key)


def verify(password, encoded):
    try:
        algo, rounds, salt, expected = encoded.split('$')
        if algo != 'pbkdf2_sha256' or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(key, expected)
    except (ValueError, TypeError):
        return False


# Absent accounts still perform the same password derivation.
DUMMY_HASH = password_hash(secrets.token_urlsafe(24))


def validate_username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_]{3,32}', value):
        raise ApiError(422, 'invalid_username', '用户名需为 3—32 位字母、数字或下划线')
    return value.lower()


def validate_password(value, minimum=12):
    if not isinstance(value, str) or not minimum <= len(value) <= 128:
        raise ApiError(422, 'invalid_password', '密码需为 '+str(minimum)+'—128 个字符')
    return value


def validate_nickname(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 16 or any(ord(x) < 32 for x in value):
        raise ApiError(422, 'invalid_nickname', '昵称需为 1—16 个可显示字符')
    return value.strip()


def public_user(user):
    return {k: user[k] for k in ('id', 'username', 'nickname')}


class Auth:
    def __init__(self, db, config):
        self.db, self.config = db, config

    def throttle(self, username, address):
        now = int(time.time())
        buckets = [('account:' + digest(username), 8), ('ip:' + digest(address), 40)]
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM auth_attempts WHERE occurred_at < ?', (now-900,))
            for bucket, limit in buckets:
                count = c.execute('SELECT COUNT(*) FROM auth_attempts WHERE bucket=? AND occurred_at>=?', (bucket, now-900)).fetchone()[0]
                if count >= limit:
                    raise ApiError(429, 'auth_rate_limited', '尝试过于频繁，请 15 分钟后重试')
            c.executemany('INSERT INTO auth_attempts VALUES(?,?)', [(b, now) for b, _ in buckets])

    def issue(self, c, user_id):
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        csrf = digest('csrf:' + token)
        c.execute('INSERT INTO auth_sessions(id,user_id,token_hash,csrf_hash,created_at,expires_at) VALUES(?,?,?,?,?,?)',
                  (uuid.uuid4().hex, user_id, digest(token), digest(csrf), now, now+self.config.session_hours*3600))
        return token, csrf

    def register(self, body, address, previous_token=None, invite_exempt=False):
        username = validate_username(body.get('username'))
        password = validate_password(body.get('password'))
        nickname = validate_nickname(body.get('nickname'))
        need_invite = getattr(self.config, 'invite_required', False) and not invite_exempt
        code = body.get('invite_code')
        if need_invite and (not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9-]{6,40}', code.strip())):
            raise ApiError(400, 'invite_required', '当前为邀请制，请填写有效的邀请码')
        self.throttle(username, address)
        encoded = password_hash(password)
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
                raise ApiError(409, 'username_unavailable', '该用户名不可使用，请换一个')
            now, user_id = int(time.time()), uuid.uuid4().hex
            if need_invite:
                code_hash = digest('invite:' + code.strip().upper())
                # Same message for unknown, used-up, expired and disabled codes.
                updated = c.execute('UPDATE invites SET used_count=used_count+1 WHERE code_hash=? AND disabled_at IS NULL '
                                    'AND used_count<max_uses AND (expires_at IS NULL OR expires_at>?)', (code_hash, now)).rowcount
                if updated != 1:
                    raise ApiError(403, 'invite_invalid', '邀请码无效、已用完或已过期')
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', (user_id, username, encoded, nickname, now, now))
            if need_invite:
                c.execute('INSERT INTO invite_redemptions VALUES(?,?,?)', (code_hash, user_id, now))
            self._revoke(c, previous_token)
            token, csrf = self.issue(c, user_id)
            return {'id': user_id, 'username': username, 'nickname': nickname}, token, csrf

    def login(self, body, address, previous_token=None):
        username = validate_username(body.get('username'))
        # Login verifies the stored credential; creation policy remains on registration.
        password = validate_password(body.get('password'), minimum=1)
        self.throttle(username, address)
        with self.db.connect() as c:
            user = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not verify(password, user['password_hash'] if user else DUMMY_HASH):
            raise ApiError(401, 'invalid_credentials', '用户名或密码不正确')
        with self.db.connect() as c:
            self._revoke(c, previous_token)
            token, csrf = self.issue(c, user['id'])
            return public_user(user), token, csrf

    def token_from_cookie(self, header):
        try:
            cookie = SimpleCookie()
            cookie.load(header or '')
            token = cookie[COOKIE].value if COOKIE in cookie else None
            return token if token and re.fullmatch(r'[A-Za-z0-9_-]{43}', token) else None
        except CookieError:
            return None

    def authenticate(self, token):
        if not token:
            raise ApiError(401, 'authentication_required', '请先登录')
        with self.db.connect() as c:
            session = c.execute('SELECT s.id AS session_id,s.csrf_hash,u.id,u.username,u.nickname FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?', (digest(token), int(time.time()))).fetchone()
            if not session:
                raise ApiError(401, 'session_expired', '登录已失效，请重新登录')
            return dict(session)

    def check_csrf(self, user, value):
        if not isinstance(value, str) or not hmac.compare_digest(digest(value), user['csrf_hash']):
            raise ApiError(403, 'csrf_rejected', '请求校验失败，请刷新后重试')

    def _revoke(self, c, token):
        if token:
            now = int(time.time())
            session = c.execute('SELECT id FROM auth_sessions WHERE token_hash=?', (digest(token),)).fetchone()
            if session:
                c.execute('UPDATE auth_sessions SET revoked_at=? WHERE id=?', (now, session['id']))
                c.execute('UPDATE conversation_uses SET closed_at=? WHERE auth_session_id=? AND closed_at IS NULL', (now, session['id']))

    def logout(self, token):
        with self.db.connect() as c:
            self._revoke(c, token)

    def cookie(self, token=None):
        value = COOKIE+'='+(token or '')+'; Path=/; HttpOnly; SameSite=Lax'
        value += '; Max-Age='+str(self.config.session_hours*3600 if token else 0)
        if self.config.secure_cookie:
            value += '; Secure'
        return value
