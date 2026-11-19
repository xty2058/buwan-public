# 页面分支 S1–S3 的补充提案

## 1. 布局原语 `.bw-grid--feature`（S1 发现页宽屏）

现状：discover.html 的“今晚推荐”（角色卡 + 开场选择）用 `.bw-grid--auto`。在 ≥768 宽屏下，`auto-fill, minmax(--bw-size-portrait, 1fr)` 会排出 4 列，只占 2 列，右侧留空。

提案（加入 styles/layout.css 布局原语区）：

```css
.bw-grid--feature { grid-template-columns: minmax(0, 1fr); }
@media (min-width: 768px) {
  .bw-grid--feature { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
}
```

采纳后，把 discover.html 中 `<section class="bw-grid bw-grid--auto" aria-label="今晚推荐">` 改为 `bw-grid bw-grid--feature`。不采纳也能用，只是宽屏右侧留白。
