# -*- coding: utf-8 -*-
"""剧情分支 (roadmap 2.1): several story lines inside one conversation.

A new line starts after any finished reply (or the opening line) of the line the
user is on. The original line stays as it was and can be returned to at any
time. Nothing is copied and no new conversation is created.

What is separate per line: its messages, and everything derived from them —
memories whose sources are on another line are invisible, pending promises,
scene state, summaries, proactive lines, and the relationship state (restored
from checkpoints when the line changes).

What is shared on purpose: explicit operations on a memory that already existed
before the fork (confirming, editing, deleting, completing a promise) apply to
every line, and so does the shared-reading activity. See the roadmap entry.

How it works: messages carry `branch_id` and a materialised `off_path` flag.
`off_path` is recomputed here whenever the active line changes; every reader in
the code base filters on `off_path=0` or goes through Memory.blocked_context_ids.

Data model and rules follow SillyTavern-Floor-Anchor as described in its README
(MIT): hierarchical branch numbering, one-step return, only leaves can be
deleted and only after a second confirmation. No code was copied.
"""
import json

from .errors import ApiError
from .service import uid, now

MAX_BRANCHES = 12
PREVIEW = 28


class Branches:
    def __init__(self, db, service, memory, relationship):
        self.db, self.service, self.memory, self.relationship = db, service, memory, relationship
        relationship.branches = self

    # ---- path ----
    def active(self, c, cid):
        row = c.execute('SELECT active_branch_id FROM branch_state WHERE conversation_id=?', (cid,)).fetchone()
        return row[0] if row else None

    def chain(self, c, bid):
        """[(branch_id, bound)] from the given line up to the main line; bound is the last ordinal of
        that line still visible (None: all of it)."""
        result, bound = [], None
        while bid is not None:
            row = c.execute('SELECT parent_id,fork_ordinal FROM branches WHERE id=?', (bid,)).fetchone()
            result.append((bid, bound))
            bid, bound = row['parent_id'], row['fork_ordinal']
        result.append((None, bound))
        return result

    def activate(self, c, cid, bid):
        c.execute('INSERT OR REPLACE INTO branch_state VALUES(?,?,?)', (cid, bid, now()))
        c.execute('UPDATE messages SET off_path=1 WHERE conversation_id=?', (cid,))
        for line, bound in self.chain(c, bid):
            c.execute('UPDATE messages SET off_path=0 WHERE conversation_id=? AND branch_id IS ? AND ordinal<=?',
                      (cid, line, 2 ** 62 if bound is None else bound))

    def top(self, c, cid):
        return c.execute('SELECT coalesce(max(ordinal),0) FROM messages WHERE conversation_id=? AND off_path=0', (cid,)).fetchone()[0]

    # ---- relationship state per line ----
    def checkpoint(self, c, cid):
        """Called by Relationships on every change, and here before leaving a line."""
        c.execute('INSERT INTO relationship_checkpoints(conversation_id,branch_id,ordinal,snapshot,created_at) VALUES(?,?,?,?,?)',
                  (cid, self.active(c, cid), self.top(c, cid), json.dumps(self.relationship.snapshot(c, cid)), now()))

    def state_for(self, c, cid, bid):
        """Newest checkpoint visible from a line: any of its own, and for each ancestor those written
        strictly before the fork message (a change recorded at the fork message itself came after it)."""
        chain = self.chain(c, bid)
        own = chain[0][0]
        bounds = {line: bound for line, bound in chain}
        for row in c.execute('SELECT branch_id,ordinal,snapshot FROM relationship_checkpoints WHERE conversation_id=? ORDER BY id DESC', (cid,)):
            line = row['branch_id']
            if line not in bounds:
                continue
            if line == own or bounds[line] is None or row['ordinal'] < bounds[line]:
                return json.loads(row['snapshot'])
        return None

    def enter(self, c, cid, bid, fork_ordinal=None):
        """Make `bid` the active line and bring the relationship state that belongs to it."""
        self.checkpoint(c, cid)  # what the line being left looks like right now
        complete = c.execute('SELECT 1 FROM relationship_checkpoints WHERE conversation_id=? AND branch_id IS NULL AND ordinal=0', (cid,)).fetchone()
        current = self.relationship.snapshot(c, cid)
        self.activate(c, cid, bid)
        snapshot = self.state_for(c, cid, bid)
        if fork_ordinal is not None and not complete:
            # A conversation older than this feature has no history of its relationship state. Start the
            # new line from today's state minus every event that came after the fork; never more than that.
            late = {r[0] for r in c.execute(
                """SELECT e.id FROM relationship_events e JOIN messages m ON m.id=e.primary_source_id
                   WHERE e.conversation_id=? AND m.ordinal>?""", (cid, fork_ordinal))}
            snapshot = {'state': current['state'], 'events': {k: v for k, v in current['events'].items() if k not in late}}
        if snapshot is not None:
            self.relationship.restore(c, cid, snapshot)
        self.memory.bump(c, cid)

    # ---- API ----
    def guard(self, c, cid):
        if c.execute("SELECT 1 FROM generation_requests WHERE conversation_id=? AND status IN ('queued','generating','awaiting_choice')", (cid,)).fetchone():
            raise ApiError(409, 'generation_pending', '先结束当前生成或决定候选回复')

    def labels(self, c, cid):
        rows = [dict(r) for r in c.execute('SELECT * FROM branches WHERE conversation_id=? ORDER BY created_at,rowid', (cid,))]
        names = {None: '主线'}
        for r in rows:
            parent = names[r['parent_id']]
            names[r['id']] = ('支线 ' if parent == '主线' else parent + '-') + str(r['number'])
        return rows, names

    def list(self, user, cid):
        with self.db.connect() as c:
            self.service.own(c, cid, user['id'])
            return self.describe(c, cid)

    def describe(self, c, cid):
        rows, names = self.labels(c, cid)
        active = self.active(c, cid)
        live = [r for r in rows if not r['deleted_at']]
        parents = {r['parent_id'] for r in live}
        deleted = {r[0] for r in c.execute('SELECT message_id FROM message_deletions WHERE conversation_id=?', (cid,))}

        def line(bid, row=None):
            messages = c.execute("""SELECT id,role,content,created_at FROM messages WHERE conversation_id=? AND branch_id IS ?
                                    AND role IN ('user','assistant') AND status IN ('saved','completed') ORDER BY ordinal""", (cid, bid)).fetchall()
            first = next((m['content'] for m in messages if m['role'] == 'user' and m['id'] not in deleted), '')
            item = {'id': bid, 'name': names[bid], 'active': bid == active, 'messages': len(messages),
                    'preview': first[:PREVIEW] + ('…' if len(first) > PREVIEW else ''),
                    'updated_at': messages[-1]['created_at'] if messages else (row['created_at'] if row else None)}
            if row:
                fork = c.execute('SELECT content FROM messages WHERE id=?', (row['fork_message_id'],)).fetchone()['content']
                fork = '' if row['fork_message_id'] in deleted else fork
                item.update(parent_id=row['parent_id'], parent_name=names[row['parent_id']],
                            fork_preview=fork[:PREVIEW] + ('…' if len(fork) > PREVIEW else ''),
                            depth=names[bid].count('-') + 1, deletable=bid != active and bid not in parents)
            else:
                item.update(parent_id=None, depth=0, deletable=False)
            return item

        # Depth-first, so each line is followed by the lines that start from it.
        ordered = []

        def walk(parent):
            for r in live:
                if r['parent_id'] == parent:
                    ordered.append(line(r['id'], r));walk(r['id'])

        walk(None)
        return {'active': active, 'items': [line(None)] + ordered, 'limit': MAX_BRANCHES}

    def fork(self, user, cid, body):
        mid = body.get('message_id')
        if not isinstance(mid, str):
            raise ApiError(422, 'invalid_branch', '请选择从哪条消息开始')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.service.own(c, cid, user['id']);self.guard(c, cid)
            m = c.execute("""SELECT * FROM messages WHERE id=? AND conversation_id=? AND off_path=0 AND role='assistant' AND status='completed'
                             AND id NOT IN (SELECT message_id FROM message_deletions WHERE conversation_id=?)""", (mid, cid, cid)).fetchone()
            if not m:
                raise ApiError(404, 'not_found', '只能从当前这条线上一条完整的回复开始')
            if c.execute('SELECT count(*) FROM branches WHERE conversation_id=? AND deleted_at IS NULL', (cid,)).fetchone()[0] >= MAX_BRANCHES:
                raise ApiError(409, 'branch_limit', '支线已经有 ' + str(MAX_BRANCHES) + ' 条了，先删掉不要的再开新的')
            if m['ordinal'] == self.top(c, cid):
                raise ApiError(409, 'nothing_to_branch', '这已经是最新一条，直接接着聊就可以')
            number = c.execute('SELECT coalesce(max(number),0)+1 FROM branches WHERE conversation_id=? AND parent_id IS ?', (cid, m['branch_id'])).fetchone()[0]
            bid = uid()
            c.execute('INSERT INTO branches VALUES(?,?,?,?,?,?,?,NULL)', (bid, cid, m['branch_id'], mid, m['ordinal'], number, now()))
            self.enter(c, cid, bid, m['ordinal'])
            return self.describe(c, cid)

    def switch(self, user, cid, body):
        bid = body.get('branch_id')
        if bid is not None and not isinstance(bid, str):
            raise ApiError(422, 'invalid_branch', '支线标识不正确')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.service.own(c, cid, user['id']);self.guard(c, cid)
            if bid is not None and not c.execute('SELECT 1 FROM branches WHERE id=? AND conversation_id=? AND deleted_at IS NULL', (bid, cid)).fetchone():
                raise ApiError(404, 'not_found', '这条线不存在或已删除')
            if bid != self.active(c, cid):
                self.enter(c, cid, bid)
            return self.describe(c, cid)

    def delete(self, user, cid, bid, body):
        """Only a leaf that is not the current line, and only with an explicit second confirmation."""
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self.service.own(c, cid, user['id'])
            row = c.execute('SELECT * FROM branches WHERE id=? AND conversation_id=? AND deleted_at IS NULL', (bid, cid)).fetchone()
            if not row:
                raise ApiError(404, 'not_found', '这条线不存在或已删除')
            if bid == self.active(c, cid):
                raise ApiError(409, 'branch_active', '不能删除正在聊的这条线，先切到别的线')
            if c.execute('SELECT 1 FROM branches WHERE parent_id=? AND deleted_at IS NULL', (bid,)).fetchone():
                raise ApiError(409, 'branch_has_children', '这条线上还分出了别的线，先删掉它们')
            if body.get('confirm') is not True:
                raise ApiError(422, 'confirm_required', '删除后这条线上的对话不能恢复，需要再确认一次')
            # Its messages stay in the database off every path; nothing can reach them again.
            c.execute('UPDATE branches SET deleted_at=? WHERE id=?', (now(), bid))
            return self.describe(c, cid)
