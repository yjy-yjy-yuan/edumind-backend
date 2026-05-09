# Tech Stack

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 技术栈

| 类别 | 技术 | 证据 |
|---|---|---|
| Web Framework | FastAPI, Uvicorn | `requirements.txt`, `app/main.py` |
| ORM | SQLAlchemy 2.0 | `requirements.txt`, `app/models/` |
| 配置 | Pydantic Settings | `app/core/config.py` |
| 数据库 | MySQL 默认 URL，SQLAlchemy engine 适配 | `app/core/config.py`, `app/core/database.py` |
| AI/LLM | Qwen/OpenAI-compatible, DeepSeek, Ollama, Whisper | `app/core/config.py`, `app/services/` |
| 视觉描述 | Qwen3VL local client, Cloud Qwen-VL fallback, Vinci legacy | `app/services/qwen3vl_realtime_client.py`, `app/services/qwen_vl_cloud_client.py`, `app/services/vinci_client.py` |
| 搜索 | Gemini/local embedding, ChromaDB store | `app/services/search/`, `requirements.txt` |
| 测试 | pytest | `pytest.ini`, `tests/` |
| 格式化 | black, isort | `AGENTS.md`, `requirements.txt` |

## 运行时依赖注意事项

| 依赖 | 当前说明 |
|---|---|
| Whisper 模型 | 默认 `WHISPER_MODEL=base`，模型目录由 `WHISPER_MODEL_PATH` 控制 |
| Ollama | 默认 `http://localhost:11434/api`，健康检查会返回 runtime status |
| Qwen3VL 服务 | 默认 `http://127.0.0.1:18082`，用于实时画面描述 |
| ChromaDB | 默认目录 `./data/chroma`，属于运行时数据，不应作为业务文档来源 |
