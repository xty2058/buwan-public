-- Roadmap 1.8 / 1.15: per-call latency and prefix-cache usage for production chat calls.
-- NULL means "not measured" (older rows, or the provider did not report it), never zero.
ALTER TABLE model_calls ADD COLUMN cached_input_tokens INTEGER CHECK(cached_input_tokens IS NULL OR cached_input_tokens>=0);
-- Milliseconds from the worker starting this attempt (includes memory retrieval) to the first visible reply text.
ALTER TABLE model_calls ADD COLUMN first_token_ms INTEGER CHECK(first_token_ms IS NULL OR first_token_ms>=0);
-- Milliseconds from the worker starting this attempt to completion or failure (user-stopped attempts stay NULL).
ALTER TABLE model_calls ADD COLUMN duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms>=0);
