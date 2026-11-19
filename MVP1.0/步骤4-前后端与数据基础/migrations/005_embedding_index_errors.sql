-- Keep the sanitized failure reason attached to the exact memory revision.
ALTER TABLE memories ADD COLUMN index_error_code TEXT;
