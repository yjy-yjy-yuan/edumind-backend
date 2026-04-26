# Vinci Integration M4（前后端联调）

本文档记录 M4 联调的最小可复现流程，目标是确保前端仅通过 EduMind 后端访问能力，且错误与流式协议对齐可恢复。

## 1. 后端启动

```bash
cd /Users/yuan/final-work/edumind-backend
.venv/bin/python run.py
```

默认地址：`http://127.0.0.1:2004`

## 2. 前端启动

```bash
cd /Users/yuan/final-work/EduMind/mobile-frontend
npm install
npm run dev
```

在前端运行时配置中将 API Base 指向后端地址（如 `http://127.0.0.1:2004`）。

## 3. 契约联调步骤

1. 前端调用学习流接口：`POST /api/agent/execute`
2. 前端调用问答接口：`POST /api/qa/ask`
3. 问答流式模式要求：
   - `Content-Type: application/x-ndjson`
   - 首事件包含 `type=status` 与 `stage=accepted`
   - 最终回答事件包含 `type=answer/stage=completed/progress=100`
4. 治理拒绝场景要求：
   - 返回 `HTTP 400`
   - 同时包含原始 `detail` 与可恢复信息（`error_code/message/suggestion/recoverable`）

## 4. 验证命令

```bash
cd /Users/yuan/final-work/edumind-backend
.venv/bin/pytest tests/api/test_agent_api.py -v
.venv/bin/pytest tests/api/test_qa_api.py -v
.venv/bin/pytest tests/unit/test_m4_frontend_proxy_contract.py -v
.venv/bin/pytest tests/unit/test_m4_integration_docs.py -v
```

```bash
cd /Users/yuan/final-work/EduMind/mobile-frontend
npm test
npm run build
```

## 5. 故障恢复建议

- 若 `POST /api/qa/ask` 流式返回首条即 `error`，优先检查后端数据库连接与依赖注入路径是否一致。
- 若治理拒绝频发，先按 `message/suggestion` 调整请求参数，再排查网关白名单与参数阈值。
