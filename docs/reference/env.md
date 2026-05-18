# Environment Reference

更新时间：2026-05-18 00:00:00 Asia/Shanghai

## 核心环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_ENV` | `local` | 运行环境 |
| `APP_NAME` | `EduMind` | 应用名称 |
| `DEBUG` | `true` | 调试模式 |
| `HOST` | `0.0.0.0` | 监听 host |
| `PORT` | `2004` | API 端口 |
| `SECRET_KEY` | dev 默认值 | 生产必须覆盖 |
| `DATABASE_URL` | MySQL 本地 URL | 数据库连接 |
| `AUTO_CREATE_TABLES` | `false` | 是否启动自动建表 |
| `AUTH_ALLOW_LEGACY_USER_ID_ONLY` | `false` | 是否允许仅 legacy user_id 鉴权 |

## AI 与搜索

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_BASE_URL` | DashScope compatible URL | OpenAI-compatible 入口 |
| `QA_DEFAULT_PROVIDER` | `qwen` | QA 默认 provider |
| `OLLAMA_BASE_URL` | `http://localhost:11434/api` | Ollama API |
| `WHISPER_MODEL` | `base` | Whisper 模型 |
| `WHISPER_MODEL_PATH` | `/Users/yuan/302_works/whisper_models` | Whisper 模型目录 |
| `WHISPER_PRELOAD_ON_STARTUP` | `true` | 启动后是否后台预热默认模型 |
| `WHISPER_LOAD_TIMEOUT_SECONDS` | `60` | 本地模型文件已存在时的加载超时 |
| `WHISPER_DOWNLOAD_TIMEOUT_SECONDS` | `300` | 首次下载模型时的加载/下载超时 |
| `WHISPER_DEBUG_LOG` | `false` | 是否开启独立 Whisper DEBUG 文件日志 |
| `WHISPER_DEBUG_LOG_FILE` | `logs/whisper_debug.log` | Whisper DEBUG 日志文件路径 |
| `SEARCH_ENABLED` | `false` | 搜索开关 |
| `SEARCH_BACKEND` | `gemini` | 搜索 embedding 后端 |
| `SEARCH_CHROMA_DB_DIR` | `./data/chroma` | ChromaDB 数据目录 |

## Frame Description

| 变量 | 默认值 | 说明 |
|---|---|---|
| `FRAME_DESC_ENABLED` | `false` | 实时画面描述开关 |
| `FRAME_DESC_BACKEND` | `qwen3vl` | 默认视觉描述后端 |
| `QWEN3VL_BASE_URL` | `http://127.0.0.1:18082` | 本地 Qwen3VL 服务 |
| `FRAME_DESC_CLOUD_FALLBACK_ENABLED` | `false` | 是否启用 Cloud Qwen-VL fallback |
| `FRAME_DESC_CLOUD_QWEN_MODEL` | `qwen3-vl-plus` | 云端视觉模型 |
| `VINCI_ENABLED` | `false` | Vinci legacy 开关 |
