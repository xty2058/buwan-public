CREATE TABLE users (
 id TEXT PRIMARY KEY NOT NULL,
 username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL,
 nickname TEXT NOT NULL CHECK(length(nickname) BETWEEN 1 AND 16),
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE auth_sessions (
 id TEXT PRIMARY KEY NOT NULL,
 user_id TEXT NOT NULL REFERENCES users(id),
 token_hash TEXT NOT NULL UNIQUE,
 csrf_hash TEXT NOT NULL,
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 revoked_at INTEGER
);
CREATE INDEX sessions_by_user ON auth_sessions(user_id, expires_at);
CREATE TABLE auth_attempts (
 bucket TEXT NOT NULL, occurred_at INTEGER NOT NULL
);
CREATE INDEX attempts_by_bucket ON auth_attempts(bucket, occurred_at);
CREATE TABLE characters (
 id TEXT PRIMARY KEY NOT NULL,
 slug TEXT NOT NULL UNIQUE,
 current_version_id TEXT,
 available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0,1)),
 created_at INTEGER NOT NULL,
 FOREIGN KEY(current_version_id,id) REFERENCES character_versions(id,character_id)
);
CREATE TABLE character_versions (
 id TEXT PRIMARY KEY NOT NULL,
 character_id TEXT NOT NULL REFERENCES characters(id),
 version INTEGER NOT NULL CHECK(version > 0),
 public_profile TEXT NOT NULL,
 private_definition TEXT NOT NULL,
 definition_hash TEXT NOT NULL,
 published_at INTEGER NOT NULL,
 UNIQUE(character_id,version), UNIQUE(id,character_id)
);
CREATE TRIGGER version_is_immutable BEFORE UPDATE ON character_versions
 BEGIN SELECT RAISE(ABORT,'published_version_is_immutable'); END;
CREATE TRIGGER version_cannot_be_deleted BEFORE DELETE ON character_versions
 BEGIN SELECT RAISE(ABORT,'published_version_cannot_be_deleted'); END;
CREATE TABLE conversations (
 id TEXT PRIMARY KEY NOT NULL,
 user_id TEXT NOT NULL REFERENCES users(id),
 character_id TEXT NOT NULL REFERENCES characters(id),
 character_version_id TEXT NOT NULL,
 initial_scene TEXT NOT NULL,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(user_id,character_id), UNIQUE(id,user_id), UNIQUE(user_id,id),
 FOREIGN KEY(character_version_id,character_id) REFERENCES character_versions(id,character_id)
);
CREATE TRIGGER conversation_binding_is_immutable BEFORE UPDATE OF user_id,character_id,character_version_id ON conversations
 BEGIN SELECT RAISE(ABORT,'conversation_binding_is_immutable'); END;
CREATE TABLE messages (
 id TEXT PRIMARY KEY NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 ordinal INTEGER NOT NULL CHECK(ordinal > 0),
 role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
 content TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('saved','pending','streaming','completed','stopped','failed','candidate','replaced','cancelled')),
 client_request_id TEXT,
 reply_to_id TEXT,
 replaces_id TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(id,conversation_id), UNIQUE(conversation_id,ordinal), UNIQUE(conversation_id,client_request_id),
 FOREIGN KEY(reply_to_id,conversation_id) REFERENCES messages(id,conversation_id),
 FOREIGN KEY(replaces_id,conversation_id) REFERENCES messages(id,conversation_id)
);
CREATE INDEX messages_in_conversation ON messages(conversation_id,ordinal);
-- Future request state can be reconciled after timeout without repeating a generation.
CREATE TABLE generation_requests (
 id TEXT PRIMARY KEY NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 client_request_id TEXT NOT NULL,
 user_message_id TEXT NOT NULL,
 original_reply_id TEXT,
 result_message_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('reply','regenerate')),
 status TEXT NOT NULL CHECK(status IN ('queued','generating','awaiting_choice','adopted','kept','stopped','failed')),
 error_code TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(conversation_id,client_request_id),
 FOREIGN KEY(user_message_id,conversation_id) REFERENCES messages(id,conversation_id),
 FOREIGN KEY(original_reply_id,conversation_id) REFERENCES messages(id,conversation_id),
 FOREIGN KEY(result_message_id,conversation_id) REFERENCES messages(id,conversation_id)
);
CREATE TABLE memories (
 id TEXT PRIMARY KEY NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 kind TEXT NOT NULL CHECK(kind IN ('user_fact','boundary','promise','experience','relationship')),
 fact_scope TEXT NOT NULL CHECK(fact_scope IN ('real_user','story')),
 story_scene TEXT,
 content TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('candidate','confirmed','conflicted','rejected','deleted','invalidated')),
 primary_source_id TEXT NOT NULL,
 confirmed_by TEXT,
 confirmed_at INTEGER,
 valid_from INTEGER, valid_until INTEGER,
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
 replaces_id TEXT,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(id,conversation_id),
 CHECK(fact_scope != 'story' OR (story_scene IS NOT NULL AND length(story_scene)>0)),
 CHECK(status != 'confirmed' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
 FOREIGN KEY(primary_source_id,conversation_id) REFERENCES messages(id,conversation_id),
 FOREIGN KEY(confirmed_by,conversation_id) REFERENCES conversations(user_id,id),
 FOREIGN KEY(replaces_id,conversation_id) REFERENCES memories(id,conversation_id)
);
CREATE INDEX memory_recall_scope ON memories(conversation_id,status,kind);
CREATE TABLE memory_sources (
 memory_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
 PRIMARY KEY(memory_id,message_id),
 FOREIGN KEY(memory_id,conversation_id) REFERENCES memories(id,conversation_id),
 FOREIGN KEY(message_id,conversation_id) REFERENCES messages(id,conversation_id)
);
CREATE TABLE memory_revisions (
 memory_id TEXT NOT NULL, conversation_id TEXT NOT NULL, revision INTEGER NOT NULL,
 content TEXT NOT NULL, status TEXT NOT NULL, changed_by TEXT NOT NULL,
 created_at INTEGER NOT NULL,
 PRIMARY KEY(memory_id,revision),
 FOREIGN KEY(memory_id,conversation_id) REFERENCES memories(id,conversation_id),
 FOREIGN KEY(changed_by,conversation_id) REFERENCES conversations(user_id,id)
);
CREATE TABLE memory_suppressions (
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 source_message_id TEXT NOT NULL,
 candidate_fingerprint TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(reason IN ('rejected','deleted','corrected','source_invalidated')),
 created_at INTEGER NOT NULL,
 PRIMARY KEY(conversation_id,source_message_id,candidate_fingerprint),
 FOREIGN KEY(source_message_id,conversation_id) REFERENCES messages(id,conversation_id)
);
CREATE TABLE relationship_events (
 id TEXT PRIMARY KEY NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 kind TEXT NOT NULL CHECK(kind IN ('promise','experience','relationship','boundary','repair','revocation')),
 content TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('proposed','accepted','completed','cancelled','invalidated','pending_review')),
 primary_source_id TEXT NOT NULL,
 consent_message_id TEXT,
 storage_consent_message_id TEXT,
 confirmed_by TEXT,
 long_term INTEGER NOT NULL DEFAULT 0 CHECK(long_term IN (0,1)),
 occurred_at INTEGER NOT NULL,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(id,conversation_id),
 CHECK(long_term=0 OR (confirmed_by IS NOT NULL AND storage_consent_message_id IS NOT NULL AND status != 'proposed')),
 FOREIGN KEY(primary_source_id,conversation_id) REFERENCES messages(id,conversation_id),
 FOREIGN KEY(consent_message_id,conversation_id) REFERENCES messages(id,conversation_id),
 FOREIGN KEY(storage_consent_message_id,conversation_id) REFERENCES messages(id,conversation_id),
 FOREIGN KEY(confirmed_by,conversation_id) REFERENCES conversations(user_id,id)
);
CREATE INDEX relationship_timeline ON relationship_events(conversation_id,occurred_at);
CREATE TABLE relationship_event_sources (
 event_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
 PRIMARY KEY(event_id,message_id),
 FOREIGN KEY(event_id,conversation_id) REFERENCES relationship_events(id,conversation_id),
 FOREIGN KEY(message_id,conversation_id) REFERENCES messages(id,conversation_id)
);
CREATE TABLE relationship_states (
 conversation_id TEXT PRIMARY KEY NOT NULL REFERENCES conversations(id),
 relationship_type TEXT NOT NULL DEFAULT 'unspecified' CHECK(relationship_type IN ('unspecified','friends','romantic')),
 familiarity TEXT NOT NULL DEFAULT 'new' CHECK(familiarity IN ('new','familiar','close')),
 pace TEXT NOT NULL DEFAULT '',
 basis_event_id TEXT,
 revocation_revision INTEGER NOT NULL DEFAULT 0 CHECK(revocation_revision >= 0),
 updated_at INTEGER NOT NULL,
 FOREIGN KEY(basis_event_id,conversation_id) REFERENCES relationship_events(id,conversation_id),
 CHECK(relationship_type='unspecified' OR basis_event_id IS NOT NULL)
);
-- Minimal durable marker only; ending reasons are not automatically stored here.
CREATE TABLE relationship_revocations (
 id TEXT PRIMARY KEY NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 revision INTEGER NOT NULL CHECK(revision > 0),
 result_type TEXT NOT NULL CHECK(result_type IN ('unspecified','friends')),
 created_at INTEGER NOT NULL,
 UNIQUE(conversation_id,revision)
);
-- One temporary use is distinct from a persistent conversation and a login session.
CREATE TABLE conversation_uses (
 id TEXT PRIMARY KEY NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 auth_session_id TEXT NOT NULL REFERENCES auth_sessions(id),
 temporary_relation TEXT CHECK(temporary_relation IN ('unspecified','friends','romantic')),
 based_on_revocation_revision INTEGER NOT NULL DEFAULT 0,
 opened_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, closed_at INTEGER
);
CREATE TRIGGER use_session_owner_insert BEFORE INSERT ON conversation_uses
 WHEN NOT EXISTS (SELECT 1 FROM conversations c JOIN auth_sessions s ON s.user_id=c.user_id WHERE c.id=NEW.conversation_id AND s.id=NEW.auth_session_id)
 BEGIN SELECT RAISE(ABORT,'use_session_owner_mismatch'); END;
CREATE TRIGGER use_binding_immutable BEFORE UPDATE OF conversation_id,auth_session_id ON conversation_uses
 BEGIN SELECT RAISE(ABORT,'use_binding_is_immutable'); END;
