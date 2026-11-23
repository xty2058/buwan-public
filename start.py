"""Start Buwan locally; model configuration is optional for browsing."""
import os
import subprocess
import sys
from pathlib import Path
root = Path(__file__).resolve().parent / "MVP1.0/步骤4-前后端与数据基础"
os.environ.setdefault("APP_OPEN_CHARACTERS", "linyu")
subprocess.run([sys.executable, str(root / "scripts/init_db.py")], cwd=root, check=True)
subprocess.run([sys.executable, "-m", "backend.server"], cwd=root, check=True)
