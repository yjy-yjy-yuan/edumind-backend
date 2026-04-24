# EduMind 实时画面描述 — 交付报告（修正版）

> **项目**：EduMind 视频播放中实时画面描述
> **交付日期**：2026-04-23
> **状态**：全部修正完成，未提交，等待用户确认

---

## 修正说明

本次修正针对以下 6 个问题：

| # | 问题 | 修正结果 |
|---|------|---------|
| 1 | 报告中的配置项名称与代码不符 | 逐项核对 `config.py`，以实际命名为准 |
| 2 | VinciClient 未接入治理网关 | 已改为 `execute_tool(\"lf_frame_description\", ...)` 经网关执行 |
| 3 | `_VinciCircuitBreaker` 重复定义两次 | 已删除重复的 stub 版本（行 162-177） |
| 4 | 前端测试为"注释型参考用例"而非可执行测试 | 已重写为 `frameDescription.test.mjs`（20个断言，全绿） |
| 5 | `package.json` 测试命令不递归子目录 | 已改为 `tests/**/*.test.mjs` |
| 6 | 报告将 Player.vue 列为"新增" | 已更正为"修改" |

---

## 一、实现清单（Executable Deliverables）

### 1.1 后端新增文件

| 文件路径 | 说明 |
|---------|------|
| `app/schemas/frame_description.py` | Pydantic 模型：请求体 + 4种 NDJSON 事件 |
| `app/services/frame_description_service.py` | 核心逻辑：Vinci 调用（走 governance）、上下文融合、去重节流、熔断降级、遥测 |
| `app/routers/frame_description.py` | FastAPI 流式端点（describe/session/health） |
| `scripts/demo_frame_description.sh` | 演示脚本（正常/降级/恢复三条链路） |
| `tests/unit/test_frame_description_service.py` | 单元测试（24项） |
| `tests/api/test_frame_description_api.py` | API 测试（10项） |

### 1.2 后端修改文件

| 文件路径 | 说明 |
|---------|------|
| `app/core/config.py` | 新增 `FRAME_DESC_*` 系列配置（13项） |
| `app/main.py` | 注册 `frame_description` 路由 |

### 1.3 前端新增文件

| 文件路径 | 说明 |
|---------|------|
| `src/api/frameDescription.js` | API 客户端（NDJSON 流解析、mock 模拟、会话管理、健康检查） |
| `tests/api/frameDescription.test.mjs` | 可执行测试（20个断言，全绿） |

### 1.4 前端修改文件

| 文件路径 | 说明 |
|---------|------|
| `src/views/Player.vue` | 集成实时描述面板（开关/档位/进度/历史/反馈），状态：`M` |
| `package.json` | 测试命令改为递归 `tests/**/*.test.mjs` |

---

## 二、配置项清单（实际代码中的名称与默认值）

以下为 `app/core/config.py` 中的实际配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `FRAME_DESC_ENABLED` | `False` | 功能总开关 |
| `FRAME_DESC_SAMPLE_MODE` | `"fixed_interval"` | 采样策略 |
| `FRAME_DESC_SAMPLE_INTERVAL_SECONDS` | `3.0` | 采样间隔（秒） |
| `FRAME_DESC_TIMEOUT_SECONDS` | `8.0` | 推理超时（秒） |
| `FRAME_DESC_CONTEXT_WINDOW_SIZE` | `5` | 上下文融合窗口（条描述） |
| `FRAME_DESC_SIMILARITY_THRESHOLD` | `0.82` | 去重阈值（Jaccard 字符相似度） |
| `FRAME_DESC_SCENE_STABLE_THRESHOLD` | `4` | 连续 N 次高相似 → 跳过推理 |
| `FRAME_DESC_DEGRADED_INTERVAL_SECONDS` | `10.0` | 降级模式采样间隔（秒） |
| `FRAME_DESC_DEGRADED_PREFIX` | `"（描述服务暂不可用，仅供参考）"` | 降级描述前缀 |
| `FRAME_DESC_MAX_FRAMES_PER_REQUEST` | `3` | 单次最大帧数 |
| `FRAME_DESC_MAX_FRAME_SIZE` | `640` | 输入帧最大边长（像素） |
| `FRAME_DESC_TOKEN_BUDGET` | `6000` | Token 预算上限 |
| `FRAME_DESC_AUTO_DEGRADE` | `True` | 超预算时自动降级 |

> 注意：熔断器复用已有的全局 `VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD`（默认 3）和 `VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS`（默认 30.0），未单独为帧描述服务新增同名配置。

---

## 三、测试验证结果

### 3.1 后端测试（40项全绿）

```
tests/unit/test_frame_description_service.py   24 passed
tests/api/test_frame_description_api.py       10 passed
tests/smoke/test_app_startup.py               6 passed
total: 40 passed
```

**单元测试覆盖**：
- `TestComputeTextSimilarity`：Jaccard 相似度（4项）
- `TestNormalizeFrames`：Base64 解码（4项）
- `TestSafeHistory`：上下文历史裁剪（1项）
- `TestBuildDescriptionPrompt`：提示词模板（4项）
- `TestVinciCircuitBreaker`：熔断器状态机（3项）
- `TestFrameDescriptionService`：禁用/空帧/会话生命周期（3项）
- `TestFrameDescriptionDegradedMode`：降级返回/禁用降级抛错（2项）
- `TestPromptTemplates`：版本化提示词（2项）
- `test_session_reuse_same_history`：历史复用（1项）

### 3.2 前端测试（20项全绿）

```
npm test → 20 passed (node --test tests/**/*.test.mjs)
```

覆盖：文件存在、函数导出、NDJSON 解析、端点路径、mock 模式、detail_level、allow_degrade、会话 action、Player.vue 集成等。

---

## 四、架构修正：Vinci 调用接入 Governance Gateway

### 修正前（不符合安全要求）

```python
# frame_description_service.py 旧代码
client = VinciClient()
result = client.request_chat(...)  # ❌ 直接绕过 governance
```

### 修正后（符合安全要求）

```python
# frame_description_service.py 新代码
from app.agents.governance.gateway import execute_tool
from app.agents.exceptions import GovernanceError

def _call_vinci_sync(self, prompt, session_id, trace_id, db):
    # 1. 检查本服务层熔断器（"熔断打开 -> 直接降级"决策）
    blocked, _, _ = self._cb.is_blocked()
    if blocked:
        raise FrameDescServiceError("Vinci circuit breaker is open (service layer)")

    # 2. 通过 governance gateway execute_tool 执行（白名单 + 参数校验 + 审计）
    try:
        result = execute_tool(
            "lf_frame_description",
            {"prompt": prompt, "history": [], "session_id": session_id},
            db=db,
            trace_id=trace_id,
            pipeline="frame_description",
        )
        answer = str(result.get("answer") or "").strip()
        return answer, None
    except GovernanceError as exc:
        self._cb.record_failure(str(exc))
        raise FrameDescServiceError(f"governance rejected: {exc}") from exc
```

**双熔断器职责分离**：

| 熔断器 | 位置 | 职责 |
|--------|------|------|
| `VinciAdapterService` 内置 | `vinci_adapter_service.py` | 保护 Vinci 服务本身（3次失败打开/30s恢复） |
| `FrameDescriptionService._cb` | `frame_description_service.py` | 决定"熔断打开时是否降级跳过推理" |

---

## 五、代码结构修正：消除重复类定义

### 修正前

```python
# frame_description_service.py（行 162-177 为冗余 stub）
class _VinciCircuitBreaker:  # stub：只有 __init__，无实际方法
    _CIRCUITS = {}
    _LOCK = threading.Lock()

class _VinciCircuitBreaker:  # 完整版：覆盖前者
    _CIRCUITS = {}
    _GLOBAL_LOCK = threading.RLock()
    def is_blocked(...): ...
    def record_failure(...): ...
    def record_success(...): ...
```

### 修正后

```python
# frame_description_service.py（唯一定义）
class _VinciCircuitBreaker:
    """防止对不可用服务持续发送请求。使用 threading.RLock() 保护状态。"""
    _CIRCUITS: dict[str, _VinciCircuitBreakerState] = {}
    _GLOBAL_LOCK = threading.RLock()
    def is_blocked(...): ...
    def record_failure(...): ...
    def record_success(...): ...
```

---

## 六、功能验收清单

| 功能 | 状态 | 证据 |
|------|------|------|
| 单帧 → 流式描述（NDJSON） | ✅ | `describe_frames` generator yields NDJSON |
| 上下文融合（最近 5 条） | ✅ | `_build_fusion_prompt` + `context_history` |
| 相似帧去重（Jaccard ≥ 0.82） | ✅ | `_compute_text_similarity` + 连续 4 次阈值跳过 |
| 双熔断降级（adapter + service） | ✅ | `VinciAdapterService` + `_VinciCircuitBreaker` |
| 自动恢复（30s 后半开） | ✅ | Circuit breaker HALF_OPEN 状态 |
| 会话管理（start/stop） | ✅ | `start_session` / `stop_session` |
| 健康检查端点 | ✅ | `GET /frame_description/health` |
| 配置开关（FRAME_DESC_ENABLED） | ✅ | `config.py` |
| 前端 NDJSON 流解析 | ✅ | `frameDescription.js` TextDecoder |
| 前端 Mock 模式 | ✅ | `dispatchMockStream` |
| 帧捕获（canvas.toDataURL） | ✅ | `Player.vue` `captureVideoFrame()` |
| 详细度档位（brief/standard/detailed） | ✅ | 3档 prompt 模板 |
| 上下文折叠展开（最近 5 条） | ✅ | `fdContextExpanded` + `fdRecentHistory` |
| 反馈按钮（准确/不准确） | ✅ | `submitFdFeedback(accurate)` |
| Governance gateway 接入 | ✅ | `execute_tool("lf_frame_description", ...)` |
| 集中式遥测 | ✅ | `_emit_telemetry` → `analytics.pipeline` |
| 轨迹复利缓冲 | ✅ | `_FRAME_DESC_TRAJECTORY_BUFFER` |

---

## 七、7项系统质量要求验收

| 要求 | 结论 | 证据 |
|------|------|------|
| **Effective** | ✅ | 规划(采样+上下文)/执行(Vinci+governance)/验证(去重) 职责分离 |
| **Efficient** | ✅ | Token 预算(6000)、Jaccard 去重(≥0.82)、连续 4 次稳定跳过、3s 采样间隔 |
| **Safe** | ✅ | `execute_tool` 白名单 + 参数校验 + 审计事件 |
| **Robust** | ✅ | 双熔断降级、30s 自动恢复、页面卸载清理、ContextVar 线程隔离 |
| **Monitorable** | ✅ | 集中式遥测：success/latency/failure/degraded/sampled/generated |
| **Updatable** | ✅ | 版本化提示词(PROMPT_TEMPLATES v1)、配置开关、回滚路径 |
| **Compounding** | ✅ | `_FRAME_DESC_TRAJECTORY_BUFFER` → 导出服务 |

---

## 八、API 契约

```
POST /api/frame_description/describe   ← NDJSON 流式描述
POST /api/frame_description/session   ← 会话管理（start/stop）
GET  /api/frame_description/health   ← 健康检查
```

**NDJSON 流事件序列**：
```
{"type":"status","stage":"connecting","progress":5}
{"type":"status","stage":"inferring","progress":35}
{"type":"description","delta":"老师正在黑板前书写公式","timestamp":30.5,"confidence":null}
{"type":"complete","stage":"completed","full_description":"...","timestamp":30.5,"degraded":false,"latency_ms":1842,"progress":100}
```

---

## 九、回滚路径

| 场景 | 操作 |
|------|------|
| 功能关闭 | `.env` → `FRAME_DESC_ENABLED=false` |
| 提示词回滚 | 将 `_build_fusion_prompt` 改回 `PROMPT_TEMPLATES["v1"]` |
| 前端下线 | `Player.vue` 删除 `<section class="fd-panel">...</section>` |
| Governance 临时禁用 | 不建议禁用；紧急时仅通过 `FRAME_DESC_ENABLED=false` 关闭功能 |

---

## 十、已解决问题清单

| # | 问题 | 根因 | 解决方案 |
|---|------|------|---------|
| 1 | 报告配置项名与代码不符 | 未读取实际 config.py | 逐项核对，修正为实际命名和默认值 |
| 2 | Vinci 调用绕过 governance | 直接用 VinciClient | 改为 `execute_tool("lf_frame_description", ...)` 统一网关执行 |
| 3 | `_VinciCircuitBreaker` 定义两次 | stub 残留 | 删除行 162-177 的冗余定义 |
| 4 | 前端测试为注释不可执行 | 误解测试要求 | 重写为 `frameDescription.test.mjs`，20个断言全绿 |
| 5 | `package.json` 测试不递归 | `tests/*.test.mjs` | 改为 `tests/**/*.test.mjs` |
| 6 | 报告将 Player.vue 列为新增 | 误解文件状态 | 已更正为"修改（M）" |

---

*本报告基于实际代码修正，配置项、测试结果、文件状态均与代码一致。确认后请告知是否执行 git commit。*
