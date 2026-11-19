/* S1 发现：一屏一个人，在首页原地开聊 @principle P0 P1 P4 P5 @scene S1 S2
   - 上下滑由 .bw-feed 的整屏吸附完成；对话区内部滚动不会带走整屏（overscroll-behavior: contain）
   - “换个开场”在同一个面板里轮换情境、他的第一句、替你写好的第一句
   - 底部顺序：开场面板 / 对话 → 名字与性格（固定）→ 输入框
   - 发送后不跳页：开场面板收起，情境 → 对话顶部系统行，他的第一句 → 第一条气泡
   - 旧消息上移进入对话区顶部的淡出遮罩；每个人的对话各自保留，滑走再滑回来还在
   - 输入框为空时只聚焦，不做任何提示语 */
(function () {
  'use strict';
  const OPENINGS = {
    linyu: [
      { scene: '雨天傍晚 · 临河旧书店', line: '「可以先坐一会儿。今天想找书，还是想找个安静的地方？」', say: '想找个安静的地方，雨停之前。' },
      { scene: '打烊前 · 二楼窗边', line: '「要关门了。……不过你要是还没看完，我可以晚一点。」', say: '那我再待十分钟，就十分钟。' },
    ],
    luye: [
      { scene: '排练刚结束 · 街角排练室', line: '「刚才那一段，你听见了？还差个结尾。想听我再弹一遍吗？」', say: '想。结尾我好像有个想法。' },
      { scene: '深夜 · 排练室天台', line: '「睡不着？巧了，我也是。要不要听一首还没起名字的歌。」', say: '你弹，我听。' },
    ],
    shenyan: [
      { scene: '街区开放日 · 窗边工作室', line: '「这张还没画完。你可以随便看看，感兴趣的话，我讲给你听。」', say: '我想听。这扇窗为什么开在这里？' },
      { scene: '加班后 · 工作室楼下', line: '「又是你。这么晚了，顺路的话，一起走一段？」', say: '好啊，正好顺路。' },
    ],
  };
  // 原型用的预置回复（真实产品由模型生成，人设锚点保证语气一致）
  const REPLIES = {
    linyu: [
      '那就靠窗那张吧，听得见雨，又不会被淋到。……要喝点热的吗？',
      '书架第三层有本旧诗集，下雨天翻正好。看不完也没关系，可以下次再来。',
      '我不催你。雨停了，我叫你。',
      '嗯，我在听。慢慢说。',
    ],
    luye: [
      '行，那你听好了。……怎么样？结尾你打算怎么接？',
      '哈，这个想法有点意思。嘴上我可不夸你，但我记下了。',
      '明天排练你要不要来？就坐门边那个位置。',
      '你说，我听着呢。',
    ],
    shenyan: [
      '下午三点以后，阳光会从这里斜着进来，正好落在那张桌子上。我想让坐在这儿的人，不用开灯。',
      '你问到点子上了。很多人只看外立面。',
      '这张我改了三稿。你要是有不同的想法，可以直接说。',
      '嗯。你接着说，我在想。',
    ],
  };
  const NAMES = { linyu: '林屿', luye: '陆野', shenyan: '沈砚' };
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const wait = ms => new Promise(r => setTimeout(r, reduce ? 0 : ms));

  function message(role, text) {
    const wrap = document.createElement('div');
    wrap.className = `bw-message bw-message--${role}`;
    const p = document.createElement('p');
    p.className = `bw-bubble bw-bubble--${role}`;
    p.textContent = text;
    wrap.appendChild(p);
    return wrap;
  }

  document.querySelectorAll('.bw-spotlight').forEach(card => {
    const id = card.getAttribute('data-character');
    const list = OPENINGS[id];
    const panel = card.querySelector('[data-panel]');
    const form = card.querySelector('[data-starter]');
    const scene = card.querySelector('[data-scene-name]');
    const line = card.querySelector('[data-line]');
    const say = card.querySelector('[data-say]');
    const sendBtn = form.querySelector('button[type="submit"]');
    let i = 0;
    let thread = null;
    let turn = 0;
    let busy = false;

    card.querySelector('[data-swap]').addEventListener('click', () => {
      i = (i + 1) % list.length;
      const apply = () => {
        scene.textContent = list[i].scene;
        line.textContent = list[i].line;
        say.value = list[i].say;
        panel.classList.remove('is-swapping');
        form.classList.remove('is-swapping');
      };
      if (reduce) { apply(); return; }
      panel.classList.add('is-swapping');
      form.classList.add('is-swapping');
      setTimeout(apply, 200);
    });

    // 直接贴底：新消息自带上浮动效（bw-rise），不依赖平滑滚动
    const toBottom = () => { thread.scrollTop = thread.scrollHeight; };

    function startChat() {
      thread = document.createElement('div');
      thread.className = 'bw-thread bw-thread--on-image bw-spotlight__thread';
      thread.setAttribute('role', 'log');
      thread.setAttribute('aria-live', 'polite');
      thread.setAttribute('aria-label', `和${NAMES[id]}的对话`);
      const sys = document.createElement('p');
      sys.className = 'bw-system-line';
      sys.textContent = list[i].scene;
      thread.appendChild(sys);
      thread.appendChild(message('character', list[i].line.replace(/^「|」$/g, '')));
      panel.parentNode.insertBefore(thread, panel);   // 对话出现在开场面板的位置，名字行和输入框不动
      card.classList.add('is-chatting');
      panel.classList.add('is-chatting');
      say.setAttribute('placeholder', '接着说…');
    }

    async function reply() {
      const typing = document.createElement('div');
      typing.className = 'bw-typing';
      typing.setAttribute('aria-label', '正在输入');
      typing.innerHTML = '<i></i><i></i><i></i>';
      thread.appendChild(typing);
      toBottom();
      await wait(900);
      typing.remove();
      const text = REPLIES[id][Math.min(turn, REPLIES[id].length - 1)];
      turn += 1;
      const msg = message('character', '');
      const bubble = msg.firstChild;
      thread.appendChild(msg);
      if (reduce) { bubble.textContent = text; toBottom(); return; }
      thread.setAttribute('aria-busy', 'true');
      bubble.classList.add('is-streaming');
      for (const ch of text) {
        bubble.textContent += ch;
        if (bubble.textContent.length % 6 === 0) toBottom();
        await wait(38);
      }
      bubble.classList.remove('is-streaming');
      thread.removeAttribute('aria-busy');
      toBottom();
    }

    form.addEventListener('submit', async e => {
      e.preventDefault();
      if (busy) return;
      const text = say.value.trim();
      if (!text) { say.focus(); return; }
      if (!thread) startChat();
      thread.appendChild(message('user', text));
      say.value = '';
      toBottom();
      busy = true;
      sendBtn.setAttribute('aria-disabled', 'true');
      await reply();
      busy = false;
      sendBtn.removeAttribute('aria-disabled');
    });
  });

  // 宽屏两侧：跟随当前角色换成同一张图的模糊版本
  const feed = document.querySelector('.bw-feed');
  const backdrop = document.querySelector('[data-backdrop]');
  if (feed && backdrop) {
    const cards = Array.from(document.querySelectorAll('.bw-spotlight'));
    let raf = 0;
    const sync = () => {
      raf = 0;
      const i = Math.round(feed.scrollTop / Math.max(feed.clientHeight, 1));
      const card = cards[Math.min(Math.max(i, 0), cards.length - 1)];
      const src = card.querySelector('.bw-spotlight__image').getAttribute('src');
      if (backdrop.getAttribute('src') !== src) backdrop.setAttribute('src', src);
    };
    feed.addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(sync); }, { passive: true });
    feed.addEventListener('scrollend', sync);
  }
})();
