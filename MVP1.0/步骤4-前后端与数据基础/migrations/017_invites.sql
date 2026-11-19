-- Invite-only registration for the hosted service (APP_INVITE_REQUIRED=true).
-- Codes are stored as SHA-256 digests; the plain code is shown once when created.
-- Numbered 017 so it does not collide with 015/016 on the unmerged P2 branches.
CREATE TABLE invites (
 code_hash TEXT PRIMARY KEY NOT NULL,
 label TEXT NOT NULL DEFAULT '',
 max_uses INTEGER NOT NULL CHECK(max_uses BETWEEN 1 AND 1000),
 used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0 AND used_count <= max_uses),
 created_at INTEGER NOT NULL,
 expires_at INTEGER,
 disabled_at INTEGER
);
CREATE TABLE invite_redemptions (
 code_hash TEXT NOT NULL REFERENCES invites(code_hash),
 user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
 redeemed_at INTEGER NOT NULL
);
