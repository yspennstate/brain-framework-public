PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    label_hash TEXT NOT NULL,
    attrs_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    session_hash TEXT NOT NULL,
    task_hash TEXT,
    started_at REAL NOT NULL,
    attrs_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_ts REAL NOT NULL,
    observed_ts REAL NOT NULL,
    session_hash TEXT NOT NULL,
    actor_hash TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_ref_hash TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    features_json TEXT NOT NULL,
    clauses_json TEXT NOT NULL,
    attrs_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    FOREIGN KEY(episode_id) REFERENCES episodes(episode_id)
);
CREATE INDEX IF NOT EXISTS idx_events_session_time ON events(session_hash, event_ts, event_id);
CREATE INDEX IF NOT EXISTS idx_events_episode_time ON events(episode_id, event_ts, event_id);
CREATE INDEX IF NOT EXISTS idx_events_surface_time ON events(surface, event_ts);
CREATE INDEX IF NOT EXISTS idx_events_action_fp ON events(action_fingerprint, event_ts);

-- PROMPTS.jsonl contains collapsed prompt summaries, not occurrence-level
-- turns.  They live in a deliberately non-temporal, compact graph so every
-- valid ledger row remains queryable without manufacturing episodes, clauses,
-- PRECEDES_WITHIN edges, or candidate-model training windows.
CREATE TABLE IF NOT EXISTS prompt_aggregates (
    aggregate_id TEXT PRIMARY KEY,
    source_hash TEXT NOT NULL,
    source_ref_hash TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK(occurrence_count > 0),
    prompt_class TEXT NOT NULL,
    origin_kind TEXT NOT NULL,
    feature_count INTEGER NOT NULL CHECK(feature_count >= 0),
    link_count INTEGER NOT NULL CHECK(link_count >= 0),
    payload_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_aggregates_source ON prompt_aggregates(source_hash, aggregate_id);
CREATE INDEX IF NOT EXISTS idx_prompt_aggregates_class ON prompt_aggregates(prompt_class, origin_kind);

CREATE TABLE IF NOT EXISTS aggregate_feature_catalog (
    feature_id TEXT PRIMARY KEY,
    feature TEXT NOT NULL UNIQUE,
    feature_kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aggregate_feature_kind ON aggregate_feature_catalog(feature_kind, feature_id);

CREATE TABLE IF NOT EXISTS prompt_aggregate_members (
    aggregate_id TEXT NOT NULL,
    feature_id TEXT NOT NULL,
    role TEXT NOT NULL,
    occurrence_weight INTEGER NOT NULL CHECK(occurrence_weight > 0),
    PRIMARY KEY(aggregate_id, feature_id),
    FOREIGN KEY(aggregate_id) REFERENCES prompt_aggregates(aggregate_id) ON DELETE CASCADE,
    FOREIGN KEY(feature_id) REFERENCES aggregate_feature_catalog(feature_id)
);
CREATE INDEX IF NOT EXISTS idx_prompt_aggregate_members_feature
    ON prompt_aggregate_members(feature_id, aggregate_id);

CREATE TABLE IF NOT EXISTS prompt_aggregate_links (
    aggregate_id TEXT NOT NULL,
    src_feature_id TEXT NOT NULL,
    dst_feature_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    occurrence_weight INTEGER NOT NULL CHECK(occurrence_weight > 0),
    PRIMARY KEY(aggregate_id, src_feature_id, dst_feature_id, relation),
    FOREIGN KEY(aggregate_id) REFERENCES prompt_aggregates(aggregate_id) ON DELETE CASCADE,
    FOREIGN KEY(src_feature_id) REFERENCES aggregate_feature_catalog(feature_id),
    FOREIGN KEY(dst_feature_id) REFERENCES aggregate_feature_catalog(feature_id)
);
CREATE INDEX IF NOT EXISTS idx_prompt_aggregate_links_src
    ON prompt_aggregate_links(src_feature_id, relation, dst_feature_id);
CREATE INDEX IF NOT EXISTS idx_prompt_aggregate_links_dst
    ON prompt_aggregate_links(dst_feature_id, relation, src_feature_id);

CREATE TABLE IF NOT EXISTS episode_members (
    episode_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    role TEXT NOT NULL,
    event_ts REAL NOT NULL,
    PRIMARY KEY(episode_id, node_id, role),
    FOREIGN KEY(episode_id) REFERENCES episodes(episode_id),
    FOREIGN KEY(node_id) REFERENCES nodes(node_id)
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id TEXT PRIMARY KEY,
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    event_ts REAL NOT NULL,
    observed_ts REAL NOT NULL,
    weight REAL NOT NULL,
    confidence REAL NOT NULL,
    polarity INTEGER NOT NULL DEFAULT 1,
    source_ref_hash TEXT NOT NULL,
    episode_id TEXT,
    actor_hash TEXT,
    surface TEXT,
    evidence_quality TEXT NOT NULL DEFAULT 'observed',
    valid_from REAL NOT NULL,
    valid_to REAL,
    superseded INTEGER NOT NULL DEFAULT 0,
    model_version TEXT NOT NULL,
    attrs_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(src_id) REFERENCES nodes(node_id),
    FOREIGN KEY(dst_id) REFERENCES nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_src_relation_time ON edges(src_id, relation, event_ts);
CREATE INDEX IF NOT EXISTS idx_edges_dst_relation_time ON edges(dst_id, relation, event_ts);
CREATE INDEX IF NOT EXISTS idx_edges_episode_time ON edges(episode_id, event_ts);

CREATE TABLE IF NOT EXISTS warnings (
    trace_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    session_hash TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    family TEXT NOT NULL,
    score REAL NOT NULL,
    tier TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    remediation_code TEXT NOT NULL,
    model_version TEXT NOT NULL,
    created_at REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_event_id TEXT,
    resolution_ts REAL,
    FOREIGN KEY(event_id) REFERENCES events(event_id)
);
CREATE INDEX IF NOT EXISTS idx_warnings_session_time ON warnings(session_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_warnings_family_time ON warnings(family, created_at);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    session_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity REAL NOT NULL DEFAULT 0,
    warning_trace_id TEXT,
    adjudication TEXT NOT NULL DEFAULT 'observed',
    created_at REAL NOT NULL,
    attrs_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(event_id) REFERENCES events(event_id),
    FOREIGN KEY(warning_trace_id) REFERENCES warnings(trace_id)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_session_time ON outcomes(session_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_outcomes_warning ON outcomes(warning_trace_id);

CREATE TABLE IF NOT EXISTS family_state (
    session_hash TEXT NOT NULL,
    family TEXT NOT NULL,
    activation REAL NOT NULL,
    warning_burden REAL NOT NULL,
    last_ts REAL NOT NULL,
    last_fire_ts REAL,
    sequence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(session_hash, family)
);

CREATE TABLE IF NOT EXISTS source_manifest (
    source_hash TEXT PRIMARY KEY,
    path_hash TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    digest TEXT NOT NULL,
    cursor TEXT,
    status TEXT NOT NULL,
    redaction_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    last_ingested REAL NOT NULL,
    attrs_json TEXT NOT NULL DEFAULT '{}'
);
