# Dependencies Reference

更新时间：2026-06-09 21:45:00 Asia/Shanghai

## 依赖来源

| 文件 | 说明 |
|---|---|
| `requirements.txt` | Python 运行与测试依赖 |
| `pytest.ini` | pytest 配置 |
| `.env.example` | 环境变量样例 |

## 关键依赖类别

| 类别 | 当前用途 |
|---|---|
| FastAPI/Uvicorn | HTTP API 服务 |
| SQLAlchemy/PyMySQL | ORM 与 MySQL 连接 |
| Pydantic/Pydantic Settings | schema 与配置 |
| pytest | 测试 |
| Whisper/Ollama/httpx | AI runtime 与外部 HTTP 客户端 |
| Chroma/Embedding 相关依赖 | 语义搜索 |
| yt-dlp/curl-cffi | 远程视频下载；`curl-cffi` 用于 yt-dlp `impersonate` 客户端伪装 |

## 维护建议

| 场景 | 要求 |
|---|---|
| 新增依赖 | 更新 `requirements.txt` 并在本文件记录用途 |
| 删除依赖 | 确认无 import 与测试引用 |
| 升级依赖 | 运行 smoke、compileall 与相关领域测试 |
