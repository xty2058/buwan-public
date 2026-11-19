"""Hosted-deployment switches: invite-only registration, no guest trial, no internal tools,
only the allowed characters, real client address behind the proxy, config validation."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_chat as helpers
from backend.api import Application
from backend.auth import digest
from backend.config import Config
from backend.model import ModelError

ROOT = Path(__file__).resolve().parents[1]
HOST = {'Host': 'test.local', 'Origin': 'http://test.local', 'Content-Type': 'application/json'}


class DeploymentTest(unittest.TestCase):
    def make(self, **flags):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = Config(db=root / 'app.sqlite3', log_dir=root / 'logs', origin='http://test.local', **flags)
        self.app = Application(self.config, provider=helpers.FakeProvider())
        return self.app

    def tearDown(self):
        if hasattr(self, 'temp'):
            self.temp.cleanup()

    def post(self, path, body, extra=None):
        r = self.app.handle('POST', path, {**HOST, **(extra or {})}, json.dumps(body).encode(), '127.0.0.1')
        return r.status, json.loads(r.body)

    def get(self, path, extra=None):
        return self.app.handle('GET', path, {**HOST, **(extra or {})}, b'', '127.0.0.1')

    def invite(self, code, uses=1, expires=None):
        with self.app.db.connect() as c:
            c.execute('INSERT INTO invites(code_hash,label,max_uses,created_at,expires_at) VALUES(?,?,?,?,?)',
                      (digest('invite:' + code), 't', uses, 1, expires))

    def user(self, name, **extra):
        return {'username': name, 'password': 'Correct-horse-2026', 'nickname': '测试', **extra}

    def test_invite_required_registration(self):
        self.make(invite_required=True)
        self.invite('ABCD-EFGH-JKLM', uses=1)
        status, body = self.post('/api/auth/register', self.user('no_code'))
        self.assertEqual((status, body['error']['code']), (400, 'invite_required'))
        status, body = self.post('/api/auth/register', self.user('bad_code', invite_code='ZZZZ-ZZZZ-ZZZZ'))
        self.assertEqual((status, body['error']['code']), (403, 'invite_invalid'))
        status, _ = self.post('/api/auth/register', self.user('first', invite_code='abcd-efgh-jklm'))
        self.assertEqual(status, 201)  # case-insensitive
        status, body = self.post('/api/auth/register', self.user('second', invite_code='ABCD-EFGH-JKLM'))
        self.assertEqual((status, body['error']['code']), (403, 'invite_invalid'))  # used up
        with self.app.db.connect() as c:
            self.assertEqual(c.execute('SELECT used_count FROM invites').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM invite_redemptions').fetchone()[0], 1)
        # A failed username check does not consume the code.
        self.invite('QQQQ-RRRR-SSSS', uses=1)
        status, _ = self.post('/api/auth/register', self.user('first', invite_code='QQQQ-RRRR-SSSS'))
        self.assertEqual(status, 409)
        status, _ = self.post('/api/auth/register', self.user('third', invite_code='QQQQ-RRRR-SSSS'))
        self.assertEqual(status, 201)

    def test_expired_invite_rejected(self):
        self.make(invite_required=True)
        self.invite('OLDD-CODE-XXXX', expires=1)
        status, body = self.post('/api/auth/register', self.user('late', invite_code='OLDD-CODE-XXXX'))
        self.assertEqual((status, body['error']['code']), (403, 'invite_invalid'))

    def test_local_default_needs_no_invite_and_keeps_guest(self):
        self.make()
        self.assertEqual(self.post('/api/auth/register', self.user('local'))[0], 201)
        self.assertEqual(self.post('/api/auth/guest', {})[0], 201)

    def test_guest_disabled_when_invite_only(self):
        self.make(invite_required=True)
        status, body = self.post('/api/auth/guest', {})
        self.assertEqual((status, body['error']['code']), (403, 'guest_disabled'))
        caps = json.loads(self.get('/api/auth/session').body)['capabilities']
        self.assertTrue(caps['invite_required'])
        self.assertFalse(caps['guest_trial'])

    def test_internal_tools_hidden(self):
        self.make(internal_tools=False)
        for path in ('/evaluation', '/evaluation.js', '/inspect', '/api/evaluation', '/api/evaluation/results'):
            self.assertEqual(self.get(path).status, 404, path)
        self.assertEqual(self.post('/api/evaluation/reply', {'model': 'deepseek', 'case_id': 'C01'})[0], 404)

    def test_only_linyu_open(self):
        self.make(open_characters=('linyu',))
        chars = {c['id']: c['available'] for c in json.loads(self.get('/api/characters').body)['items']}
        self.assertEqual(chars, {'linyu': True, 'luye': False, 'shenyan': False})
        with self.app.db.connect() as c:
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', ('u', 'user_u', 'x', 'n', 1, 1))
            token, csrf = self.app.auth.issue(c, 'u')
        auth = {'Cookie': self.app.auth.cookie(token).split(';')[0], 'X-CSRF-Token': csrf}
        self.assertEqual(self.post('/api/conversations', {'character_id': 'luye'}, auth)[0], 409)
        self.assertEqual(self.post('/api/conversations', {'character_id': 'linyu'}, auth)[0], 201)
        caps = json.loads(self.get('/api/auth/session').body)['capabilities']
        self.assertEqual(caps['chat_roles'], ['linyu'])

    def test_client_address_from_proxy_only_when_trusted(self):
        app = self.make(trust_proxy=True)
        self.assertEqual(app.client_address({'x-forwarded-for': '1.1.1.1, 9.9.9.9'}, '172.18.0.2'), '9.9.9.9')
        self.assertEqual(app.client_address({}, '172.18.0.2'), '172.18.0.2')
        self.temp.cleanup()
        app = self.make()
        self.assertEqual(app.client_address({'x-forwarded-for': '9.9.9.9'}, '127.0.0.1'), '127.0.0.1')


class ConfigTest(unittest.TestCase):
    def env(self, **values):
        base = {k: v for k, v in os.environ.items() if not k.startswith('APP_')}
        return patch.dict(os.environ, {**base, **values}, clear=True)

    def test_public_bind_requires_flag_and_https(self):
        with self.env(APP_HOST='0.0.0.0'), patch('backend.config.load_env'):
            with self.assertRaises(ValueError):
                Config.from_env()
        with self.env(APP_HOST='0.0.0.0', APP_ALLOW_PUBLIC_BIND='true', APP_ORIGIN='http://x.example'), patch('backend.config.load_env'):
            with self.assertRaises(ValueError):
                Config.from_env()
        with self.env(APP_HOST='0.0.0.0', APP_ALLOW_PUBLIC_BIND='true', APP_ORIGIN='https://x.example',
                      APP_INVITE_REQUIRED='true', APP_GUEST_TRIAL='false', APP_INTERNAL_TOOLS='false',
                      APP_OPEN_CHARACTERS='linyu', APP_TRUST_PROXY='true'), patch('backend.config.load_env'):
            c = Config.from_env()
        self.assertEqual((c.invite_required, c.guest_trial, c.internal_tools, c.open_characters, c.trust_proxy),
                         (True, False, False, ('linyu',), True))

    def test_flag_rejects_typos(self):
        with self.env(APP_INVITE_REQUIRED='yes'), patch('backend.config.load_env'):
            with self.assertRaises(ValueError):
                Config.from_env()


class DeployConfigScriptTest(unittest.TestCase):
    def test_generates_configs_and_private_key_files(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'app'
            (app / 'scripts').mkdir(parents=True)
            script = app / 'scripts' / 'deploy_config.py'
            script.write_text((ROOT / 'scripts' / 'deploy_config.py').read_text(encoding='utf-8'), encoding='utf-8')
            env = {'PATH': os.environ.get('PATH', ''), 'DEEPSEEK_API_KEY': 'sk-test-deepseek', 'DASHSCOPE_API_KEY': 'sk-test-dash'}
            subprocess.run([sys.executable, str(script)], env=env, check=True, capture_output=True)
            runtime = app / 'runtime'
            reply = json.loads((runtime / 'reply-config.json').read_text())
            self.assertEqual(reply['prompt_version'], 'linyu-v1.4')
            self.assertNotIn('sk-test', json.dumps(reply))
            key = Path(reply['key_file'])
            self.assertEqual(key.read_text(), 'sk-test-deepseek')
            self.assertEqual(oct(key.stat().st_mode & 0o777), '0o600')
            self.assertTrue((runtime / 'retrieval-policy.json').is_file())
            self.assertFalse((runtime / 'tts-config.json').exists())  # no MiniMax key -> voice off
            missing = subprocess.run([sys.executable, str(script)], env={'PATH': env['PATH']}, capture_output=True)
            self.assertNotEqual(missing.returncode, 0)


class DailyLimitTest(unittest.TestCase):
    """Per-user and site-wide reply caps; failed attempts are free."""
    setUpClass = classmethod(helpers.ChatTest.setUpClass.__func__)
    setUp = helpers.ChatTest.setUp
    tearDown = helpers.ChatTest.tearDown
    req = helpers.ChatTest.req
    message = helpers.ChatTest.message
    snap = helpers.ChatTest.snap
    wait = helpers.ChatTest.wait
    def generate(self, user='a'):
        source = self.message()
        payload = {'kind': 'reply', 'user_message_id': source, 'client_request_id': 'g_' + str(time.time_ns())}
        return self.req('POST', '/api/conversations/' + self.cid + '/generations', payload, user)

    def test_per_user_cap_and_failures_free(self):
        self.app.chat.daily_limits = (2, 0)
        self.provider.plans = [[ModelError('provider_failed')]]
        r, data = self.generate(); self.assertEqual(r.status, 202, data)
        self.assertEqual(self.wait(data['request']['id'])['request']['status'], 'failed')
        for _ in range(2):
            r, data = self.generate(); self.assertEqual(r.status, 202, data)
            self.wait(data['request']['id'])
        r, data = self.generate()
        self.assertEqual((r.status, data['error']['code']), (429, 'daily_limit_reached'))
        _, session = self.req('GET', '/api/auth/session')
        self.assertEqual((session['daily_replies_left'], session['daily_reply_limit']), (0, 2))

    def test_site_wide_cap(self):
        self.app.chat.daily_limits = (0, 1)
        r, data = self.generate(); self.assertEqual(r.status, 202, data)
        self.wait(data['request']['id'])
        r, data = self.generate()
        self.assertEqual((r.status, data['error']['code']), (429, 'site_daily_limit_reached'))

    def test_day_boundary_is_beijing_midnight(self):
        from backend import limits
        # 2026-11-19 00:30 Beijing == 2026-11-18 16:30 UTC
        self.assertEqual(limits.day_start(1795019400), 1795017600)


class ReleasedCharacterTest(unittest.TestCase):
    """A fresh deployment serves the released Linyu version, not the v1 seed."""
    def test_fresh_database_gets_released_linyu(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Application(Config(db=Path(folder) / 'a.sqlite3', log_dir=Path(folder) / 'logs', origin='http://test.local'),
                              provider=helpers.FakeProvider())
            released = json.loads((ROOT / 'backend' / 'characters' / 'linyu-v6.json').read_text(encoding='utf-8'))
            self.assertEqual(app.service.character('linyu')['version'], 1)  # Application alone keeps the seed
            self.assertEqual(app.service.publish_releases(), ['linyu-v6'])  # what init_db.py does at deploy
            self.assertEqual(app.service.character('linyu')['version'], released['version'])
            self.assertEqual(app.service.character('linyu')['openings'], released['public']['openings'])
            self.assertEqual(app.service.publish_releases(), [])  # idempotent on restart


if __name__ == '__main__':
    unittest.main()
