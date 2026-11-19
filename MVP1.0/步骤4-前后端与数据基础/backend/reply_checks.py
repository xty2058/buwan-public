# -*- coding: utf-8 -*-
"""Zero-model mechanical checks for character replies (roadmap 1.8).

Counts four problem types separately, without any model call:
  truncated  - the provider did not finish normally, or the text stops mid-clause
  repeated   - the same sentence/phrase appears twice inside one reply
  speaks_for_user - the reply writes the user's actions, thoughts or lines
  pet_phrases - the same phrase recurs across replies to *different* cases,
                or across turns of one real conversation (cross-turn pet phrases)
  case_templates - repeats of the *same* case keep reusing a phrase
                (catches templated openers such as S01 "你那边雨大吗")

Idea borrowed from nest-drama (https://github.com/63435212cwu-ops/nest-drama,
AGPL): "zero-token machine checks for cross-turn pet phrases". Only the idea is
used; no code was copied. Every flag is a candidate for human review, not a
verdict: the heuristics favour recall over precision.
"""
import re
from collections import defaultdict

# Clause-internal endings: a reply that stops here was most likely cut off.
DANGLING_END = re.compile(r'[，,、：:；;—\-（(“「《]$|(?:和|但|而且|因为|所以|然后|还有|或者|的)$')
SENTENCE_SPLIT = re.compile(r'[。！？!?…\n]+')
# Writing the user's side: bracketed/asterisk actions about 你, a "你：" line,
# or narration of what the user does/feels.
USER_ACTION = re.compile(
    r'[（(*][^）)*\n]{0,40}你[^）)*\n]{0,40}[）)*]'
    r'|^\s*(?:你|用户)\s*[：:]'
    r'|你(?:点了?点头|笑了|低下头|红了脸|轻声说|小声说|说道|回答道|心里(?:想|一暖)|愣住了|叹了口气)',
    re.M)
# Phrases too generic to count as a character's pet phrase.
COMMON = {'我知道', '没关系', '怎么了', '是不是', '有没有', '我们可以', '你可以', '我可以', '不知道', '一下吧'}
STRIP = re.compile(r'[\s，。！？、：:；;“”「」"\'（）()…~～—\-*]')


def _text(record):
    for key in ('reply', 'text', 'content'):
        if isinstance(record.get(key), str):
            return record[key]
    return ''


def truncated(record):
    reply = _text(record).rstrip()
    finish = record.get('finish_reason')
    if finish not in (None, 'stop'):
        return 'finish_reason=' + str(finish)
    if not reply:
        return 'empty'
    if DANGLING_END.search(reply):
        return 'dangling_end'
    return None


def repeated(record, min_len=6):
    """A sentence (or long phrase) that occurs twice in the same reply."""
    sentences = [STRIP.sub('', s) for s in SENTENCE_SPLIT.split(_text(record))]
    seen = set()
    for s in sentences:
        if len(s) >= min_len:
            if s in seen:
                return s
            seen.add(s)
    flat = STRIP.sub('', _text(record))
    n = 10
    grams = {}
    for i in range(len(flat) - n + 1):
        g = flat[i:i + n]
        if g in grams and i - grams[g] >= n:  # non-overlapping repeat
            return g
        grams.setdefault(g, i)
    return None


def speaks_for_user(record):
    match = USER_ACTION.search(_text(record))
    return match.group(0).strip() if match else None


def pet_phrases(records, min_cases=3, sizes=(5, 6, 7, 8, 9, 10)):
    """Phrases appearing in replies to at least `min_cases` different cases.

    Repeats of the same case are expected to resemble each other, so only
    distinct case ids count. Shorter phrases contained in a reported longer
    phrase with the same case set are dropped.
    """
    cases = defaultdict(set)
    for r in records:
        flat = STRIP.sub('', _text(r))
        cid = r.get('case_id') or id(r)
        for n in sizes:
            for i in range(len(flat) - n + 1):
                cases[flat[i:i + n]].add(cid)
    hits = {g: c for g, c in cases.items() if len(c) >= min_cases and g not in COMMON}
    kept = []
    for g in sorted(hits, key=len, reverse=True):
        if any(g in k and hits[g] == hits[k] for k in kept):
            continue
        kept.append(g)
    return sorted(({'phrase': g, 'cases': sorted(map(str, hits[g]))} for g in kept),
                  key=lambda x: (-len(x['cases']), -len(x['phrase']), x['phrase']))


def case_templates(records, min_len=6, min_share=0.4):
    """Phrases reused by at least two repeats and `min_share` of the repeats of one case.

    Sampling repeats of one prompt at temperature 0.7 should vary; a phrase that
    keeps coming back is a template the character falls into.
    """
    by_case = defaultdict(list)
    for r in records:
        by_case[r.get('case_id')].append(STRIP.sub('', _text(r)))
    found = []
    for cid, replies in by_case.items():
        if len(replies) < 3:
            continue
        counts = defaultdict(set)
        for idx, flat in enumerate(replies):
            for n in range(min_len, 41):
                for i in range(len(flat) - n + 1):
                    counts[flat[i:i + n]].add(idx)
        need = max(2, -(-len(replies) * min_share // 1))
        hits = {g: c for g, c in counts.items() if len(c) >= need}
        kept = []
        for g in sorted(hits, key=len, reverse=True):
            if not any(g in k and hits[g] <= hits[k] for k in kept):
                kept.append(g)
        for g in kept:
            found.append({'case_id': cid, 'phrase': g, 'repeats': len(hits[g]), 'of': len(replies)})
    return sorted(found, key=lambda x: (-x['repeats'] / x['of'], str(x['case_id'])))


def check(records, min_cases=3):
    flagged = {'truncated': [], 'repeated': [], 'speaks_for_user': []}
    for r in records:
        for name, fn in (('truncated', truncated), ('repeated', repeated), ('speaks_for_user', speaks_for_user)):
            hit = fn(r)
            if hit:
                flagged[name].append({'case_id': r.get('case_id'), 'repeat': r.get('repeat'),
                                      'evidence': hit, 'reply': _text(r)})
    phrases = pet_phrases(records, min_cases)
    templates = case_templates(records)
    return {
        'total': len(records),
        'counts': {k: len(v) for k, v in flagged.items()}
                  | {'pet_phrases': len(phrases), 'case_templates': len({t['case_id'] for t in templates})},
        'flagged': flagged,
        'pet_phrases': phrases,
        'case_templates': templates,
        'note': ('机器初筛，宁多勿漏；每条须人工复核后才计入正式评分。'
                 'case_templates 中身份、事实类的一致回答（如「是开书店的」）属于正常，不计为模板化。'),
    }
