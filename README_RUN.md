# EduMind Backend 运行指南

这份文档描述如何从零启动当前独立后端仓库 `edumind-backend/`。

## 1. 创建环境

```bash
python3 -m venv .venv
. .venv/bin/activate
```

## 2. 安装依赖

```bash
pip install -r requirements.txt
```

## 3. 配置环境变量

```bash
cp .env.example .env
```

按实际环境填写数据库、模型服务和密钥配置。若修改端口，请同步调整 `PORT` 与 `CORS_ORIGINS`。

如果需要联调 Vinci 微服务，请至少配置：

```bash
VINCI_ENABLED=true
VINCI_BASE_URL=http://127.0.0.1:8010
VINCI_API_KEY=
VINCI_CHAT_PATH=/api/v1/chat
VINCI_STREAM_PATH=/api/v1/chat/stream
```

更多说明见 [`docs/VINCI_INTEGRATION_M1.md`](docs/VINCI_INTEGRATION_M1.md)。

## 4. 启动依赖服务（按需）

```bash
brew services start mysql
ollama serve
```

## 5. 可选：导入本地 GGUF 到 Ollama

```bash
bash scripts/import_qwen35_gguf_to_ollama.sh /absolute/path/to/model.gguf
# 或
bash scripts/import_qwen35_gguf_to_ollama.sh hf.co/owner/repo:tag
```

说明：这一步是后端本地 LLM 回退能力（摘要/标签/语义整理），不是 iOS 端 ASR 引擎切换。

## 6. 启动后端

```bash
python run.py
```

启动成功后默认：

- `http://127.0.0.1:2004`
- `http://127.0.0.1:2004/docs`

## 7. 基础验证

```bash
curl http://127.0.0.1:2004/health
```

## 8. 本仓库推荐验证链路

```bash
pytest tests/smoke/test_app_startup.py -v
mkdir -p .pycache-hook
PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts
python scripts/validate_system_requirements.py
```

## 9. Git hooks 验证（提交/推送前）

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

如果本次改动包含 Vinci 适配层，建议额外执行：

```bash
pytest tests/unit/test_vinci_adapter_service.py -v
```

## 10. 常见问题

### 端口占用

```bash
lsof -i :2004
```

### 依赖安装失败

```bash
pip install --upgrade pip
pip install -r requirements.txt --force-reinstall
```
