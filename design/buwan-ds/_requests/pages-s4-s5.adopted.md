# 组件补充请求 · pages-s4-s5（S4 control / S5 payment）

## 1. `.bw-fieldset`：分组表单的原生 fieldset 重置（可访问性）
需求：S5 套餐选择、S4 聊天节奏本应使用 `<fieldset>` + `<legend>`，但 components.css 里没有登记重置 fieldset 默认边框和内边距的类。
当前凑合做法：`<div role="radiogroup" aria-labelledby="…">`，语义等价，屏幕阅读器可以正确读出分组名称。
提案（加到 components.css「3. Select / Segmented」之后）：

```css
/* Fieldset：原生分组重置 @a11y */
.bw-fieldset { margin: 0; padding: 0; border: 0; min-inline-size: 0; display: flex; flex-direction: column; gap: var(--bw-space-2); }
.bw-fieldset__legend { padding: 0; margin-bottom: var(--bw-space-2); font-size: var(--bw-text-lg); font-weight: var(--bw-weight-semibold); line-height: var(--bw-leading-snug); }
```
登记后，把 payment.html `#plans`、control.html `#tempo` 改回 `fieldset.bw-fieldset` + `legend.bw-fieldset__legend`（`#tempo` 可写成 `fieldset.bw-segmented`，因为 .bw-segmented 已经重置了 margin，但还需要 `padding` 与 legend 的处理）。
