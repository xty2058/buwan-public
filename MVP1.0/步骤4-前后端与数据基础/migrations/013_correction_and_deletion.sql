-- Roadmap 1.4: memories kept out of recall because a related memory was corrected.
-- Soft and auditable: nothing is deleted; released_at re-enables recall.
CREATE TABLE memory_recall_suppressions(
 memory_id TEXT NOT NULL REFERENCES memories(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 cause_memory_id TEXT NOT NULL REFERENCES memories(id),
 score REAL NOT NULL,
 created_at INTEGER NOT NULL,
 released_at INTEGER,
 PRIMARY KEY(memory_id,cause_memory_id)
);
CREATE INDEX memory_recall_suppressions_active ON memory_recall_suppressions(conversation_id,released_at);
-- Roadmap 1.14: messages the user deleted. The row stays (ordinals, audit); its text is hidden
-- and it no longer feeds context, extraction, summaries, scene ledger or recall.
CREATE TABLE message_deletions(
 message_id TEXT PRIMARY KEY REFERENCES messages(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 deleted_by TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
