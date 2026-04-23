# Vinci Integration M1-M3（EduMind Backend）

本文档记录 EduMind 后端在 M1-M2 阶段对 Vinci 微服务的接入结果，覆盖：

- M1-1（测试先行）
- M1-2（最小实现）
- M1-3（治理接入）
- M1-4（可观测与运维）
- M2-1/M2-2（接入既有 agent 编排主干）
- M2-3（治理防绕过加固）
- M3（可观测与稳定性：熔断、恢复窗口、主流程可继续）

## 1. 目标与范围

本阶段目标：

- 通过适配层把 EduMind 后端与 Vinci（HTTP/SSE）打通
- 保持 Vinci 独立部署，不与主后端强耦合运行环境
- 将 Vinci 能力接入既有 Planner/Executor/Validator 主干
- 统一 trace_id、错误码、降级路径与结构化遥测

本阶段未覆盖：

- 生产告警平台接线（Prometheus/Grafana/日志告警与企业通知渠道联动）
- 已完成本地实接入验收，见 `docs/monitoring/VINCI_ALERTING_ACCEPTANCE_M3.md`

## 2. 代码与文件落点

- `app/services/vinci_client.py`
  - 负责 HTTP/SSE 调用
  - 提供 `VinciTimeoutError` / `VinciUnavailableError` / `VinciHTTPError`
- `app/services/vinci_adapter_service.py`
  - 对上游返回做契约标准化
  - 统一错误码映射与降级响应
  - 输出 `app.analytics.telemetry` 结构化事件
  - `request_chat/stream_chat` 强制治理上下文校验，阻断绕过调用
  - 新增进程内熔断器（失败阈值 + 恢复窗口 + 探测恢复）
- `app/agents/pipelines/learning_flow_pipeline.py`
  - 在 Executor 阶段接入 `lf_vinci_chat`（经治理网关）
  - 保持 Planner/Executor/Validator 主干不变
  - Vinci 失败/降级时回退 `lf_generate_summary_fallback`，主流程可继续
- `app/core/config.py` 与 `.env.example`
  - 增加 Vinci 配置项
- `tests/unit/test_vinci_adapter_service.py`
  - 覆盖 9 个核心场景（含熔断开启/恢复窗口/指标覆盖）
- `app/agents/governance/gateway.py`
  - 将 `lf_vinci_chat` 纳入工具白名单与参数校验
  - 审计日志复用统一 `agent_tool_*` 事件
- `app/agents/governance/tools_learning_flow.py`
  - 新增 `tool_lf_vinci_chat`（仅可在治理上下文内调用）
- `tests/unit/test_agent_governance_gateway.py`
  - 新增 Vinci 治理测试（白名单、参数校验、审计、绕过阻断）
- `tests/unit/test_learning_flow_pipeline_vinci.py`
  - 覆盖“pipeline 中 Vinci 必经 governance gateway”
- `tests/api/test_agent_api.py`
  - 覆盖 Vinci 接入后响应契约兼容
  - 覆盖 Vinci 工具不在白名单时 API 400 拒绝且阻断写库
- `app/analytics/alerting.py`
  - 新增模块指标快照（成功/错误/超时/P95/降级计数）
  - 新增 P95 告警阈值
- `app/analytics/pipeline.py`
  - 暴露 `module_metrics(module)` 供运维读取窗口快照
- `app/routers/ops.py`
  - 新增 `/api/ops/vinci/metrics` 输出 `module_metrics("vinci")`
- `tests/unit/test_analytics_alerting.py`、`tests/unit/test_analytics_pipeline.py`
  - 覆盖 M1-4 指标与阈值告警测试
- `tests/api/test_vinci_ops_api.py`
  - 覆盖 Vinci 运维观测接口鉴权与返回契约
- `docs/monitoring/local/docker-compose.grafana-loki.yaml`
  - 本地 Grafana/Loki 实接入验收编排文件
- `docs/monitoring/local/grafana/provisioning/datasources/loki.yaml`
  - Grafana Loki 数据源 provisioning 模板
- `docs/monitoring/VINCI_ALERTING_ACCEPTANCE_M3.md`
  - M3 告警平台实接入验收记录（时间、阈值、生效结果、回滚步骤）
- `docs/monitoring/evidence/m3/`
  - 验收日志、API 证据与截图归档
- `app/utils/vinci_alerting_acceptance.py`
  - 预发布/生产验收配置校验、降级演练 payload 与验收命令模板生成
- `scripts/run_vinci_alerting_acceptance_prep.py`
  - 一键生成 preprod/prod 演练 payload 与命令清单（不直接执行远端调用）

## 3. 配置项（M1）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `VINCI_ENABLED` | `false` | Vinci 功能总开关（开启后由 learning_flow pipeline 在治理链路内触发） |
| `VINCI_BASE_URL` | `http://127.0.0.1:8010` | Vinci 服务根地址 |
| `VINCI_API_KEY` | 空 | 可选 Bearer Key |
| `VINCI_CHAT_PATH` | `/api/v1/chat` | 非流式接口路径 |
| `VINCI_STREAM_PATH` | `/api/v1/chat/stream` | SSE 接口路径 |
| `VINCI_REQUEST_TIMEOUT_SECONDS` | `30` | 非流式请求超时 |
| `VINCI_CONNECT_TIMEOUT_SECONDS` | `8` | 建连超时 |
| `VINCI_STREAM_TIMEOUT_SECONDS` | `120` | SSE 流超时 |
| `VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `3` | 连续失败达到阈值后打开熔断 |
| `VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS` | `30` | 熔断恢复窗口（秒） |
| `ANALYTICS_ALERT_MAX_P95_LATENCY_MS` | `12000` | P95 告警阈值（毫秒） |

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
| 熔断窗口内 | `VINCI_CIRCUIT_OPEN` | 快速降级返回（不再调用上游） |

## 5. 遥测与 trace_id

适配层所有路径都会带 `trace_id`，并写入 `app.analytics.telemetry`：

- 请求：`vinci_request_started` / `vinci_request_completed`
- 异常：`vinci_request_timeout` / `vinci_request_error` / `vinci_request_degraded`
- 熔断：`vinci_circuit_opened` / `vinci_circuit_recovered`
- 流式：`vinci_stream_started` / `vinci_stream_timeout` / `vinci_stream_error` / `vinci_stream_degraded` / `vinci_stream_closed`

状态使用统一枚举：`started / ok / error / timeout / degraded`。

## 6. 测试与验证

M1 契约测试（TDD）：

```bash
pytest tests/unit/test_vinci_adapter_service.py -v
```

M3 稳定性测试（熔断/恢复窗口/主流程降级继续）：

```bash
pytest tests/unit/test_vinci_adapter_service.py \
       tests/unit/test_learning_flow_pipeline_vinci.py -v
```

治理接入与防绕过测试：

```bash
pytest tests/unit/test_agent_governance_gateway.py -v
pytest tests/unit/test_learning_flow_pipeline_vinci.py -v
```

Vinci 运维观测接口测试：

```bash
pytest tests/api/test_vinci_ops_api.py -v
```

Vinci 接入后 agent API 契约与治理拒绝回归：

```bash
pytest tests/api/test_agent_api.py -v
```

与治理/遥测基线联合回归：

```bash
pytest tests/unit/test_vinci_adapter_service.py \
       tests/unit/test_agent_governance_gateway.py \
       tests/unit/test_learning_flow_pipeline_vinci.py \
       tests/unit/test_analytics_pipeline.py \
       tests/api/test_agent_api.py -v
```

M3 后续告警接线脚本测试：

```bash
pytest tests/unit/test_vinci_alerting_acceptance.py -v
```

提交前 Hook 验证：

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

## 7. 已知限制与后续计划

- 当前已完成 `lf_vinci_chat` 的治理白名单、参数校验、审计日志与绕过阻断（含适配层与工具层双重阻断）。
- 当前已具备窗口级指标快照与 P95 告警能力；详细运维流程见 [`docs/VINCI_RUNBOOK.md`](VINCI_RUNBOOK.md)。
- 当前已完成本地 Grafana/Loki 告警实接入验收（规则导入、触发演练、证据留存），见 `docs/monitoring/VINCI_ALERTING_ACCEPTANCE_M3.md`。
- 当前已提供 `/api/ops/vinci/metrics` 作为进程内快照接口。
- 下一步建议将 Runbook 阈值接入真实告警平台，补齐跨实例集中观测与告警闭环。

## 8. M4 联调补充

- M4 已落地前后端契约联调，见 `docs/VINCI_M4_INTEGRATION.md`。
- 关键补充：
  - `/api/agent/execute` 治理拒绝返回“可恢复错误对象”（保留原始 `detail` + `error_code/message/suggestion/recoverable`）。
  - `/api/qa/ask` 流式输出统一 `answer` 结束事件（`stage=completed`、`progress=100`、`message=回答已完成`）。
  - 前端契约校验覆盖“仅通过后端代理调用，不直连 Vinci”。

## 9. M5 交付收口文档

- 架构与时序：`docs/VINCI_M5_ARCHITECTURE_AND_SEQUENCES.md`
- 接口契约与错误码：`docs/VINCI_M5_API_CONTRACT_ERROR_CODES.md`
- 验证报告：`docs/VINCI_M5_VERIFICATION_REPORT.md`
- 里程碑提交清单：`docs/VINCI_M5_MILESTONE_COMMITS.md`
