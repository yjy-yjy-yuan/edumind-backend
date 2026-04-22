"""数据库迁移脚本 - 为语义搜索添加表和字段

执行方式：
1. 使用 Alembic（推荐）：
   cd backend_fastapi
   alembic revision --autogenerate -m "Add semantic search support"
   alembic upgrade head

2. 手动执行 SQL：
   mysql -u root -p edumind < scripts/migrations/semantic_search_migration.sql
"""

# SQL 迁移脚本内容
SQL_MIGRATION = """
-- 创建 vector_indexes 表
CREATE TABLE IF NOT EXISTS vector_indexes (
    id INT PRIMARY KEY AUTO_INCREMENT,
    video_id INT NOT NULL,
    user_id INT NOT NULL,
    collection_name VARCHAR(255) NOT NULL UNIQUE,
    chunk_count INT DEFAULT 0,
    embedding_backend VARCHAR(50) DEFAULT 'gemini',
    embedding_model VARCHAR(100),
    status ENUM('pending', 'processing', 'completed', 'failed') DEFAULT 'pending',
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    indexed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_video (video_id),
    INDEX idx_status (status),
    UNIQUE KEY unique_video_user (video_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 修改 videos 表，添加搜索相关字段
ALTER TABLE videos ADD COLUMN has_semantic_index BOOLEAN DEFAULT FALSE AFTER processing_origin;
ALTER TABLE videos ADD COLUMN vector_index_id INT DEFAULT NULL AFTER has_semantic_index;

-- 添加外键约束（可选）
ALTER TABLE videos ADD CONSTRAINT fk_video_vector_index
    FOREIGN KEY (vector_index_id) REFERENCES vector_indexes(id) ON DELETE SET NULL;

-- 创建索引以提高搜索性能
CREATE INDEX idx_video_has_semantic_index ON videos(has_semantic_index);
CREATE INDEX idx_vector_index_status ON vector_indexes(status);
CREATE INDEX idx_vector_index_indexed_at ON vector_indexes(indexed_at);
"""


def get_migration_sql() -> str:
    """获取迁移 SQL"""
    return SQL_MIGRATION
