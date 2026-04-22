-- Migration: Add user_id field to videos table
-- Purpose: Support multi-user video isolation for semantic search

ALTER TABLE videos ADD COLUMN user_id INT NOT NULL DEFAULT 1 AFTER id;
ALTER TABLE videos ADD CONSTRAINT FK_video_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
