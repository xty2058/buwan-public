# -*- coding: utf-8 -*-
"""Real-model validation for P1 features that were only tested with fakes (2026-11-18).

Covers: 1.1 time awareness, 1.2 promise mentions, 1.12 scene ledger (real
extraction), 1.9 「Ta 心中的我」, and calibration data for the 1.4 correction
similarity threshold.

Safety: a fresh temporary database; production data and D1 are untouched. The
embedding call counter is redirected to a temp file. Every model/embedding call
is counted and the run stops hard at --cap. No automatic retries.

Usage:
  python3 -B scripts/live_validation.py --runtime <main runtime dir> --out <dir> [--cap 60]
"""
import argparse
import json
import re
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.api import Application  # noqa: E402
from backend.config import Config  # noqa: E402
from backend.embedding import EmbeddingProvider, cosine  # noqa: E402
from backend.model import CompatibleProvider, ModelSettings  # noqa: E402
from backend.service import uid  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--runtime', required=True)
ap.add_argument('--out', required=True)
ap.add_argument('--cap', type=int, default=60)
ap.add_argument('--only', default='time,promise,scene,portrait,threshold')
args = ap.parse_args()
RUNTIME, OUT = Path(args.runtime), Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
ONLY = set(args.only.split(','))

# ---- call accounting -------------------------------------------------------
LEDGER = []


class CapReached(Exception):
    pass


def counted(kind, fn):
    def wrapper(*a, **k):
        if len(LEDGER) >= args.cap:
            raise CapReached()
        LEDGER.append({'kind': kind, 't': time.time()})
        return fn(*a, **k)
    return wrapper


_stream = CompatibleProvider.stream
CompatibleProvider.stream = lambda self, m, c: counted('model:' + self.settings.model, _stream)(self, m, c)
_embed = EmbeddingProvider.embed
EmbeddingProvider.embed = lambda self, t: counted('embedding', _embed)(self, t)

# ---- application on a temp DB with production model settings ---------------
tmp = Path(tempfile.mkdtemp(prefix='weiwan-live-'))
reply = ModelSettings(**json.loads((RUNTIME / 'reply-config.json').read_text(encoding='utf-8')))
reply.validate()
memory_settings = ModelSettings(**json.loads((RUNTIME / 'memory-config.json').read_text(encoding='utf-8')))
memory_settings.validate()
emb_cfg = json.loads((RUNTIME / 'embedding-config.json').read_text(encoding='utf-8'))
emb_cfg['budget_db'] = str(tmp / 'embedding-budget.sqlite3')
cfg = Config(db=tmp / 'live.sqlite3', log_dir=tmp / 'logs', origin='http://127.0.0.1:8899', host='127.0.0.1', port=8899)
extractor = CompatibleProvider(replace(memory_settings, max_output_tokens=2048))
embedding = EmbeddingProvider(emb_cfg)
app = Application(cfg, provider=CompatibleProvider(reply), memory_provider=extractor, embedding_provider=embedding)
app.memory.retrieval_policy = json.loads((RUNTIME / 'retrieval-policy.json').read_text(encoding='utf-8'))
mem = app.memory
H = {'Host': '127.0.0.1:8899', 'Origin': 'http://127.0.0.1:8899', 'Content-Type': 'application/json'}
RESULTS = {'config': {'reply_model': reply.model, 'prompt_version': reply.prompt_version, 'memory_model': memory_settings.model,
                      'embedding_model': emb_cfg['model'], 'retrieval_policy': app.memory.retrieval_policy}}


def call(method, path, body=None, headers=H):
    r = app.handle(method, path, headers, json.dumps(body or {}).encode())
    return r, (json.loads(r.body) if r.stream is None else None)


def new_user():
    name = 'live_' + uid()[:10]
    r, _ = call('POST', '/api/auth/register', {'username': name, 'password': 'Live-validation-2026', 'nickname': '验证'})
    cookie = [v for k, v in r.headers.items() if k.lower() == 'set-cookie'][0].split(';')[0]
    _, s = call('GET', '/api/auth/session', headers=dict(H, Cookie=cookie))
    auth = dict(H, Cookie=cookie, **{'X-CSRF-Token': s['csrf_token']})
    _, conv = call('POST', '/api/conversations', {'character_id': 'linyu'}, auth)
    with app.db.connect() as c:
        owner = c.execute('SELECT user_id FROM conversations WHERE id=?', (conv['id'],)).fetchone()[0]
    return auth, conv['id'], owner


def backdated(cid, pairs, seconds_ago):
    """Insert user/assistant exchanges `seconds_ago` in the past. Returns the user message ids."""
    ids = []
    with app.db.connect() as c:
        ordinal = c.execute('SELECT max(ordinal) FROM messages WHERE conversation_id=?', (cid,)).fetchone()[0]
        t = int(time.time()) - seconds_ago
        c.execute('UPDATE messages SET created_at=?,updated_at=? WHERE conversation_id=?', (t - 60, t - 60, cid))
        for user_text, assistant_text in pairs:
            u, a = uid(), uid()
            ordinal += 1
            c.execute("INSERT INTO messages(id,conversation_id,ordinal,role,content,status,created_at,updated_at) VALUES(?,?,?,'user',?,'saved',?,?)", (u, cid, ordinal, user_text, t, t))
            ordinal += 1
            c.execute("INSERT INTO messages(id,conversation_id,ordinal,role,content,status,reply_to_id,created_at,updated_at) VALUES(?,?,?,'assistant',?,'completed',?,?,?)", (a, cid, ordinal, assistant_text, u, t + 5, t + 5))
            ids.append((u, a))
            t += 10
    return ids


def say(auth, cid, text):
    _, m = call('POST', f'/api/conversations/{cid}/messages', {'content': text, 'client_request_id': 'message_live_%d' % time.time_ns()}, auth)
    r, g = call('POST', f'/api/conversations/{cid}/generations', {'kind': 'reply', 'user_message_id': m['id'], 'client_request_id': 'generation_live_%d' % time.time_ns()}, auth)
    if r.status != 202:
        return {'error': g}
    rid = g['request']['id']
    owner = None
    with app.db.connect() as c:
        owner = c.execute('SELECT user_id FROM conversations WHERE id=?', (cid,)).fetchone()[0]
    deadline = time.time() + 90
    while time.time() < deadline:
        snap = app.chat.snapshot(owner, cid, rid)
        if snap['request']['status'] in ('adopted', 'failed', 'stopped'):
            break
        time.sleep(.3)
    with app.db.connect() as c:
        system = None
        offered = [r[0] for r in c.execute('SELECT memory_id FROM promise_offers WHERE generation_id=?', (rid,))]
    return {'status': snap['request']['status'], 'error': snap['request'].get('error_code'),
            'reply': (snap.get('message') or {}).get('content'), 'offered_promises': len(offered)}


def add_memory(cid, owner, source_id, kind, content, vector=None, topic=None):
    mid = uid()
    with app.db.connect() as c:
        c.execute("""INSERT INTO memories(id,conversation_id,kind,fact_scope,content,status,primary_source_id,confirmed_by,confirmed_at,created_at,updated_at,topic_key,index_status)
                     VALUES(?,?,?,'real_user',?,'confirmed',?,?,?,?,?,?,?)""",
                  (mid, cid, kind, content, source_id, owner, int(time.time()), int(time.time()), int(time.time()), topic or content[:10], 'ready' if vector else 'unconfigured'))
        c.execute('INSERT INTO memory_sources VALUES(?,?,?)', (mid, cid, source_id))
        c.execute('INSERT INTO memory_evidence VALUES(?,?,?)', (mid, source_id, content))
        if vector:
            c.execute('INSERT INTO memory_vectors VALUES(?,?,?,?,?)', (mid, 1, embedding.model, embedding.dimensions, json.dumps(vector)))
    return mid


def last_context(cid):
    """The system prompt actually sent for the latest generation is not stored; reconstruct key facts instead."""
    return None


def save():
    RESULTS['ledger'] = {'calls': len(LEDGER), 'by_kind': {k: sum(1 for x in LEDGER if x['kind'] == k) for k in {x['kind'] for x in LEDGER}}}
    (OUT / 'results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding='utf-8')


CLOCK = re.compile(r'\d{1,2}\s*[:：点]\s*\d{0,2}|[零一二两三四五六七八九十]{1,3}点')
REUNION = re.compile(r'好久|几天|这几天|前几天|有段时间|隔了|又来了|回来了|终于来|好一阵')

try:
    # Background extraction off during chat scenarios: each adopted reply would otherwise cost an extra call.
    mem.extractor = None

    if 'time' in ONLY:
        prior = [('今天店里人多吗', '还行，下午来了几个学生，翻完书又走了。')]
        cases = [('T1_gap_3days_return', 3 * 86400, '我来了'),
                 ('T2_gap_3min_continue', 180, '那你现在在忙什么'),
                 ('T3_gap_2days_unrelated', 2 * 86400, '最近有什么好看的书推荐吗')]
        RESULTS['time'] = []
        for name, gap, text in cases:
            for rep in range(3):
                auth, cid, _ = new_user()
                backdated(cid, prior, gap)
                out = say(auth, cid, text)
                out.update(case=name, repeat=rep + 1, mentions_clock=bool(CLOCK.search(out.get('reply') or '')),
                           reunion_tone=bool(REUNION.search(out.get('reply') or '')))
                RESULTS['time'].append(out);save()
                print(name, rep + 1, out.get('status'), (out.get('reply') or '')[:60], flush=True)

    if 'promise' in ONLY:
        promise = '下次一起把那本诗集读完'
        vector = embedding.embed([promise])[0][0]
        cases = [('P1_return_after_2days', '我来了'), ('P2_return_upset', '今天被老板骂了，好烦')]
        RESULTS['promise'] = []
        for name, text in cases:
            for rep in range(3):
                auth, cid, owner = new_user()
                (u, a), = backdated(cid, [('下次一起把那本诗集读完吧', '好，下次接着读，我把书签夹在第三首那儿。')], 2 * 86400)
                add_memory(cid, owner, u, 'promise', promise, vector, '诗集约定')
                out = say(auth, cid, text)
                out.update(case=name, repeat=rep + 1, mentions_promise=bool(re.search(r'诗集|诗|接着读|读完', out.get('reply') or '')))
                RESULTS['promise'].append(out);save()
                print(name, rep + 1, out.get('status'), out.get('offered_promises'), (out.get('reply') or '')[:60], flush=True)

    if 'scene' in ONLY:
        mem.extractor = extractor
        dialogues = [('S1_subway', [('我在地铁上，刚下班，好挤', '那先找个扶手站稳，到站再说。')]),
                     ('S2_riverside', [('我们去河边走走吧', '好，带上伞，河边风大。'), ('到河边了，这边人不多', '嗯，安静点好，沿着石阶慢慢走。')]),
                     ('S3_only_us', [('店里就我们俩吧？', '嗯，这个点没别人，就我们俩。')]),
                     ('S4_no_scene_memory', [('记住，我喜欢喝乌龙茶', '好，记住了。')]),
                     ('S5_overtime', [('我现在在公司加班，还得一个小时', '那别太晚，记得吃点东西。')])]
        RESULTS['scene'] = []
        for name, pairs in dialogues:
            auth, cid, owner = new_user()
            ids = backdated(cid, pairs, 120)
            reply_id = ids[-1][1]
            mem.schedule(owner, cid, reply_id)
            deadline = time.time() + 90
            while time.time() < deadline:
                with app.db.connect() as c:
                    job = c.execute('SELECT status,error_code FROM memory_jobs WHERE reply_id=?', (reply_id,)).fetchone()
                if job and job[0] != 'running':
                    break
                time.sleep(.3)
            with app.db.connect() as c:
                scene_rows = [json.loads(r[0]) for r in c.execute('SELECT state FROM scene_states WHERE conversation_id=?', (cid,))]
                memories = [dict(r) for r in c.execute('SELECT kind,content,status FROM memories WHERE conversation_id=?', (cid,))]
                from backend import scene as ledger
                current = ledger.read(c, cid, mem.blocked_context_ids(c, cid))
            item = {'case': name, 'job': list(job) if job else None, 'scene_state': current, 'scene_versions': len(scene_rows), 'memories': memories}
            if name == 'S2_riverside':
                mem.extractor = None
                item['followup'] = say(auth, cid, '这边风景怎么样')
                mem.extractor = extractor
            RESULTS['scene'].append(item);save()
            print(name, item['job'], current, memories, flush=True)
        mem.extractor = None

    if 'portrait' in ONLY:
        auth, cid, owner = new_user()
        facts = [('user_fact', '我喜欢乌龙茶'), ('user_fact', '我每天下午都要喝茶'), ('user_fact', '我是一名护士'),
                 ('boundary', '我不喜欢别人叫我宝贝'), ('promise', '下次一起把那本诗集读完'), ('user_fact', '我养了一只叫团子的猫')]
        ids = backdated(cid, [(f[1], '好，我记下了。') for f in facts], 86400)
        for (kind, content), (u, _) in zip(facts, ids):
            add_memory(cid, owner, u, kind, content)
        RESULTS['portrait'] = []
        for rep in range(3):
            r, d = call('POST', f'/api/conversations/{cid}/portrait', {}, auth)
            RESULTS['portrait'].append({'repeat': rep + 1, 'status': r.status, 'result': d});save()
            print('portrait', rep + 1, r.status, json.dumps(d, ensure_ascii=False)[:300], flush=True)

    if 'threshold' in ONLY:
        related = [('我养了一只猫', '我家猫叫团子'), ('我喜欢乌龙茶', '我每天下午都要喝一杯乌龙'), ('我在杭州工作', '我每天在杭州挤地铁上班'),
                   ('我是一名护士', '我上夜班照顾病人'), ('我喜欢跑步', '我每周末去江边跑步'), ('我住在公司附近', '我走路十分钟就到公司'),
                   ('我最喜欢的作家是村上春树', '我最近在重读挪威的森林'), ('我不吃辣', '我吃火锅只点清汤锅')]
        unrelated = [('我养了一只猫', '我喜欢乌龙茶'), ('我喜欢乌龙茶', '我喜欢跑步'), ('我在杭州工作', '我养了一只猫'),
                     ('我是一名护士', '我最喜欢的作家是村上春树'), ('我喜欢跑步', '我不吃辣')]
        hard = [('我喜欢乌龙茶', '我不喜欢咖啡'), ('我养了一只猫', '我对狗毛过敏'), ('我在杭州工作', '我老家在苏州'),
                ('我是一名护士', '我妈妈是医生'), ('我喜欢跑步', '我膝盖不太好')]
        texts = sorted({t for pairs in (related, unrelated, hard) for p in pairs for t in p})
        vectors = {}
        for i in range(0, len(texts), 10):
            batch = texts[i:i + 10]
            for t, v in zip(batch, embedding.embed(batch)[0]):
                vectors[t] = v
        def scores(pairs):
            return [{'cause': a, 'other': b, 'cosine': round(cosine(vectors[a], vectors[b]), 4)} for a, b in pairs]
        RESULTS['threshold'] = {'related_should_suppress': scores(related), 'unrelated': scores(unrelated), 'hard_negative_same_domain': scores(hard)}
        save()
        for k, v in RESULTS['threshold'].items():
            print(k, [x['cosine'] for x in v], flush=True)
except CapReached:
    RESULTS['stopped'] = 'cap_reached'
    print('达到调用上限，已停止', flush=True)
finally:
    save()
    print('calls:', RESULTS['ledger'], flush=True)
