-- 用户系统(预埋 · 未开放)
-- 设计目标:
--   - 本地多用户 + 案件隔离
--   - SQLite users 表 + 密码 bcrypt hash + JWT token
--   - 数据隔离策略:files/folders/documents/sessions 全部加 owner_user_id 字段
--   - 单用户阶段(当前):owner_user_id 默认 1, 单 device admin
--   - 多用户阶段(v0.2.0):admin 用户自动建 + 注册开放

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE,
    password_hash TEXT NOT NULL,           -- bcrypt hash (cost=12)
    salt TEXT NOT NULL,                    -- 额外 salt 防 rainbow table
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user','viewer')),
    display_name TEXT,
    avatar_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    last_login_at INTEGER,
    last_login_ip TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- 会话 token 表(JWT refresh + 撤销列表)
CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,      -- SHA256(jwt), 不存明文
    token_type TEXT NOT NULL CHECK(token_type IN ('access','refresh')),
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER,
    user_agent TEXT,
    ip TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash);

-- 登录尝试日志(防爆破)
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT,
    success INTEGER NOT NULL,
    failure_reason TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_user_time ON login_attempts(username, created_at DESC);

-- ── 数据隔离:为现有核心表加 owner_user_id(v0.2.0 启用, 暂默认 NULL/0) ──
-- ALTER TABLE files ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
-- ALTER TABLE case_folders ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
-- ALTER TABLE chat_sessions ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
-- ALTER TABLE chat_messages ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
-- ALTER TABLE documents ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
-- ALTER TABLE templates ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
-- 注:SQLite 不支持 IF NOT EXISTS for column,放注释里待 v0.2.0 启用时执行