#!/usr/bin/env node
/* =====================================================================
   不晚 · 一致性检查（阶段 4 自检的自动部分）
   用法：node scripts/audit.mjs          → 打印结果并写入 audit/report.json
   检查项：
     1. 组件/布局/文档 CSS 中不得出现颜色字面量；px 只允许出现在 @media 条件里
     2. 所有 var(--bw-*) 引用都必须在 tokens.css（或组件内登记的局部变量）中定义
     3. HTML 不得有 style 属性、<style> 标签、颜色字面量
     4. HTML 中出现的每个 class 都必须是 CSS 中登记过的类
     5. JS 中不得出现颜色字面量
   统计：每个页面用到的组件、Token（经由 class → CSS 规则 → var 引用 → Token 依赖链展开）
   ===================================================================== */
import { readFileSync, writeFileSync, readdirSync, mkdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = p => readFileSync(join(ROOT, p), 'utf8');
const walk = (dir, ext) => readdirSync(join(ROOT, dir)).flatMap(f => {
  const p = join(dir, f);
  if (f === 'explorations' || f === 'audit' || f.startsWith('.')) return [];
  return statSync(join(ROOT, p)).isDirectory() ? walk(p, ext) : p.endsWith(ext) ? [p] : [];
});

const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g;
const stripComments = s => s.replace(/\/\*[\s\S]*?\*\//g, '');

/* ---------- CSS 解析：得到 [{selector, body, media}] ---------- */
function parseCss(src) {
  src = stripComments(src);
  const rules = []; let i = 0;
  function block(media) {
    let prelude = '';
    while (i < src.length) {
      const c = src[i];
      if (c === '{') {
        i++;
        const p = prelude.trim(); prelude = '';
        if (p.startsWith('@media') || p.startsWith('@supports')) { block(p); continue; }
        let depth = 1, body = '';
        while (i < src.length && depth) { const ch = src[i++]; if (ch === '{') depth++; else if (ch === '}') depth--; if (depth) body += ch; }
        if (!p.startsWith('@keyframes')) rules.push({ selector: p, body, media });
        else rules.push({ selector: p, body: '', media, keyframes: body });
      } else if (c === '}') { i++; return; }
      else { prelude += c; i++; if (c === ';' && prelude.trim().startsWith('@')) prelude = ''; }
    }
  }
  block(null);
  return rules;
}

const tokenSrc = read('styles/tokens.css');
const tokenDefs = new Map();
for (const r of parseCss(tokenSrc)) for (const m of r.body.matchAll(/(--bw-[\w-]+)\s*:\s*([^;]+);/g)) if (!tokenDefs.has(m[1])) tokenDefs.set(m[1], m[2]);
const tokenDeps = n => [...(tokenDefs.get(n) || '').matchAll(/var\((--bw-[\w-]+)/g)].map(m => m[1]);

const CSS_FILES = ['styles/layout.css', 'styles/components.css', 'styles/docs.css'];
const issues = [];
const registered = new Set(); const localVars = new Set();
const classRules = new Map(); // class -> Set(tokens)

for (const f of CSS_FILES) {
  const raw = read(f);
  const src = stripComments(raw);
  // 颜色字面量
  for (const m of src.matchAll(COLOR_RE)) issues.push({ file: f, kind: '颜色字面量', detail: m[0] });
  // px 字面量（@media 条件除外）
  src.split('\n').forEach((line, n) => {
    if (/^\s*@media/.test(line)) return;
    for (const m of line.matchAll(/(?<![\w-])\d*\.?\d+px\b/g)) issues.push({ file: f, kind: 'px 字面量', detail: `第 ${n + 1} 行 ${m[0]}` });
  });
  for (const r of parseCss(raw)) {
    for (const m of r.body.matchAll(/(--bw-[\w-]+)\s*:/g)) localVars.add(m[1]);
    const classes = [...r.selector.matchAll(/\.([a-zA-Z][\w-]*)/g)].map(m => m[1]);
    const vars = [...(r.body + (r.keyframes || '')).matchAll(/var\((--bw-[\w-]+)/g)].map(m => m[1]);
    classes.forEach(c => { registered.add(c); if (!classRules.has(c)) classRules.set(c, new Set()); vars.forEach(v => classRules.get(c).add(v)); });
    vars.forEach(v => { if (!tokenDefs.has(v) && !localVars.has(v)) issues.push({ file: f, kind: '未定义 Token', detail: v }); });
  }
}
// 二次确认局部变量（顺序无关）
for (let k = issues.length - 1; k >= 0; k--) if (issues[k].kind === '未定义 Token' && localVars.has(issues[k].detail)) issues.splice(k, 1);

/* ---------- 组件登记表：class 前缀 → 组件名（与 components.html 章节一一对应） ---------- */
const COMPONENTS = [
  ['bw-btn', '按钮 Button'], ['bw-icon-btn', '图标按钮 IconButton'], ['bw-field', '表单项 Field'], ['bw-input', '输入框 Input'],
  ['bw-composer', '聊天输入 Composer'], ['bw-select', '选择器 Select'], ['bw-segmented', '分段选择 Segmented'], ['bw-tag', '标签 Tag'],
  ['bw-card', '卡片 Card'], ['bw-character', '角色卡 CharacterCard'], ['bw-avatar', '头像 Avatar'], ['bw-opening', '开场选择 OpeningPicker'],
  ['bw-table', '表格 Table'], ['bw-topbar', '顶栏 Topbar'], ['bw-tabbar', '标签栏 Tabbar'], ['bw-sidenav', '侧边导航 Sidenav'],
  ['bw-modal', '弹窗 Modal/Sheet'], ['bw-toast', '提示 Toast'], ['bw-notice', '通知 Notice'], ['bw-empty', '空状态 Empty'],
  ['bw-error-state', '错误状态 ErrorState'], ['bw-spinner', '加载 Spinner'], ['bw-skeleton', '骨架 Skeleton'], ['bw-typing', '正在输入 Typing'],
  ['bw-retry', '内联重试 Retry'], ['bw-thread', '对话流 Thread'], ['bw-message', '消息 Message'], ['bw-bubble', '气泡 Bubble'],
  ['bw-system-line', '系统行 SystemLine'], ['bw-day-divider', '日期分隔 DayDivider'], ['bw-choice-group', '选择组 ChoiceGroup'],
  ['bw-quota', '额度 Quota'], ['bw-meter', '进度条 Meter'], ['bw-quota-hint', '额度提示 QuotaHint'], ['bw-plan', '套餐 Plan'],
  ['bw-checklist', '规则清单 Checklist'], ['bw-memory-chip', '记忆标签 MemoryChip'], ['bw-recap', '上次聊到 Recap'],
  ['bw-promise', '约定 Promise'], ['bw-memory-list', '记忆列表 MemoryList'], ['bw-memory-item', '记忆条目 MemoryItem'],
  ['bw-correction', '纠正 Correction'], ['bw-timeline', '时间线 Timeline'], ['bw-branch-point', '分岔点 BranchPoint'],
  ['bw-branch-switcher', '分支切换 BranchSwitcher'], ['bw-branch', '分支项 Branch'], ['bw-continuity', '存续状态 Continuity'], ['bw-stage', '关系阶段 Stage'],
  ['bw-spotlight', '全屏人物 Spotlight'], ['bw-opening-panel', '开场面板 OpeningPanel'], ['bw-starter', '开聊输入 Starter'],
  ['bw-app', '布局模板 App'], ['bw-docs', '布局模板 Docs'], ['bw-immersive', '布局模板 Immersive'], ['bw-feed', '布局模板 Immersive'],
];
const componentOf = c => { const hit = COMPONENTS.filter(([p]) => c === p || c.startsWith(p + '--') || c.startsWith(p + '__')).sort((a, b) => b[0].length - a[0].length)[0]; return hit && hit[1]; };

/* ---------- HTML ---------- */
const htmlFiles = walk('.', '.html');
const pages = {};
for (const f of htmlFiles) {
  const src = read(f);
  const body = src.replace(/<script[\s\S]*?<\/script>/g, '').replace(/<pre[\s\S]*?<\/pre>/g, '');
  if (/<style[\s>]/i.test(body)) issues.push({ file: f, kind: '<style> 标签', detail: '页面内不得写样式' });
  for (const m of body.matchAll(/\sstyle\s*=\s*"([^"]*)"/g)) issues.push({ file: f, kind: 'style 属性', detail: m[1] });
  // 链接、id、for、aria-*、data-* 的值不是样式（如 href="#feed" 会被误判成颜色），先剔除再扫颜色
  const styleScan = body.replace(/\s(?:href|id|for|aria-[\w-]+|data-[\w-]+)\s*=\s*"[^"]*"/g, '');
  for (const m of styleScan.matchAll(COLOR_RE)) issues.push({ file: f, kind: '颜色字面量', detail: m[0] });
  const classes = new Set();
  for (const m of body.matchAll(/\sclass\s*=\s*"([^"]*)"/g)) m[1].split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
  const unknown = [...classes].filter(c => !registered.has(c));
  unknown.forEach(c => issues.push({ file: f, kind: '未登记 class', detail: c }));
  const tokens = new Set();
  const expand = t => { if (tokens.has(t)) return; tokens.add(t); tokenDeps(t).forEach(expand); };
  classes.forEach(c => (classRules.get(c) || []).forEach(expand));
  [...src.matchAll(/var\((--bw-[\w-]+)/g)].forEach(m => { if (!tokenDefs.has(m[1]) && !localVars.has(m[1])) issues.push({ file: f, kind: '未定义 Token', detail: m[1] }); else expand(m[1]); });
  const components = [...new Set([...classes].map(componentOf).filter(Boolean))].sort();
  pages[f] = {
    principles: [...new Set([...src.matchAll(/data-principle="([^"]+)"/g)].flatMap(m => m[1].split(/\s+/)))].sort(),
    scenes: [...new Set([...src.matchAll(/data-scene="([^"]+)"/g)].flatMap(m => m[1].split(/\s+/)))].sort(),
    components, tokenCount: [...tokens].filter(t => tokenDefs.has(t)).length,
    tokens: [...tokens].filter(t => tokenDefs.has(t)).sort(),
    unregistered: unknown.length,
  };
}

/* ---------- JS ---------- */
for (const f of walk('scripts', '.js')) for (const m of stripComments(read(f)).matchAll(COLOR_RE)) issues.push({ file: f, kind: '颜色字面量', detail: m[0] });

/* ---------- 输出 ---------- */
const byFile = {};
issues.forEach(x => { (byFile[x.file] ||= []).push(x); });
const report = {
  generatedAt: new Date().toISOString(),
  tokenTotal: tokenDefs.size, registeredClasses: registered.size,
  issueCount: issues.length, issues, pages,
};
mkdirSync(join(ROOT, 'audit'), { recursive: true });
writeFileSync(join(ROOT, 'audit/report.json'), JSON.stringify(report, null, 2));
writeFileSync(join(ROOT, 'audit/report.js'), 'window.BW_AUDIT = ' + JSON.stringify(report) + ';\n');

console.log(`Token ${tokenDefs.size} 个 · 已登记 class ${registered.size} 个 · HTML ${htmlFiles.length} 个`);
for (const [f, p] of Object.entries(pages)) console.log(`  ${f.padEnd(26)} 组件 ${String(p.components.length).padStart(2)} · Token ${String(p.tokenCount).padStart(3)} · 未登记 ${p.unregistered}`);
if (!issues.length) console.log('\n✓ 未发现未登记样式'); else {
  console.log(`\n✗ ${issues.length} 个问题`);
  for (const [f, xs] of Object.entries(byFile)) { console.log(`  ${f}`); xs.slice(0, 40).forEach(x => console.log(`    - ${x.kind}: ${x.detail}`)); }
}
process.exitCode = issues.length ? 1 : 0;
