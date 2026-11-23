# -*- coding: utf-8 -*-
"""Time awareness for character context (roadmap 1.1, 1.2, 1.3).

1.1  current time-of-day and the gap since the previous message, as coarse bands
     the character may use to adjust tone (never to recite timestamps);
1.3  a relative-age label on every recalled memory ("约 3 天前");
1.2  confirmed, still-pending promises from an earlier sitting, offered once to
     be raised naturally when the user comes back after a break.

Ideas from 溪语 AI's "unfinished things" (https://github.com/dimang01/xiyu-ai, MIT)
and Memoria's recall time labels (https://github.com/delay813/Memoria, MIT),
taken from their READMEs only; no code was copied.

All times are UNIX seconds. Calendar labels use Beijing time: the product and
its users are in mainland China.
"""
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
WEEKDAYS = '一二三四五六日'
# A gap at least this long counts as a new sitting: pending promises may be raised.
NEW_SITTING_SECONDS = 6 * 3600
MAX_PROMISES = 2


def _day(ts):
    return datetime.fromtimestamp(ts, BEIJING).date()


def period(ts):
    hour = datetime.fromtimestamp(ts, BEIJING).hour
    if hour < 5:
        return '深夜'
    if hour < 9:
        return '早上'
    if hour < 12:
        return '上午'
    if hour < 14:
        return '中午'
    if hour < 18:
        return '下午'
    if hour < 23:
        return '晚上'
    return '深夜'


def now_label(ts):
    return '周' + WEEKDAYS[datetime.fromtimestamp(ts, BEIJING).weekday()] + period(ts)


def gap_label(previous, current):
    """Coarse band for the silence before the current message; None for a first message."""
    if previous is None:
        return None
    seconds = max(0, current - previous)
    days = (_day(current) - _day(previous)).days
    if seconds < 30 * 60:
        return '刚聊过，对话还在继续'
    if days == 0:
        return '同一天里隔了几个小时'
    if days == 1:
        return '隔了一晚'
    if days < 7:
        return '隔了' + str(days) + '天'
    if days < 30:
        return '隔了一周多' if days < 14 else '隔了几周'
    return '隔了一个多月'


def ago_label(ts, current):
    """Relative age of a memory's source message, by Beijing calendar day."""
    if not ts:
        return None
    days = (_day(current) - _day(ts)).days
    if days <= 0:
        return '今天'
    if days == 1:
        return '昨天'
    if days < 7:
        return '约' + str(days) + '天前'
    if days < 30:
        return '约' + str(days // 7) + '周前'
    return '约' + str(max(1, days // 30)) + '个月前'


# Characters that make a two-character fragment too common to prove a promise was mentioned
# ("下次", "那本", "一起" appear in all kinds of replies).
FUNCTION_CHARS = set('下次一起把那本的了我你们再去来要就也还个这在是和吧啊呢好')


def mentioned(reply, content):
    """Whether a reply visibly brings up a promise: shares a content-specific fragment with it.

    Errs towards "not mentioned": a promise judged unmentioned is simply offered again at a
    later sitting, whereas a wrong "mentioned" would drop it for good.
    """
    from .portrait import grams
    specific = {g for g in grams(content) if not set(g) & FUNCTION_CHARS}
    return bool(specific & grams(reply or ''))


def previous_message_time(c, cid, source_ordinal):
    # The user's own previous message: the character's opening line is not a sitting.
    row = c.execute("SELECT max(created_at) FROM messages WHERE conversation_id=? AND off_path=0 AND ordinal<? "
                    "AND role='user' AND status='saved'", (cid, source_ordinal)).fetchone()
    return row[0] if row else None


def pending_promises(c, cid, current, previous):
    """Confirmed promises not completed/withdrawn/expired, made before the break, never yet delivered.

    Only offered when this message opens a new sitting, so a promise made five
    minutes ago is not brought straight back up.
    """
    if previous is None or current - previous < NEW_SITTING_SECONDS:
        return []
    rows = c.execute(
        """SELECT m.id,m.content,s.created_at AS made_at FROM memories m
           JOIN messages s ON s.id=m.primary_source_id
           WHERE m.conversation_id=? AND m.kind='promise' AND m.status='confirmed'
             AND (m.valid_until IS NULL OR m.valid_until>?)
             AND s.off_path=0
             AND m.id NOT IN (SELECT memory_id FROM promise_lifecycle WHERE conversation_id=?)
             AND m.id NOT IN (SELECT o.memory_id FROM promise_offers o JOIN generation_requests g ON g.id=o.generation_id
                              JOIN messages u ON u.id=g.user_message_id WHERE o.conversation_id=? AND o.delivered=1 AND u.off_path=0)
             AND m.id NOT IN (SELECT p.promise_id FROM proactive_messages p JOIN messages x ON x.id=p.message_id
                              WHERE p.conversation_id=? AND p.promise_id IS NOT NULL AND x.off_path=0)
             AND s.created_at<=?
           ORDER BY s.created_at DESC LIMIT ?""",
        (cid, current, cid, cid, cid, previous, MAX_PROMISES)).fetchall()
    return [{'id': r['id'], 'content': r['content'], '约定时间': ago_label(r['made_at'], current)} for r in rows]


def context(c, cid, source_ordinal, current):
    previous = previous_message_time(c, cid, source_ordinal)
    data = {'现在': now_label(current)}
    gap = gap_label(previous, current)
    if gap:
        data['距上一条消息'] = gap
    promises = pending_promises(c, cid, current, previous)
    return data, promises


GUIDANCE = ('时间信息只用来调整语气和承接：刚聊过就直接接着说；隔了一晚或几天，可以有一句自然的再见面感，'
            '不必每次都提。不报具体钟点，不在无关话题里谈时间，不据此推断对方这段时间做了什么。')
PROMISE_GUIDANCE = ('待提起的约定是用户已确认、尚未完成的约定。这次可以在合适时用自己的话自然提一句，'
                    '问对方还想不想继续；对方正在说别的事或有情绪时先回应对方，不硬转话题；'
                    '只依据约定原文，不补写约定之外的细节，不当作已经完成。')
