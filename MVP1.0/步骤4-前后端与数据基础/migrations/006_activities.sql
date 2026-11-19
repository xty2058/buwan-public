CREATE TABLE activities (
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 article_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','paused','completed')),
 current_index INTEGER NOT NULL DEFAULT 0 CHECK(current_index>=0),
 revision INTEGER NOT NULL DEFAULT 1,
 memory_id TEXT REFERENCES memories(id),
 memory_suppressed INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER,
 UNIQUE(id,conversation_id)
);
CREATE UNIQUE INDEX one_unfinished_activity ON activities(conversation_id) WHERE status!='completed';
CREATE TABLE activity_operations (
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 client_request_id TEXT NOT NULL,
 body_hash TEXT NOT NULL,
 activity_id TEXT NOT NULL REFERENCES activities(id),
 PRIMARY KEY(conversation_id,client_request_id)
);
CREATE TABLE activity_messages (
 id TEXT PRIMARY KEY,
 activity_id TEXT NOT NULL REFERENCES activities(id),
 role TEXT NOT NULL CHECK(role IN ('user','assistant')),
 content TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('saved','pending','completed','failed','cancelled')),
 client_request_id TEXT,
 body_hash TEXT,
 reply_to_id TEXT REFERENCES activity_messages(id),
 base_revision INTEGER NOT NULL,
 error_code TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(activity_id,client_request_id)
);
CREATE UNIQUE INDEX one_activity_reply ON activity_messages(activity_id) WHERE status='pending';
CREATE TABLE activity_model_calls (
 id TEXT PRIMARY KEY,
 activity_id TEXT NOT NULL REFERENCES activities(id),
 message_id TEXT NOT NULL REFERENCES activity_messages(id),
 approval_id TEXT NOT NULL, mode TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
 context_hash TEXT NOT NULL, context_snapshot TEXT NOT NULL,
 error_code TEXT, started_at INTEGER NOT NULL, ended_at INTEGER
);
CREATE INDEX activity_budget ON activity_model_calls(mode,approval_id);
CREATE TABLE activity_memory_sources (
 message_id TEXT PRIMARY KEY REFERENCES messages(id),
 activity_id TEXT NOT NULL REFERENCES activities(id),
 milestone TEXT NOT NULL,
 UNIQUE(activity_id,milestone)
);
ALTER TABLE memories ADD COLUMN source_type TEXT NOT NULL DEFAULT 'chat';
ALTER TABLE memories ADD COLUMN activity_id TEXT REFERENCES activities(id);
CREATE TABLE activity_events (
 id TEXT PRIMARY KEY, activity_id TEXT NOT NULL REFERENCES activities(id),
 event TEXT NOT NULL, revision INTEGER NOT NULL, created_at INTEGER NOT NULL
);
ALTER TABLE activity_messages ADD COLUMN context_usable INTEGER NOT NULL DEFAULT 1;
CREATE TABLE promise_lifecycle (
 memory_id TEXT PRIMARY KEY REFERENCES memories(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 status TEXT NOT NULL CHECK(status IN ('completed','withdrawn')),
 source_message_id TEXT NOT NULL REFERENCES messages(id),
 updated_at INTEGER NOT NULL
);
CREATE TABLE promise_operations (
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 client_request_id TEXT NOT NULL, body_hash TEXT NOT NULL,
 PRIMARY KEY(conversation_id,client_request_id)
);
