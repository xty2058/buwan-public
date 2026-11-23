"""Daily reply caps for the hosted service (APP_DAILY_REPLY_LIMIT per user, APP_DAILY_TOTAL_LIMIT site-wide).

A "reply" is one model generation the user asked for: a chat reply or regeneration, or a reply while
reading together. Failed attempts do not count; pending, running and stopped requests do.
Stopped requests may already incur provider costs. These operational caps are NOT paid credits.
Future paid-credit settlement must exempt failed and user-stopped incomplete replies.
Days follow the Beijing calendar. Auxiliary model calls are not individually counted here;
these caps are not an exact monetary budget.
0 means unlimited (the local default).
"""
import time
from datetime import datetime, timedelta, timezone

from .errors import ApiError

TZ = timezone(timedelta(hours=8))


def day_start(now=None):
    moment = datetime.fromtimestamp(now if now is not None else time.time(), TZ)
    return int(moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def used_today(c, user_id=None, now=None):
    start = day_start(now)
    who = ' AND v.user_id=?' if user_id else ''
    args = (start, user_id) if user_id else (start,)
    chat = c.execute("SELECT COUNT(*) FROM generation_requests g JOIN conversations v ON v.id=g.conversation_id "
                     "WHERE g.created_at>=? AND g.status!='failed'" + who, args).fetchone()[0]
    reading = c.execute("SELECT COUNT(*) FROM activity_model_calls m JOIN activities a ON a.id=m.activity_id "
                        "JOIN conversations v ON v.id=a.conversation_id WHERE m.started_at>=? AND m.status!='failed'" + who,
                        args).fetchone()[0]
    return chat + reading


def check(c, user_id, per_user, total):
    """Raise 429 before a new paid reply is created. Call inside the creating transaction."""
    if per_user and used_today(c, user_id) >= per_user:
        raise ApiError(429, 'daily_limit_reached', f'今天的 {per_user} 次回复已用完，北京时间明天 0 点恢复。已保存的内容不受影响。')
    if total and used_today(c) >= total:
        raise ApiError(429, 'site_daily_limit_reached', '今天的服务额度已用完，北京时间明天 0 点恢复。已保存的内容不受影响。')


def remaining(c, user_id, per_user):
    return None if not per_user else max(0, per_user - used_today(c, user_id))
