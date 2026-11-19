import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self):
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at INTEGER NOT NULL)')
        for file in sorted((ROOT / 'migrations').glob('*.sql')):
            sql = file.read_text(encoding='utf-8')
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            with self.connect() as c:
                # BEGIN IMMEDIATE serializes concurrent startup/migration attempts.
                c.execute('BEGIN IMMEDIATE')
                row = c.execute('SELECT checksum FROM schema_migrations WHERE version=?', (file.name,)).fetchone()
                if row:
                    if row['checksum'] != checksum:
                        raise RuntimeError('Applied migration has changed: ' + file.name)
                    continue
                # execute statements individually: executescript would implicitly commit.
                statement = ''
                for line in sql.splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        c.execute(statement)
                        statement = ''
                if statement.strip():
                    raise RuntimeError('Incomplete migration: ' + file.name)
                c.execute('INSERT INTO schema_migrations VALUES(?,?,?)', (file.name, checksum, int(time.time())))
        self.path.chmod(0o600)


def rows(c, sql, params=()):
    return [dict(r) for r in c.execute(sql, params)]
