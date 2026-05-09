# Coding Prompts

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 编码工作提示词

| 场景 | 提示词 |
|---|---|
| 新增后端功能 | 基于现有 `routers -> schemas -> services -> models/tasks` 分层实现，保持 user_id 隔离，补充对应测试与 docs 功能日志 |
| 修改配置 | 同步更新 `app/core/config.py`、`.env.example`、`docs/reference/env.md`，避免提交真实 secrets |
| 修改 API | 更新 Pydantic schema、API tests、`docs/architecture/api-flow.md`，发布时附 request/response 样例 |
| 修改任务 | 检查异常清理、幂等、重启恢复和运行时残留文件 |

## 完成功能后的日志要求

| 必填项 | 目标文件 |
|---|---|
| 修改文件 | `docs/features/completed.md` |
| 实现原因 | `docs/features/completed.md` |
| 风险 | `docs/features/completed.md` |
| 后续优化 | `docs/features/completed.md` |
| session 记录 | `docs/summaries/session-history.md` |
