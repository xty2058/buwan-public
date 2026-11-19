# 不晚 · 服务器部署

一台 Linux 服务器（香港或海外可免 ICP 备案）+ 一个域名 + Docker，即可得到 `https://你的域名` 的邀请制服务。

## 部署形态

- `app`：Python 标准库后端 + SQLite，数据库、日志、生成的模型配置与密钥文件都在数据卷 `buwan-runtime`，不进镜像、不进 Git。
- `caddy`：自动申请和续期 HTTPS 证书，反向代理到 `app`，流式回复不缓冲。
- 部署开关（已写在 `docker-compose.yml`）：邀请制注册、关闭免注册试用、关闭评测页与召回检查页、只开放林屿、Cookie 仅 HTTPS、从代理取真实 IP 用于登录限流。

## 首次部署

1. 域名的 A 记录指向服务器 IP；服务器开放 80、443 端口；装好 Docker。
2. 拉取代码，进入仓库根目录：
   ```bash
   cp deploy/.env.example deploy/.env
   chmod 600 deploy/.env
   ```
   编辑 `deploy/.env`：填 `BUWAN_DOMAIN`、`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`；要语音再填 `MINIMAX_API_KEY`。
3. 启动：
   ```bash
   docker compose -f deploy/docker-compose.yml up -d --build
   ```
4. 打开 `https://你的域名`，应看到登录页提示「当前为邀请制」，首页只有林屿可聊。

## 邀请码

```bash
docker compose -f deploy/docker-compose.yml exec app python3 scripts/invite.py create --label 朋友A --uses 1
docker compose -f deploy/docker-compose.yml exec app python3 scripts/invite.py create --label 第一批 --count 5 --days 14
docker compose -f deploy/docker-compose.yml exec app python3 scripts/invite.py list
docker compose -f deploy/docker-compose.yml exec app python3 scripts/invite.py disable --label 朋友A
```

邀请码只在生成时显示一次，数据库只存哈希；输入时不区分大小写。

## 备份

```bash
docker compose -f deploy/docker-compose.yml exec app python3 scripts/backup_db.py --keep 14
```

使用 SQLite backup API，服务运行中也能得到一致的副本，保存在数据卷的 `backups/`，并做完整性检查。建议在服务器上用 cron 每天执行一次，并定期把 `backups/` 拷到服务器之外。

## 更新与回退

```bash
git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

回退：`git checkout <上一个版本>` 后同样执行上面这条。注意数据库迁移不会随代码回退自动撤销，回退前先备份。

## 尚未做的护栏

- 没有按人或按天的聊天调用上限；目前靠邀请制控制人数。上线后请每周核对各模型服务商的扣费。
- 语音有总调用上限（`TTS_MAX_CALLS`、`TTS_MAX_CHARS`），超出后语音停用。
- 正式大范围公开前，需要确认生成式 AI 服务相关的合规要求。
