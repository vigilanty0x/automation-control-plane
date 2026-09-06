
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    spec_hash TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    state TEXT NOT NULL,
    kill_switch INTEGER NOT NULL DEFAULT 0 CHECK (kill_switch IN (0, 1)),
    failure_reason TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    max_attempts INTEGER NOT NULL,
    max_wall_seconds REAL NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    event_head_hash TEXT NOT NULL DEFAULT '0000000000000000000000000000000000000000000000000000000000000000'
);
CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_attempt_at REAL,
    lease_owner TEXT,
    lease_expires_at REAL,
    result_json TEXT,
    error TEXT,
    started_at REAL,
    finished_at REAL,
    PRIMARY KEY (run_id, task_id)
);
CREATE INDEX IF NOT EXISTS tasks_schedulable
    ON tasks(run_id, state, sort_order, next_attempt_at);
CREATE TABLE IF NOT EXISTS dependencies (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id, dependency_id),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, dependency_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    task_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    event_key TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    UNIQUE(run_id, event_key)
);
CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id, sequence);
CREATE TABLE IF NOT EXISTS receipts (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    receipt_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, task_id, attempt),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
);
