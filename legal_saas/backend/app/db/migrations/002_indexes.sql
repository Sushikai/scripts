-- 性能索引
CREATE INDEX IF NOT EXISTS idx_sessions_type_updated ON chat_sessions(type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);

CREATE INDEX IF NOT EXISTS idx_chunks_file ON file_chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_vector ON file_chunks(vector_id);

CREATE INDEX IF NOT EXISTS idx_templates_category ON templates(category);
CREATE INDEX IF NOT EXISTS idx_templates_builtin ON templates(is_builtin);

CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_name);
CREATE INDEX IF NOT EXISTS idx_documents_folder ON documents(folder_id);
CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(source_session);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);

CREATE INDEX IF NOT EXISTS idx_corrections_type ON document_corrections(issue_type, severity);
CREATE INDEX IF NOT EXISTS idx_corrections_doc ON document_corrections(document_id);

CREATE INDEX IF NOT EXISTS idx_statutes_category ON statutes(category);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON async_tasks(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_type ON async_tasks(type);

CREATE INDEX IF NOT EXISTS idx_logs_action_time ON operation_logs(action, created_at DESC);