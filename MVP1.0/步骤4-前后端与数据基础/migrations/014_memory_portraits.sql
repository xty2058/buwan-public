-- Roadmap 1.9: latest 「Ta 心中的我」 per conversation. Each sentence keeps the memory ids and
-- revisions it was grounded on, so changed or deleted memories hide their sentences on read.
CREATE TABLE memory_portraits(
 conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 memory_epoch INTEGER NOT NULL,
 sentences TEXT NOT NULL,
 dropped INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL
);
