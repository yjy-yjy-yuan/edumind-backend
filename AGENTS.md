# Repository Guidelines

## Project Structure & Module Organization
`app/` contains the FastAPI backend organized by domain:
- `routers/` — HTTP endpoints
- `schemas/` — Pydantic models
- `models/` — SQLAlchemy models
- `services/` — business logic, grouped by domain:
  - `video/` — video content analysis, API serialization, processing registry, recommendation, URL import, external candidates
  - `frame_desc/` — frame description service, source extraction, debug logging
  - `similarity/` — similarity analytics, audit log, score parsing, persistence container
  - `recommendation/` — recommendation ops metrics
  - `llm_clients/` — LLM client wrappers (Qwen3VL, Qwen-VL Cloud, Vinci, Ollama runtime)
  - `whisper/` — Whisper runtime management and debug logging
  - `search/` — semantic search (embedder, chunker, store, similarity fusion)
  - `sleek_service.py` — design assistant
  - `storage_maintenance.py` — storage cleanup worker
- `tasks/` — background jobs (video_processing, vector_indexing, video_download, resumable_state_machine)
- `agents/` — agent orchestration:
  - `learning_flow_agent.py` — learning flow agent (moved from services/)
  - `governance/` — governance gateway, context, tool implementations
  - `pipelines/` — learning flow pipeline (Planner → Executor → Validator)
  - `prompt_engine.py` — token-aware prompt assembly
  - `skill_registry.py` — versioned skill registry with canary rollout
  - `trajectory.py` — system-level trajectory recorder
  - `budget.py` — token budget management
  - `prompts/` — prompt version constants
- `core/` — config, DB, executor
- `analytics/` — centralized telemetry pipeline
- `compounding/` — incremental value export (export, formats, quality, sanitization)
- `utils/` — cross-domain utilities (auth, chat, qa, subtitle IO, ollama compat, semantic utils)
- `repositories/` — data access layer

`tests/` is split into `unit/`, `api/`, `smoke/`, and `integration/`.
Use `migrations/` for SQL migration files and `scripts/` for operational/validation scripts. Runtime entrypoints are `run.py` (local) and `run_prod.py` (production-like).

## Build, Test, and Development Commands
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # or cp .env.local .env for local dev
python run.py
```
- Starts the API locally (default `127.0.0.1:2004`).

```bash
pytest tests/smoke/test_app_startup.py -v
mkdir -p .pycache-hook
PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts
python scripts/validate_system_requirements.py
```
- Preferred validation chain for changes in this repository.

`pytest` is kept for historical suites (for example `pytest tests/unit -v`) but is not the default verification path for routine changes.

## Coding Style & Naming Conventions
Use Python conventions: 4-space indentation, explicit type hints, and 120-character line width.
Format with:
```bash
black --line-length 120 app/ tests/
isort --profile black app/ tests/
```
Use `async def` for async route handlers, `snake_case` for modules/functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep imports ordered: stdlib, third-party, local.

## Testing Guidelines
Follow `pytest.ini` naming rules: files `test_*.py`, classes `Test*`, functions `test_*`.
Place tests by scope:
- `tests/unit/` for isolated logic
- `tests/api/` for route behavior and response contracts
- `tests/smoke/` for startup/minimal-path checks
- `tests/integration/` for multi-component flows

No fixed coverage threshold is enforced; add or update tests for every changed service, router, or task.

## Commit & Pull Request Guidelines
Recent history uses short imperative commit subjects (example: `Harden governance and analytics compliance`). Keep one logical change per commit.
**重要**: 每次提交后，必须将提交记录同步写入 `COMMIT_LOG.md`，按日期倒序排列，格式为日期 + commit hash + 提交信息。
**重要**: 每次提交后，必须将变更内容同步写入 `CHANGELOG.md`，按日期倒序排列，描述变更模块、涉及文件路径及影响范围（参考 EduMind 前端 CHANGELOG.md 格式）。
PRs should include: change summary, affected paths, config/migration impact, and exact validation commands run. For API changes, include sample request/response payloads and link the related issue.

## Security & Configuration Tips
Never commit secrets; keep `.env` local only. When adding config, update both `.env.example` and `app/core/config.py`. For schema changes, add a migration in `migrations/` and document rollout/backfill steps in the PR.

## Critical Agent Notes

### ProcessPoolExecutor Database Rule
Background tasks run in `ProcessPoolExecutor` — **you MUST create a new database connection inside each task function**. Do not share the main app's `SessionLocal` or `engine` across process boundaries.

```python
def my_task(video_id: int):
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        # ... work
        db.commit()
    finally:
        db.close()
```

### Env File Isolation
- `.env.example` — Git-tracked template
- `.env.local` — Git-ignored, local dev config
- `.env.cloud` — Git-ignored, cloud deploy config
- `.env` — Git-ignored, runtime config (copy from `.env.local` or `.env.cloud`)

Local dev: `cp .env.local .env`
Cloud deploy: `cp .env.cloud .env`

### Auth Strategy
Routes accept auth in this priority order:
1. `Authorization: Bearer <token>` (recommended)
2. Legacy `X-User-ID` header or `query user_id`
3. Dev fallback: `DEV_DEFAULT_USER_EMAIL` → then `user_id=1`

If a valid Bearer token is provided but is invalid, the route returns `401` — it will NOT silently fall back.

### Alembic Migrations
```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
```
SQL migrations also live in `migrations/` for manual application.

### Git Hooks (pre-commit)
Install: `bash scripts/setup_git_hooks.sh`
- `pre-commit`: isort → black → AST → YAML/TOML → secrets
- `commit-msg`: Conventional Commits format
- `pre-push`: mypy (core type boundaries) + curated unit tests

Manual run:
```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```
Skip (emergency only): `git commit --no-verify` / `git push --no-verify`

### Pre-push Mypy Scope
Mypy only checks: `app/utils/auth_token.py app/utils/auth_deps.py app/schemas`

### Pre-push Test Suite
Only these unit tests run on push:
`tests/unit/test_auth_token.py tests/unit/test_agent_budget.py tests/unit/test_agent_governance_gateway.py tests/unit/test_analytics_schema.py tests/unit/test_compounding_export.py`

### Python Version
Recommended: Python 3.11. Minimum: 3.9+. If using Chroma, ensure linked `sqlite3 >= 3.35.0`.

### Frame Description Backend Chain
Default chain: `Local Qwen3VL → Cloud Qwen-VL API → Caption Fallback → Minimal Safe Response`
- `FRAME_DESC_BACKEND=qwen3vl` (default)
- `FRAME_DESC_CLOUD_FALLBACK_ENABLED=false` (default off)
- Vinci is legacy, only used when `FRAME_DESC_BACKEND=vinci`

### Video Soft Delete
Videos use soft delete (`is_deleted` / `deleted_at`). Deleted videos are hidden from frontend lists but retained in DB. Search and video access filters exclude `is_deleted=true` rows.

### Streaming Responses
AI Q&A and chat use `StreamingResponse`. Never use plain `Response` for streaming endpoints.

### Validation Errors
FastAPI returns `422` for validation errors, not `400`.

### Analytics Telemetry
Use centralized pipeline:
```python
from app.analytics.pipeline import get_telemetry
from app.analytics.schema import AnalyticsEvent, AnalyticsStatus

get_telemetry().emit(AnalyticsEvent(...))
```

### Subtitle Encoding
- Read: fallback decode chain `utf-8 → utf-8-sig → gb18030 → gbk → utf-16` with mojibake auto-fix (`app/utils/subtitle_io.py`)
- Output: always `utf-8-sig` (with BOM) with explicit `charset=utf-8`
