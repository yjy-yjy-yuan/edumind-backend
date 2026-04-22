-- 新增推荐运营事件日志表：recommendation_ops_events
-- 用途：为 /api/recommendations/ops/metrics 提供跨进程、可重启、可多 worker 对齐的聚合数据源

CREATE TABLE IF NOT EXISTS recommendation_ops_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    trace_id VARCHAR(128) NULL,
    metadata_json JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX ix_recommendation_ops_events_event_type (event_type),
    INDEX ix_recommendation_ops_events_trace_id (trace_id),
    INDEX ix_recommendation_ops_events_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
