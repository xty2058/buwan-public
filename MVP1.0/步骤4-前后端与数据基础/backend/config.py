import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    db: Path
    log_dir: Path
    origin: str = 'http://127.0.0.1:8840'
    host: str = '127.0.0.1'
    port: int = 8840
    session_hours: int = 168
    secure_cookie: bool = False
    # Deployment switches. Defaults keep the local single-user behaviour unchanged.
    invite_required: bool = False      # registration needs a valid invite code
    guest_trial: bool = True           # "免注册试用" creates throwaway accounts
    internal_tools: bool = False        # /evaluation and /inspect (paid evaluation calls)
    open_characters: tuple = ()        # empty = every character the database marks available
    trust_proxy: bool = False          # take the client address from X-Forwarded-For (behind Caddy)
    daily_reply_limit: int = 0         # replies per user per Beijing day; 0 = unlimited
    daily_total_limit: int = 0         # replies site-wide per Beijing day; 0 = unlimited

    @classmethod
    def from_env(cls):
        load_env()
        origin = os.getenv('APP_ORIGIN', 'http://127.0.0.1:8840').rstrip('/')
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path:
            raise ValueError('APP_ORIGIN must be an HTTP origin without a path')
        host = os.getenv('APP_HOST', '127.0.0.1')
        public_bind = flag('APP_ALLOW_PUBLIC_BIND', False)
        if host not in ('127.0.0.1', 'localhost', '::1') and not public_bind:
            raise ValueError('Binding beyond loopback requires APP_ALLOW_PUBLIC_BIND=true (behind a TLS proxy)')
        if public_bind and parsed.scheme != 'https' and host not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('A publicly bound server must use an https APP_ORIGIN')
        hours = int(os.getenv('APP_SESSION_HOURS', '168'))
        if not 1 <= hours <= 720:
            raise ValueError('APP_SESSION_HOURS must be between 1 and 720')
        port = int(os.getenv('APP_PORT', '8840'))
        if not 1 <= port <= 65535:
            raise ValueError('APP_PORT must be between 1 and 65535')
        return cls(db=ROOT / os.getenv('APP_DB', 'runtime/app.sqlite3'),
                   log_dir=ROOT / os.getenv('APP_LOG_DIR', 'runtime/logs'), origin=origin,
                   host=host, port=port,
                   session_hours=hours,
                   secure_cookie=os.getenv('APP_COOKIE_SECURE', 'false').lower() == 'true',
                   invite_required=flag('APP_INVITE_REQUIRED', False),
                   guest_trial=flag('APP_GUEST_TRIAL', True),
                   internal_tools=False,
                   open_characters=tuple(x.strip() for x in os.getenv('APP_OPEN_CHARACTERS', 'linyu').split(',') if x.strip()),
                   trust_proxy=flag('APP_TRUST_PROXY', False),
                   daily_reply_limit=count('APP_DAILY_REPLY_LIMIT'),
                   daily_total_limit=count('APP_DAILY_TOTAL_LIMIT'))


def count(name):
    value = int(os.getenv(name, '0') or 0)
    if not 0 <= value <= 100000:
        raise ValueError(f'{name} must be between 0 and 100000')
    return value


def flag(name, default):
    value = os.getenv(name, '').strip().lower()
    if not value:
        return default
    if value not in ('true', 'false'):
        raise ValueError(f'{name} must be true or false')
    return value == 'true'
