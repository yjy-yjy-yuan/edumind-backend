# Vinci Integration M5 - API Contract & Error Codes

本文档汇总 M5 上线前接口契约、错误码与恢复语义。

## 1. Agent 执行接口

### 1.1 Endpoint

- `POST /api/agent/execute`
- Request: `AgentExecuteRequest`
- Response: `AgentPlanResponse`

### 1.2 成功响应关键字段

- `intent`, `plan`, `actions`, `result`
- `result.pipeline_meta.trace_id`
- `result.pipeline_meta.token_budget`

### 1.3 失败响应

| HTTP | 场景 | 响应结构 |
|---|---|---|
| 400 | 治理拒绝（`GovernanceError`） | `detail={detail,error_code,message,suggestion,recoverable}` |
| 404 | 视频不存在 | `detail="视频不存在"` |
| 500 | 未分类系统错误 | `detail="学习流智能体执行失败，请稍后重试"` |

### 1.4 治理拒绝 payload

```json
{
  "detail": {
    "detail": "tool_not_allowed:lf_vinci_chat",
    "error_code": "GOVERNANCE_REJECTED",
    "message": "请求未通过治理校验，请调整输入后重试。",
    "suggestion": "请简化输入内容，避免超长参数或未授权动作；如多次失败请联系管理员排查策略。",
    "recoverable": true
  }
}
```

## 2. QA 问答接口

### 2.1 Endpoint

- `POST /api/qa/ask`
- 非流式：JSON
- 流式：`application/x-ndjson`

### 2.2 流式事件契约

- 首事件：`type=status`, `stage=accepted`
- 回答结束事件：`type=answer`, `stage=completed`, `progress=100`, `message=回答已完成`
- 错误事件：`type=error`, `stage=<validation|config_error|provider_error|server_error>`

### 2.3 QA HTTP 错误

| HTTP | 场景 |
|---|---|
| 400 | mode/provider 参数非法；视频模式缺少 video_id；无可问答上下文 |
| 404 | 视频不存在 |
| 502 | 上游模型调用失败（`QAProviderError`） |
| 503 | 模型配置错误（`QAConfigError`） |
| 500 | 未分类服务器错误 |

## 3. Vinci 适配层错误码

来源：`app/services/vinci_adapter_service.py`

| 错误码 | 场景 | 默认处理 |
|---|---|---|
| `VINCI_TIMEOUT` | 上游超时 | 抛 `VinciAdapterError`（504）或流式 error 事件 |
| `VINCI_UPSTREAM_<status>` | 上游非 2xx | 抛 `VinciAdapterError`（502） |
| `VINCI_UNAVAILABLE` | 网络不可达/服务不可用 | 降级返回（`degraded=true`） |
| `VINCI_CIRCUIT_OPEN` | 熔断窗口内短路 | 降级返回（快速失败） |

## 4. 治理层拒绝码（摘要）

来源：`app/agents/governance/gateway.py`

- 通用：`invalid_tool_name`, `tool_not_allowed:<tool>`
- 参数结构：`params_must_be_object`
- Vinci 参数：`missing_prompt`, `prompt_too_long`, `missing_session_id`, `invalid_history`, `history_too_long`, `history_item_content_too_long`
- 笔记与时间戳参数：`invalid_video_id`, `missing_title`, `missing_content`, `invalid_note_id`, `invalid_time_seconds` 等

## 5. 运维观测接口

### 5.1 Endpoint

- `GET /api/ops/vinci/metrics`

### 5.2 契约摘要

- 需登录（未登录 `401`）
- 返回：窗口计数、成功/错误/超时/降级比率、P95、阈值快照
- 透传头：`X-Trace-Id` / `X-Request-Id`

## 6. 前端恢复语义（M4 对齐）

- Agent 治理错误：展示 `message` + `suggestion`，保留技术 `detail` 便于支持排查。
- QA 流式错误：优先展示 `message`，再回退 `detail`。

## 7. 关联运维文档

- Runbook（运行排查、恢复与回滚）：`docs/VINCI_RUNBOOK.md`
