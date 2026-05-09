# Backend Architecture

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 后端分层

| 层级 | 路径 | 说明 |
|---|---|---|
| App Entry | `app/main.py` | FastAPI app、lifespan、中间件、路由注册、健康检查 |
| Config | `app/core/config.py` | Pydantic Settings，集中管理环境变量 |
| Database | `app/core/database.py` | SQLAlchemy engine/session |
| Routers | `app/routers/` | HTTP endpoint；当前包括 video、subtitle、note、qa、chat、design、auth、ops、recommendation、search、agent、frame_description |
| Schemas | `app/schemas/` | Pydantic 请求/响应模型 |
| Models | `app/models/` | SQLAlchemy ORM 模型 |
| Services | `app/services/` | 业务服务与外部模型/搜索/推荐适配 |
| Tasks | `app/tasks/` | 后台处理、下载、向量索引、清理 |
| Utils | `app/utils/` | 鉴权、字幕、语义、调试等共享工具 |

## 中间件

| 中间件 | 当前行为 |
|---|---|
| `CORSMiddleware` | 使用 `settings.CORS_ORIGINS`，允许 credentials、全部 methods/headers |
| `RequestTimingMiddleware` | 记录 method、path、status、elapsed_ms；4xx warning，5xx error |
| `SecurityHeadersMiddleware` | 添加基础安全响应头，production 下添加 HSTS |

## 启动与关闭

| 阶段 | 行为 |
|---|---|
| Startup | 可选自动建表、恢复中断视频任务、初始化持久化服务、创建上传/字幕/预览/临时目录、启动存储维护、预加载 Whisper |
| Shutdown | 停止存储维护 worker，关闭 Whisper runtime |
