#!/bin/zsh
project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir" || exit 1
if [[ -x .venv/bin/python3 ]]; then
  .venv/bin/python3 -B -m backend.server
else
  python3 -B -m backend.server
fi
