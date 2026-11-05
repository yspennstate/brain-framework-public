PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_events (
  ingest_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  source_id TEXT NOT NULL,
  source_seq INTEGER NOT NULL,
  source_ref_hmac TEXT NOT NULL UNIQUE,
  content_hmac TEXT NOT NULL,
  source_content_hmac TEXT,
  occurred_at TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  session_hmac TEXT NOT NULL,
  provenance TEXT NOT NULL CHECK (provenance IN ('owner','automation','system','unknown')),
  runtime TEXT NOT NULL,
  span_count INTEGER NOT NULL,
  clause_count INTEGER NOT NULL,
  parser_overflow INTEGER NOT NULL DEFAULT 0 CHECK (parser_overflow IN (0,1))
  ,projection_version INTEGER NOT NULL DEFAULT 5
);

CREATE TABLE IF NOT EXISTS coverage_spans (
  span_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES prompt_events(event_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  start_offset INTEGER NOT NULL,
  end_offset INTEGER NOT NULL,
  span_kind TEXT NOT NULL,
  span_hmac TEXT NOT NULL,
  UNIQUE(event_id, ordinal),
  CHECK(start_offset >= 0 AND end_offset >= start_offset)
);

CREATE TABLE IF NOT EXISTS coverage_dispositions (
  span_id TEXT PRIMARY KEY REFERENCES coverage_spans(span_id) ON DELETE CASCADE,
  event_id TEXT NOT NULL REFERENCES prompt_events(event_id) ON DELETE CASCADE,
  disposition TEXT NOT NULL CHECK(disposition IN
    ('non_actionable_separator','retained_unparsed','retained_overflow')),
  reason_code TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clauses (
  clause_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES prompt_events(event_id) ON DELETE CASCADE,
  span_id TEXT NOT NULL REFERENCES coverage_spans(span_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  role TEXT NOT NULL,
  weight REAL NOT NULL,
  features_json TEXT NOT NULL,
  exact_categories_json TEXT NOT NULL,
  forbidden_ops_json TEXT NOT NULL,
  actionable INTEGER NOT NULL CHECK (actionable IN (0,1)),
  UNIQUE(event_id, ordinal)
);

CREATE TABLE IF NOT EXISTS projects (
  project_id TEXT PRIMARY KEY,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clause_assignments (
  revision_id TEXT PRIMARY KEY,
  clause_id TEXT NOT NULL REFERENCES clauses(clause_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  score REAL NOT NULL,
  confidence_band TEXT NOT NULL,
  evidence_codes_json TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(clause_id, project_id, classifier_version)
);

CREATE TABLE IF NOT EXISTS obligation_progress (
  clause_id TEXT PRIMARY KEY REFERENCES clauses(clause_id) ON DELETE CASCADE,
  state TEXT NOT NULL,
  best_support REAL NOT NULL DEFAULT 0,
  trajectory_support REAL NOT NULL DEFAULT 0,
  support_groups INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  direct_evidence_count INTEGER NOT NULL DEFAULT 0,
  inferred_evidence_count INTEGER NOT NULL DEFAULT 0,
  CHECK(state IN ('unseen','acknowledged','planned','in_progress','evidenced','satisfied',
                  'deferred','blocked','superseded','violated','non_actionable'))
);

CREATE TABLE IF NOT EXISTS action_events (
  action_id TEXT PRIMARY KEY,
  action_key_hmac TEXT NOT NULL,
  session_hmac TEXT NOT NULL,
  project_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  operation TEXT NOT NULL,
  progress_capable INTEGER NOT NULL CHECK (progress_capable IN (0,1)),
  features_json TEXT NOT NULL,
  outcome TEXT NOT NULL,
  provenance_group TEXT NOT NULL,
  UNIQUE(session_hmac, project_id, action_key_hmac)
);

CREATE TABLE IF NOT EXISTS evidence_edges (
  edge_id TEXT PRIMARY KEY,
  action_id TEXT NOT NULL REFERENCES action_events(action_id) ON DELETE CASCADE,
  clause_id TEXT NOT NULL REFERENCES clauses(clause_id) ON DELETE CASCADE,
  relation TEXT NOT NULL CHECK (relation IN ('ACTION_ADDRESSES','ACTION_VIOLATES',
                                             'EVIDENCE_SUPPORTS','EVIDENCE_CONTRADICTS')),
  support REAL NOT NULL,
  provenance_group TEXT NOT NULL,
  inferred INTEGER NOT NULL CHECK (inferred IN (0,1)),
  created_at TEXT NOT NULL,
  UNIQUE(action_id, clause_id, relation)
);

CREATE TABLE IF NOT EXISTS drift_state (
  session_hmac TEXT NOT NULL,
  project_id TEXT NOT NULL,
  progress_actions INTEGER NOT NULL DEFAULT 0,
  consecutive_low_support INTEGER NOT NULL DEFAULT 0,
  drift REAL NOT NULL DEFAULT 0,
  residual REAL NOT NULL DEFAULT 1,
  last_warning_at TEXT,
  last_warning_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(session_hmac, project_id)
);

CREATE TABLE IF NOT EXISTS project_review_state (
  project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
  attempt_generation INTEGER NOT NULL DEFAULT 0,
  published_generation INTEGER NOT NULL DEFAULT 0,
  attempt_started_at TEXT,
  published_at TEXT,
  last_error_code TEXT
);

CREATE TABLE IF NOT EXISTS review_windows (
  window_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  hour_bucket TEXT NOT NULL,
  generation INTEGER NOT NULL,
  occurrence_count INTEGER NOT NULL,
  clause_count INTEGER NOT NULL,
  coverage_root TEXT NOT NULL,
  packet_path TEXT NOT NULL,
  packet_sha256 TEXT NOT NULL,
  published_at TEXT NOT NULL,
  UNIQUE(project_id, hour_bucket)
);

CREATE TABLE IF NOT EXISTS context_memberships (
  window_id TEXT NOT NULL REFERENCES review_windows(window_id) ON DELETE CASCADE,
  clause_id TEXT NOT NULL REFERENCES clauses(clause_id) ON DELETE CASCADE,
  disposition TEXT NOT NULL CHECK (disposition IN ('represented','superseded','not_due',
                                                   'non_actionable_context','unknown')),
  PRIMARY KEY(window_id, clause_id)
);

CREATE TABLE IF NOT EXISTS context_offers (
  offer_id TEXT PRIMARY KEY,
  window_id TEXT NOT NULL REFERENCES review_windows(window_id) ON DELETE CASCADE,
  session_hmac TEXT NOT NULL,
  offered_at TEXT NOT NULL,
  token_hmac TEXT NOT NULL UNIQUE,
  UNIQUE(window_id, session_hmac)
);

CREATE TABLE IF NOT EXISTS context_reads (
  read_id TEXT PRIMARY KEY,
  offer_id TEXT NOT NULL UNIQUE REFERENCES context_offers(offer_id) ON DELETE CASCADE,
  observed_at TEXT NOT NULL,
  packet_sha256 TEXT NOT NULL,
  byte_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS review_shards (
  window_id TEXT NOT NULL REFERENCES review_windows(window_id) ON DELETE CASCADE,
  shard_index INTEGER NOT NULL,
  shard_path TEXT NOT NULL,
  shard_sha256 TEXT NOT NULL,
  byte_count INTEGER NOT NULL,
  PRIMARY KEY(window_id, shard_index)
);

CREATE TABLE IF NOT EXISTS context_shard_reads (
  offer_id TEXT NOT NULL REFERENCES context_offers(offer_id) ON DELETE CASCADE,
  shard_index INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  shard_sha256 TEXT NOT NULL,
  byte_count INTEGER NOT NULL,
  PRIMARY KEY(offer_id, shard_index)
);

CREATE TABLE IF NOT EXISTS context_acknowledgements (
  acknowledgement_id TEXT PRIMARY KEY,
  offer_id TEXT NOT NULL UNIQUE REFERENCES context_offers(offer_id) ON DELETE CASCADE,
  acknowledged_at TEXT NOT NULL,
  occurrence_count INTEGER NOT NULL,
  packet_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_manifests (
  source_id TEXT PRIMARY KEY,
  size_bytes INTEGER NOT NULL,
  line_count INTEGER NOT NULL,
  prefix_hmac TEXT NOT NULL,
  cursor_seq INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL DEFAULT 0,
  observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runner_health (
  runner_id TEXT PRIMARY KEY,
  attempt_started_at TEXT,
  completed_at TEXT,
  status TEXT NOT NULL,
  error_code TEXT,
  next_retry_at TEXT
);

CREATE TABLE IF NOT EXISTS hourly_deferrals (
  hour_bucket TEXT PRIMARY KEY,
  reason_code TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS obligation_dispositions (
  disposition_id TEXT PRIMARY KEY,
  clause_id TEXT NOT NULL REFERENCES clauses(clause_id) ON DELETE CASCADE,
  state TEXT NOT NULL CHECK(state IN ('deferred','blocked','superseded')),
  reason_code TEXT NOT NULL,
  note_hmac TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classification_rebuilds (
  rebuild_id TEXT PRIMARY KEY,
  artifact_hmac TEXT NOT NULL UNIQUE,
  candidate_sha256 TEXT NOT NULL,
  prior_config_digest TEXT NOT NULL,
  target_config_digest TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  assignment_digest TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  eligible_event_count INTEGER NOT NULL,
  eligible_clause_count INTEGER NOT NULL,
  quarantined_event_count INTEGER NOT NULL,
  applied_at TEXT NOT NULL,
  cache_refreshed_at TEXT,
  cache_sha256 TEXT
);

CREATE INDEX IF NOT EXISTS idx_prompt_session ON prompt_events(session_hmac, ingest_seq DESC);
CREATE INDEX IF NOT EXISTS idx_clause_event ON clauses(event_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_clause_span ON clauses(span_id);
CREATE INDEX IF NOT EXISTS idx_disposition_event ON coverage_dispositions(event_id, span_id);
CREATE INDEX IF NOT EXISTS idx_assignment_project ON clause_assignments(project_id, clause_id);
CREATE INDEX IF NOT EXISTS idx_review_project ON review_windows(project_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_session ON action_events(session_hmac, observed_at DESC);
