CREATE TABLE tts_metrics (
 audio_id TEXT NOT NULL REFERENCES tts_audio(id),
 attempt INTEGER NOT NULL,
 provider TEXT NOT NULL,
 elapsed_seconds REAL NOT NULL,
 estimated_cny REAL,
 error_details TEXT NOT NULL,
 PRIMARY KEY(audio_id,attempt)
);
