CREATE TABLE tts_audio (
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 message_id TEXT NOT NULL REFERENCES messages(id),
 fingerprint TEXT NOT NULL,
 voice_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','ready','failed')),
 attempt INTEGER NOT NULL,
 audio BLOB,
 error_code TEXT,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 UNIQUE(conversation_id,message_id,fingerprint)
);
CREATE TABLE tts_calls (
 id TEXT PRIMARY KEY,
 audio_id TEXT NOT NULL REFERENCES tts_audio(id),
 attempt INTEGER NOT NULL,
 approval_id TEXT NOT NULL,
 mode TEXT NOT NULL,
 text_chars INTEGER NOT NULL,
 billable_chars INTEGER,
 status TEXT NOT NULL,
 error_code TEXT,
 started_at INTEGER NOT NULL,
 ended_at INTEGER,
 UNIQUE(audio_id,attempt)
);
CREATE INDEX tts_budget ON tts_calls(approval_id,mode);
CREATE TABLE tts_reuse (
 id TEXT PRIMARY KEY,
 audio_id TEXT NOT NULL REFERENCES tts_audio(id),
 kind TEXT NOT NULL CHECK(kind IN ('cache','inflight','delivery')),
 created_at INTEGER NOT NULL
);
