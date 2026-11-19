# -*- coding: utf-8 -*-
"""Isolated subprocess provider for crash recovery tests; never loads a real model key."""
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.api import Application
from backend.config import Config
from backend.server import make_server
from test_chat import FakeProvider

if __name__=='__main__':
    directory,port=Path(sys.argv[1]),int(sys.argv[2])
    provider=FakeProvider()
    if len(sys.argv)>3:
        provider.plans=[['PERSISTED_BEFORE_CRASH',threading.Event(),'NEVER_COMMITTED']]
    else:
        provider.plans=[['VALID_AFTER_RESTART'],['NEW_CANDIDATE_AFTER_RESTART'],['CONTINUED_AFTER_RESTORE']]
    config=Config(db=directory/'process.sqlite3',log_dir=directory/'logs',port=port,origin='http://127.0.0.1:'+str(port))
    server=make_server(Application(config,provider=provider))
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
