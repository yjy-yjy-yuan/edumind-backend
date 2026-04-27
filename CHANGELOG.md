# 变更日志

> 说明：
> - 按日期倒序排列，记录每次功能、修复、文档等变更。
> - 每条变更描述涉及的模块路径、变更内容及影响。
> - 每项变更均标注具体文件路径，参考 EduMind 前端 CHANGELOG.md 格式。

---

## 2026-04-27

### 本地隔离验收与交互稳定性修复

- **backend**：新增运行域自检接口 `GET /api/ops/runtime-scope`，用于明确当前是否本地隔离运行（`scope_label`、`local_isolation_ok`）；涉及 `app/routers/ops.py`、`tests/smoke/test_app_startup.py`。
- **backend**：视频删除链路优化为“先软删立即返回、重清理异步执行”，并保留轻量关联数据同步清理，确保前端删除后可立即跳转与列表即时消失；涉及 `app/routers/video.py`、`app/tasks/video_cleanup.py`、`tests/api/test_video_api.py`。
- **backend**：实时画面描述新增推理前短超时探测（Vinci 不可达时快速降级），降低“连接迟迟不上”的体感；涉及 `app/core/config.py`、`app/services/frame_description_service.py`、`.env.example`、`tests/unit/test_frame_description_service.py`。
- **backend**：关键词搜索增强支持“标签部分内容命中”并参与排序融合，默认请求侧启用标签增强；涉及 `app/services/search/search.py`、`app/routers/search.py`、`app/schemas/search.py`、`tests/api/test_search_api.py`、`tests/unit/test_search_partial_tag_match.py`。
- **docs**：补充本地/云端隔离与验收文档并新增云端发布索引说明（通常无需重建向量库）；涉及 `docs/LOCAL_CLOUD_ISOLATION_AND_VERIFICATION_2026-04-27.md`、`README.md`。

### 完善语义索引清理与导出轨迹质量管理

- **backend**：更新 `app/services/search/vector_indexing.py`，确保视频软删除时同步清理 ChromaDB 语义索引。
- **backend**：更新 `app/services/search/similarity_fusion.py`，完善语义相似度融合逻辑。
- **backend**：新增 `app/compounding/export_service.py`，增量价值导出服务，支持轨迹质量检查与 PII 脱敏。
- **backend**：新增 `app/compounding/formats.py`，导出格式规范（JSON/CSV/Markdown 等）。
- **backend**：新增 `app/compounding/quality.py`，导出轨迹质量检查模块。
- **backend**：新增 `app/compounding/sanitization.py`，PII 脱敏处理模块。
- **backend**：更新 `app/tasks/video_processing.py`，强化临时音频文件处理失败时的清理逻辑，防止残留文件泄漏。

### 字幕导出修复与多用户隔离加固

- **backend**：修复 `app/routers/subtitle.py` 导出接口，处理非 ASCII 文件名的 RFC5987 编码，修复 VTT 格式中文字符集问题。
- **backend**：更新 `app/services/storage_maintenance.py`，新增运行时残留文件与过期临时产物自动清理机制。
- **backend**：完成多用户会话隔离改造，统一在 `app/routers/`、`app/services/` 各模块加上用户 ID 过滤，防止跨用户数据泄露。
- **backend**：更新 `app/utils/auth_deps.py`，统一认证依赖注入，隔离用户上下文。

---

## 2026-04-26

### 视频压缩与存储清理增强

- **backend**：新增 `app/services/storage/compression.py`，视频压缩功能，优化存储占用。
- **backend**：新增 `app/services/storage/cleanup.py`，运行时残留文件与过期临时产物自动清理。
- **backend**：更新 `app/tasks/video_processing.py`，集成视频压缩流水线。

### 多用户隔离集成

- **backend**：更新 `app/routers/` 各路由（`videos.py`、`notes.py`、`subtitles.py`、`qa.py` 等），统一加上用户 ID 过滤。
- **backend**：更新 `app/services/` 各服务，传递 user_id 参数，确保数据隔离。
- **backend**：新增 Alembic 迁移 `migrations/versions/xxxx_add_user_id.py`，完成用户隔离数据结构改造。

---

## 2026-04-24

### Vinci 帧描述联调（M4 阶段）

- **backend**：新增 `tests/unit/test_m4_frontend_proxy_contract.py`，M4 前端代理契约测试。
- **backend**：新增 `tests/unit/test_m4_integration_docs.py`，M4 集成文档契约测试。
- **backend**：新增 `docs/VINCI_M4_INTEGRATION.md`，Vinci M4 联调文档。
- **backend**：更新 `scripts/demo_frame_description.py`，修复 demo 脚本中流式 httpx 响应的解析问题。

---

## 2026-04-23

### Vinci 帧描述流水线（M3 告警与 M2 断路器）

- **backend**：新增 `app/agents/governance/gateway.py`，治理网关，强制帧描述请求经治理管道。
- **backend**：新增 `app/agents/governance/service.py`，治理服务，校验请求权限与合规性。
- **backend**：新增 `app/agents/governance/audit.py`，治理审计日志，记录所有治理决策。
- **backend**：新增 `app/agents/pipelines/learning_flow.py`，学习流编排，含断路器（circuit breaker）机制，Vinci 异常时优雅降级。
- **backend**：新增 `app/analytics/alerting/rules.py`，Vinci 告警规则模板。
- **backend**：新增 `app/analytics/alerting/templates/`，告警通知模板（email/slack/webhook）。
- **backend**：新增 `app/analytics/pipeline.py` 指标端点，暴露 Vinci 可观测性指标。
- **backend**：新增 `app/agents/budget.py`，Token 预算管理服务。
- **backend**：新增 Alembic 迁移 `migrations/versions/xxxx_add_agent_prompt_skill.py`，agent 基础数据模型（prompt/skill/trajectory）。
- **backend**：更新 `app/agents/`，新增断点续跑（resumable）基础能力。
- **backend**：更新 `docs/VINCI_M1_M2_M3.md`，同步 M1-M3 交付文档。

---

## 2026-04-22

### Vinci M1 集成与治理基础

- **backend**：新增 `app/agents/vinci/adapter.py`，Vinci Adapter 基线，支持帧描述请求与治理校验。
- **backend**：新增 `app/agents/vinci/client.py`，Vinci HTTP 客户端封装。
- **backend**：新增 `app/agents/vinci/models.py`，Vinci 请求/响应 Pydantic 模型。
- **backend**：更新 `app/agents/governance/service.py`，强制 governance 路径覆盖 Vinci 帧描述调用。
- **backend**：更新 `docs/VINCI_M1.md`，Vinci M1 基线文档。
- **backend**：更新 `scripts/validate_backend_smoke.py`，新增 governance 路径校验。
- **backend**：更新 `scripts/hooks/pre-commit.py`，加固 hooks 管道可靠性检查。
- **backend**：更新 `app/routers/search.py`，统一搜索认证一致性，防止未授权访问。

---

## 2026-04-21

### 初始版本上线

- **backend**：初始提交 `cad1c23`，EduMind Backend FastAPI 服务，包含：
  - `app/main.py` — FastAPI 应用入口，lifespan 管理，中间件注册
  - `app/core/config.py` — Pydantic Settings 配置单例
  - `app/core/database.py` — SQLAlchemy 2.0 数据库连接
  - `app/core/executor.py` — ProcessPoolExecutor 后台任务调度
  - `app/routers/` — HTTP 路由层（videos、subtitles、notes、qa、chat、auth、recommendations、design、agent、search）
  - `app/schemas/` — Pydantic 请求/响应模型
  - `app/models/` — SQLAlchemy 2.0 ORM 模型
  - `app/services/` — 业务逻辑层（search、analytics、storage 等）
  - `app/tasks/` — 后台任务（video_processing、vector_indexing、video_download）
  - `app/utils/` — 通用工具（auth_deps、auth_token、chat_system、semantic_utils）
  - `app/repositories/` — 数据访问层
  - `app/analytics/` — 集中式遥测管道
  - `app/agents/` — 智能体编排（governance、pipelines、budget）
  - `tests/` — 单元测试、API 测试、烟雾测试、集成测试
  - `migrations/` — Alembic 数据库迁移文件
  - `scripts/` — 运维与验证脚本
  - `docs/` — 部署与实现文档
