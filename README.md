# EduMind Backend (FastAPI)

`backend_fastapi/` 是当前仓库的主后端。认证、视频、字幕、笔记、问答等新接口应优先落在这里。

## 技术栈

| 类别 | 技术 |
|------|------|
| Web | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 |
| 数据校验 | Pydantic v2 |
| 后台任务 | 可配置执行器（本地默认 ThreadPoolExecutor） |
| AI | Whisper、Ollama / 外部模型服务 |

## 快速开始

```bash
python3 -m venv .venv
. .venv/bin/activate
cd backend_fastapi
pip install -r requirements.txt
cp .env.example .env
python run.py
```

默认监听：

- `http://127.0.0.1:2004/health`
- `http://127.0.0.1:2004/docs`

端口和主机由 `.env` 中的 `HOST`、`PORT` 控制，默认值定义在 `app/core/config.py`。
如果你调整了前端端口，也要同步更新 `.env` 的 `CORS_ORIGINS`。

其中 `/health` 当前会返回：

- `database`
- `whisper`
- `ollama`

其中 `ollama` 会进一步给出 `available`、`model`、`model_present` 和 `models`，便于你直接确认本机后端是否已经感知到 Ollama 运行时，以及当前配置目标模型是否真的已经导入。

## 目录结构

| 路径 | 说明 |
|------|------|
| `app/main.py` | FastAPI 应用入口 |
| `app/core/` | 配置、数据库、执行器 |
| `app/models/` | ORM 模型 |
| `app/routers/` | 路由定义 |
| `app/schemas/` | 请求/响应模型 |
| `app/services/` | 业务服务 |
| `app/tasks/` | 耗时任务 |
| `app/utils/` | 通用工具 |
| `scripts/` | 辅助脚本 |
| `tests/` | FastAPI 专属测试根目录 |

## 依赖服务

按功能启用以下服务：

- MySQL
- Redis
- FFmpeg
- Ollama

部分接口不依赖全部服务，最小联调时可只启动当前链路所需组件。

说明：

- `ios-app/` 内的本地离线转录走 Apple `Speech` 端侧识别，不走 Ollama
- `backend_fastapi/` 中的 Ollama 仅用于本地摘要、标题、标签、语义整理等兼容回退链路
- `POST /api/videos/sync-offline-transcript` 会继续把 iOS 离线结果写入现有 MySQL `videos`、`subtitles`，并支持显式 `tags` 或 `auto_generate_tags`
- 当前默认 `OLLAMA_MODEL` 已切到 `qwen-3.5:9b`，对应你本地导入的 Qwen 3.5 9B GGUF 别名
- `backend_fastapi/scripts/import_qwen35_gguf_to_ollama.sh` 同时支持本地 `.gguf` 导入和 `hf.co/...` 直接拉取

## 用户认证约定

- 注册接口：邮箱或手机号至少一项必填，密码必须满足强密码规则。
- 登录接口：仅接受邮箱/手机号 + 密码。
- 数据落点：继续写入现有 `users` 表，不新增认证平行表。
- 资料更新接口现已支持登录后修改用户名，并直接写回 `users.username`。
- 头像上传接口会把文件保存到 `backend_fastapi/uploads/avatars/`，并将访问路径写入现有 `users.avatar` 字段，不新增附件表。
- 执行 `python scripts/init_db.py --create` 时，会自动为已有 `users` 表补齐手机号、密码重复检测指纹和登录次数字段。

## 设计助手代理

当前已把 `agent-skills` 对应的 Sleek 能力接入为 EduMind 后端代理，而不是让移动端直接请求第三方：

- 配置项：`SLEEK_API_KEY`、`SLEEK_API_BASE`
- 路由前缀：`/api/design/*`
- 设计结果：支持项目列表、项目创建、聊天生成、运行状态查询、组件 HTML 拉取、截图预览

建议在 Sleek 控制台创建最小权限 key，并至少授予：

- `projects:read`
- `projects:write`
- `components:read`
- `chats:read`
- `chats:write`
- `screenshots`

这些接口当前要求 EduMind 用户已登录，前端会自动带上现有 Bearer token。

## 验证与历史测试

```bash
python3 -m venv .venv
. .venv/bin/activate
python ../scripts/validate_backend_smoke.py
mkdir -p ../.pycache-hook
PYTHONPYCACHEPREFIX="$PWD/../.pycache-hook" python -m compileall app scripts ../scripts/hooks ../scripts/validate_backend_smoke.py
```

说明：

- `backend_fastapi/tests/` 仍保留历史回归测试目录和 pytest 风格组织方式。
- 当前仓库规则要求修改程序时不要用 `pytest` 作为本次验证手段，因此默认验证链路改为 `validate_backend_smoke.py + compileall`。

历史测试目录约定：

- `tests/unit/`：service、task、runtime、工具函数等单元测试
- `tests/api/`：FastAPI 路由与响应行为测试
- `tests/smoke/`：启动与最小链路验证
- `tests/integration/`：跨模块集成测试

新增后端测试请统一放进 `backend_fastapi/tests/`，不要散落到 `app/` 或仓库根目录。更详细的放置规则见 [backend_fastapi/tests/README.md](/Users/yuan/final-work/EduMind/backend_fastapi/tests/README.md)。

## 语义搜索后端

当前 `backend_fastapi/` 已经接入一版语义搜索后端主链路，入口主要包括：

- `app/routers/search.py`
- `app/schemas/search.py`
- `app/models/vector_index.py`
- `app/services/search/`
- `app/tasks/vector_indexing.py`

当前已支持：

- 对 `mp4` / `mov` 做重叠切片
- 按视频时长自动选择切片参数，短视频更细、长视频更粗
- `gemini` / `local` 双后端工厂
- `local` 后端可直接对字幕文本分块做本地向量索引与查询
- ChromaDB 持久化集合
- 手动触发索引、查询索引状态、执行语义搜索
- 视频处理完成后按配置自动提交索引任务
- 搜索结果回填字幕预览文本 `preview_text`

当前限制：

- 搜索用户识别当前优先取 `X-User-ID` 请求头，否则回退默认用户
- 当前更偏向字幕语义召回；若没有字幕文件，本地 `local` 后端不会退化成可用的视频视觉嵌入

部署步骤、数据库字段和当前已知限制见 [backend_fastapi/SEMANTIC_SEARCH_DEPLOYMENT.md](/Users/yuan/final-work/EduMind/backend_fastapi/SEMANTIC_SEARCH_DEPLOYMENT.md)。

## 端口约定

| 服务 | 默认端口 |
|------|---------|
| FastAPI 后端 | 2004 |
| Mobile Frontend Vite | 5173 |
## 开发说明

- 修改接口时，同时检查根目录 README 和 `docs/` 中是否需要同步更新。
- 如果接口会被移动端调用，注意 `mobile-frontend/` 的字段兼容性。
- 旧版 `backend/` 中存在历史实现，迁移时优先保证 FastAPI 行为一致，而不是继续扩展 Flask 分支。
