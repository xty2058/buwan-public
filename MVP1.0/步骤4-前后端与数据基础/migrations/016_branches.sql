-- Roadmap 2.1: story branches inside one conversation.
-- A branch starts after one message of an existing line. Every message belongs to the line that was
-- active when it was written (branch_id; NULL is the main line). off_path is the materialised answer
-- to "is this message on the line the user is currently on": it is recomputed whenever the active
-- line changes, so every reader only needs `off_path=0`.
ALTER TABLE messages ADD COLUMN branch_id TEXT;
ALTER TABLE messages ADD COLUMN off_path INTEGER NOT NULL DEFAULT 0 CHECK(off_path IN (0,1));
CREATE INDEX messages_on_path ON messages(conversation_id,off_path,ordinal);
CREATE TABLE branches(
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 parent_id TEXT REFERENCES branches(id),
 fork_message_id TEXT NOT NULL REFERENCES messages(id),
 fork_ordinal INTEGER NOT NULL,
 number INTEGER NOT NULL,
 created_at INTEGER NOT NULL,
 deleted_at INTEGER
);
CREATE INDEX branches_by_conversation ON branches(conversation_id,parent_id);
CREATE TABLE branch_state(
 conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
 active_branch_id TEXT REFERENCES branches(id),
 updated_at INTEGER NOT NULL
);
-- Every writer of messages gets the active line without knowing about branches.
CREATE TRIGGER messages_take_active_branch AFTER INSERT ON messages
WHEN NEW.branch_id IS NULL
BEGIN
 UPDATE messages SET branch_id=(SELECT active_branch_id FROM branch_state WHERE conversation_id=NEW.conversation_id) WHERE id=NEW.id;
END;
-- Relationship state as it stood on a line at a point in time, written on every change, so that
-- leaving a line and coming back, or starting a line from an earlier message, restores it exactly.
CREATE TABLE relationship_checkpoints(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 branch_id TEXT REFERENCES branches(id),
 ordinal INTEGER NOT NULL,
 snapshot TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
CREATE INDEX relationship_checkpoints_by_conversation ON relationship_checkpoints(conversation_id,id);
