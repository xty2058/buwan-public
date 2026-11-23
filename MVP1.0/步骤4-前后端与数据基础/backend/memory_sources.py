"""Memory sources used by the in-chat correction sheet."""
import json

class MemorySources:
    def __init__(self, db, service):
        self.db, self.service = db, service

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
