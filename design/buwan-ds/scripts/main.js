/* =====================================================================
   不晚 · 共享交互脚本（规范站 + 核心页面）
   只操作状态类（.is-*）与 ARIA 属性，不写任何样式字面量。
   全局 API：window.bw.toast() / bw.openModal() / bw.closeModal()
   ===================================================================== */
(function () {
  'use strict';
  const root = document.documentElement;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const ICONS = (document.querySelector('meta[name="bw-icons"]') || {}).content || 'assets/icons.svg';
  const icon = (id, cls = 'bw-icon') => `<svg class="${cls}" aria-hidden="true"><use href="${ICONS}#${id}"/></svg>`;

  /* ---------- 主题：深色为品牌默认，用户选择会被记住 ---------- */
  function applyTheme(t) {
    if (t === 'light') root.setAttribute('data-theme', 'light'); else root.removeAttribute('data-theme');
    $$('[data-bw-theme-toggle]').forEach(btn => {
      btn.setAttribute('aria-pressed', String(t === 'light'));
      btn.setAttribute('aria-label', t === 'light' ? '切换到深色' : '切换到浅色');
      const use = btn.querySelector('use');
      if (use) use.setAttribute('href', `${ICONS}#${t === 'light' ? 'moon' : 'sun'}`);
    });
  }
  let theme = null;
  try { theme = localStorage.getItem('bw-theme'); } catch (e) {}
  applyTheme(theme === 'light' ? 'light' : 'dark');
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-bw-theme-toggle]');
    if (!btn) return;
    const next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    applyTheme(next);
    try { localStorage.setItem('bw-theme', next); } catch (err) {}
    // 同步给内嵌的页面预览
    $$('iframe').forEach(f => { try { f.contentWindow.bw && f.contentWindow.bw.setTheme(next); } catch (err) {} });
  });

  /* ---------- Toast（系统声部） ---------- */
  let region = null;
  function toast({ text, type = 'info', icon: ic, action, timeout = 3600 } = {}) {
    if (!region) {
      region = document.createElement('div');
      region.className = 'bw-toast-region';
      region.setAttribute('aria-live', 'polite');
      document.body.appendChild(region);
    }
    const iconId = ic || { memory: 'bookmark', success: 'check', danger: 'alert-circle', info: 'info' }[type] || 'info';
    const el = document.createElement('div');
    el.className = `bw-toast bw-toast--${type}`;
    el.setAttribute('role', type === 'danger' ? 'alert' : 'status');
    el.innerHTML = `${icon(iconId)}<span class="bw-toast__text"></span>`;
    el.querySelector('.bw-toast__text').textContent = text;
    if (action) {
      const b = document.createElement('button');
      b.className = 'bw-btn bw-btn--ghost bw-btn--sm bw-toast__action';
      b.type = 'button';
      b.textContent = action.label;
      b.addEventListener('click', () => { action.onClick && action.onClick(); el.remove(); });
      el.appendChild(b);
    }
    region.appendChild(el);
    setTimeout(() => el.remove(), timeout);
    return el;
  }

  /* ---------- Modal / Sheet（原生 dialog） ---------- */
  let lastTrigger = null;
  function openModal(id, trigger) {
    const d = document.getElementById(id);
    if (!d || typeof d.showModal !== 'function') return;
    lastTrigger = trigger || document.activeElement;
    d.showModal();
  }
  function closeModal(d) {
    if (!d) return;
    d.close();
    if (lastTrigger && lastTrigger.focus) lastTrigger.focus();
  }
  document.addEventListener('click', e => {
    const opener = e.target.closest('[data-bw-open]');
    if (opener) { e.preventDefault(); openModal(opener.getAttribute('data-bw-open'), opener); return; }
    const closer = e.target.closest('[data-bw-close]');
    if (closer) { closeModal(closer.closest('dialog')); return; }
    if (e.target.tagName === 'DIALOG' && e.target.open) {
      const r = e.target.getBoundingClientRect();
      const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      if (!inside) closeModal(e.target);
    }
  });

  /* ---------- 单选组：关系选择 / 开场 / 分支（等权） @principle P4 ---------- */
  document.addEventListener('click', e => {
    const group = e.target.closest('[data-bw-single]');
    if (!group) return;
    const item = e.target.closest('[aria-pressed],[aria-current]');
    if (!item || !group.contains(item) || item.disabled) return;
    const attr = item.hasAttribute('aria-current') ? 'aria-current' : 'aria-pressed';
    $$(`[${attr}]`, group).forEach(x => x.setAttribute(attr, 'false'));
    item.setAttribute(attr, 'true');
    group.dispatchEvent(new CustomEvent('bw:select', { detail: { item }, bubbles: true }));
  });

  /* ---------- 规范站：侧边导航（移动端抽屉） ---------- */
  const sidebar = $('[data-bw-sidebar]');
  const scrim = $('[data-bw-scrim]');
  function setNav(open) {
    if (!sidebar) return;
    sidebar.classList.toggle('is-open', open);
    scrim && scrim.classList.toggle('is-open', open);
    $$('[data-bw-nav-toggle]').forEach(b => b.setAttribute('aria-expanded', String(open)));
    if (open) { const first = $('a', sidebar); first && first.focus(); }
  }
  document.addEventListener('click', e => {
    if (e.target.closest('[data-bw-nav-toggle]')) setNav(!sidebar.classList.contains('is-open'));
    else if (e.target.closest('[data-bw-scrim]')) setNav(false);
    else if (sidebar && e.target.closest('[data-bw-sidebar] a')) setNav(false);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && sidebar && sidebar.classList.contains('is-open')) setNav(false); });

  /* 当前章节高亮 */
  const spyLinks = $$('.bw-sidenav__link[href^="#"]');
  if (spyLinks.length && 'IntersectionObserver' in window) {
    const map = new Map(spyLinks.map(a => [a.getAttribute('href').slice(1), a]));
    const io = new IntersectionObserver(entries => {
      entries.forEach(en => {
        if (!en.isIntersecting) return;
        spyLinks.forEach(a => a.removeAttribute('aria-current'));
        const a = map.get(en.target.id);
        a && a.setAttribute('aria-current', 'location');
      });
    }, { rootMargin: '-20% 0px -70% 0px' });
    map.forEach((_, id) => { const s = document.getElementById(id); s && io.observe(s); });
  }

  /* ---------- 代码片段复制 ---------- */
  document.addEventListener('click', async e => {
    const btn = e.target.closest('[data-bw-copy]');
    if (!btn) return;
    const pre = btn.closest('.bw-code').querySelector('pre');
    try { await navigator.clipboard.writeText(pre.innerText); toast({ text: '已复制代码', type: 'success' }); }
    catch (err) { toast({ text: '复制失败，请手动选择文字', type: 'danger' }); }
  });

  /* ---------- 演示：加载态 ---------- */
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-bw-demo-loading]');
    if (!btn) return;
    btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true');
    setTimeout(() => { btn.classList.remove('is-loading'); btn.removeAttribute('aria-busy'); }, 1600);
  });
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-bw-demo-toast]');
    if (!btn) return;
    toast({ text: btn.getAttribute('data-bw-demo-toast'), type: btn.getAttribute('data-type') || 'info' });
  });

  /* ---------- Composer 自适应高度 ---------- */
  document.addEventListener('input', e => {
    const t = e.target;
    if (!t.matches || !t.matches('textarea.bw-composer__input')) return;
    t.style.height = 'auto';
    t.style.height = t.scrollHeight + 'px';
  });

  /* ---------- 色板与 Token 表：直接读取 tokens.css，保证规范与实现一致 ---------- */
  const cssVal = (name, el = root) => getComputedStyle(el).getPropertyValue(name).trim();
  $$('[data-bw-swatch]').forEach(sw => {
    const name = sw.getAttribute('data-bw-swatch');
    const chip = $('.bw-swatch__chip', sw);
    if (chip) chip.style.background = `var(${name})`;
    const val = $('[data-bw-swatch-value]', sw);
    if (val) val.textContent = cssVal(name);
  });

  const PRINCIPLE = [
    [/^--bw-color-memory|^--bw-gradient-memory|^--bw-glow-memory|^--bw-ember/, 'P3 记得对'],
    [/^--bw-color-user/, 'P4 由你决定'],
    [/^--bw-color-accent|^--bw-gradient-accent|^--bw-glow-accent|^--bw-color-on-accent|^--bw-rose|^--bw-apricot/, 'P1 同一个人'],
    [/^--bw-color-(success|warning|danger|info)/, 'P6 规则先行'],
    [/^--bw-glass|^--bw-blur|^--bw-aurora|^--bw-texture|^--bw-grain|^--bw-scrim/, 'P0 角色在前'],
    [/^--bw-portrait/, 'P0 角色在前 · S1'],
    [/^--bw-dur|^--bw-ease|^--bw-press|^--bw-lift/, 'P2 稳'],
    [/^--bw-text-hero/, 'P6 规则先行 · S5'],
    [/^--bw-size-tap|^--bw-focus|^--bw-color-focus/, '可访问性'],
  ];
  const CATEGORY = [
    ['原始色板', /^--bw-(apricot|rose|plum|ember|night|dawn|ink)-/],
    ['语义色', /^--bw-(color|gradient)-/],
    ['材质与环境光', /^--bw-(glass|blur|scrim|aurora|texture|grain|glow-orb|portrait)/],
    ['阴影与发光', /^--bw-(shadow|glow)-/],
    ['字体', /^--bw-(font|text|leading|weight|tracking)-/],
    ['空间', /^--bw-space-/],
    ['形状', /^--bw-(radius|border)-/],
    ['尺寸', /^--bw-(size|icon|measure)/],
    ['布局', /^--bw-(app|panel|modal|sidebar|content|header|device)-/],
    ['动效', /^--bw-(dur|ease|press|lift)/],
    ['其他', /.*/],
  ];
  function readTokens() {
    const dark = new Map(), light = new Map();
    for (const sheet of Array.from(document.styleSheets)) {
      if (!sheet.href || !/tokens\.css/.test(sheet.href)) continue;
      for (const rule of Array.from(sheet.cssRules)) {
        if (!rule.style) continue;
        const target = rule.selectorText === ':root' ? dark : /data-theme="light"/.test(rule.selectorText) ? light : null;
        if (!target) continue;
        for (let i = 0; i < rule.style.length; i++) {
          const p = rule.style[i];
          if (p.startsWith('--bw-')) target.set(p, rule.style.getPropertyValue(p).trim());
        }
      }
    }
    return { dark, light };
  }
  const tables = $$('[data-bw-token-table]');
  if (tables.length) {
    const { dark, light } = readTokens();
    const isColor = n => /^--bw-(color|gradient|glass|shadow|glow|aurora|portrait|apricot|rose|plum|ember|night|dawn|ink|scrim)/.test(n);
    tables.forEach(host => {
      const filter = host.getAttribute('data-bw-token-table');
      const groups = new Map();
      dark.forEach((v, n) => {
        const cat = CATEGORY.find(([, re]) => re.test(n))[0];
        if (filter && filter !== 'all' && filter !== cat) return;
        if (!groups.has(cat)) groups.set(cat, []);
        groups.get(cat).push(n);
      });
      host.innerHTML = Array.from(groups).map(([cat, names]) => `
        <div class="bw-table-wrap"><table class="bw-table bw-table--compact">
          <caption>${cat} <span class="bw-fine">· ${names.length} 个</span></caption>
          <thead><tr><th scope="col">Token</th><th scope="col">深色（默认）</th><th scope="col">浅色</th><th scope="col">预览</th><th scope="col">原则</th></tr></thead>
          <tbody>${names.map(n => {
            const pr = (PRINCIPLE.find(([re]) => re.test(n)) || [, '—'])[1];
            return `<tr id="token${n.slice(1)}"><td><code class="bw-code-inline">${n}</code></td>
              <td class="bw-token-value">${dark.get(n)}</td>
              <td class="bw-token-value">${light.has(n) ? light.get(n) : '同深色'}</td>
              <td>${isColor(n) ? `<span class="bw-token-preview" data-token="${n}"></span>` : ''}</td>
              <td class="bw-fine">${pr}</td></tr>`;
          }).join('')}</tbody></table></div>`).join('');
      $$('[data-token]', host).forEach(p => { p.style.background = `var(${p.getAttribute('data-token')})`; });
      const count = $('[data-bw-token-count]');
      if (count) count.textContent = String(dark.size);
    });
  }

  window.bw = Object.assign(window.bw || {}, { toast, openModal, closeModal: id => closeModal(document.getElementById(id)), setTheme: applyTheme, icon });
})();
