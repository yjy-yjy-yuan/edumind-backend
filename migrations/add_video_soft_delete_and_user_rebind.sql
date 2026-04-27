-- Add soft-delete support for videos and rebind residual user-scoped data
-- Target default account: 2702965216@qq.com

START TRANSACTION;

SET @video_is_deleted_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'videos' AND COLUMN_NAME = 'is_deleted'
);
SET @sql := IF(
  @video_is_deleted_exists = 0,
  'ALTER TABLE videos ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @video_deleted_at_exists := (
  SELECT COUNT(*)
  FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'videos' AND COLUMN_NAME = 'deleted_at'
);
SET @sql := IF(
  @video_deleted_at_exists = 0,
  'ALTER TABLE videos ADD COLUMN deleted_at DATETIME NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @video_deleted_idx_exists := (
  SELECT COUNT(*)
  FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'videos' AND INDEX_NAME = 'idx_videos_is_deleted'
);
SET @sql := IF(
  @video_deleted_idx_exists = 0,
  'CREATE INDEX idx_videos_is_deleted ON videos(is_deleted)',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @target_user_id := (
  SELECT id FROM users WHERE email = '2702965216@qq.com' LIMIT 1
);

UPDATE videos SET user_id = @target_user_id WHERE user_id <> @target_user_id;
UPDATE vector_indexes SET user_id = @target_user_id WHERE user_id <> @target_user_id;
UPDATE vector_indices SET user_id = @target_user_id WHERE user_id <> @target_user_id;
UPDATE semantic_search_logs SET user_id = @target_user_id WHERE user_id <> @target_user_id;

COMMIT;
