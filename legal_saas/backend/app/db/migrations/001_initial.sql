-- ═══════════════════════════════════════════════════
-- 法律 SaaS · 初始化 DDL · 12 张表
-- ═══════════════════════════════════════════════════
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

-- 1. 会话表
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('normal', 'case_gen')),
    role TEXT NOT NULL CHECK(role IN ('legal_expert', 'litigator', 'corp_counsel', 'contract_specialist')),
    system_prompt TEXT,
    model TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 2. 消息表
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    model TEXT,
    parent_id INTEGER REFERENCES chat_messages(id),
    created_at INTEGER NOT NULL
);

-- 3. 案件文件夹
CREATE TABLE IF NOT EXISTS case_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    case_number TEXT,
    description TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 4. 本地文件索引
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id INTEGER REFERENCES case_folders(id) ON DELETE SET NULL,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mime TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','parsing','indexed','failed')),
    chunk_count INTEGER DEFAULT 0,
    indexed_at INTEGER,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 5. 文件分片
CREATE TABLE IF NOT EXISTS file_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    vector_id TEXT,
    metadata TEXT,
    UNIQUE(file_id, chunk_index)
);

-- 6. 模板
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    variables TEXT NOT NULL,
    description TEXT,
    version INTEGER DEFAULT 1,
    is_builtin INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 7. 文书归档
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    case_name TEXT,
    doc_type TEXT NOT NULL,
    content TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'docx',
    source_files TEXT,
    source_session INTEGER REFERENCES chat_sessions(id),
    statutes TEXT,
    risk_tags TEXT,
    folder_id INTEGER REFERENCES case_folders(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 8. 纠错记录
CREATE TABLE IF NOT EXISTS document_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    session_id INTEGER REFERENCES chat_sessions(id) ON DELETE SET NULL,
    issue_type TEXT NOT NULL,
    original TEXT NOT NULL,
    corrected TEXT NOT NULL,
    note TEXT,
    severity TEXT DEFAULT 'medium' CHECK(severity IN ('low','medium','high')),
    created_at INTEGER NOT NULL
);

-- 9. 法条缓存
CREATE TABLE IF NOT EXISTS statutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    fetched_at INTEGER NOT NULL
);

-- 10. 系统配置(密钥加密)
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value_encrypted TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 11. 异步任务
CREATE TABLE IF NOT EXISTS async_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed','cancelled')),
    progress INTEGER DEFAULT 0,
    payload TEXT,
    result TEXT,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 12. 操作日志
CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL
);