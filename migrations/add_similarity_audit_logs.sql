-- 相似度审计日志持久化表
-- 对应 ORM 模型: app.models.similarity_audit_log.SimilarityAuditLogModel
--
-- 部署方式：
--   mysql -h 127.0.0.1 -u root -p edumind < backend_fastapi/migrations/add_similarity_audit_logs.sql
--
-- 回滚方式（若需要）：
--   mysql -h 127.0.0.1 -u root -p edumind -e "DROP TABLE IF EXISTS similarity_audit_logs;"

CREATE TABLE IF NOT EXISTS similarity_audit_logs (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '主键',
    trace_id VARCHAR(50) NOT NULL COMMENT '追踪 ID（便于回溯单次计算流程）',
    date_key VARCHAR(10) NOT NULL COMMENT '日期键（ISO 格式 YYYY-MM-DD，用于按天快速分组）',
    tag1 VARCHAR(255) NOT NULL COMMENT '第一个标签',
    tag2 VARCHAR(255) NOT NULL COMMENT '第二个标签',
    event_type VARCHAR(50) NOT NULL COMMENT '事件类型（similarity_call_start/similarity_call_success/similarity_call_failed 等）',
    provider VARCHAR(50) NOT NULL COMMENT '提供商（openai/ollama/fallback）',
    model VARCHAR(100) NULL COMMENT '使用的模型名称',
    prompt_version VARCHAR(50) NOT NULL DEFAULT 'v2' COMMENT '提示词版本',
    success BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否成功',
    score FLOAT NULL COMMENT '最终分值（0-1 或浮点范围）',
    score_raw TEXT NULL COMMENT '原始提取的分值字符串',
    score_normalized FLOAT NULL COMMENT '正规化后的分值',
    latency_ms FLOAT NOT NULL DEFAULT 0.0 COMMENT '总耗时（毫秒）',
    provider_latency_ms FLOAT NOT NULL DEFAULT 0.0 COMMENT '提供商调用耗时（毫秒）',
    parse_latency_ms FLOAT NOT NULL DEFAULT 0.0 COMMENT '解析耗时（毫秒）',
    parse_ok BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否解析成功',
    parse_error_type VARCHAR(50) NULL COMMENT '解析错误类型（若 parse_ok=FALSE）',
    retry_count INT NOT NULL DEFAULT 0 COMMENT '重试次数',
    retry_failed BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否经历了失败后的重试',
    fallback_reason VARCHAR(255) NULL COMMENT '降级原因（若使用了降级分值）',
    error_message TEXT NULL COMMENT '错误信息详情',
    extra_metadata JSON NULL COMMENT '扩展字段（自定义元数据）',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    INDEX idx_trace_id (trace_id),
    INDEX idx_date_key (date_key),
    INDEX idx_date_trace (date_key, trace_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
