# -*- coding: utf-8 -*-
"""Consistent online backup of the SQLite database (SQLite backup API; safe while the server runs).

  python3 scripts/backup_db.py [--keep 14]
Writes runtime/backups/app-YYYYmmdd-HHMMSS.sqlite3 and keeps the newest N copies.
In Docker:  docker compose -f deploy/docker-compose.yml exec app python3 scripts/backup_db.py
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import Config  # noqa: E402


def main():
    p = argparse.ArgumentParser(); p.add_argument('--keep', type=int, default=14); a = p.parse_args()
    db = Config.from_env().db
    folder = db.parent / 'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    target = folder / time.strftime('app-%Y%m%d-%H%M%S.sqlite3')
    src = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    os.chmod(target, 0o600)
    check = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    ok = check.execute('PRAGMA integrity_check').fetchone()[0]
    check.close()
    if ok != 'ok':
        sys.exit('备份完整性检查失败：' + ok)
    for old in sorted(folder.glob('app-*.sqlite3'))[:-a.keep]:
        old.unlink()
    print('备份完成：' + target.name)


if __name__ == '__main__':
    main()
