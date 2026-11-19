/* 自检页：渲染 audit/report.js（由 node scripts/audit.mjs 生成）的自动检查结果 */
(function () {
  'use strict';
  const R = window.BW_AUDIT;
  const $ = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  if (!R) {
    const host = $('[data-audit-summary]');
    if (host) host.innerHTML = '<div class="bw-notice bw-notice--warning"><span class="bw-notice__body"><span class="bw-notice__title">还没有检查报告</span><span>在 design/buwan-ds 目录运行 node scripts/audit.mjs 后刷新本页。</span></span></div>';
    return;
  }
  const ICONS = (document.querySelector('meta[name="bw-icons"]') || {}).content || 'assets/icons.svg';
  const icon = id => `<svg class="bw-icon bw-icon--sm" aria-hidden="true"><use href="${ICONS}#${id}"/></svg>`;
  const ok = R.issueCount === 0;

  const sum = $('[data-audit-summary]');
  if (sum) sum.innerHTML = `
    <div class="bw-grid bw-grid--3">
      <div class="bw-card bw-stack bw-stack--2"><span class="bw-label">Token 总数</span><span class="bw-num bw-num--hero">${R.tokenTotal}</span></div>
      <div class="bw-card bw-stack bw-stack--2"><span class="bw-label">已登记 class</span><span class="bw-num bw-num--hero">${R.registeredClasses}</span></div>
      <div class="bw-card bw-stack bw-stack--2"><span class="bw-label">未登记样式 / 问题</span><span class="bw-num bw-num--hero">${R.issueCount}</span>
        <span class="bw-tag ${ok ? 'bw-tag--success' : 'bw-tag--danger'}">${icon(ok ? 'check' : 'alert-circle')}${ok ? '全部通过' : '需要修复'}</span></div>
    </div>
    <p class="bw-fine">报告生成于 ${esc(new Date(R.generatedAt).toLocaleString('zh-CN'))}</p>`;

  const rows = Object.entries(R.pages).sort(([a], [b]) => a.localeCompare(b));
  const tbl = $('[data-audit-pages]');
  if (tbl) tbl.innerHTML = rows.map(([f, p]) => `
    <tr>
      <th scope="row"><a href="${esc(f)}">${esc(f)}</a></th>
      <td>${p.scenes.map(s => `<span class="bw-tag">${esc(s)}</span>`).join(' ') || '—'}</td>
      <td>${p.principles.map(s => `<span class="bw-tag bw-tag--accent">${esc(s)}</span>`).join(' ') || '—'}</td>
      <td class="bw-table__num">${p.components.length}</td>
      <td class="bw-table__num">${p.tokenCount}</td>
      <td><span class="bw-tag ${p.unregistered ? 'bw-tag--danger' : 'bw-tag--success'}">${p.unregistered ? p.unregistered + ' 个' : '无'}</span></td>
    </tr>`).join('');

  const det = $('[data-audit-details]');
  if (det) det.innerHTML = rows.filter(([f]) => f.startsWith('pages/')).map(([f, p]) => `
    <details class="bw-card bw-card--compact bw-stack bw-stack--3">
      <summary class="bw-subheading">${esc(f)} · ${p.components.length} 个组件 · ${p.tokenCount} 个 Token</summary>
      <div class="bw-stack bw-stack--2"><span class="bw-label">组件</span><div class="bw-cluster">${p.components.map(c => `<span class="bw-tag">${esc(c)}</span>`).join('')}</div></div>
      <div class="bw-stack bw-stack--2"><span class="bw-label">Token</span><div class="bw-cluster">${p.tokens.map(t => `<code class="bw-code-inline">${esc(t)}</code>`).join(' ')}</div></div>
    </details>`).join('');

  const iss = $('[data-audit-issues]');
  if (iss) iss.innerHTML = ok
    ? `<div class="bw-notice bw-notice--success">${icon('check')}<div class="bw-notice__body"><p class="bw-notice__title">没有发现未登记样式</p><p>所有页面只使用了 tokens.css 定义的变量和 CSS 中登记过的 class；组件与布局样式中没有颜色或 px 字面量。</p></div></div>`
    : `<div class="bw-table-wrap"><table class="bw-table bw-table--compact"><caption>问题清单</caption><thead><tr><th scope="col">文件</th><th scope="col">类型</th><th scope="col">详情</th></tr></thead><tbody>${R.issues.map(x => `<tr><td>${esc(x.file)}</td><td>${esc(x.kind)}</td><td>${esc(x.detail)}</td></tr>`).join('')}</tbody></table></div>`;
})();
