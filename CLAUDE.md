# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

FastAPI + SQLAlchemy 2.0 + ProcessPoolExecutor 后端服务

## Bash Commands

```bash
# 启动服务
python run.py
uvicorn app.main:app --reload --port 2004

# 验证 (当前仓库首选验证链路)
pytest tests/smoke/test_app_startup.py -v
mkdir -p .pycache-hook && PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts
python scripts/validate_system_requirements.py

# 数据库迁移（Alembic）
alembic revision --autogenerate -m "描述"
alembic upgrade head

# 回滚
alembic downgrade -1

# 手动执行迁移（含 SQL 输出）
alembic upgrade head --sql

# 初始化 Alembic（新建项目时）
alembic init alembic
# 然后编辑 alembic.ini 指向 app.core.config.settings.DATABASE_URL

# 代码格式化
black app/ tests/
isort app/ tests/

# 数据库初始化
python scripts/init_db.py --create

# 语义搜索迁移
python scripts/migrations_semantic_search.py

# Compounding 导出
python scripts/export_compounding_trajectories.py

# 健壮性维护
python scripts/robust_maintenance.py
```

## Architecture Overview

```
app/
├── main.py              # FastAPI 应用入口，lifespan 管理，中间件注册
├── core/                # 核心基础设施
│   ├── config.py        # Pydantic Settings 配置单例
│   ├── database.py      # SQLAlchemy 2.0 连接，get_db 依赖
│   └── executor.py      # ProcessPoolExecutor 后台任务调度
├── routers/             # HTTP 路由层
├── schemas/             # Pydantic 请求/响应模型
├── models/              # SQLAlchemy 2.0 ORM 模型 (Mapped[] 类型注解)
├── services/            # 业务逻辑层（按领域分组）
│   ├── video/           # 视频领域（content, api, processing_registry, recommendation, url_import, external_candidate）
│   ├── frame_desc/      # 画面描述领域（service, source_extractor, debug）
│   ├── similarity/      # 相似度领域（analytics, service_container, audit_log_service, score_parser）
│   ├── recommendation/  # 推荐运营（ops_service）
│   ├── llm_clients/     # LLM 客户端（qwen3vl, qwen_vl_cloud, vinci, vinci_adapter, ollama_runtime）
│   ├── whisper/         # Whisper 运行时（runtime, debug）
│   ├── search/          # 语义搜索（embedder, chunker, store, similarity_fusion）
│   ├── sleek_service.py # 设计助手
│   └── storage_maintenance.py  # 存储维护
├── tasks/               # 后台任务 (video_processing, vector_indexing, video_download, resumable_state_machine)
├── agents/              # 智能体编排
│   ├── learning_flow_agent.py   # 学习流智能体编排（从 services/ 迁入）
│   ├── governance/      # 治理审计（gateway, context, tools_learning_flow）
│   ├── pipelines/       # 学习流编排（learning_flow_pipeline）
│   ├── prompt_engine.py # Token 感知提示词组装
│   ├── skill_registry.py# 技能注册与版本管理
│   ├── trajectory.py    # 轨迹记录器
│   ├── budget.py        # Token 预算管理
│   └── prompts/         # 提示词版本常量
├── analytics/           # 集中式遥测管道 (pipeline, alerting, adapters, schema)
├── compounding/         # 增量价值导出 (export_service, formats, quality, sanitization, report)
├── utils/               # 跨域通用工具 (auth_deps, auth_security, auth_token, ai_response_control, chat_system, ollama_compat, qa_utils, semantic_utils, subtitle_io)
└── repositories/        # 数据访问层 (similarity_audit_log_repository)
```

### Key Architectural Patterns

1. **分层架构**: Routers → Services → Repositories/Models
2. **依赖注入**: 使用 `Depends(get_db)` 注入数据库会话
3. **异步优先**: 路由使用 `async def`，后台任务通过 ProcessPoolExecutor 执行
4. **AI Serving 入口保护**: QA/Chat 主链路通过 `AIAdmissionMiddleware` 和 `ai_response_control` 做 admission、budget、熔断、fallback 与 event loop lag 观测
5. **流式响应**: AI 问答/聊天使用 `StreamingResponse`，async 路由内使用 async generator
6. **运行时封装**: Whisper/Ollama 通过 Runtime 类管理生命周期
7. **集中式遥测**: `app.analytics.pipeline.get_telemetry().emit()` 统一事件发布
8. **语义搜索**: 双后端工厂模式 (gemini/local) + ChromaDB 持久化
9. **领域驱动**: services/ 按业务域分组（video/, frame_desc/, similarity/, recommendation/, llm_clients/, whisper/）

## Code Style

- **行宽**: 120 字符
- **格式化**: black + isort
- **类型注解**: Pydantic v2 + SQLAlchemy 2.0 `Mapped[]`
- **Import 顺序**: stdlib → third-party → local

```python
# 路由示例
@router.get("/{video_id}")
async def get_video(video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    return video.to_dict()
```

## API Endpoints

| 路由前缀 | 说明 |
|---------|------|
| `/api/videos` | 视频上传、处理、列表、流式传输、URL 导入 |
| `/api/subtitles` | 字幕提取、语义合并、导出 |
| `/api/notes` | 笔记 CRUD、时间戳、批量操作 |
| `/api/qa` | AI 问答 (流式响应，支持 qwen/deepseek) |
| `/api/chat` | 聊天系统 (流式响应) |
| `/api/auth` | 用户注册、登录、信息更新、头像上传 |
| `/api/recommendations` | 视频推荐 (home/continue/review/related 场景，支持站外候选) |
| `/api/design` | 设计助手代理 (Sleek 集成) |
| `/api/agent` | 学习流智能体编排 |
| `/api/search` | 语义搜索 (视频/字幕语义召回) |
| `/api/ops/ai-serving/metrics` | AI Serving admission、upstream、event loop 观测指标 |

## Background Tasks

**IMPORTANT**: 使用 ProcessPoolExecutor 替代 Celery

```python
from app.core.executor import submit_task
from app.tasks.video_processing import process_video_task

# 提交后台任务
submit_task(process_video_task, video_id, language, model)

# 任务内创建独立数据库连接 (ProcessPoolExecutor 限制)
def process_video_task(video_id: int, language: str, model: str):
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    # ...
```

## Configuration

配置通过 `app/core/config.py` 的 `Settings` 类管理，支持 `.env` 文件。

### 环境文件隔离策略

```
.env.example  — Git 跟踪：统一配置模板（不含敏感值）
.env.local   — Git 忽略：本地开发配置（本地数据库、API 密钥等）
.env.cloud   — Git 忽略：云端部署配置模板
.env         — Git 忽略：运行时配置（由 .env.local 或 .env.cloud 复制而来）

# 本地开发
cp .env.local .env

# 云端部署
cp .env.cloud .env
# 或直接在部署平台设置环境变量
```

### 关键配置项:
- `DATABASE_URL`: MySQL 连接字符串
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL`: 本地 LLM 配置
- `OPENAI_API_KEY`, `QWEN_API_KEY`, `DEEPSEEK_API_KEY`: 外部 LLM 配置
- `AI_ADMISSION_*`, `AI_UPSTREAM_*`, `AI_*_MAX_TOKENS`: AI Serving admission、上游并发、超时与 token budget 配置
- `SLEEK_API_KEY`, `SLEEK_API_BASE`: 设计助手配置
- `SEARCH_ENABLED`, `SEARCH_BACKEND`: 语义搜索开关和后端
- `AGENT_GOVERNANCE_AUDIT_ENABLED`: 智能体治理审计
- `RECOMMENDATION_*`: 推荐系统配置
- `FRAME_DESC_BACKEND`: 实时画面描述后端（`qwen3vl` 优先，`vinci` 备选）
- `QWEN3VL_BASE_URL`: Qwen3-VL 视觉描述服务地址

## Development Environment

```bash
# 激活环境
conda activate ai-edvision

# 端口
# Backend: 2004
# Frontend: 328 / 5173
```

## Key Differences from Flask

| Flask | FastAPI |
|-------|---------|
| `@bp.route('/path', methods=['POST'])` | `@router.post('/path')` |
| `request.get_json()` | Pydantic Model 参数 |
| `jsonify({...})` | 直接返回 dict |
| `current_app.config['KEY']` | `settings.KEY` |
| `Response(generate())` | `StreamingResponse(generate())` |
| `Video.query.get(id)` | `db.query(Video).filter(Video.id == id).first()` |

## Testing

当前仓库首选验证链路:

```bash
pytest tests/smoke/test_app_startup.py -v
mkdir -p .pycache-hook && PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts
python scripts/validate_system_requirements.py
```

提交或推送前必须按 hooks 运行:

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

`pytest` 测试目录包含:
- `tests/unit/`: service、task、runtime、工具函数等单元测试
- `tests/api/`: FastAPI 路由与响应行为测试
- `tests/smoke/`: 启动与最小链路验证
- `tests/integration/`: 跨模块集成测试

## Warnings

- **提交日志**: 每次提交后，必须将提交记录同步写入 `COMMIT_LOG.md`，按日期倒序排列，格式为日期 + commit hash + 提交信息。
- **变更日志**: 每次提交后，必须将变更内容同步写入 `CHANGELOG.md`，按日期倒序排列，描述变更模块、涉及文件路径及影响范围（参考 EduMind 前端 CHANGELOG.md 格式）。

- **YOU MUST** 在任务函数内创建新的数据库连接 (ProcessPoolExecutor 限制)
- **YOU MUST** 使用 `Depends(get_db)` 注入数据库会话
- **AI Serving**: async 路由中的上游模型调用必须使用 async client、`asyncio.to_thread` 或独立 worker pool，禁止同步 IO 阻塞 event loop
- **流式响应**: 使用 `StreamingResponse`，不要用 `Response`；async route 中的 stream generator 应为 `async def`
- **验证错误**: 返回 422，不是 400
- **敏感信息**: 永远不要提交 `.env` 文件或 API 密钥到代码
- **Schema 变更**: 添加迁移文件并记录回填步骤

## Async Architecture Status (高并发 AI 服务 async 架构修复阶段)

### 当前已知阻塞点（必须优先解决）

**系统最大瓶颈是"同步 IO 阻塞 async 事件循环"，不是 CPU、SQLite 或 token。**

#### 1. 同步 HTTP 客户端在 async 路径中

| 文件 | 调用 | 上下文 |
|---|---|---|
| `app/utils/qa_utils.py:198,548` | `requests.post(...)` | DeepSeek/OpenAI 聊天 |
| `app/utils/semantic_utils.py:24,64,163` | `requests.get/post(...)` | Ollama 语义合并 |
| `app/services/video/content.py:546,575` | `requests.post(...)` | 在线聊天/Ollama |
| `app/services/video/external_candidate.py:450,502,588,631` | `requests.get(...)` | 站外候选抓取 |
| `app/services/llm_clients/qwen3vl.py:120,196,290` | `httpx.Client(...)` | Qwen3VL 帧描述 |
| `app/services/llm_clients/vinci.py:277,360,444,507` | `httpx.Client(...)` | Vinci 聊天/视觉 |
| `app/services/llm_clients/qwen_vl_cloud.py:147` | `httpx.Client(...)` | 云 Qwen-VL |
| `app/services/sleek_service.py:82` | `httpx.Client(...)` | 设计助手 |

**修复要求**: 改用 `httpx.AsyncClient` 或 `asyncio.to_thread` 或独立 worker pool

#### 2. 同步 subprocess 在请求路径

| 文件 | 调用 | 上下文 |
|---|---|---|
| `app/services/frame_desc/source_extractor.py:220` | `subprocess.run(cmd)` | **CRITICAL**: 帧提取直接阻塞 async 路由 |
| `app/services/search/chunker.py:30,78,99,247` | `subprocess.run(...)` | 视频分块（部分在 background task 可暂缓） |

**修复要求**: 改用 `asyncio.create_subprocess_exec` 或 `asyncio.to_thread`

#### 3. 同步阻塞调用

| 文件 | 调用 | 上下文 |
|---|---|---|
| `app/services/sleek_service.py:188` | `time.sleep(...)` | 轮询循环 |
| `app/services/similarity/audit_log_service.py:157` | `time.sleep(delay)` | 重试退避 |
| `app/services/video/external_candidate.py:711` | `time.sleep(0.25)` | 重试退避 |

**修复要求**: 改用 `asyncio.sleep`

#### 4. Admission Control 位置错误

当前 `AIResponseController` 使用 `threading.BoundedSemaphore`（同步原语），仅在 QA/chat 调用处包裹，**不覆盖**：
- 帧描述 (Qwen3VL/Vinci)
- 站外候选抓取
- Sleek API
- 语义操作
- 搜索/embedding

**修复要求**: Admission control 必须前移到 FastAPI/Starlette 中间件入口层

#### 5. 无显式队列系统

当前是"隐式 await 堆积"，请求直接进入业务链路后排队，导致 90s~150s 尾延迟。

**修复要求**: 实现显式队列，支持快速 429/503、最大等待时间、请求超时取消

### 修复优先级

1. **入口级 Admission Control** (风险最低、见效最快)
2. **LLM 客户端 async 化** (工作量最大、效果最显著)
3. **显式队列系统** (防止雪崩)
4. **subprocess async 化** (source_extractor.py 必须改)
5. **time.sleep → asyncio.sleep**
6. **DB session async 化** (工作量大，可最后做)

### 最终目标

- 不阻塞 event loop
- 不雪崩
- 不无限排队
- 快速失败、快速降级
- 保持服务可用

宁可返回短答案/降级结果/429，也不要等待 100 秒拖死整个系统。

## Analytics & Telemetry

使用集中式遥测管道:

```python
from app.analytics.pipeline import get_telemetry
from app.analytics.schema import AnalyticsEvent, AnalyticsStatus

get_telemetry().emit(
    AnalyticsEvent(
        event_type="video_upload_completed",
        trace_id=trace_id,
        module="video",
        status=AnalyticsStatus.OK.value,
        latency_ms=elapsed_ms,
        metadata={"video_id": video.id, "user_id": user.id},
    )
)
```

配置项: `ANALYTICS_LOG_LEVEL`, `ANALYTICS_ALERT_MAX_FAILURE_RATE`, `ANALYTICS_ALERT_LATENCY_TIMEOUT_MS`

## Semantic Search

架构: 双后端工厂 + ChromaDB 持久化
- `gemini`: 使用 Google Generative AI 的 embedding
- `local`: 本地 sentence-transformers 模型

关键配置:
- `SEARCH_ENABLED`: 功能开关
- `SEARCH_BACKEND`: gemini/local
- `SEARCH_CHROMA_DB_DIR`: ChromaDB 持久化路径
- `SEARCH_AUTO_INDEX_NEW_VIDEOS`: 自动索引新视频
- `SEARCH_INDEX_STARTUP_MODE`: 索引启动时机 (after_video_completed/inline_after_subtitle)

## Recommendation System

支持场景: home, continue, review, related
- 站内候选: 基于相似度和用户历史
- 站外候选: 可配置的外部 HTTP 抓取
- 自动入库: `RECOMMENDATION_AUTO_IMPORT_EXTERNAL` 对登录用户自动导入站外推荐

配置项:
- `RECOMMENDATION_MAX_CANDIDATES_SCAN`: 候选扫描上限
- `RECOMMENDATION_INCLUDE_EXTERNAL_DEFAULT`: 默认是否包含站外
- `RECOMMENDATION_SIMILARITY_MIN_SCORE`: 相似度下限
- `RECOMMENDATION_RETURN_MIN/MAX_ITEMS`: 返回条数窗口

## Agent System

智能体编排位于 `app/agents/`:
- `LearningFlowAgent`: 学习流编排
- `GovernanceService`: 治理审计
- `BudgetService`: Token 预算管理

配置:
- `AGENT_LEARNING_FLOW_TOKEN_BUDGET`: Token 预算上限
- `AGENT_GOVERNANCE_AUDIT_ENABLED`: 治理审计开关

## Compounding

增量价值导出位于 `app/compounding/`:
- `export_service.py`: 导出服务
- `formats.py`: 格式规范
- `quality.py`: 质量检查
- `sanitization.py`: PII 脱敏

配置:
- `COMPOUNDING_USER_ID_HASH_SALT`: 用户 ID 哈希盐
- `COMPOUNDING_*_MAX_CHARS`: 各字段最大长度
