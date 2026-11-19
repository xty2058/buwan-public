# 不晚设计系统（暮光玻璃）

规范站是单一事实源：颜色、字号、间距、圆角、阴影、动效只在 `styles/tokens.css` 定义；组件只引用 Token；页面只使用已登记的组件和布局模板。

## 运行

必须经由本地服务器打开（图标用 `<use href>` 引用外部 SVG，Token 表要读取样式表规则，直接双击 HTML 会失效）：

```bash
cd design/buwan-ds
python3 scripts/serve.py        # 不缓存，改完刷新即是最新；默认 http://localhost:8765
```

改完样式或页面后重新自检（会刷新 `audit.html` 的数据）：

```bash
node scripts/audit.mjs
```

## 文件结构

```
design/buwan-ds/
├── index.html            总览：原则、关键词、页面预览
├── foundations.html      推导表、色彩、字体、空间、栅格、形状、图标、动效、可访问性、文案、Token 表
├── components.html       组件规范（用途 / 适用 / 不适用 / 变体 / 状态 / 尺寸 / 间距 / 可访问性 / 代码）
├── audit.html            一致性自检表（读取 audit/report.js）
├── pages/                五个核心页面 discover · chat · return · control · payment
├── styles/
│   ├── tokens.css        唯一允许出现颜色、字号、间距字面量的文件
│   ├── layout.css        基础层、字体样式、布局原语、App 布局模板 T1–T3、沉浸全屏 T5
│   ├── components.css    全部组件与状态
│   └── docs.css          规范站布局模板 T4 与文档组件
├── scripts/
│   ├── main.js           主题、Toast、弹窗、单选组、Token 表（实时读取 tokens.css）
│   ├── pages/*.js        各页面的演示交互
│   ├── audit.mjs         一致性检查
│   └── serve.py          本地预览（不缓存）
├── assets/
│   ├── icons.svg         图标库
│   └── characters/       角色立绘
└── explorations/         色彩方向探索稿（不受规范约束）
```

## 新增样式的流程

1. 在 `tokens.css` 加 Token，标 `@principle` / `@scene`
2. 在 `components.css` 或 `layout.css` 登记 class
3. 在 `components.html` 补用途、适用、不适用、状态、代码
4. 最后才在页面里使用，并跑一次 `node scripts/audit.mjs`

## 正式产品接入（2026-11-18 起）

本规范是不晚这个产品的视觉设计规范。正式产品（`MVP1.0/步骤4-前后端与数据基础/`）按下面的方式接入，不保留任何副本：

- **Token**：产品页面先加载 `/buwan/styles/tokens.css`，后端直接读取本目录的 `styles/tokens.css`。改 Token 后刷新产品即生效。
- **图标与角色立绘**：`/buwan/assets/icons.svg`、`/buwan/assets/characters/*.jpg` 同样直接读取本目录。
- **产品样式**：`frontend/style.css` 只引用 `var(--bw-*)`，不写颜色字面量，px 只出现在媒体查询里；每个组件块的注释标了它对应本规范的哪个组件。
- **本机后台服务**：装在 `~/Library/Application Support/` 下的服务读不到桌面目录，`scripts/redeploy_local.py` 每次部署会把 `styles/tokens.css` 和 `assets/` 重新发布到服务目录的 `buwan-ds/`。所以改了规范之后要重新部署一次，线上才会更新。
- **自动检查**：`tests/test_visual_spec.py` 会检查上面三条（Token 是否都在本规范登记过、有没有颜色字面量、文件是否原样提供）。

产品里要新增视觉样式时，仍按「新增样式的流程」先在这里登记 Token 和组件，再到产品里使用。

目前产品与规范页面的差异（属于功能改动，未随视觉接入一起做）：

- 首页已按规范做成上下滑切换、原地开聊（2026-11-18）。和规范原型的三处不同：没有「换个开场」（开场由服务端在建立对话时决定）；输入框里没有替用户写好的第一句（产品没有这份文案，只给占位提示）；原地对话只显示气泡，重新生成、纠正、删除、语音、记忆与关系等工具在右上角「完整对话」里。
- 聊天页是单列，规范 ≥1024 的右侧记忆面板（T2 分栏）未做。
- 只接入了深色（品牌默认），浅色「暮光·浅」的切换入口未做。
- `/evaluation`、`/inspect` 是内部工具页，不在规范范围内。
