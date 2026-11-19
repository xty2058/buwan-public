/* 不晚 · S4 control.html 演示交互
   只切换 .is-* 与 ARIA、插入已登记 class 的 DOM、调用 window.bw.toast。 @scene S4 @principle P4 P3 P5 */
(function () {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const thread = $('#thread');
  const icon = id => window.bw.icon(id, 'bw-icon bw-icon--sm');
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const scrollEnd = el => el.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });

  function message(role, text) {
    const m = document.createElement('div');
    m.className = `bw-message bw-message--${role}`;
    const b = document.createElement('div');
    b.className = `bw-bubble bw-bubble--${role}`;
    b.textContent = text;
    m.appendChild(b);
    thread.appendChild(m);
    scrollEnd(m);
    return m;
  }
  function systemLine(text) {
    const p = document.createElement('p');
    p.className = 'bw-system-line';
    p.innerHTML = `${icon('info')}<span></span>`;
    p.querySelector('span').textContent = text;
    thread.appendChild(p);
    return p;
  }
  async function characterReply(text) {
    const t = document.createElement('div');
    t.className = 'bw-typing';
    t.setAttribute('aria-label', '沈砚正在输入');
    t.innerHTML = '<i></i><i></i><i></i>';
    thread.appendChild(t);
    scrollEnd(t);
    await wait(900);
    t.remove();
    return message('character', text);
  }

  /* ---------- 邀约：等权选择，拒绝不惩罚 @principle P4 ---------- */
  let chosen = null;
  const REPLIES = {
    yes: { user: '好，周六见', sys: null, char: '那就说定了。周六上午我去楼下等你，记得带件外套，海边风大。' },
    no: { user: '这周先不了', sys: '你拒绝了，这不会影响你们的关系', char: '好。这周你先好好准备复试，看海的事不急，海一直在。' },
    later: { user: '换个时间吧', sys: null, char: '好，你定。哪天方便了告诉我就行。' },
  };
  $('#invite-choices').addEventListener('bw:select', async e => {
    if (chosen) {
      $$('#invite-choices .bw-btn').forEach(b => b.setAttribute('aria-pressed', String(b.getAttribute('data-reply') === chosen)));
      return;
    }
    const key = e.detail.item.getAttribute('data-reply');
    chosen = key;
    $$('#invite-choices .bw-btn').forEach(b => b.setAttribute('aria-disabled', 'true'));
    const r = REPLIES[key];
    message('user', r.user);
    if (r.sys) systemLine(r.sys);
    await characterReply(r.char);
    if (key === 'yes') window.bw.toast({ type: 'memory', text: '记下了一个约定：周六一起去看海' });
    $('#branch-main-name').textContent = `主线 · ${r.user}`;
  });
  $('#invite-choices').addEventListener('click', e => {
    const b = e.target.closest('.bw-btn');
    if (b && b.getAttribute('aria-disabled') === 'true' && chosen) {
      window.bw.toast({ type: 'info', text: '已经回应过了。想看另一种走向，可以从这里分岔' });
    }
  });

  /* ---------- 分岔：原线保留，可切回 @principle P4 P5 ---------- */
  $('#branch-confirm').addEventListener('click', async e => {
    const btn = e.currentTarget;
    const name = $('#branch-name').value.trim() || '新的线';
    btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true');
    await wait(700);
    btn.classList.remove('is-loading'); btn.removeAttribute('aria-busy');
    window.bw.closeModal('sheet-branch');
    $('#branch-alt-name').textContent = `支线 · ${name}`;
    if (!chosen) $('#branch-main-name').textContent = '主线 · 还没回应';
    const box = $('#branches');
    box.hidden = false;
    scrollEnd(box);
    window.bw.toast({ type: 'success', text: `已分岔为「${name}」，原来那条线还在` });
  });
  $('#branch-switcher').addEventListener('bw:select', e => {
    const name = $('.bw-branch__name', e.detail.item).textContent;
    window.bw.toast({ type: 'info', text: `已切到「${name}」` });
  });

  /* ---------- 关系阶段：只由用户推进，二次确认 @principle P4 ---------- */
  const steps = $$('#stage .bw-stage__step');
  const nextIndex = () => steps.findIndex(s => s.classList.contains('is-active')) + 1;
  function syncAdvance() {
    const i = nextIndex();
    const btn = $('#advance-open');
    if (i >= steps.length) { btn.textContent = '已经是恋人了'; btn.disabled = true; return; }
    btn.textContent = `推进到「${steps[i].textContent}」`;
    $('[data-next-stage]').textContent = steps[i].textContent;
  }
  $('#advance-confirm').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.classList.add('is-loading');
    await wait(600);
    btn.classList.remove('is-loading');
    const i = nextIndex();
    const cur = steps[i - 1];
    cur.classList.remove('is-active'); cur.classList.add('is-done'); cur.removeAttribute('aria-current');
    steps[i].classList.add('is-active'); steps[i].setAttribute('aria-current', 'step');
    window.bw.closeModal('modal-advance');
    window.bw.toast({ type: 'success', text: `关系已推进到「${steps[i].textContent}」，随时可以放慢` });
    syncAdvance();
  });
  $('#tempo').addEventListener('change', e => {
    window.bw.toast({ type: 'info', text: `节奏调整为「${e.target.value}」，从他的下一句开始生效` });
  });

  /* ---------- 记忆：纠正与删除 @principle P3 ---------- */
  let target = null;
  const textOf = li => $('[data-text]', li).textContent;
  const count = () => {
    const n = $$('#memory-list .bw-memory-item').length + 18;
    $('#memory-count').textContent = n; $('#memory-count-top').textContent = n;
  };
  document.addEventListener('click', e => {
    const opener = e.target.closest('[data-memory]');
    if (!opener) return;
    target = document.getElementById(opener.getAttribute('data-memory'));
    if (!target) return;
    const field = $('#correct-field');
    field.classList.remove('is-error');
    $('#correct-input').removeAttribute('aria-invalid');
    if (opener.getAttribute('data-bw-open') === 'sheet-correct') {
      $('#correct-from').textContent = textOf(target);
      $('#correct-input').value = target.id === 'mem-interview' && !target.classList.contains('is-corrected') ? '复试在周四' : textOf(target);
      setTimeout(() => { $('#correct-input').focus(); $('#correct-input').select(); }, 0);
    } else {
      $('#delete-text').textContent = textOf(target);
    }
  });

  $('#correct-form').addEventListener('submit', async e => {
    e.preventDefault();
    const input = $('#correct-input');
    const value = input.value.trim();
    const field = $('#correct-field');
    if (!value) { field.classList.add('is-error'); input.setAttribute('aria-invalid', 'true'); input.focus(); return; }
    const old = textOf(target);
    if (value === old) { window.bw.closeModal('sheet-correct'); return; }
    const btn = $('#correct-submit');
    btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true');
    target.classList.add('is-saving');
    await wait(700);
    btn.classList.remove('is-loading'); btn.removeAttribute('aria-busy');
    target.classList.remove('is-saving');
    target.classList.add('is-corrected');
    $('[data-old]', target).innerHTML = '<span class="bw-sr-only">原来记成：</span>';
    $('[data-old]', target).append(old);
    $('[data-text]', target).textContent = value;
    $('[data-meta]', target).textContent = '刚刚 你改过';
    $$('[aria-label^="纠正"],[aria-label^="删除"]', target).forEach(b => b.setAttribute('aria-label', `${b.getAttribute('aria-label').slice(0, 2)}：${value}`));
    window.bw.closeModal('sheet-correct');
    window.bw.toast({ type: 'memory', text: `已记住：${value}。之后按这个来` });
    if (target.id === 'mem-interview') {
      const chip = $('#chip-interview');
      $('#chip-interview-text').textContent = `想起了：${value}`;
      chip.setAttribute('aria-label', `他想起了：${value}。点击查看或纠正`);
      chip.classList.remove('is-new'); void chip.offsetWidth;
      chip.classList.add('is-new', 'is-corrected');
      await characterReply('记下了，是周四。周四早上 7:30，我叫你。');
    }
  });

  $('#delete-confirm').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.classList.add('is-loading');
    await wait(500);
    btn.classList.remove('is-loading');
    const text = textOf(target);
    target.remove();
    count();
    window.bw.closeModal('modal-delete');
    window.bw.toast({ type: 'info', text: `已删除。他不会再提起「${text}」` });
  });

  /* ---------- 输入框 ---------- */
  $('#composer').addEventListener('submit', async e => {
    e.preventDefault();
    const ta = $('#composer-input');
    const v = ta.value.trim();
    if (!v) return;
    message('user', v);
    ta.value = ''; ta.style.height = 'auto';
    await characterReply('嗯，我在听。');
  });
  $('#composer-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#composer').requestSubmit(); }
  });

  syncAdvance();
})();
