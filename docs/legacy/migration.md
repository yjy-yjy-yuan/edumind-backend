# Migration Notes

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 已知迁移

| 日期 | 迁移 | 文件 | 影响 |
|---|---|---|---|
| 2026-04-27 | 视频软删除与用户重绑定 | `migrations/add_video_soft_delete_and_user_rebind.sql` | 删除语义从物理删除转为软删除 |
| 2026-04-27 | 推荐运营事件 | `migrations/add_recommendation_ops_events.sql` | 增加推荐运营聚合数据 |
| 2026-04-27 | 语义搜索日志 | `migrations/add_semantic_search_logs.sql` | 增加搜索日志记录 |
| 2026-04-27 | 相似度审计 | `migrations/add_similarity_audit_logs.sql` | 增加相似度审计记录 |
| 2026-04-26 | 用户隔离字段 | `migrations/add_user_id_to_videos.sql`, `migrations/add_user_scope_to_notes_and_questions.sql` | 数据按用户隔离 |
| 2026-04-23 | Agent tables | `alembic/versions/001_agent_tables.py` | 智能体 prompt/skill/trajectory 基础表 |

## 迁移风险

| 风险 | 建议 |
|---|---|
| 历史数据缺少 user_id | 执行迁移前准备 backfill 策略 |
| 软删除后索引残留 | 删除路径必须同步清理或过滤语义索引 |
| SQLite/MySQL 差异 | 本地测试与生产数据库需分别验证 |
