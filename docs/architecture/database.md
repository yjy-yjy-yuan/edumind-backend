# Database Architecture

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 数据库配置

| 项 | 当前值或来源 |
|---|---|
| 默认 URL | `mysql+pymysql://root:password@127.0.0.1:3306/edumind` |
| 配置入口 | `DATABASE_URL` in `app/core/config.py` |
| Session | `SessionLocal` in `app/core/database.py` |
| 自动建表 | `AUTO_CREATE_TABLES=false` 默认关闭 |

## ORM 模型

| 模型文件 | 领域 |
|---|---|
| `app/models/user.py` | 用户 |
| `app/models/video.py` | 视频、处理状态、软删除相关字段 |
| `app/models/subtitle.py` | 字幕 |
| `app/models/note.py` | 笔记 |
| `app/models/qa.py` | 问答 |
| `app/models/vector_index.py` | 语义索引状态 |
| `app/models/semantic_search_log.py` | 搜索日志 |
| `app/models/similarity_audit_log.py` | 相似度审计 |
| `app/models/recommendation_ops_event.py` | 推荐运营事件 |
| `app/models/agent_trajectory.py` | 智能体轨迹 |
| `app/models/task_checkpoint.py` | 任务 checkpoint |

## 迁移文件

| 文件 | 说明 |
|---|---|
| `alembic/versions/001_agent_tables.py` | agent 基础表 |
| `migrations/add_user_id_to_videos.sql` | 视频 user_id |
| `migrations/add_user_scope_to_notes_and_questions.sql` | notes/questions 用户隔离 |
| `migrations/add_video_soft_delete_and_user_rebind.sql` | 视频软删除与用户重绑定 |
| `migrations/add_semantic_search_logs.sql` | 语义搜索日志 |
| `migrations/add_similarity_audit_logs.sql` | 相似度审计日志 |
| `migrations/add_recommendation_ops_events.sql` | 推荐运营事件 |
