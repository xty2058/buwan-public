ALTER TABLE generation_requests ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;
ALTER TABLE generation_requests ADD COLUMN auth_session_id TEXT REFERENCES auth_sessions(id);
ALTER TABLE generation_requests ADD COLUMN engine_id TEXT;
ALTER TABLE generation_requests ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE generation_requests ADD COLUMN context_hash TEXT;
ALTER TABLE generation_requests ADD COLUMN context_message_ids TEXT;
ALTER TABLE generation_requests ADD COLUMN finish_reason TEXT;
CREATE UNIQUE INDEX one_pending_generation_per_conversation ON generation_requests(conversation_id)
 WHERE status IN ('queued','generating','awaiting_choice');
CREATE TABLE model_calls (
 id TEXT PRIMARY KEY NOT NULL,
 generation_request_id TEXT NOT NULL REFERENCES generation_requests(id),
 attempt INTEGER NOT NULL,
 approval_id TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('live','simulated')),
 provider TEXT NOT NULL, model TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('started','completed','stopped','failed')),
 input_tokens INTEGER, output_tokens INTEGER,
 usage_complete INTEGER NOT NULL DEFAULT 0 CHECK(usage_complete IN (0,1)),
 estimated_cost TEXT, currency TEXT,
 error_code TEXT, finish_reason TEXT,
 started_at INTEGER NOT NULL, ended_at INTEGER,
 UNIQUE(generation_request_id,attempt)
);
CREATE INDEX model_call_budget ON model_calls(mode,approval_id);
