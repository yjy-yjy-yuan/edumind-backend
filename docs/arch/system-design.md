# System Design

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 系统边界

| 层 | 责任 | 当前路径 |
|---|---|---|
| API 层 | HTTP 路由、请求校验、响应契约 | `app/routers/`, `app/schemas/` |
| 服务层 | 业务流程、外部服务适配、推荐/搜索/画面描述 | `app/services/` |
| 数据层 | SQLAlchemy ORM 模型与数据库连接 | `app/models/`, `app/core/database.py` |
| 任务层 | 视频处理、下载、向量索引、清理任务 | `app/tasks/` |
| 智能体层 | Learning Flow、治理网关、预算、轨迹 | `app/agents/` |
| 遥测层 | 结构化事件、告警、指标适配 | `app/analytics/`, `app/services/analytics/` |
| 运维脚本 | 初始化、迁移、验证、demo | `scripts/`, `migrations/`, `alembic/` |

## 主要请求流

| 流程 | 入口 | 核心处理 | 输出 |
|---|---|---|---|
| 视频管理 | `/api/videos`, `/api/video` | `video_api_service`, `video_processing` | 视频元数据、处理状态、软删除 |
| 字幕 | `/api/subtitles` | 字幕读取、编码兼容、导出 | VTT/字幕响应 |
| 问答/聊天 | `/api/qa`, `/api/chat` | Qwen/DeepSeek/OpenAI compatible 配置 | QA 或流式聊天 |
| 推荐 | `/api/recommendations` | 站内候选、站外候选、相似度融合 | 推荐视频列表 |
| 搜索 | `/api/search/*` | chunk、embedding、Chroma store、日志 | 语义/关键词检索 |
| Frame Description | `/api/frame_description` | Qwen3VL、Cloud Qwen-VL、字幕 fallback | 同步或流式画面描述 |
| Agent | `/api/agent` | governance gateway、learning flow | 智能体执行结果 |

## 关键非功能设计

| 主题 | 当前设计 |
|---|---|
| 鉴权 | Bearer token 推荐，兼容 `X-User-ID` / query user_id 的开发链路由配置控制 |
| CORS | `settings.CORS_ORIGINS` 控制，并暴露 trace/request headers |
| 安全头 | `SecurityHeadersMiddleware` 添加 nosniff、DENY、referrer policy、permissions policy |
| 启动恢复 | 重启时将中断的视频后台状态置为 failed，避免永久卡住 |
| 存储维护 | `storage_maintenance` 可按配置启动定期清理 |
