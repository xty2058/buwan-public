import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import Config
from backend.db import Database
from backend.service import Service
config = Config.from_env()
db = Database(config.db)
db.migrate()
service = Service(db)
service.seed()
released = service.publish_releases()
print('数据库迁移与三个角色初始化完成。' + ('已发布：' + '、'.join(released) if released else ''))
