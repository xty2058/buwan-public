"""Configure explicit Beijing v4/1024 approval; changes never erase call history."""
import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--authorize-text-embedding-v4', action='store_true')
    limits = p.add_mutually_exclusive_group(required=True)
    limits.add_argument('--max-requests', type=int)
    limits.add_argument('--unlimited', action='store_true')
    p.add_argument('--approval-id', required=True)
    p.add_argument('--extend-existing-approval', action='store_true')
    p.add_argument('--authorization-note', default='')
    args = p.parse_args()
    if not args.authorize_text_embedding_v4 or (not args.unlimited and not 1 <= args.max_requests <= 20):
        p.error('Explicit model approval and a 1..20 limit or explicitly authorized unlimited mode are required')
    if args.unlimited and not args.authorization_note:
        p.error('Unlimited authorization must have an explicit authorization note')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', args.approval_id):
        p.error('Invalid approval ID')
    runtime = Path(__file__).resolve().parent.parent / 'runtime'
    q = json.loads((runtime / 'qwen-config.json').read_text(encoding='utf-8'))
    if q.get('provider') != 'aliyun-qwen' or not q.get('allow_live'):
        raise SystemExit('An authorized Aliyun Qwen configuration is required')
    base = q['base_url'].rstrip('/')
    u = urlsplit(base)
    if (u.scheme != 'https' or u.port not in (None, 443) or u.username or u.password or u.query or u.fragment
            or u.path != '/compatible-mode/v1'
            or not (u.hostname == 'dashscope.aliyuncs.com' or re.fullmatch(r'[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com', u.hostname or ''))):
        raise SystemExit('A verified Beijing compatible-mode endpoint is required; configuration was not written')
    key = Path(q['key_file']).expanduser()
    if not key.is_file():
        raise SystemExit('The saved local credential file is unavailable')
    out = runtime / 'embedding-config.json'
    settings = {'allow_live': True, 'model': 'text-embedding-v4', 'dimensions': 1024,
                'region': 'cn-beijing', 'base_url': base, 'key_file': str(key),
                'approval_id': args.approval_id, 'max_requests': args.max_requests,
                'budget_db': str(runtime / 'embedding-call-budget.sqlite3')}
    if args.unlimited:
        settings['unlimited_authorized'] = True
    previous = json.loads(out.read_text()) if out.exists() else None
    if previous and previous != settings:
        fields = {'max_requests', 'unlimited_authorized'}
        if (not args.extend_existing_approval or not args.authorization_note
                or {k:v for k,v in previous.items() if k not in fields} != {k:v for k,v in settings.items() if k not in fields}):
            raise SystemExit('Existing configuration differs; explicit extension of the same approval is required')
        old_limit = -1 if previous.get('unlimited_authorized') and previous['max_requests'] is None else previous['max_requests']
        new_limit = -1 if args.unlimited else args.max_requests
        with sqlite3.connect(settings['budget_db']) as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT request_limit FROM budgets WHERE approval=?', (args.approval_id,)).fetchone()
            if not row or row[0] != old_limit:
                raise SystemExit('Existing ledger limit does not match configuration; no implicit reset')
            used = c.execute('SELECT count(*) FROM calls WHERE approval=?', (args.approval_id,)).fetchone()[0]
            if new_limit != -1 and used > new_limit:
                raise SystemExit('New limit cannot be below requests already consumed')
            c.execute('CREATE TABLE IF NOT EXISTS authorization_changes (approval TEXT, changed_at REAL, old_limit INTEGER, new_limit INTEGER, used_at_change INTEGER, note TEXT)')
            c.execute('INSERT INTO authorization_changes VALUES(?,?,?,?,?,?)',
                      (args.approval_id, time.time(), old_limit, new_limit, used, args.authorization_note))
            c.execute('UPDATE budgets SET request_limit=? WHERE approval=?', (new_limit,args.approval_id))
    out.touch(mode=0o600, exist_ok=True)
    out.chmod(0o600)
    out.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    print('Saved explicit Beijing text-embedding-v4 / 1024 approval; existing request history retained. No API call or key output.')


if __name__ == '__main__':
    main()
