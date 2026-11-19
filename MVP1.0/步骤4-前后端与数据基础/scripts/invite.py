# -*- coding: utf-8 -*-
"""Manage invite codes for the invite-only deployment. Codes are shown once; only digests are stored.

  python3 scripts/invite.py create --label 朋友A --uses 1 [--days 30] [--count 5]
  python3 scripts/invite.py list
  python3 scripts/invite.py disable --label 朋友A

In Docker:  docker compose exec app python3 scripts/invite.py create --label 朋友A
"""
import argparse
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.auth import digest  # noqa: E402
from backend.config import Config  # noqa: E402
from backend.db import Database  # noqa: E402

ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O/1/I to avoid misreading


def new_code():
    raw = ''.join(secrets.choice(ALPHABET) for _ in range(12))
    return f'{raw[:4]}-{raw[4:8]}-{raw[8:]}'


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('create'); c.add_argument('--label', required=True); c.add_argument('--uses', type=int, default=1)
    c.add_argument('--days', type=int); c.add_argument('--count', type=int, default=1)
    sub.add_parser('list')
    d = sub.add_parser('disable'); d.add_argument('--label', required=True)
    a = p.parse_args()
    db = Database(Config.from_env().db)
    db.migrate()
    now = int(time.time())
    with db.connect() as conn:
        if a.cmd == 'create':
            if not 1 <= a.uses <= 1000 or not 1 <= a.count <= 100:
                sys.exit('--uses 取 1–1000，--count 取 1–100')
            expires = now + a.days * 86400 if a.days else None
            for i in range(a.count):
                code = new_code()
                label = a.label if a.count == 1 else f'{a.label}-{i + 1}'
                conn.execute('INSERT INTO invites(code_hash,label,max_uses,created_at,expires_at) VALUES(?,?,?,?,?)',
                             (digest('invite:' + code), label, a.uses, now, expires))
                print(f'{label}\t{code}')
            print('邀请码只显示这一次，请现在复制保存。', file=sys.stderr)
        elif a.cmd == 'list':
            for r in conn.execute('SELECT label,used_count,max_uses,expires_at,disabled_at FROM invites ORDER BY created_at'):
                state = '已停用' if r['disabled_at'] else ('已过期' if r['expires_at'] and r['expires_at'] <= now else '有效')
                print(f"{r['label']}\t已用 {r['used_count']}/{r['max_uses']}\t{state}")
        else:
            n = conn.execute('UPDATE invites SET disabled_at=? WHERE label=? AND disabled_at IS NULL', (now, a.label)).rowcount
            print(f'已停用 {n} 个邀请码')


if __name__ == '__main__':
    main()
