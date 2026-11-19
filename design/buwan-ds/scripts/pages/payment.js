/* 不晚 · S5 payment.html 演示交互
   只切换 .is-* 与 ARIA、改写文本、调用 window.bw.toast。 @scene S5 @principle P6 P4 */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const wait = ms => new Promise(r => setTimeout(r, ms));

  const PLANS = {
    wait: { action: '好，明天见' },
    pack: { action: '支付 ¥6', name: '购买加油包', amount: '¥6', cycle: '一次性，不续费', quota: '立即可用 150 条，不过期', next: '不会再扣', total: 150, label: '加油包剩余', unit: '/ 150 条 · 不过期', text: '加油包已生效 · 用完前一直有效' },
    month: { action: '订阅 ¥18/月', name: '订阅月卡', amount: '¥18', cycle: '每月自动续费', quota: '今天起每月 600 条', next: '12月17日', total: 600, label: '本月额度', unit: '/ 600 条 · 本月', text: '月卡已生效 · 下次扣费 12月17日，可随时退订' },
  };
  let current = 'wait';
  let paid = false;
  const action = $('#plan-action');

  $('#plans').addEventListener('change', e => {
    current = e.target.value;
    action.textContent = PLANS[current].action;
  });

  action.addEventListener('click', () => {
    if (paid) { location.href = 'chat.html'; return; }
    if (current === 'wait') {
      window.bw.toast({ type: 'success', text: '好的，明早 6:00 恢复 30 条。聊到的地方都在' });
      return;
    }
    const p = PLANS[current];
    $('#pay-name').textContent = p.name;
    $('#pay-amount').textContent = p.amount;
    $('#pay-cycle').textContent = p.cycle;
    $('#pay-quota').textContent = p.quota;
    $('#pay-next').textContent = p.next;
    window.bw.openModal('modal-pay', action);
  });

  async function pay(ok) {
    const btn = $('#pay-confirm');
    btn.classList.add('is-loading'); btn.setAttribute('aria-busy', 'true');
    await wait(1000);
    btn.classList.remove('is-loading'); btn.removeAttribute('aria-busy');
    window.bw.closeModal('modal-pay');
    if (!ok) {
      window.bw.toast({ type: 'danger', text: '支付没成功，没有扣钱。可以稍后再试' });
      return;
    }
    const p = PLANS[current];
    paid = true;
    $('#quota-label').textContent = p.label;
    $('#quota-num').textContent = p.total;
    $('#quota-unit').textContent = p.unit;
    const meter = $('#quota-meter');
    meter.max = p.total; meter.value = p.total;
    meter.classList.remove('is-low');
    $('#quota-text').textContent = p.text;
    action.textContent = '回去接着聊';
    document.querySelectorAll('#plans input').forEach(i => { i.disabled = true; });
    window.bw.toast({ type: 'success', text: current === 'month' ? '已订阅，今天起可用 600 条' : '已购买，150 条已到账' });
  }
  $('#pay-confirm').addEventListener('click', () => pay(true));
  $('#pay-fail').addEventListener('click', () => pay(false));
})();
