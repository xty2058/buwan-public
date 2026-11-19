#!/bin/zsh
project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir" || exit 1
python3 -m venv --without-pip .venv || exit 1
.venv/bin/python3 -B scripts/init_db.py || exit 1
print '开发环境已初始化。双击「启动开发环境.command」，访问 http://127.0.0.1:8840。'
