# API Flow

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 请求处理链路

| 步骤 | 组件 | 说明 |
|---|---|---|
| 1 | FastAPI router | `app/main.py` 注册业务 router |
| 2 | Middleware | CORS、请求耗时日志、安全响应头 |
| 3 | Auth dependency | 业务 router 使用 `app/utils/auth_deps.py` 等工具解析身份 |
| 4 | Schema validation | `app/schemas/` 定义请求/响应模型 |
| 5 | Service | `app/services/` 执行业务逻辑或外部服务调用 |
| 6 | DB/session | `app/core/database.py` 提供 session，`app/models/` 映射数据 |
| 7 | Response | 返回 JSON、流式响应、文件/字幕等 |

## 关键 API 分组

| Router | Prefix | 说明 |
|---|---|---|
| `video.router` | `/api/videos`, `/api/video` | 视频管理与兼容路径 |
| `subtitle.router` | `/api/subtitles` | 字幕管理 |
| `note.router` | `/api/notes` | 笔记 |
| `qa.router` | `/api/qa` | 问答 |
| `chat.router` | `/api/chat` | 聊天 |
| `design.router` | `/api/design` | 设计助手 |
| `auth.router` | `/api/auth` | 用户认证 |
| `ops.router` | `/api/ops` | 运维观测 |
| `recommendation.router` | `/api/recommendations` | 视频推荐 |
| `search.router` | router 内自带路径 | 语义搜索 |
| `agent.router` | `/api/agent` | 学习流智能体 |
| `frame_description.router` | `/api/frame_description` | 实时画面描述 |
