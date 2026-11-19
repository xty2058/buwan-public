# -*- coding: utf-8 -*-
"""Internal inspection views (roadmap 1.13 recall inspector, 1.8 latency view).

Read-only. Every query is scoped to the signed-in user's own conversations, so
the page can be used for D1 judging and manual review without exposing anyone
else's data. Memories that have since been deleted, invalidated or rejected
keep their place in the timeline but their content is masked: a deletion must
not be undone by an audit screen.

Idea borrowed from Amber Heart's recall page
(https://github.com/lixiaomeng-eng/amber-heart-memory, AGPL): show, per turn,
which memories were recalled and the original words behind them. Only the idea
is used; no code was copied.
"""
import json

LIVE = ('confirmed', 'candidate', 'conflicted')


def percentile(values, share):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(share * (len(values) - 1))))]


class Inspection:
    def __init__(self, db, service):
        self.db, self.service = db, service

    def recalls(self, user_id, cid, limit=50):
        limit = max(1, min(int(limit), 200))
        with self.db.connect() as c:
            self.service.own(c, cid, user_id)
            rows = c.execute(
                '''SELECT g.id,g.kind,g.status,g.attempt,g.created_at,u.content AS user_text,r.content AS reply_text,
                          t.selected,t.mode,t.memory_revision,
                          k.first_token_ms,k.duration_ms,k.input_tokens,k.cached_input_tokens,k.output_tokens,k.model
                   FROM generation_requests g
                   JOIN messages u ON u.id=g.user_message_id
                   LEFT JOIN messages r ON r.id=g.result_message_id
                   LEFT JOIN memory_retrievals t ON t.generation_id=g.id
                   LEFT JOIN model_calls k ON k.generation_request_id=g.id AND k.attempt=g.attempt
                   WHERE g.conversation_id=? ORDER BY g.rowid DESC LIMIT ?''', (cid, limit)).fetchall()
            items = []
            for row in rows:
                recalled = []
                for record in json.loads(row['selected'] or '[]'):
                    now_status = c.execute('SELECT status FROM memories WHERE id=? AND conversation_id=?',
                                           (record.get('id'), cid)).fetchone()
                    status = now_status[0] if now_status else 'missing'
                    visible = status in LIVE
                    quotes = [q[0] for q in c.execute('SELECT quote FROM memory_evidence WHERE memory_id=?',
                                                      (record.get('id'),))] if visible else []
                    recalled.append({
                        'id': record.get('id'), 'kind': record.get('kind'), 'fact_scope': record.get('fact_scope'),
                        'score': record.get('score'), 'source_time': record.get('source_time'),
                        'status_now': status,
                        'content': record.get('content') if visible else None,
                        'quotes': quotes,
                    })
                items.append({
                    'generation_id': row['id'], 'kind': row['kind'], 'status': row['status'],
                    'created_at': row['created_at'], 'user_text': row['user_text'], 'reply_text': row['reply_text'],
                    # None = this turn ran without a recall step; [] = recall ran and found nothing.
                    'recalled': recalled if row['selected'] is not None else None,
                    'retrieval_mode': row['mode'], 'memory_revision': row['memory_revision'],
                    'call': {'model': row['model'], 'first_token_ms': row['first_token_ms'],
                             'duration_ms': row['duration_ms'], 'input_tokens': row['input_tokens'],
                             'cached_input_tokens': row['cached_input_tokens'], 'output_tokens': row['output_tokens']},
                })
        return {'items': items}

    def recalled_for_message(self, user_id, cid, mid):
        """Roadmap 1.7: still-valid memories that the reply `mid` was generated with.

        Feeds the in-chat "说错了" sheet so the user can correct the memory behind a
        wrong reply through the normal memory edit path.
        """
        with self.db.connect() as c:
            self.service.own(c, cid, user_id)
            row = c.execute("""SELECT t.selected FROM generation_requests g JOIN messages m ON m.id=g.result_message_id
                               LEFT JOIN memory_retrievals t ON t.generation_id=g.id
                               WHERE g.conversation_id=? AND g.result_message_id=? AND m.role='assistant'
                               ORDER BY g.rowid DESC LIMIT 1""", (cid, mid)).fetchone()
            items = []
            for record in json.loads(row['selected'] or '[]') if row else []:
                m = c.execute("SELECT id,revision,kind,fact_scope,content FROM memories WHERE id=? AND conversation_id=? AND status='confirmed'",
                              (record.get('id'), cid)).fetchone()
                if m and m['id'] not in {x['id'] for x in items}:
                    items.append(dict(m))
        return {'items': items}

    def metrics(self, user_id):
        """Latency and cache use by model and character, completed calls only."""
        with self.db.connect() as c:
            rows = c.execute(
                '''SELECT k.model,cv.character_id,k.first_token_ms,k.duration_ms,k.input_tokens,k.cached_input_tokens
                   FROM model_calls k
                   JOIN generation_requests g ON g.id=k.generation_request_id
                   JOIN conversations cv ON cv.id=g.conversation_id
                   WHERE cv.user_id=? AND k.status='completed' ''', (user_id,)).fetchall()
        groups = {}
        for r in rows:
            groups.setdefault((r['model'], r['character_id']), []).append(r)
        result = []
        for (model, character), calls in sorted(groups.items()):
            first = [x['first_token_ms'] for x in calls if x['first_token_ms'] is not None]
            total = [x['duration_ms'] for x in calls if x['duration_ms'] is not None]
            reported = [x for x in calls if x['cached_input_tokens'] is not None and x['input_tokens']]
            inputs = sum(x['input_tokens'] for x in reported)
            result.append({
                'model': model, 'character_id': character, 'calls': len(calls), 'measured': len(first),
                'first_token_ms': {'p50': percentile(first, .5), 'p90': percentile(first, .9)},
                'duration_ms': {'p50': percentile(total, .5), 'p90': percentile(total, .9)},
                # Share of input tokens served from the provider's prefix cache, where reported.
                'cache_hit_rate': round(sum(x['cached_input_tokens'] for x in reported) / inputs, 4) if inputs else None,
            })
        return {'items': result}
