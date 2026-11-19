#!/bin/sh
# Container start: model configs from environment -> database migrations and seed -> server.
set -eu
cd /app
python3 scripts/deploy_config.py
python3 scripts/init_db.py
exec python3 -m backend.server
