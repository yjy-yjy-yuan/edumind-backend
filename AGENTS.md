# Repository Guidelines

## Project Structure & Module Organization
`app/` contains the FastAPI backend by layer: `routers/` (HTTP endpoints), `schemas/` (Pydantic models), `models/` (SQLAlchemy models), `services/` (business logic), `tasks/` (background jobs), `core/` (config, DB, executor), and `utils/` (shared helpers).  
`tests/` is the only test root and is split into `unit/`, `api/`, `smoke/`, and `integration/`.  
Use `migrations/` for SQL migration files and `scripts/` for operational/validation scripts. Runtime entrypoints are `run.py` (local) and `run_prod.py` (production-like).

## Build, Test, and Development Commands
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
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
black app/ tests/
isort app/ tests/
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
