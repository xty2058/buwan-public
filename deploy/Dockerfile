# 不晚 hosted service. Build from the repository root:
#   docker compose -f deploy/docker-compose.yml build
# The backend uses only the Python standard library, so there is nothing to pip install.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
RUN useradd --create-home --uid 10001 buwan
WORKDIR /app

COPY ["MVP1.0/步骤4-前后端与数据基础/backend", "/app/backend"]
COPY ["MVP1.0/步骤4-前后端与数据基础/frontend", "/app/frontend"]
COPY ["MVP1.0/步骤4-前后端与数据基础/migrations", "/app/migrations"]
COPY ["MVP1.0/步骤4-前后端与数据基础/scripts/deploy_config.py", "MVP1.0/步骤4-前后端与数据基础/scripts/init_db.py", "MVP1.0/步骤4-前后端与数据基础/scripts/invite.py", "MVP1.0/步骤4-前后端与数据基础/scripts/backup_db.py", "/app/scripts/"]
# The visual spec is served directly by the product (backend/api.py looks for /app/buwan-ds).
COPY design/buwan-ds /app/buwan-ds
COPY deploy/entrypoint.sh /app/entrypoint.sh

# runtime/ holds the database, logs, generated configs and key files: a volume, never baked in.
RUN mkdir -p /app/runtime && chown -R buwan:buwan /app/runtime && chmod 0700 /app/runtime && chmod 0755 /app/entrypoint.sh
USER buwan
VOLUME ["/app/runtime"]
EXPOSE 8840

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import os,http.client,urllib.parse;o=urllib.parse.urlsplit(os.environ['APP_ORIGIN']);c=http.client.HTTPConnection('127.0.0.1',8840,timeout=4);c.request('GET','/api/health',headers={'Host':o.netloc});exit(0 if c.getresponse().status==200 else 1)"

ENTRYPOINT ["/app/entrypoint.sh"]
