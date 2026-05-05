# EduMind Backend (FastAPI)

`edumind-backend/` 是 EduMind 的独立后端仓库。

后端负责：认证、视频上传与处理、字幕、问答、笔记、推荐、语义搜索、智能体治理与遥测。

## 技术栈

| 类别 | 技术 |
|------|------|
| Web | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 |
| 数据校验 | Pydantic v2 |
| 后台任务 | 可配置执行器（开发默认 ThreadPool，生产建议 ProcessPool） |
| AI | Whisper、Ollama / 外部模型服务 |

## 快速开始

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

默认监听：

- `http://127.0.0.1:2004/health`
- `http://127.0.0.1:2004/docs`

`/health` 会返回 `database`、`whisper`、`ollama` 状态，其中 `ollama` 包含 `available`、`model`、`model_present`、`models`。

## 目录结构

| 路径 | 说明 |
|------|------|
| `app/main.py` | FastAPI 应用入口 |
| `app/core/` | 配置、数据库、执行器 |
| `app/models/` | ORM 模型 |
| `app/routers/` | 路由定义 |
| `app/schemas/` | 请求/响应模型 |
| `app/services/` | 业务服务 |
| `app/tasks/` | 后台任务 |
| `app/utils/` | 通用工具 |
| `docs/` | 运维与能力文档 |
| `scripts/` | 运维/迁移/验证脚本 |
| `tests/` | 后端测试根目录 |

## 关键能力

- 用户认证（邮箱/手机号 + 强密码）
- 视频上传、URL 导入、后台处理
- 字幕抽取与语义聚合
- QA 问答与聊天流式响应
- 推荐系统（含站内/站外候选）
- 语义搜索（索引、检索、状态查询）
- 智能体治理、预算控制、集中遥测、轨迹导出
- Qwen3-VL 实时画面描述（`app/services/qwen3vl_realtime_client.py`，本地模型默认后端）
- Vinci 微服务适配层（HTTP/SSE、统一错误码、降级与 trace_id 透传）
- Vinci 运维观测接口（`/api/ops/vinci/metrics`）

## Vinci 接入（M1）

已完成能力（M1-1/M1-2）：

- `app/services/vinci_client.py`：Vinci HTTP/SSE 客户端与超时/不可用/非 2xx 异常分类
- `app/services/vinci_adapter_service.py`：返回契约标准化、错误映射、降级路径、结构化遥测
- `app/core/config.py` 与 `.env.example`：Vinci 配置项对齐
- `tests/unit/test_vinci_adapter_service.py`：5 个核心契约测试
- `app/agents/governance/gateway.py` + `app/agents/governance/tools_learning_flow.py`：M1-3 治理接入（白名单、参数校验、审计、绕过阻断）

当前文档：[`docs/VINCI_INTEGRATION_M1.md`](docs/VINCI_INTEGRATION_M1.md)。

注意：

- M1-3/M1-4 已完成（治理接入 + 指标与 Runbook）。
- 运行处置指南见 [`docs/VINCI_RUNBOOK.md`](docs/VINCI_RUNBOOK.md)。

## 语义搜索说明

主要入口：

- `app/routers/search.py`
- `app/services/search/`
- `app/models/vector_index.py`
- `app/tasks/vector_indexing.py`

当前用户识别策略：

1. `Authorization: Bearer <token>`（推荐）
2. 兼容旧链路 `X-User-ID` / `query user_id`
3. 开发兜底：优先 `DEV_DEFAULT_USER_EMAIL` 对应用户；若未配置才回退 `user_id=1`

> 注意：若显式携带 Bearer 但 token 无效，接口会返回 `401`，不会再静默回退。

部署与限制详见 [`SEMANTIC_SEARCH_DEPLOYMENT.md`](SEMANTIC_SEARCH_DEPLOYMENT.md)。

## 验证链路（默认）

```bash
pytest tests/smoke/test_app_startup.py -v
mkdir -p .pycache-hook
PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts
python scripts/validate_system_requirements.py
```

说明：

- 日常改动首选上述验证链路。
- `pytest` 历史测试目录仍保留在 `tests/`，用于回归与 hook 的 pre-push 检查。

## Git Hooks

本仓库使用 `pre-commit` 管理 hooks。

### 安装

```bash
pip install pre-commit detect-secrets
bash scripts/setup_git_hooks.sh
```

或手动：

```bash
pre-commit install
pre-commit install --hook-type commit-msg
pre-commit install --hook-type pre-push
detect-secrets scan --baseline .secrets.baseline --exclude-files '\\.env$'
```

### Hook 覆盖范围

| Hook | 时机 | 检查项 |
|------|------|------|
| `pre-commit` | `git commit` 前 | isort / black / AST / YAML/TOML / secrets |
| `commit-msg` | `git commit` 时 | Conventional Commits |
| `pre-push` | `git push` 前 | `mypy`（核心类型边界）+ 精选稳定单元测试集 |

### 手动运行

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

### 跳过（仅紧急场景）

```bash
git commit --no-verify -m "message"
git push --no-verify
```

## 端口约定

| 服务 | 默认端口 |
|------|---------|
| Backend API | 2004 |

## 最近修正文档（2026-04-22）

- 对齐“独立后端仓库”定位，移除旧 `backend_fastapi/` 路径说明。
- 对齐语义搜索鉴权逻辑（Bearer 优先，兼容 `X-User-ID`）。
- 对齐 hook 文档与实际配置（pre-push 包含 mypy + unit tests）。
- 新增 Vinci M1 接入文档并对齐配置/测试入口（`docs/VINCI_INTEGRATION_M1.md`）。
- 同步 Vinci M1-3 治理接入文档（白名单、参数校验、审计与绕过阻断）。
- 新增 Vinci M1-4 可观测与运维文档（指标、阈值建议、Runbook）。


## Cloud Patch (2026-04-27)

- 新增视频软删除（`videos.is_deleted` / `videos.deleted_at`）：删除后前端列表不可见，但数据库保留记录。
- 删除接口改为软删除语义：清理媒体文件并置空路径，不再物理删除 `videos` 行。
- 搜索与视频访问过滤已删除视频（`is_deleted=false`）。
- 上传接口鉴权与其他路由一致：支持 Bearer、`X-User-ID`、`query user_id` 开发兼容链路。
- MySQL 迁移脚本：`migrations/add_video_soft_delete_and_user_rebind.sql`。

## Unified Deployment Patch (2026-04-27)

- 字幕读取增强编码回退（`utf-8/utf-16/gb*`），降低中文字幕乱码概率：
  - `app/utils/subtitle_io.py`
- 视频字幕接口默认输出改为 `vtt` 且 `utf-8` 响应：
  - `GET /api/videos/{video_id}/subtitle`
  - `app/routers/video.py`
- 删除视频接口新增兼容路径，适配不同客户端实现：
  - `DELETE /api/videos/{video_id}/delete`（原路径）
  - `DELETE /api/videos/{video_id}`（新增别名）
  - `DELETE /api/video/{video_id}/delete`（历史前缀兼容）
  - `app/main.py`, `app/routers/video.py`
- 实时画面描述新增稳定性开关（默认更偏向实时可见结果）：
  - `FRAME_DESC_SKIP_STABLE_SCENE=false`
  - `FRAME_DESC_ENABLE_CONTEXT_FUSION=false`
  - `app/core/config.py`, `app/services/frame_description_service.py`
- 关键词搜索新增可选标签增强排序（默认关闭，不影响线上现有行为）：
  - 请求参数：`include_tag_match`
  - 配置：`SEARCH_TAG_MATCH_ENABLED`、`SEARCH_TAG_MATCH_WEIGHT`
  - `app/schemas/search.py`, `app/routers/search.py`, `app/services/search/search.py`

### Cloud Rollout Note (Search)

- 本次“标签部分匹配增强”主要作用于检索排序融合层（`app/services/search/search.py`），
  **通常不需要**重建既有向量索引。
- 若历史视频本身尚未构建语义索引（`has_semantic_index=false`）或索引损坏，再按视频执行重建即可：
  - `POST /api/search/videos/{video_id}/index`
