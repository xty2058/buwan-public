/* =====================================================================
   不晚 · foundations.html 专用脚本
   - 按 data 属性把 Token 映射到可视化元素（宽度、圆角、阴影、字号…）
   - 浅色色板：克隆深色色板到 data-theme="light" 容器
   - 色板取值与对比度：直接解析 tokens.css 规则（不依赖当前主题）
   - 动效演示
   不写任何颜色字面量；所有样式值都是 var(--bw-*)。
   ===================================================================== */
(function () {
  'use strict';
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

  /* ---------- 1. 浅色色板：克隆 ---------- */
  $$('[data-bw-clone-from]').forEach(host => {
    const src = document.getElementById(host.getAttribute('data-bw-clone-from'));
    if (!src) return;
    Array.from(src.children).forEach(ch => {
      const c = ch.cloneNode(true);
      c.removeAttribute('id');
      $$('[id]', c).forEach(x => x.removeAttribute('id'));
      $$('.bw-swatch__name', c).forEach(n => n.classList.add('bw-muted'));
      host.appendChild(c);
    });
  });

  /* ---------- 2. Token → 可视化属性 ---------- */
  const MAP = {
    'data-bw-width': 'width',
    'data-bw-radius': 'borderRadius',
    'data-bw-shadow': 'boxShadow',
    'data-bw-font-size': 'fontSize',
    'data-bw-line-height': 'lineHeight',
    'data-bw-font-weight': 'fontWeight',
    'data-bw-letter-spacing': 'letterSpacing',
    'data-bw-font-family': 'fontFamily',
    'data-bw-bg': 'background',
    'data-bw-border-width': 'borderWidth',
  };
  Object.entries(MAP).forEach(([attr, prop]) => {
    $$(`[${attr}]`).forEach(el => {
      const v = el.getAttribute(attr);
      el.style[prop] = v.startsWith('--') ? `var(${v})` : v;
    });
  });
  $$('[data-bw-swatch] .bw-swatch__chip').forEach(chip => {
    chip.style.background = `var(${chip.closest('[data-bw-swatch]').getAttribute('data-bw-swatch')})`;
  });
  // 色带：色块本身就是这个颜色（半透明色叠在色带容器的页面底色上，看到的就是真实效果）
  $$('.bw-palette__tile[data-bw-swatch]').forEach(t => { t.style.background = `var(${t.getAttribute('data-bw-swatch')})`; });

  /* ---------- 3. 解析 tokens.css ---------- */
  function readMaps() {
    const dark = new Map(), lightOnly = new Map();
    for (const sheet of Array.from(document.styleSheets)) {
      if (!sheet.href || !/tokens\.css/.test(sheet.href)) continue;
      let rules = [];
      try { rules = Array.from(sheet.cssRules); } catch (e) { continue; }
      for (const r of rules) {
        if (!r.style) continue;
        const target = r.selectorText === ':root' ? dark : /data-theme="light"/.test(r.selectorText || '') ? lightOnly : null;
        if (!target) continue;
        for (let i = 0; i < r.style.length; i++) {
          const p = r.style[i];
          if (p.startsWith('--bw-')) target.set(p, r.style.getPropertyValue(p).trim());
        }
      }
    }
    return { dark, light: new Map([...dark, ...lightOnly]) };
  }
  const maps = readMaps();
  function resolve(map, v, depth = 0) {
    return String(v || '').replace(/var\((--bw-[\w-]+)\)/g, (m, n) => (depth > 8 ? m : resolve(map, map.get(n), depth + 1)));
  }

  /* 色板取值：按所在主题显示解析后的真实值 */
  $$('[data-bw-swatch]').forEach(sw => {
    const name = sw.getAttribute('data-bw-swatch');
    const map = sw.closest('[data-theme="light"]') ? maps.light : maps.dark;
    const val = sw.querySelector('[data-bw-swatch-value]');
    if (!val) return;
    const full = resolve(map, map.get(name)).replace(/\s+/g, ' ').trim();
    // 渐变在色块里只写简称：线性写“色标 → 色标”，径向写层数；完整取值见 Token 表
    const stops = full.match(/#[0-9a-f]{3,8}\b|rgba?\([^)]*\)/gi) || [];
    if (/^linear-gradient/.test(full) && stops.length <= 3) val.textContent = stops.join(' → ');
    else if (/radial-gradient/.test(full)) val.textContent = `径向渐变 · ${(full.match(/radial-gradient/g) || []).length} 层`;
    else val.textContent = full;
    val.title = full;
  });

  /* ---------- 4. 对比度实测 ---------- */
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  function toChannels(str) {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = 'transparent';
    ctx.fillStyle = str;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  }
  const over = (fg, bg) => [0, 1, 2].map(i => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat(1);
  const lum = c => c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); })
    .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

  /* 色带文字深浅：取色块的实际显示色（渐变取各色标平均，半透明先叠到页面底色上），亮就用深字 */
  const COLOR_IN = /#[0-9a-f]{3,8}\b|rgba?\([^)]*\)/gi;
  $$('.bw-palette__tile[data-bw-swatch]').forEach(t => {
    const map = t.closest('[data-theme="light"]') ? maps.light : maps.dark;
    const bg = toChannels(resolve(map, map.get('--bw-color-bg')));
    bg[3] = 1;
    const raw = resolve(map, map.get(t.getAttribute('data-bw-swatch')));
    let stops = (raw.match(COLOR_IN) || []).map(c => over(toChannels(c), bg));
    if (!stops.length) return;
    // 文字贴在色块底部：从上往下的渐变只看最后一个色标
    if (/180deg/.test(raw)) stops = stops.slice(-1);
    const avg = [0, 1, 2].map(i => stops.reduce((a, c) => a + c[i], 0) / stops.length).concat(1);
    // 直接拿两种候选文字色（Token）比对比度，谁更清楚用谁
    const ink = toChannels(resolve(map, map.get('--bw-color-on-image-ink')));
    const paper = toChannels(resolve(map, map.get('--bw-color-on-image')));
    t.classList.toggle('is-on-light', ratio(avg, ink) > ratio(avg, paper));
  });
  function measure(map, fg, stack) {
    let bg = toChannels(resolve(map, map.get(stack[0])));
    bg[3] = 1;
    stack.slice(1).forEach(n => { bg = over(toChannels(resolve(map, map.get(n))), bg); });
    return ratio(over(toChannels(resolve(map, map.get(fg))), bg), bg);
  }
  const PAIRS = [
    ['正文', '--bw-color-text', ['--bw-color-bg']],
    ['次要文字', '--bw-color-text-2', ['--bw-color-bg']],
    ['提示 / 系统声部', '--bw-color-text-3', ['--bw-color-bg']],
    ['强调文字（链接）', '--bw-color-accent', ['--bw-color-bg']],
    ['次要文字 · 实底玻璃上', '--bw-color-text-2', ['--bw-color-bg', '--bw-glass-solid']],
    ['主按钮文字 · 渐变深端', '--bw-color-on-accent', ['--bw-rose-400']],
    ['主按钮文字 · 渐变浅端', '--bw-color-on-accent', ['--bw-apricot-300']],
    ['记忆文字 · 记忆底', '--bw-color-memory', ['--bw-color-bg', '--bw-color-memory-soft']],
    ['用户气泡文字', '--bw-color-user-text', ['--bw-color-user-bubble']],
    ['成功 · soft 底', '--bw-color-success', ['--bw-color-bg', '--bw-color-success-soft']],
    ['警告 · soft 底', '--bw-color-warning', ['--bw-color-bg', '--bw-color-warning-soft']],
    ['危险 · soft 底', '--bw-color-danger', ['--bw-color-bg', '--bw-color-danger-soft']],
    ['信息 · soft 底', '--bw-color-info', ['--bw-color-bg', '--bw-color-info-soft']],
  ];
  const cell = r => {
    const ok = r >= 4.5;
    return `<td class="bw-table__num">${r.toFixed(2)}:1</td><td><span class="bw-tag ${ok ? 'bw-tag--success' : 'bw-tag--danger'}">${ok ? '通过' : '不足'}</span></td>`;
  };
  $$('[data-bw-contrast]').forEach(tbody => {
    let pass = 0, total = 0;
    tbody.innerHTML = PAIRS.map(([label, fg, stack]) => {
      const d = measure(maps.dark, fg, stack), l = measure(maps.light, fg, stack);
      total += 2; pass += (d >= 4.5) + (l >= 4.5);
      return `<tr><td>${label}<br><code class="bw-fine">${fg}</code></td>${cell(d)}${cell(l)}</tr>`;
    }).join('');
    const sum = document.querySelector('[data-bw-contrast-summary]');
    if (sum) sum.textContent = `${pass} / ${total} 组达到 4.5:1`;
  });

  /* ---------- 5. 动效演示 ---------- */
  const bars = $$('[data-bw-dur]');
  bars.forEach(b => {
    b.style.transition = `width var(${b.getAttribute('data-bw-dur')}) var(--bw-ease-out)`;
    b.style.width = 'var(--bw-space-8)';
  });
  let wide = false;
  document.addEventListener('click', e => {
    if (e.target.closest('[data-bw-play-duration]')) {
      wide = !wide;
      bars.forEach(b => { b.style.width = wide ? '100%' : 'var(--bw-space-8)'; });
    }
    const replay = e.target.closest('[data-bw-replay]');
    if (replay) {
      const t = document.getElementById(replay.getAttribute('data-bw-replay'));
      const cls = replay.getAttribute('data-class');
      if (!t || !cls) return;
      t.classList.remove(cls);
      void t.offsetWidth;
      t.classList.add(cls);
    }
  });
})();
