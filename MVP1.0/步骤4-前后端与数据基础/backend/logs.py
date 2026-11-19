import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path

# Never serialize headers, bodies, query values, exception messages, passwords, or message contents.
SAFE_FIELDS = {'request_id', 'method', 'route', 'status', 'duration_ms', 'code', 'exception_type', 'frames', 'component'}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                           'level': record.levelname, 'event': record.msg,
                           **{k: v for k, v in getattr(record, 'fields', {}).items() if k in SAFE_FIELDS}}, ensure_ascii=False)


def create_logger(directory):
    Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    logger = logging.getLogger('mvp1.' + str(Path(directory).resolve()))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(str(Path(directory)/'app.jsonl'), maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        (Path(directory)/'app.jsonl').chmod(0o600)
    return logger


def emit(logger, event, level=logging.INFO, **fields):
    logger.log(level, event, extra={'fields': fields})
