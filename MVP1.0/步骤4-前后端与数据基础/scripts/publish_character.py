"""Local operator utility: publish a new immutable role version, never edit old versions."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import Config
from backend.db import Database
from backend.service import Service

parser = argparse.ArgumentParser(description='Publish one local character version from a JSON file')
parser.add_argument('file', type=Path)
args = parser.parse_args()
config = Config.from_env()
db = Database(config.db)
db.migrate()
version_id = Service(db).publish(json.loads(args.file.read_text(encoding='utf-8')))
print('Published version ID: ' + version_id)
