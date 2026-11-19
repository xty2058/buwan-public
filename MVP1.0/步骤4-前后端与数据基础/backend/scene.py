# -*- coding: utf-8 -*-
"""Scene ledger (roadmap 1.12): in-story time, place and who is present.

Filled by the existing asynchronous memory extraction (no extra model call):
the extractor may return an optional "scene" object next to "memories". Every
value must be a verbatim fragment of a quote, and every quote a verbatim
fragment of a message in the extraction window, so the ledger can only repeat
what was actually said in the conversation; it never infers.

Each version stores the merged state plus, per field, the message ids that
support it. On read, each field takes the newest version whose supporting
messages are all still usable; a deleted or blocked message therefore removes
its field (falling back to an older value) instead of leaving a stale fact.
Versions are keyed by the reply they were extracted after, which later gives
story branches (roadmap 2.1) a ready snapshot.

Idea from 梨园 (https://github.com/weidu12123/Liyuan, no licence stated): a
per-turn scene state ledger against setting drift. Idea only; no code copied.
"""
import json

from .service import uid, now

FIELDS = ('time', 'place', 'present')
LABELS = {'time': '场景时间', 'place': '地点', 'present': '在场人物'}
MAX_VALUE = 30
MAX_PRESENT = 5
# A lone pronoun is ambiguous (the user's "我" vs the character's "我"), so it is not recorded as a person.
PRONOUNS = {'我', '你', '他', '她', '咱', '您'}

PROMPT = ('\n另可返回键"scene"，记录当前场景状态：{"time":"场景中的时间或null","place":"所在地点或null",'
          '"present":["在场人物"],"evidence":[{"message_id":"原ID","quote":"连续原文"}]}。'
          '只记录对话里明确说出的当前场景（如“我在地铁上”“店里就我们俩”），每个值必须是某条quote里的连续原文；'
          '没有明确说出的字段填null或空数组，不推断、不补全；没有任何场景信息时"scene":null。场景状态不是用户事实，不放进memories。')

GUIDANCE = ('场景状态只记录对话中明确出现过的场景时间、地点和在场人物：接着当前场景说话时与它保持一致，'
            '不擅自换地点、加人物；用户说场景变了就以用户为准。它不是用户事实，也不证明发生过其他事。')


def _clean(value):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= MAX_VALUE else None


def validate(scene, sources):
    """Return {field: (value, [message_ids])} for fields proven by verbatim quotes, else {}."""
    if not isinstance(scene, dict) or not isinstance(scene.get('evidence'), list):
        return {}
    usable = {s['id']: s for s in sources if s.get('status') in ('saved', 'completed')}
    quotes = []
    for e in scene['evidence'][:8]:
        if not isinstance(e, dict):
            continue
        s, quote = usable.get(e.get('message_id')), e.get('quote')
        if s and isinstance(quote, str) and len(quote) >= 2 and quote in s['content']:
            quotes.append((s['id'], quote))

    def proof(value):
        return sorted({mid for mid, q in quotes if value in q})

    found = {}
    for field in ('time', 'place'):
        value = _clean(scene.get(field))
        if value and proof(value):
            found[field] = (value, proof(value))
    present = scene.get('present')
    if isinstance(present, list):
        names, ids = [], set()
        for name in present[:MAX_PRESENT]:
            name = _clean(name)
            if name and name not in PRONOUNS and name not in names and proof(name):
                names.append(name);ids.update(proof(name))
        if names:
            found['present'] = (names, sorted(ids))
    return found


def record(c, cid, reply_id, scene, sources):
    """Store a new version merging proven fields over the previous one. Returns True if stored."""
    found = validate(scene, sources)
    if not found:
        return False
    last = c.execute('SELECT version,state FROM scene_states WHERE conversation_id=? ORDER BY version DESC LIMIT 1',
                     (cid,)).fetchone()
    state = json.loads(last['state']) if last else {}
    for field, (value, ids) in found.items():
        state[field] = {'value': value, 'sources': ids}
    version = (last['version'] if last else 0) + 1
    c.execute('INSERT INTO scene_states VALUES(?,?,?,?,?,?)',
              (uid(), cid, reply_id, version, json.dumps(state, ensure_ascii=False), now()))
    return True


def read(c, cid, blocked=()):
    """Current scene for the model: per field, the newest value whose sources are all still usable."""
    blocked = set(blocked)
    usable = lambda mid: mid not in blocked and c.execute(
        "SELECT 1 FROM messages WHERE id=? AND conversation_id=? AND status IN ('saved','completed')",
        (mid, cid)).fetchone() is not None
    result = {}
    for row in c.execute('SELECT state FROM scene_states WHERE conversation_id=? ORDER BY version DESC LIMIT 20', (cid,)):
        state = json.loads(row['state'])
        for field in FIELDS:
            if field in result or field not in state:
                continue
            if all(usable(mid) for mid in state[field]['sources']):
                result[field] = state[field]['value']
        if len(result) == len(FIELDS):
            break
    return {LABELS[f]: result[f] for f in FIELDS if f in result}
