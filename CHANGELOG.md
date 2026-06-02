# 变更日志

> 说明：
> - 按日期倒序排列，记录每次功能、修复、文档等变更。
> - 每条变更描述涉及的模块路径、变更内容及影响。
> - 每项变更均标注具体文件路径，参考 EduMind 前端 CHANGELOG.md 格式。

---

## 2026-06-02

### 视频软删除访问过滤补齐

- **backend**：更新 `app/routers/qa.py`、`app/routers/subtitle.py`、`app/routers/recommendation.py`、`app/routers/frame_description.py`、`app/routers/note.py`，为 QA、字幕、推荐 seed、帧描述和笔记关联视频查询补齐 `Video.is_deleted.is_(False)` 过滤，避免软删除视频继续通过非 video 核心守卫端点访问。
- **backend**：更新 `app/services/video/recommendation.py`、`app/services/search/search.py` 与 `app/routers/search.py`，推荐候选加载和语义搜索结果元数据回查排除软删除视频，并将显式搜索软删除视频的访问守卫对齐为 404，降低推荐/搜索结果泄漏已删除视频的风险。
- **tests**：更新 `tests/api/test_recommendation_api.py`、`tests/api/test_video_api.py`、`tests/api/test_qa_api.py`、`tests/api/test_frame_description_api.py` 与 `tests/api/test_search_api.py`，修正 0518 领域服务重构后的旧 import/mock 路径，并补充软删除访问回归测试。
- **impact**：已删除视频在 QA、字幕、笔记、帧描述、推荐和搜索链路中按软删除语义隐藏；字幕路由仍保持当前认证契约，本次仅补齐软删除过滤。
## 2026-06-01

### 推荐用户作用域隔离修复 + 实时画面描述链路参数收敛

- **backend**：更新 `app/routers/recommendation.py`、`app/services/video/recommendation.py`、`app/routers/video.py`，推荐候选增加 `user_id` 与 `is_deleted` 过滤，`related` 场景 seed 校验归属，修复跨用户视频泄漏风险。
- **backend**：更新 `scripts/init_db.py`，新增 `sync_user_scope_table_schema`，为 `notes`/`questions` 表补齐 `user_id` 字段与索引，补全历史数据的用户作用域基础。
- **backend**：更新 `app/services/frame_desc/source_extractor.py`、`app/core/config.py`、`.env.example`，收紧服务端抽帧超时与重试参数，新增 `FRAME_DESC_SERVER_FRAME_FETCH_MAX_ATTEMPTS`，降低长尾等待。
- **backend**：更新 `app/services/whisper/runtime.py`，在 MPS 模型加载失败时自动回退 CPU 重试，提升可用性。
- **tests**：更新 `tests/api/test_recommendation_api.py`，并新增 `tests/api/test_recommendation_user_scope.py`，覆盖推荐用户隔离、软删除隔离、related seed 归属校验与未登录访问行为。
- **ops**：新增 `scripts/worktree_manager.sh`，支持 worktree 列表、端口检查、创建与移除等本地并行开发辅助操作。
- **docs**：新增 `微博画面描述功能技术分析.md`，沉淀画面描述链路技术分析与问题排查信息。
- **repo hygiene**：更新 `.gitignore`，忽略本地 `backups/` 导出的 SQL 备份，避免误提交运行时数据。
- **impact**：推荐接口的多用户隔离一致性提升；实时描述抽帧链路失败恢复更快；本地并行开发运维成本降低。

---

## 2026-05-20

### 文档同步：Async 架构状态与已知阻塞点

- **docs**：更新 `CLAUDE.md`，新增完整 Async Architecture Status 章节，包含 5 类已知阻塞点（同步 HTTP、subprocess、sleep、admission 位置、无队列）、具体文件:行号表格、修复优先级排序、最终目标说明
- **docs**：更新 `AGENTS.md`，在 AI Serving Async Control 后新增 Async Architecture Status 子章节，精简版包含 5 个已知阻塞点、6 步修复优先级、最终目标
- **impact**：为后续 async 架构修复提供明确的代码级锚点和优先级指导，不影响任何运行时功能

### AI Serving 异步阻塞链路专项改造（Phase 1/2）

- **backend**：新增 `app/utils/ai_response_control.py`，引入 AI admission、event loop lag 监控、async upstream semaphore、硬超时与取消统计、滑动窗口熔断（含 `429/5xx` 状态码识别）、动态 budget 压缩（`1024 -> 256 -> 96`）。
- **backend**：更新 `app/main.py`，新增入口中间件 `AIAdmissionMiddleware`，在 `/api/qa/ask` 与 `/api/chat/completions` 进入业务链路前执行活跃请求/排队阈值检查，支持快速 `429/503` 与 `Retry-After`，并返回 admission 与 loop lag 响应头。
- **backend**：更新 `app/utils/qa_utils.py`，新增 `call_provider_chat_async` 与 `call_deepseek_reasoner_stream_async`（`httpx.AsyncClient`），并新增 `QASystem.ask_async`/`answer_stream_async` 供路由异步主链路调用；保留既有同步路径用于兼容存量调用点。
- **backend**：更新 `app/utils/chat_system.py`、`app/routers/chat.py`、`app/routers/qa.py`，将 chat/qa 主路径切换到 async 调用与 async streaming generator，避免 `requests` 在这两条主链路阻塞事件循环。
- **backend**：更新 `app/routers/ops.py`，新增 `/api/ops/ai-serving/metrics`，输出 admission、upstream、event loop 三类运行态指标，用于压测与灰度观测。
- **config**：更新 `app/core/config.py` 与 `.env.example`，新增 `AI_ADMISSION_*`、`AI_EVENT_LOOP_LAG_*`、`AI_REQUEST_HARD_TIMEOUT_SECONDS` 等参数，支持入口限流与超时止血策略。
- **tests**：新增 `tests/unit/test_chat_system.py`；更新 `tests/unit/test_qa_utils.py`，补充 async 调用路径用例，确保同步/异步路径回归可用。
- **impact**：在本地 ASGI 验收下，AI 主链路 `max_observed_active` 从历史压测的 `1` 恢复到 `8`，慢模型场景尾延迟从 `90s~150s` 降至亚秒级；但全仓库尚未完成 AsyncSession 与其他域同步 IO 清理，当前为“主链路止血 + 渐进迁移”状态。

---

## 2026-05-18

### 项目结构领域驱动重构（Deep Refactoring）

- **backend**：将 `app/services/` 从扁平结构重构为按业务域分组的目录结构：
  - `video/` — `video_content_service.py` → `content.py`，`video_api_service.py` → `api.py`，`video_processing_registry.py` → `processing_registry.py`，`video_url_import_service.py` → `url_import.py`，`video_recommendation_service.py` → `recommendation.py`，`external_candidate_service.py` → `external_candidate.py`
  - `frame_desc/` — `frame_description_service.py` → `service.py`，`frame_source_extractor.py` → `source_extractor.py`；`app/utils/frame_description_debug.py` → `app/services/frame_desc/debug.py`
  - `similarity/` — `similarity_analytics.py` → `analytics.py`，`similarity_service_container.py` → `service_container.py`，`similarity_audit_log_service.py` → `audit_log_service.py`，`similarity_score_parser.py` → `score_parser.py`
  - `recommendation/` — `recommendation_ops_service.py` → `ops_service.py`
  - `llm_clients/` — `qwen3vl_realtime_client.py` → `qwen3vl.py`，`qwen_vl_cloud_client.py` → `qwen_vl_cloud.py`，`vinci_client.py` → `vinci.py`，`vinci_adapter_service.py` → `vinci_adapter.py`，`ollama_runtime.py` → `ollama_runtime.py`
  - `whisper/` — `whisper_runtime.py` → `runtime.py`；`app/utils/whisper_debug.py` → `app/services/whisper/debug.py`
- **backend**：将 `app/services/learning_flow_agent.py` 移至 `app/agents/learning_flow_agent.py`，使其与智能体编排模块归位。
- **backend**：移除死代码：`app/dependencies.py`（从未被引用）、`app/services/analytics/`（纯转发门面，实际使用 `app/analytics/`）、`app/services/llm_similarity_service.py`（无调用方）、`app/services/tag_similarity_prompts.py`（仅被死代码引用）、`app/services/config_model_params.py`（仅被死代码引用）；`app/utils/vinci_alerting_acceptance.py` 移至 `app/services/llm_clients/vinci_alerting_acceptance.py`。
- **backend**：更新全部跨文件 import 语句，覆盖 `app/main.py`、`app/routers/`（agent, frame_description, recommendation, video）、`app/services/` 各域内文件、`app/agents/`（pipelines, governance）、`app/tasks/`（video_processing）、`app/utils/qa_utils.py`、`app/repositories/`、`app/analytics/adapters/` 等 30+ 文件。
- **docs**：更新 `CLAUDE.md` 架构图与关键模式，反映领域驱动分组与 dead code 清理结果。
- **docs**：更新 `AGENTS.md` 项目结构说明，按域详细列出 `services/` 子目录职责。
- **impact**：不改变任何运行时行为与 API 契约；代码组织更符合领域驱动设计（DDD），新开发者可按业务域快速定位代码；消除了 5 个死代码文件和 1 个冗余门面模块。

---

## 2026-05-13

### 智能问答 Provider 路由修复

- **backend**：更新 `app/routers/qa.py`，`chat_mode=direct` 不再强制改走 Qwen，而是保留请求中的 `provider`，使正式问答页选择 DeepSeek 普通模式时可正确路由到 DeepSeek。
- **backend**：更新 `app/utils/qa_utils.py`，普通问答按 provider 直接调用云端模型（Qwen -> `qwen-plus`，DeepSeek -> `deepseek-chat`），移除 QA 路径对本地 Qwen3-VL `/chat` 的探测与回退依赖。
- **backend**：扩展 DeepSeek 流式深度思考解析，兼容 `reasoning_content` 与旧的 `thinking_content` 字段。
- **impact**：智能问答的 Qwen 普通模式、DeepSeek 普通模式、DeepSeek Reasoner 与流式深度思考链路在云端可区分且可用；Qwen3-VL 继续仅作为画面描述相关服务，不再影响 QA 普通回答延迟与错误路径。

---

### Whisper 运行时诊断日志与敏感环境文件忽略修复

- **backend**：新增 `app/utils/whisper_debug.py`，提供独立 Whisper DEBUG 文件日志记录器；更新 `app/services/whisper_runtime.py`，在设备检测、模型加载、缓存命中、转录成功/失败、MPS 回退 CPU 与启动预热路径写入可选诊断日志。
- **config**：更新 `app/core/config.py` 与 `.env.example`，新增 `WHISPER_DEBUG_LOG`、`WHISPER_DEBUG_LOG_FILE`，默认关闭，仅在排查模型加载或转录问题时开启。
- **repo**：更新 `.gitignore`，修复 `.env`、`.env.local`、`.env.cloud` 规则因行内注释失效的问题，避免云端/本地敏感配置文件被误加入提交；保留已跟踪的 `COMMIT_LOG.md` 与 `CHANGELOG.md` 可正常同步。
- **docs**：更新 `docs/reference/env.md`，补齐 Whisper 模型目录、预热、加载/下载超时和 DEBUG 日志相关环境变量。
- **tests**：更新 `tests/unit/test_whisper_runtime.py`，覆盖 Whisper debug logger 使用配置路径且不重复添加同一文件 handler 的行为。
- **impact**：排查 Whisper 模型下载、加载、设备选择或转录失败时，可通过独立日志收集细节；环境文件会被 Git 正确忽略，降低误提交密钥风险。

---

### 暂停 YouTube 与中国大学慕课链接上传链路

- **backend**：更新 `app/services/video_url_import_service.py`，在远程视频链接来源识别阶段直接拦截 `youtube.com`、`youtu.be` 与 `icourse163.org`，返回明确的 400 提示，不再创建视频记录，也不再提交下载和处理任务。
- **backend**：更新 `app/schemas/video.py`，将 `VideoUploadURL.url` 的说明收敛为当前仅支持 B站链接，避免 API 文档继续暗示 YouTube / 中国大学慕课可上传。
- **tests**：更新 `tests/api/test_video_api.py`，将推荐元数据持久化用例改为 B站链接，并新增 YouTube / 中国大学慕课链接被拒绝且不会提交后台任务的回归测试。
- **impact**：视频链接上传阶段当前只保留 B站入口；YouTube 与中国大学慕课相关下载修复、代理配置和验证脚本不再进入本次变更范围。

---

## 2026-05-11

### 中文字幕乱码链路收敛（UTF-8-SIG + fallback decode + mojibake 修复）

- **backend**：更新 `app/utils/subtitle_io.py`，统一字幕读取为中文友好的 fallback decode（`utf-8`/`utf-8-sig`/`gb18030`/`gbk`/`utf-16`）并增加 mojibake 修复评分与优选逻辑，修复 `ä¸­æ...`、`鑰佸笀...` 一类编码乱码。
- **backend**：更新 `app/routers/video.py`，`GET /api/videos/{id}/subtitle` 及 `format=txt|srt` 导出统一 `utf-8-sig` 输出，响应头显式 `charset=utf-8`，返回体携带 BOM（`efbbbf`）。
- **backend**：更新 `app/routers/subtitle.py`，`/api/subtitles/videos/{id}/subtitles`、`/export`、`/semantic-merged` 全链路加入乱码修复，导出统一 BOM，并修复中文文件名 `Content-Disposition` 兼容。
- **backend**：更新 `app/services/video_content_service.py`、`app/utils/qa_utils.py`，摘要/标签/QA-RAG 读取字幕时改为统一 fallback decode，消除 `encoding="utf-8"` 单路径读取带来的隐性乱码风险。
- **backend**：更新 `app/services/frame_description_service.py`、`app/tasks/video_processing.py`，字幕 fallback 文本拼接与离线转录入库路径统一应用 mojibake 自动修复，避免错误文本继续扩散。
- **tests**：新增 `tests/unit/test_subtitle_io.py`；更新 `tests/api/test_video_api.py`、`tests/api/test_frame_description_api.py`、`tests/unit/test_qa_utils.py`、`tests/unit/test_video_content_service.py`，覆盖 BOM 输出、fallback decode 与乱码修复行为。
- **docs**：更新 `README.md`、`docs/architecture/frontend.md`、`docs/guides/troubleshooting.md`、`docs/summaries/session-history.md`，修正文档中“默认 UTF-8”这类不完整描述，统一为“字幕文本链路输出 UTF-8-SIG（含 BOM）”。
- **impact**：字幕“编码乱码”主路径已收敛；若仍出现 `平司边形`、`减几处`、`去球` 等文本，属于 ASR/Whisper 识别误差而非编码问题。

### 本地/云端可删除隔离验证环境

- **ops**：新增本地可删除隔离目录 `/Users/yuan/final-work/edumind-local-isolation`，提供独立 SQLite、独立上传目录、独立端口（backend `2104` / frontend `5174`）与独立环境变量。
- **ops**：新增隔离验证脚本 `run_backend.sh`、`run_frontend.sh`、`verify_subtitle_encoding.sh`、`cleanup.sh`，可一键验证字幕接口 BOM、charset 与乱码修复链路，不污染云端与本地主环境。
- **impact**：排查字幕乱码时可稳定复现并快速回归；隔离环境支持整目录删除，运维风险可控。

---

## 2026-05-09

### 初始化长期项目日志与全局上下文入口

- **docs**：新增 `docs/INDEX.md`，作为全局上下文入口、项目状态面板、Prompt Registry、Session 恢复中心和文档导航。
- **docs**：新增 `docs/MILESTONES.md`，汇总当前后端里程碑、状态和风险。
- **docs**：新增 `docs/arch/` 与 `docs/architecture/`，记录 ADR、系统设计、技术栈、后端分层、前端对接边界、数据库结构和 API 流程。
- **docs**：新增 `docs/features/`、`docs/bugs/`、`docs/testing/`、`docs/updates/`，建立功能、Bug、测试、发布变更的长期审计日志。
- **docs**：新增 `docs/guides/`、`docs/reference/`、`docs/prompts/`、`docs/summaries/`、`docs/legacy/`，沉淀 setup/deploy/release/troubleshooting、命令/环境/依赖约定、Prompt、session 历史和 legacy 迁移说明。
- **docs**：更新 `docs/prompts/workflow.md` 与 `docs/summaries/session-history.md`，固化“继续/恢复工作”必须先读取 `docs/INDEX.md`、功能完成必须补齐 changelog/session/feature/INDEX 引用等规则。
- **impact**：本次不修改运行时代码；后续代码修改、功能开发、Bug 修复、架构调整和发布都需要同步维护 `docs/INDEX.md` 与对应日志，否则视为开发未完成。

---

## 2026-05-08

### Frame Description Qwen3VL Cloud Fallback 与文档同步

- **backend**：新增 `app/services/qwen_vl_cloud_client.py`，通过 DashScope/OpenAI-compatible 接口调用 Cloud Qwen-VL；本地 `Qwen3VLRealtimeClient` 不可用时，按配置进入 `cloud_qwen_vl` 补偿层。
- **backend**：更新 `app/services/frame_description_service.py`，将 fallback 链调整为 `Local Qwen3VL -> Cloud Qwen-VL API -> Caption Fallback -> Minimal Safe Response`；空帧统一允许以 `base64_frames=[]` 进入 Qwen3VL/Cloud 文本模式。
- **backend**：更新 `app/agents/governance/tools_learning_flow.py`，Learning Flow 的同步与流式 frame description 路径在 `FRAME_DESC_BACKEND=qwen3vl` 下先走 Qwen3VL，失败后可进入 Cloud Qwen-VL，不再默认回落到 Vinci。
- **backend**：更新 `app/core/config.py`、`.env.example`、`app/main.py`，新增 `FRAME_DESC_CLOUD_*` 配置并在 `frame_description_debug` startup 日志中输出 cloud fallback 状态。
- **backend**：更新 `app/routers/frame_description.py`、`app/services/frame_source_extractor.py`、`app/utils/frame_description_debug.py`，保留独立 debug 文件日志、stream 403/抽帧失败日志，并使用 PNG 抽帧再转 JPEG 避免 ffmpeg MJPEG 编码失败。
- **docs**：新增 `docs/FRAME_DESCRIPTION_QWEN3VL_CLOUD_FALLBACK.md`；更新 `docs/FRAME_DESCRIPTION_RUNBOOK.md`、`docs/FRAME_DESCRIPTION_ACCEPTANCE.md`、`docs/FRAME_DESCRIPTION_ROLLBACK.md`、`README.md`，修正旧 Vinci 主路径、旧 ops endpoint 与降级链路表述。
- **tests**：更新 `tests/unit/test_frame_description_service.py`，覆盖空帧文本模式与本地 Qwen3VL 不可用时先使用 Cloud Qwen-VL 的行为。
- **impact**：本地 Qwen3VL 不稳定或未运行时，Frame Description 不会直接掉到“暂无字幕信息”；在显式开启 `FRAME_DESC_CLOUD_FALLBACK_ENABLED=true` 后，会先使用通义千问视觉 API 生成画面描述。Vinci 保持 legacy 兼容路径，仅在 `FRAME_DESC_BACKEND=vinci` 时使用。

---

## 2026-05-06

### 实时画面描述用户体验优化（移除"降级"字样）

- **backend**：更新 `app/services/frame_description_service.py`，移除所有"降级"相关显示文字。当画面描述服务不可用时，改为输出字幕内容，显示更友好的"字幕模式"提示。
- **backend**：简化 `app/utils/chat_system.py`，移除本地 Qwen 模型优先调用逻辑，保留云端 Qwen API 调用，适配云端部署场景。
- **frontend**：更新 `mobile-frontend/src/views/Player.vue`，删除"降级模式"徽章，将 `degraded` 状态改为 `subtitle`，移除描述文字中的"（降级）"前缀。
- **frontend**：更新 `mobile-frontend/src/api/frameDescription.js`，将超时提示从"已切换到降级描述"改为"已切换到字幕描述"。
- **docs**：删除本地/云端隔离验证相关文档 `docs/LOCAL_CLOUD_ISOLATION_AND_VERIFICATION_2026-04-27.md` 及相关脚本 `scripts/verify_local_cloud_isolation.py`。
- **tests**：删除 `tests/unit/test_local_cloud_isolation_verify.py`；更新 `tests/smoke/test_app_startup.py`。
- **impact**：用户在画面描述服务不可用时，看到的是自然的字幕内容，而非"降级模式"等提示，体验更流畅。

---

## 2026-05-04

### 实时画面描述 Qwen3VL 本地模型后端集成与隔离验证

- **backend**：新增 Qwen3-VL 实时画面描述微服务客户端 `app/services/qwen3vl_realtime_client.py`，支持同步描述与 SSE 流式描述，默认对接本地 `127.0.0.1:18082`。
- **backend**：重构 `app/services/frame_description_service.py`，支持 `qwen3vl` / `vinci` 双后端切换（配置项 `FRAME_DESC_BACKEND`），新增服务端视频帧抽取能力（`app/services/frame_source_extractor.py`），解决 iOS WKWebView `file://` 跨域采帧失败问题。
- **backend**：增强 Vinci 适配层 `app/services/vinci_adapter_service.py` 与原始客户端 `app/services/vinci_client.py`，统一错误码映射、SSE 事件标准化、独立熔断器与降级遥测。
- **backend**：扩展 `app/routers/frame_description.py` 路由，新增会话管理、三层健康检查（功能开关/服务实例/上游可达性）、NDJSON 流式响应。
- **backend**：治理网关 `app/agents/governance/gateway.py` 与学习流 `app/agents/governance/tools_learning_flow.py` 注册 `lf_frame_description` 工具，支持同步与流式调用。
- **backend**：`app/core/config.py` 新增 Qwen3VL 与 frame description 全量配置项；`app/main.py` 集成 frame description DEBUG 日志与生命周期管理。
- **backend**：`app/routers/ops.py` 移除硬运行时域自检接口，改为外部验证脚本方式，避免本地调试时配置加载失败。
- **scripts**：新增 `scripts/verify_local_cloud_isolation.py`，通过静态分析验证本地 `.env` 与前端配置未指向云端生产环境。
- **tests**：新增 `tests/unit/test_qwen3vl_realtime_client.py`、`tests/unit/test_local_cloud_isolation_verify.py`；扩展 `tests/api/test_frame_description_api.py`、`tests/unit/test_frame_description_service.py`、`tests/unit/test_vinci_adapter_service.py`。
- **docs**：更新 `docs/LOCAL_CLOUD_ISOLATION_AND_VERIFICATION_2026-04-27.md`，反映 `runtime-scope` 接口移除、新增 Qwen3-VL 本地模型后端说明；更新 `docs/FRAME_DESCRIPTION_RUNBOOK.md`，修正错误端点引用（`/api/ops/metrics` → `/api/frame_description/health`）、更新架构图与配置表以覆盖 Qwen3-VL 双后端架构；更新 `README.md` 移除已下线的运行时自检接口说明。
- **impact**：本地开发可独立使用 Qwen3-VL 模型进行实时画面描述，不与云端 Vinci 服务耦合；前端可通过配置切换后端或启用服务端抽帧兜底。

### 测试修复与配置清理

- **backend**：修复 `tests/unit/test_vinci_client.py` 中 mock `httpx.Client` 未接受 `trust_env` 关键字参数导致的测试失败。
- **backend**：修复 `tests/unit/test_video_processing_task.py` 因 `videos.user_id` 新增非空约束导致的插入失败。
- **backend**：修复 `app/tasks/video_processing.py` 中 `temp_audio_path` 在异常路径下可能触发 `UnboundLocalError` 的问题。
- **config**：修正 `.env` 中 `VINCI_ENABLED=true` 与 `FRAME_DESC_USE_VINCI_STREAM=true` 为 `false`，消除本地联调时 Vinci 不可达的日志噪音。

---

## 2026-04-27

### 实时画面描述降级可用性修复（Vinci 不可达时输出可读内容）

- **backend**：更新 `app/services/frame_description_service.py`，新增字幕驱动的降级描述生成逻辑。
- **backend**：当 Vinci 熔断打开、适配层降级或推理异常时，不再只返回“服务不可用”占位文案，改为基于当前时间点附近字幕片段生成可读实时描述（若存在字幕）。
- **impact**：在上游 Vinci 502/不可达场景下，前端实时描述可持续返回有内容文本，降低“连上但无内容”的失败体感。

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
