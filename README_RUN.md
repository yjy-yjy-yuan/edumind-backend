# EduMind 后端运行指南

这份文档描述如何从零启动 `backend_fastapi/`。

## 1. 创建环境

按当前仓库约定，统一使用项目根目录下的 `.venv`。

```bash
python3 -m venv .venv
. .venv/bin/activate
```

## 2. 安装依赖

```bash
cd backend_fastapi
pip install -r requirements.txt
```

## 3. 配置环境变量

```bash
cp .env.example .env
```

按实际环境修改 `.env` 中的数据库、模型服务和密钥配置。
如果需要改后端端口，请同步调整 `PORT`，并把 `CORS_ORIGINS` 加上前端实际端口。

## 4. 启动依赖服务

按需要启动：

```bash
brew services start mysql
ollama serve
```

如果某些功能暂时不用，可以只启动当前接口链路需要的依赖。

如果你要把本地 GGUF 模型接入当前项目，请先导入到 Ollama。
支持两种方式：本地 `.gguf` 文件，或直接使用 `hf.co/...` 模型引用。

```bash
. .venv/bin/activate
bash backend_fastapi/scripts/import_qwen35_gguf_to_ollama.sh /absolute/path/to/Qwen3.5-9B-Claude-4.6-Opus-Reasoning-Distilled-v2-GGUF.gguf
```

```bash
. .venv/bin/activate
bash backend_fastapi/scripts/import_qwen35_gguf_to_ollama.sh hf.co/Jackrong/Qwen3.5-9B-Claude-4.6-Opus-Reasoning-Distilled-GGUF:Q4_K_M
```

说明：

- 这一步导入的是“后端本地 LLM 回退模型”，用于摘要、标题、语义整理等能力
- iOS 本地离线视频转录仍然使用 Apple `Speech` 端侧识别，不会因为这里切换 GGUF 而自动变成新的 ASR 引擎

## 5. 启动后端

```bash
cd backend_fastapi
. .venv/bin/activate
python run.py
```

启动成功后默认监听：

- `http://127.0.0.1:2004`
- `http://127.0.0.1:2004/docs`

## 6. 验证服务

```bash
curl http://localhost:2004/health
```

## 7. 配合前端运行

当前仓库只保留 `mobile-frontend/`：

```bash
cd mobile-frontend
npm install
npm run dev
```

## 8. 运行验证

```bash
. .venv/bin/activate
python scripts/validate_backend_smoke.py
mkdir -p .pycache-hook
PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall backend_fastapi/app backend_fastapi/scripts scripts/hooks scripts/validate_backend_smoke.py
```

如需查看历史回归测试组织方式，可参考 [backend_fastapi/tests/README.md](/Users/yuan/final-work/EduMind/backend_fastapi/tests/README.md)。当前仓库规则要求修改程序时不要用 `pytest` 作为本次验证手段。

## 9. 常见问题

### 端口占用

```bash
lsof -i :2004
```

### 依赖安装失败

```bash
pip install --upgrade pip
pip install -r requirements.txt --force-reinstall
```
