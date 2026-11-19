-- Roadmap 1.12: scene ledger versions (time, place, present), each field with its supporting message ids.
CREATE TABLE scene_states(
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 reply_id TEXT REFERENCES messages(id),
 version INTEGER NOT NULL CHECK(version>0),
 state TEXT NOT NULL,
 created_at INTEGER NOT NULL,
 UNIQUE(conversation_id,version)
);
