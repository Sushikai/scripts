-- flow 数据库 schema
-- 所有时间戳用 epoch ms,JSON 列用 TEXT + JSON1

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    tool_id TEXT NOT NULL,
    name TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',  -- JSON
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|cancelled
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'  -- JSON
);
CREATE INDEX IF NOT EXISTS idx_projects_tool ON projects(tool_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    step TEXT NOT NULL,  -- research|script|voice|material|compose|upload|style_diff 等
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0.0,  -- 0..1
    log_path TEXT,
    started_at INTEGER,
    finished_at INTEGER,
    artifacts TEXT NOT NULL DEFAULT '{}',  -- JSON
    error TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,  -- bilibili|douyin|youtube|local|tiktok
    url TEXT,
    path TEXT,
    hash TEXT,
    tags TEXT NOT NULL DEFAULT '[]',  -- JSON array
    meta TEXT NOT NULL DEFAULT '{}',
    used_in TEXT,  -- 关联 project_id
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_source ON assets(source);
CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets(hash);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    platform TEXT NOT NULL,  -- bilibili|douyin
    account TEXT,
    vid_id TEXT,  -- BV id 或 视频 id
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|success|failed
    error TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_uploads_platform ON uploads(platform);
CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(status);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    cookie_path TEXT NOT NULL,
    last_check_at INTEGER,
    status TEXT NOT NULL DEFAULT 'unknown',  -- ok|fail|unknown
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_accounts_platform ON accounts(platform);