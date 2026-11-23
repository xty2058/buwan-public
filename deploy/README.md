# 不晚 · 服务器部署

一台 Linux 服务器（香港或海外可免 ICP 备案）+ 一个域名 + Docker，即可得到 `https://你的域名` 的邀请制服务。

## 部署形态

- `app`：Python 标准库后端 + SQLite，数据库、日志、生成的模型配置与密钥文件都在数据卷 `buwan-runtime`，不进镜像、不进 Git。
- `caddy`：自动申请和续期 HTTPS 证书，反向代理到 `app`，流式回复不缓冲。
- 部署开关（已写在 `docker-compose.yml`）：邀请制注册、关闭免注册试用、不包含内部评测页与诊断页、只开放林屿、Cookie 仅 HTTPS、从代理取真实 IP 用于登录限流。

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

## 回复版本与生效范围

部署脚本默认使用林屿 `linyu-v1.8`，公开版只包含此当前提示词。升级已有服务器前，删除 `.env` 中指向旧版的 `PROMPT_VERSION` 覆盖，再重新构建、启动容器。更新源码不会自动更新已运行服务。

## 每日调用限制与付费边界

- Docker 部署默认每人每天 **60 次**、全站每天 **1500 次**，任一上限达到即拒绝新的生成请求；北京时间每天 0 点恢复。分别通过 `DAILY_REPLY_LIMIT`、`DAILY_TOTAL_LIMIT` 调整，设为 `0` 表示关闭对应限制。本机未配置时默认不限制。
- 普通聊天、重新生成、共读回复均计入；已创建且未标记失败的请求（包括等待中、生成中、用户停止）计入，失败后不计入。停止可能已经产生服务商费用，但不能据此断言每次停止都实际扣费。
- 这是当前邀请测试的防滥用与成本控制次数，**不是会员额度、积分或扣费账本**；当前没有付费功能。停止仍占调用次数，不表示向用户收费。
- 商业化前必须单独实现付费额度结算，遵守「失败、用户停止的未完成回复、重新生成失败不扣付费额度」，并分别展示调用限制与付费余额；不得直接用当前计数器扣款。
- 此上限不是精确的金额预算：记忆提取、摘要、向量、画像、回复选项、回访主动消息、语音识别等调用不按此计数器逐笔计数，失败请求也可能产生服务商费用。上线后仍需核对各模型服务商账单。

## 其他限制

- 语音有总调用上限（`TTS_MAX_CALLS`、`TTS_MAX_CHARS`），超出后语音停用。
- 正式大范围公开前，需要确认生成式 AI 服务相关的合规要求。
