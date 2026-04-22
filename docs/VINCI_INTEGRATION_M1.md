# Vinci Integration M1（EduMind Backend）

本文档记录 EduMind 后端在 M1 阶段对 Vinci 微服务的接入结果，覆盖 M1-1（测试先行）与 M1-2（最小实现）。

## 1. 目标与范围

本阶段目标：

- 通过适配层把 EduMind 后端与 Vinci（HTTP/SSE）打通
- 保持 Vinci 独立部署，不与主后端强耦合运行环境
- 统一 trace_id、错误码、降级路径与结构化遥测

本阶段未覆盖：

- M1-3：治理网关强制接入（白名单、参数校验、不可绕过）
- M1-4：聚合监控指标（成功率/错误率/超时率/P95）与运维 Runbook

## 2. 代码与文件落点

- `app/services/vinci_client.py`
  - 负责 HTTP/SSE 调用
  - 提供 `VinciTimeoutError` / `VinciUnavailableError` / `VinciHTTPError`
- `app/services/vinci_adapter_service.py`
  - 对上游返回做契约标准化
  - 统一错误码映射与降级响应
  - 输出 `app.analytics.telemetry` 结构化事件
- `app/core/config.py` 与 `.env.example`
  - 增加 Vinci 配置项
- `tests/unit/test_vinci_adapter_service.py`
  - 覆盖 5 个核心场景（先红后绿）

## 3. 配置项（M1）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `VINCI_ENABLED` | `false` | Vinci 功能总开关（当前仅配置，后续路由接线使用） |
| `VINCI_BASE_URL` | `http://127.0.0.1:8010` | Vinci 服务根地址 |
| `VINCI_API_KEY` | 空 | 可选 Bearer Key |
| `VINCI_CHAT_PATH` | `/api/v1/chat` | 非流式接口路径 |
| `VINCI_STREAM_PATH` | `/api/v1/chat/stream` | SSE 接口路径 |
| `VINCI_REQUEST_TIMEOUT_SECONDS` | `30` | 非流式请求超时 |
| `VINCI_CONNECT_TIMEOUT_SECONDS` | `8` | 建连超时 |
| `VINCI_STREAM_TIMEOUT_SECONDS` | `120` | SSE 流超时 |

## 4. 统一契约与错误映射

### 4.1 非流式响应标准化

适配层返回字段：

- `answer: str`
- `history: list[dict]`
- `session_id: str`
- `trace_id: str`
- `degraded: bool`
- `error_code: str`（仅降级时）

### 4.2 SSE 事件标准化

适配层输出事件：

- 增量：`{"event":"delta","delta":"...","session_id":"...","trace_id":"..."}`
- 结束：`{"event":"done","session_id":"...","trace_id":"..."}`
- 错误：`{"event":"error","error_code":"...","message":"...","session_id":"...","trace_id":"..."}`

若上游未显式结束，适配层会补发 `done` 事件，保证消费端可收敛。

### 4.3 错误码映射

| 场景 | 适配层错误码 | 行为 |
|---|---|---|
| 超时 | `VINCI_TIMEOUT` | 抛出 `VinciAdapterError` |
| 非 2xx | `VINCI_UPSTREAM_<status>` | 抛出 `VinciAdapterError`，携带 `upstream_status_code` |
| 服务不可用 | `VINCI_UNAVAILABLE` | 返回降级 payload（`degraded=true`） |

## 5. 遥测与 trace_id

适配层所有路径都会带 `trace_id`，并写入 `app.analytics.telemetry`：

- 请求：`vinci_request_started` / `vinci_request_completed`
- 异常：`vinci_request_timeout` / `vinci_request_error` / `vinci_request_degraded`
- 流式：`vinci_stream_started` / `vinci_stream_timeout` / `vinci_stream_error` / `vinci_stream_degraded` / `vinci_stream_closed`

状态使用统一枚举：`started / ok / error / timeout / degraded`。

## 6. 测试与验证

M1 契约测试（TDD）：

```bash
pytest tests/unit/test_vinci_adapter_service.py -v
```

与治理/遥测基线联合回归：

```bash
pytest tests/unit/test_vinci_adapter_service.py \
       tests/unit/test_agent_governance_gateway.py \
       tests/unit/test_analytics_pipeline.py -v
```

提交前 Hook 验证：

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

## 7. 已知限制与后续计划

- 当前仅完成服务层适配，尚未把 Vinci 调用接入现有治理网关强制执行路径（M1-3）。
- 当前遥测已打点但未形成 Ops 聚合指标与告警阈值文档（M1-4）。
- 下一步建议先补 M1-3 的“先测后实现”，确保任何 Vinci 调用都无法绕过治理。
