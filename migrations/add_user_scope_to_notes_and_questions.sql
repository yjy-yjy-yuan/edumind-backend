-- Add user scope to notes and questions to prevent cross-user data leakage.
-- Notes:
-- 1) Backfill note.user_id from videos.user_id when video_id exists.
-- 2) Backfill remaining orphan notes/questions to default user 1 for legacy compatibility.

ALTER TABLE notes ADD COLUMN user_id INT NULL;
UPDATE notes n
LEFT JOIN videos v ON n.video_id = v.id
SET n.user_id = COALESCE(v.user_id, 1)
WHERE n.user_id IS NULL;
ALTER TABLE notes MODIFY COLUMN user_id INT NOT NULL;
CREATE INDEX idx_notes_user_id ON notes(user_id);

ALTER TABLE questions ADD COLUMN user_id INT NULL;
UPDATE questions q
LEFT JOIN videos v ON q.video_id = v.id
SET q.user_id = COALESCE(v.user_id, 1)
WHERE q.user_id IS NULL;
ALTER TABLE questions MODIFY COLUMN user_id INT NOT NULL;
CREATE INDEX idx_questions_user_id ON questions(user_id);
