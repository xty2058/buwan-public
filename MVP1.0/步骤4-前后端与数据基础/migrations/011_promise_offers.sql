-- Roadmap 1.2: pending promises offered to the character for one generation.
-- delivered=1 once that generation's reply is adopted; a delivered promise is not offered again.
CREATE TABLE promise_offers(
 generation_id TEXT NOT NULL REFERENCES generation_requests(id),
 memory_id TEXT NOT NULL REFERENCES memories(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 delivered INTEGER NOT NULL DEFAULT 0 CHECK(delivered IN (0,1)),
 created_at INTEGER NOT NULL,
 PRIMARY KEY(generation_id,memory_id)
);
CREATE INDEX promise_offers_by_conversation ON promise_offers(conversation_id,delivered);
