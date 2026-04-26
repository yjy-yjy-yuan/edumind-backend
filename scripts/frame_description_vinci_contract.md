# 画面描述 Vinci 契约与适配层文档

> 本文档定义了 EduMind 实时画面描述服务与 Vinci 模型服务之间的完整适配层契约，
> 包括：数据流、两种操作模式、错误处理、统一配置项、版本历史。

---

## 1. 架构总览

```
前端 Player.vue
    │  frames[] (base64 JPEG)
    ▼
FrameDescriptionService.describe_frames()
    │  base64_frames[] → execute_tool()
    ▼
gateway.execute_tool("lf_frame_description", params)
    │  校验: base64_frames / prompt / history / session_id
    ▼
tool_lf_frame_description(handler)
    │  有帧 → service.request_vision_chat()
    │  无帧 → service.request_vision_chat(silent=True)
    ▼
VinciAdapterService
    │  熔断器（按 VINCI_CIRCUIT_BREAKER_* 配置）
    │  遥测事件（vinci_vision_started/completed/timeout/error/degraded）
    ▼
VinciClient.request_vision_chat()
    │  history 格式转换: EduMind {role,content} → internvl [role_code, text]
    │  帧数据清理: data-URI 前缀剥离
    ▼
POST /api/v1/inference/internvl  (Vinci Server:18081)
    │
    ▼
InternVL Model → answer (text)
```

---

## 2. 两种操作模式

### 2.1 Vision 模式（有图像帧）

当 `base64_frames` 非空时启用。

**触发路径**：
```
tool_lf_frame_description → VinciAdapterService.request_vision_chat(base64_frames=[...])
```

**请求体**（Vinci internvl 端点）：
```json
{
  "question": "简单描述视频中当前发生的事情，用一句话。",
  "history": [[0, "用户: 上一条描述"], [1, "助手: 上一条回复"]],   // internvl 格式 [role_code, text]
  "session_id": "fd-session-abc",
  "timestamp": 0,
  "silent": false,
  "frames": [],
  "base64_frames": [
    "/9j/4AAQSkZJRgABAQAAAQ...",
    "/9j/4AAQSkZJRgABAQAAAQ..."
  ]
}
```

**字段说明**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `question` | string | 当前帧描述提示词（含上下文融合内容） |
| `history` | `list[list]` | internvl 格式：`[[role_code, text], ...]`，`role_code=0`=user，`role_code=1`=assistant |
| `session_id` | string | 会话隔离 ID |
| `timestamp` | int | 请求字段，当前固定传 `0`（由 Vinci 会话内部维护真实时间上下文） |
| `silent` | bool | `false`=回答 question，`true`=自动描述动作 |
| `frames` | `list` | 保留字段，当前始终为空 |
| `base64_frames` | `list[string]` | 实际帧数据，data-URI 前缀会被剥离 |

**限制**：
- 单次最多 `MAX_VINCI_BASE64_FRAMES = 3` 帧
- 单帧最大 `MAX_VINCI_BASE64_FRAME_SIZE_CHARS = 200000` 字符（约 150KB JPEG）
- 超限由 `gateway._validate_params` 在治理层直接拒绝（不调用 Vinci）

### 2.2 Silent/Text 模式（无图像帧）

当 `base64_frames` 为空/None 时启用。

**触发路径**：
```
tool_lf_frame_description → VinciAdapterService.request_vision_chat(base64_frames=[], silent=True)
```

**请求体**：
```json
{
  "question": "ping",
  "history": [],
  "session_id": "health_check",
  "timestamp": 0,
  "silent": true,
  "frames": [],
  "base64_frames": []
}
```

Vinci internvl 的 `silent=True` 模式会：
1. 不要求 base64_frames（Vinci 自动处理空帧）
2. 使用固定提示词 "简单的描述视频中我的动作" 自动描述

**适用场景**：
- 降级模式（Vinci 不可达时保持服务可用性）
- 健康检查（`VinciAdapterService.health_check()`）
- 调试/纯文本推理

---

## 3. History 格式转换

### 3.1 问题背景

| 系统 | History 格式 | 示例 |
|------|-------------|------|
| EduMind 内部 | `{role: str, content: str}` | `[{"role":"user","content":"描述一下"}, {"role":"assistant","content":"老师在讲课"}]` |
| Vinci internvl | `[role_code: int, text: str]` | `[[0, "描述一下"], [1, "老师在讲课"]]` |

Vinci internvl `add_history` 期望 `history[i]` 为 `[role_code, text]` 二元组，
传错格式可能导致语义错误或 `IndexError`。

### 3.2 转换规则

```python
def _normalize_history_for_internvl(history: list[dict]) -> list[list]:
    """
    EduMind: [{role, content}, ...]
    Vinci internvl: [[role_code, text], ...]

    role_code 约定:
      0 = user/human (提问方)
      1 = assistant (回答方)
    """
    result = []
    for item in history[-50:]:  # 最多 50 条
        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("content") or "").strip()
        if not role or not text:
            continue
        role_code = 0 if role in ("user", "human") else 1
        result.append([role_code, text])
    return result
```

### 3.3 兼容性保障

- **传入端**（EduMind → Vinci）：由 `VinciClient._normalize_history_for_internvl()` 转换
- **传出端**（Vinci → EduMind）：`VinciAdapterService._normalize_response()` 统一为 `{answer, history, session_id, trace_id, degraded}`
- **Gateway 校验**：`_validate_params` 对 history 的类型/长度/内容有硬边界限制

---

## 4. 错误处理与降级

### 4.1 错误分类

| 来源 | 错误类型 | HTTP 响应码 | 处理策略 |
|------|---------|------------|---------|
| Vinci 超时 | `VinciTimeoutError` | 504 | 记录失败 → 打开熔断器 → 返回降级文本 |
| Vinci HTTP 错误 | `VinciHTTPError` | 502 | 同上 |
| Vinci 不可达 | `VinciUnavailableError` | 降级 | 同上，优先返回降级结果 |
| 熔断器打开 | `VINCI_CIRCUIT_OPEN` | 降级 | 直接返回降级文本，不调用 Vinci |
| 参数校验失败 | `GovernanceError` | 400 | 治理层拒绝，透传错误码 |

### 4.2 降级文本

```
Vinci 服务暂不可用，已返回降级结果，请稍后重试。
```

降级时 `response["degraded"] = True`，前端可据此展示不同 UI 状态。

### 4.3 熔断器恢复

- **打开阈值**：`VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD` 次连续失败（默认 3）
- **恢复等待**：`VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS` 秒（默认 30）
- **探测模式**：恢复窗口到期后允许一次探测请求（半开状态）
- **探测成功**：立即关闭熔断器，发送 `vinci_circuit_recovered` 遥测事件
- **探测失败**：重新打开熔断器，延长恢复等待

---

## 5. 健康检查

### 5.1 三层检查模型

`GET /api/frame_description/health` 返回三层状态：

```json
{
  "enabled": true,
  "service": "active",
  "description": "实时画面描述服务",
  "vinci": {
    "reachable": true,
    "latency_ms": 42.5,
    "error": null,
    "error_code": null
  }
}
```

| 层级 | 检查内容 | 决定因素 |
|------|---------|---------|
| L1: 功能开关 | `settings.FRAME_DESC_ENABLED` | 前端显示"功能未启用" |
| L2: 服务实例 | `FrameDescriptionService` 实例化 | 前端显示"服务未就绪" |
| L3: Vinci 可达性 | `VinciAdapterService.health_check()` | 运维告警阈值依据 |

### 5.2 L3 检查实现

```python
def health_check(self, timeout_seconds=5.0) -> VinciHealthResult:
    # POST 一个空帧请求到 /api/v1/inference/internvl
    # 不使用业务请求的 30s 超时，独立 5s 控制
    # 返回: reachable(bool), latency_ms(float), error(str), error_code(str)
```

---

## 6. 配置项清单

### 6.1 Frame Description 服务开关

| 配置项 | 类型 | 默认值 | 说明 |
|-------|------|--------|------|
| `FRAME_DESC_ENABLED` | bool | `False` | 功能总开关 |
| `FRAME_DESC_SAMPLING_INTERVAL_MS` | int | `5000` | 帧采样间隔（毫秒） |
| `FRAME_DESC_CONTEXT_WINDOW` | int | `5` | 上下文融合窗口（条描述） |
| `FRAME_DESC_SIMILARITY_THRESHOLD` | float | `0.85` | 相似度阈值 |
| `FRAME_DESC_AUTO_DEGRADE` | bool | `True` | 失败时自动降级 |
| `FRAME_DESC_DEGRADE_PREFIX` | str | `[降级]` | 降级文本前缀 |

### 6.2 Vinci 适配层

| 配置项 | 类型 | 默认值 | 说明 |
|-------|------|--------|------|
| `VINCI_BASE_URL` | str | `http://127.0.0.1:18081` | Vinci 服务地址 |
| `VINCI_API_KEY` | str | `""` | API 密钥（可选） |
| `VINCI_CHAT_PATH` | str | `/api/v1/chat` | 文本对话路径 |
| `VINCI_STREAM_PATH` | str | `/api/v1/chat/stream` | 流式对话路径 |
| `VINCI_REQUEST_TIMEOUT_SECONDS` | float | `30.0` | 推理超时 |
| `VINCI_CONNECT_TIMEOUT_SECONDS` | float | `8.0` | 连接超时 |
| `VINCI_STREAM_TIMEOUT_SECONDS` | float | `120.0` | 流式超时 |
| `VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | int | `3` | 熔断打开阈值 |
| `VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS` | float | `30.0` | 熔断恢复等待 |

### 6.3 帧数据硬限制（Gateway 层）

| 配置项 | 值 | 说明 |
|-------|-----|------|
| `MAX_VINCI_PROMPT_CHARS` | `20,000` | prompt 最大字符数 |
| `MAX_VINCI_SESSION_ID_CHARS` | `128` | session_id 最大长度 |
| `MAX_VINCI_HISTORY_ITEMS` | `50` | history 最大条数 |
| `MAX_VINCI_HISTORY_CONTENT_CHARS` | `8,000` | 单条 history 内容最大长度 |
| `MAX_VINCI_BASE64_FRAMES` | `3` | 单次最大帧数 |
| `MAX_VINCI_BASE64_FRAME_SIZE_CHARS` | `200,000` | 单帧最大 base64 字符数 |

---

## 7. 遥测事件清单

| 事件名 | 触发时机 | 关键 metadata |
|--------|---------|-------------|
| `vinci_vision_started` | vision 请求发出 | `frame_count`, `silent` |
| `vinci_vision_completed` | vision 请求成功 | `frame_count`, `degraded`, `latency_ms` |
| `vinci_vision_timeout` | vision 超时 | `frame_count`, `circuit_opened` |
| `vinci_vision_error` | vision HTTP 错误 | `upstream_status_code`, `circuit_opened` |
| `vinci_circuit_opened` | 熔断器打开 | `reason`, `opened_at`, `recovery_seconds` |
| `vinci_circuit_recovered` | 熔断器恢复 | `session_id` |
| `frame_desc_inference_degraded` | 描述推理降级 | `video_id`, `timestamp`, `error` |

---

## 8. 版本历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-04-26 | 初始版本：P0 帧传递链路 + P1 health/history 修复 |

---

## 9. 设计原则

1. **治理优先**：所有 Vinci 调用必须经 `execute_tool` 白名单，参数由 `gateway._validate_params` 校验
2. **路径固定**：本功能链路明确通过 `request_vision_chat` 走固定 internvl 端点（`request_chat` 的兼容回退属于其他通道能力）
3. **格式显式转换**：EduMind 内部与 Vinci 之间的 history 格式转换在 `VinciClient` 层明确处理
4. **双重熔断**：Service 层（`_VinciCircuitBreaker`）+ Adapter 层（`_CircuitBreakerState`）各自独立
5. **可观测**：所有关键路径发出遥测事件，结构化日志含 trace_id/session_id
