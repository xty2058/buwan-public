-- Roadmap 2.5: a restrained proactive line, written only when the user opens the chat after a break.
-- proactive_messages marks which assistant messages were proactive (at most one per Beijing day) and
-- which pending promise, if any, that line actually brought up, so it is not raised again.
CREATE TABLE proactive_messages(
 message_id TEXT PRIMARY KEY REFERENCES messages(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 promise_id TEXT REFERENCES memories(id),
 day TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
CREATE INDEX proactive_messages_by_conversation ON proactive_messages(conversation_id,created_at);
-- The off switch. No row means on.
CREATE TABLE proactive_settings(
 conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 updated_at INTEGER NOT NULL
);
