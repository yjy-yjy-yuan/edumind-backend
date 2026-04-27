# Multi-User Session Isolation Delivery (2026-04-26)

## Scope

本次交付聚焦「用户切换时数据隔离」：

- 切到用户 B 后，不应看到用户 A 的上传/处理/搜索/笔记数据
- 切回用户 A 后，应恢复看到 A 自己历史数据
- 开发态未登录场景支持默认用户邮箱兜底

## Implemented Changes

### 1) Unified user resolution

- 新增统一解析函数：`resolve_user_id_from_request`
- 优先级：Bearer > legacy user_id (`X-User-ID` / query) > 开发默认用户
- 新增配置：`DEV_DEFAULT_USER_EMAIL`

Files:

- `app/utils/auth_deps.py`
- `app/core/config.py`

### 2) Route-level user scoping

将以下路由收敛到当前用户域：

- 视频：`app/routers/video.py`
- 笔记：`app/routers/note.py`
- 问答：`app/routers/qa.py`
- 搜索：`app/routers/search.py`

### 3) Data model hardening

为跨用户隔离补齐持久化字段：

- `notes.user_id`
- `questions.user_id`

Files:

- `app/models/note.py`
- `app/models/qa.py`
- `app/agents/governance/tools_learning_flow.py`（学习流持久化笔记时绑定视频所属用户）

### 4) Migration script

新增 SQL 迁移：

- `migrations/add_user_scope_to_notes_and_questions.sql`

策略：

- 从 `videos.user_id` 回填 `notes/questions.user_id`
- 无法映射时回填 `1`（历史兼容）
- 增加索引 `idx_notes_user_id` / `idx_questions_user_id`

### 5) Tests

新增用户切换隔离回归：

- `tests/api/test_user_scope_isolation.py`

并更新既有测试夹具/样例构造：

- `tests/conftest.py`
- `tests/api/test_video_api.py`
- `tests/unit/test_models.py`

## Documentation Fixes

修正文档错误与不完整项：

- `tests/README.md` 中旧路径 `backend_fastapi/...` 已改为当前仓库真实路径
- 移除不存在的 `scripts/validate_backend_smoke.py` 引用
- 更新为当前验证链路：smoke + compileall + `validate_system_requirements.py`

## Validation Executed

本地执行并通过：

- `pytest tests/api/test_user_scope_isolation.py -q`
- `pytest tests/api/test_video_api.py -q`
- `pytest tests/api/test_qa_api.py -q`
- `pytest tests/unit/test_models.py -q`
- `PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app tests`

提交前/推送前 hooks 以 `.pre-commit-config.yaml` 为准，要求全部通过。


## Cloud Follow-up (2026-04-27)

### Additional fixes delivered on server

1. 视频删除从硬删除改为软删除
- 新增字段：`videos.is_deleted`、`videos.deleted_at`
- 列表/详情/播放/搜索均过滤 `is_deleted=true`
- 删除后保留数据库记录，满足审计与回溯需求

2. 上传鉴权链路一致化
- `/api/videos/upload` 与 `/api/videos/upload-url` 统一接入 `resolve_user_id_from_request`
- 兼容 Bearer + `X-User-ID` + `query user_id`

3. 云端用户归属修复
- 执行 `migrations/add_video_soft_delete_and_user_rebind.sql`
- 将视频/向量索引/搜索日志归并到默认账号 `2702965216@qq.com`（id=9）

4. 搜索索引重建
- 因 user_id 变更导致历史 collection 名不匹配，已批量重建 active 视频索引（user_id=9）

### Validation summary (server-side)

- 上传：`POST /api/videos/upload` 成功进入 processing
- 播放：`GET /api/videos/{id}/stream` 返回 200（存在源文件时）
- 删除：`DELETE /api/videos/{id}/delete` 返回 `soft_deleted=true`，且列表不可见、DB 记录保留
- 搜索：`POST /api/search/semantic/search` 返回非空结果
