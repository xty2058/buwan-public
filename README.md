# 不晚 · AI 情感陪伴

手机优先的 AI 情感陪伴产品。你和一位人格稳定的成年原创角色——独立书店「未晚」的店主**林屿**——建立一段慢热、长期、可回溯的关系：他记得你确认过的事，不编造你们没有过的经历，关系怎么推进由你决定。

> 这是项目的公开版本：完整可运行的代码、角色设定与部署文件。用户研究、方案和验收记录不在此仓库。

## 能做什么

- **聊天**：流式回复，可停止、重试、重新生成候选并自己选择采用哪一版；回复里说错的事可以就地「说错了」纠正。
- **长期记忆**：模型只提出「候选」，**你确认后才记住**；可修改、删除，删除后不再被召回。召回带相对时间（「约 3 天前」），纠正一条事实时相近的旧记忆一并停用。
- **关系与约定**：关系变化必须由你明确确认；约定到期时，角色会在你隔一段时间回来后自然提起。
- **时间感知**：角色知道现在是周几、什么时段，以及距上次聊天隔了多久。
- **共读**：和林屿一起分段读一篇短文，可暂离、续读，读完留下可确认的活动记忆。
- **语音**：角色回复可合成语音播放（MiniMax，可选）。
- **你的数据**：聊天、记忆和关系记录可导出为 Markdown / JSON。

## 技术

- 后端：Python 标准库（无第三方依赖）+ SQLite，版本化迁移。
- 前端：原生 JavaScript，无构建步骤；视觉规范见 `design/buwan-ds/`。
- 模型：回复 DeepSeek（`deepseek-flash`），记忆提取 / 重排 / 摘要用通义千问 `qwen-flash`，向量 `text-embedding-v4`，语音 MiniMax（可选）。林屿的专属提示词在 `backend/prompts/`，当前角色版本在 `backend/characters/`，30 题回复评测集在 `backend/evaluation_cases.json`。
- 测试：后端 300+ 项单元与集成测试、前端 node 测试，全部使用模拟模型，不调用付费接口。

## 部署（邀请制服务）

需要一台 Linux 服务器、一个域名和 Docker。详见 [deploy/README.md](deploy/README.md)：

```bash
cp deploy/.env.example deploy/.env      # 填域名与 API Key
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml exec app python3 scripts/invite.py create --label 朋友 --count 5
```

部署默认：邀请码注册、只开放林屿、每人每天 60 次回复上限、Caddy 自动 HTTPS、内部评测工具关闭。API Key 只放在服务器的 `deploy/.env`，不进 Git、不进镜像。

## 本地开发

```bash
cd "MVP1.0/步骤4-前后端与数据基础"
python3 scripts/init_db.py
python3 -m backend.server        # http://127.0.0.1:8840
```

运行全部测试（仓库根目录）：

```bash
python3 scripts/verify.py
```

不配置模型时，页面会明确显示「真实模型未配置」，账号、记忆管理等非生成功能仍可使用。配置真实模型所需的环境变量见 `scripts/deploy_config.py`。

## 目录

| 路径 | 内容 |
|---|---|
| `MVP1.0/步骤4-前后端与数据基础/` | 应用：`backend/`、`frontend/`、`migrations/`、`scripts/`、`tests/`（目录名为历史沿用） |
| `design/buwan-ds/` | 不晚视觉规范（Token、图标、角色立绘），产品直接加载；在线浏览：https://xty2058.github.io/buwan-public/ |
| `deploy/` | Dockerfile、docker compose、Caddy 配置与部署说明 |
| `scripts/verify.py` | 一键运行全部测试 |

## 许可证

[MIT](LICENSE)。角色「林屿」的设定、台词与立绘同样以 MIT 发布。
