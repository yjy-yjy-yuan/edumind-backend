# Frontend Integration Notes

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 当前仓库边界

| 项目 | 说明 |
|---|---|
| 当前仓库 | 仅包含 EduMind FastAPI 后端 |
| 前端代码 | 不在当前仓库结构内；`CHANGELOG.md` 中曾记录 `mobile-frontend/src/views/Player.vue` 等跨仓库变更 |
| 后端对前端契约 | 通过 FastAPI routers 与 Pydantic schemas 暴露 |

## 前端主要对接点

| 场景 | 后端入口 | 注意事项 |
|---|---|---|
| 视频列表/详情/删除 | `/api/videos`, `/api/video` | 存在兼容旧路径；删除为软删除语义 |
| 字幕展示 | `/api/subtitles`, `/api/videos/{video_id}/subtitle` | 当前文档记录默认 VTT/UTF-8 输出 |
| 实时画面描述 | `/api/frame_description` | 支持会话、健康检查、同步/流式描述 |
| 推荐 | `/api/recommendations` | 返回契约版本受 `RECOMMENDATION_CONTRACT_VERSION` 控制 |
| 搜索 | `/api/search/*` | Bearer 优先，兼容开发身份传递 |
| 认证 | `/api/auth` | 使用 HMAC token 相关工具 |

## 风险记录

| 风险 | 影响 | 建议 |
|---|---|---|
| 前端仓库不在当前工作区 | docs 无法直接验证前端实现 | API 变更时在 PR 中附 request/response 样例 |
| 旧路径兼容存在 | 客户端可能继续调用历史前缀 | 保留兼容期并在 release notes 标注 |
