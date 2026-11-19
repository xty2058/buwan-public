/* =====================================================================
   S2 日常聊天 · 对话流演示（chat.html，return.html 复用）
   @principle P2 稳：先出“正在输入”，再流式出字；不伪造延迟以外的等待
   @principle P4 由你决定：选择组只记录用户的选择，系统行确认
   @principle P6 规则先行：出错回复标“本条不扣”，续写 / 重写一步完成
   只切换 .is-* 与 ARIA，插入的 DOM 全部由已登记 class 构成。
   ===================================================================== */
(function () {
  'use strict';
  const thread = document.getElementById('thread');
  const form = document.getElementById('composer');
  const input = document.getElementById('msg');
  if (!thread) return;

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ICONS = (document.querySelector('meta[name="bw-icons"]') || {}).content || '../assets/icons.svg';
  const REPLIES = [
    '嗯。说完了就去洗澡，别又坐着发呆到一点。',
    '我在。慢慢说，不急。',
    '……那就再讲一遍给我听，你讲得顺了，周四就不怕了。',
    '先喝点热的。桂花乌龙还有吗？',
    '知道了。我记着。',
  ];
  let replyIndex = 0;
  const nextReply = () => REPLIES[replyIndex++ % REPLIES.length];

  const scrollEnd = () => window.scrollTo({ top: document.body.scrollHeight, behavior: reduce ? 'auto' : 'smooth' });

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function appendUser(text) {
    const m = el('div', 'bw-message bw-message--user');
    m.appendChild(el('p', 'bw-bubble bw-bubble--user', text));
    thread.appendChild(m);
    scrollEnd();
  }

  function appendSystem(text, key) {
    let line = key ? thread.querySelector(`[data-bw-system="${key}"]`) : null;
    if (!line) {
      line = el('p', 'bw-system-line');
      if (key) line.setAttribute('data-bw-system', key);
      thread.appendChild(line);
    }
    line.textContent = text;
    scrollEnd();
  }

  /* 正在输入 → 流式出字。整段结束前 thread 标 aria-busy，读屏只在完成后播报一次。 */
  function stream(text, target) {
    return new Promise(resolve => {
      const typing = el('div', 'bw-typing');
      typing.setAttribute('role', 'status');
      typing.setAttribute('aria-label', '沈砚正在输入');
      typing.append(el('i'), el('i'), el('i'));
      thread.appendChild(typing);
      thread.setAttribute('aria-busy', 'true');
      scrollEnd();
      setTimeout(() => {
        typing.remove();
        let bubble = target;
        if (!bubble) {
          const m = el('div', 'bw-message bw-message--character');
          bubble = el('p', 'bw-bubble bw-bubble--character');
          m.appendChild(bubble);
          thread.appendChild(m);
        }
        const host = bubble.closest('.bw-bubble') || bubble;
        host.classList.add('is-streaming');
        const base = bubble.textContent;
        if (reduce) {
          bubble.textContent = base + text;
          done();
          return;
        }
        let i = 0;
        const t = setInterval(() => {
          i++;
          bubble.textContent = base + text.slice(0, i);
          if (i % 6 === 0) scrollEnd();
          if (i >= text.length) { clearInterval(t); done(); }
        }, 45);
        function done() {
          host.classList.remove('is-streaming');
          thread.setAttribute('aria-busy', 'false');
          scrollEnd();
          resolve(bubble);
        }
      }, reduce ? 0 : 900);
    });
  }

  /* 额度提示只在系统行减少，不进入聊天流 */
  const quota = document.querySelector('[data-bw-quota]');
  let left = quota ? parseInt(quota.textContent.replace(/\D+/g, ' ').trim().split(' ')[0], 10) || 0 : 0;
  function spend() {
    if (!quota) return;
    left = Math.max(0, left - 1);
    quota.textContent = `今天还剩 ${left} 条 · 没生成完不扣`;
  }

  /* 发送 */
  if (form && input) {
    form.addEventListener('submit', e => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      appendUser(text);
      input.value = '';
      input.style.height = 'auto';
      stream(nextReply()).then(spend);
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
  }

  /* 出错回复：续写 / 重写（都不计费） */
  thread.addEventListener('click', e => {
    const btn = e.target.closest('[data-bw-retry]');
    if (!btn) return;
    const bubble = btn.closest('.bw-bubble');
    const retry = btn.closest('.bw-retry');
    const textEl = bubble.querySelector('[data-bw-bubble-text]') || bubble;
    if (btn.getAttribute('data-bw-retry') === 'continue') {
      bubble.classList.remove('is-error');
      retry.remove();
      stream('早点睡，闹钟我替你记着。', textEl).then(() => appendSystem('已续写完整 · 这条没有扣额度'));
    } else {
      bubble.classList.remove('is-error');
      bubble.classList.add('is-invalidated');
      retry.replaceWith(el('p', 'bw-message__meta', '已重写 · 原回复不计费'));
      stream('那我就一直打，打到你接为止。前一晚别熬，十二点前睡。').then(() => appendSystem('重写完成 · 这条没有扣额度'));
    }
  });

  /* 关系选择：等权，选完由系统行确认，角色按用户的选择回应 */
  thread.addEventListener('bw:select', e => {
    const group = e.target.closest('[data-bw-choice]');
    if (!group) return;
    const label = e.detail.item.textContent.trim();
    appendSystem(`你选了「${label}」，他会按这个来。`, 'choice');
    const reply = /自己/.test(label) ? '好。那我就不打扰你，周四加油。' : '嗯。七点半，我打给你。';
    stream(reply);
  });

  /* 图标路径供插入 DOM 时使用（保留扩展位） */
  window.bwChat = { stream, appendUser, appendSystem, ICONS };
})();
