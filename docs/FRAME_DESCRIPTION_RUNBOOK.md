# Frame Description 服务运维手册（Runbook）

> 适用版本：EduMind Backend v2.0+
> 最后更新：2026-04-24

---

## 1. 服务概述

实时画面描述（Frame Description）是一项在视频播放过程中持续采样视频帧并输出当前画面描述的能力。

### 1.1 架构链路

```
前端 Player.vue
  └─► /api/frame_description/describe  (POST, NDJSON 流式)
         └─► FrameDescriptionService.describe_frames()
                ├─► governance gateway execute_tool("lf_frame_description")
                │     └─► tool_lf_frame_description()
                │           └─► VinciAdapterService.request_chat()
                │                 └─► Vinci 服务 (外部)
                ├─► 场景去重 (相似度阈值 0.82)
                └─► 上下文融合 (最近 N 条描述历史)
                      ├─► 正常: Vinci 推理
                      └─► 降级: 降级文本 + 熔断器打开
```

### 1.2 核心端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/frame_description/describe` | POST | NDJSON 流式画面描述 |
| `/api/frame_description/session` | POST | 会话管理（start/stop） |
| `/api/frame_description/health` | GET | 服务健康检查 |

---

## 2. 配置项

所有配置均在 `.env` 中管理，修改后需重启后端。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `FRAME_DESC_ENABLED` | `false` | 功能总开关 |
| `FRAME_DESC_SAMPLE_INTERVAL_SECONDS` | `3.0` | 采样间隔（秒），前端实际每 8s 发送一帧 |
| `FRAME_DESC_TIMEOUT_SECONDS` | `8.0` | Vinci 单次推理超时（秒） |
| `FRAME_DESC_CONTEXT_WINDOW_SIZE` | `5` | 上下文历史窗口大小 |
| `FRAME_DESC_SIMILARITY_THRESHOLD` | `0.82` | 场景去重相似度阈值 |
| `FRAME_DESC_SCENE_STABLE_THRESHOLD` | `4` | 连续相似次数达到此值则跳过推理 |
| `FRAME_DESC_DEGRADED_INTERVAL_SECONDS` | `10.0` | 降级模式下轮询间隔 |
| `FRAME_DESC_DEGRADED_PREFIX` | `（描述服务暂不可用，仅供参考）` | 降级文本前缀 |
| `FRAME_DESC_AUTO_DEGRADE` | `true` | 推理失败时是否自动降级 |
| `VINCI_BASE_URL` | — | Vinci 服务地址 |
| `VINCI_API_KEY` | — | Vinci API 密钥 |
| `VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `3` | 熔断器失败阈值 |
| `VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS` | `30` | 熔断器恢复等待秒数 |

---

## 3. 故障分级与响应

### P0 — 服务不可用（立即响应）

**症状**：`/api/frame_description/describe` 返回 503 或持续超时。

**排查步骤**：

```bash
# Step 1: 检查功能开关
curl http://127.0.0.1:2004/api/frame_description/health
# 期望: {"enabled": true, "service": "active", "description": "实时画面描述服务"}

# Step 2: 检查 Vinci 服务是否可达
curl -s http://<VINCI_HOST>:8010/health || echo "Vinci unreachable"

# Step 3: 检查后端日志
grep -i "frame_desc\|vinci" /var/log/edumind/app.log | tail -50

# Step 4: 检查熔断器状态（通过遥测日志）
grep "circuit_open\|circuit_breaker" /var/log/edumind/app.log | tail -20
```

**快速止血**：
```bash
# 临时关闭功能（止血）
# 编辑 .env
FRAME_DESC_ENABLED=false

# 重启后端
pkill -f "uvicorn app.main:app" && python run.py &
```

### P1 — 降级模式（30 分钟响应）

**症状**：描述文本显示"描述服务暂不可用"、badge 显示"降级模式"。

**排查步骤**：

```bash
# 检查遥测日志中的降级事件
grep "frame_desc_inference_degraded\|frame_desc_circuit_open" /var/log/edumind/app.log | tail -20

# 检查 Vinci 响应延迟
# 正常 P95 < 4s；如延迟高则需扩容或联系 Vinci 团队
```

**自动恢复**：熔断器 30s 后自动进入探针模式，成功一次则恢复正常。

### P2 — 描述质量差（4 小时响应）

**症状**：描述内容不准确、重复、延迟过高。

**排查步骤**：

```bash
# 检查轨迹缓冲（compounding 导出）
curl -s http://127.0.0.1:2004/api/ops/metrics | grep frame_desc

# 检查 P95 延迟（遥测）
# 正常: < 4s；如 > 8s 需调整 FRAME_DESC_TIMEOUT_SECONDS
```

**调优建议**：
- 延迟高：增大 `FRAME_DESC_TIMEOUT_SECONDS`
- 描述重复：调高 `FRAME_DESC_SIMILARITY_THRESHOLD`（如 0.88）
- 上下文不融合：调大 `FRAME_DESC_CONTEXT_WINDOW_SIZE`

---

## 4. 运维命令

### 4.1 启用功能

```bash
# 编辑 .env
FRAME_DESC_ENABLED=true
VINCI_BASE_URL=http://your-vinci-host:8010
VINCI_API_KEY=your_key

# 重启后端
pkill -f "uvicorn app.main:app"
python run.py &
```

### 4.2 禁用功能

```bash
# 编辑 .env
FRAME_DESC_ENABLED=false

# 重启后端
pkill -f "uvicorn app.main:app"
python run.py &
```

### 4.3 健康检查

```bash
# 基础健康检查
curl -s http://127.0.0.1:2004/api/frame_description/health | python -m json.tool

# 预期响应
{
    "enabled": true,
    "service": "active",
    "description": "实时画面描述服务"
}
```

### 4.4 测试流式端点

```bash
# 测试流式端点（返回 NDJSON）
curl -X POST http://127.0.0.1:2004/api/frame_description/describe \
  -H "Content-Type: application/json" \
  -H "Accept: application/x-ndjson" \
  -d '{
    "video_id": 1,
    "frames": ["/9j/4AAQSkZJRg=="],
    "timestamp": 10.0,
    "detail_level": "standard"
  }' \
  --no-buffer
```

### 4.5 查看轨迹缓冲

```bash
# 通过遥测指标查看（需有遥测端点）
curl -s http://127.0.0.1:2004/api/ops/metrics | grep frame_desc
```

---

## 5. 监控告警

### 5.1 建议告警规则

| 指标 | 告警条件 | 严重性 | 动作 |
|------|----------|--------|------|
| `frame_desc_inference_degraded` 事件数 | 5min > 10 次 | P1 | 检查 Vinci 服务 |
| P95 推理延迟 | > 8s | P1 | 检查网络/Vinci 负载 |
| `frame_desc_circuit_open` 事件数 | 1h > 3 次 | P0 | 立即检查 Vinci 可用性 |
| 功能启用率 | < 80% | P2 | 检查配置是否正确 |

### 5.2 日志关键词

```bash
# 关键日志标签
frame_desc_circuit_open       # 熔断器打开
frame_desc_inference_degraded  # 降级触发
frame_desc_completed           # 正常完成（遥测）
frame_desc_session_started     # 会话开启
frame_desc_session_stopped     # 会话关闭
lf_frame_description          # governance gateway 调用
```

---

## 6. 升级与提示词管理

### 6.1 提示词版本

提示词版本化管理位于 `app/services/frame_description_service.py`：

```python
PROMPT_TEMPLATES = {
    "v1": PromptTemplate(
        version="v1",
        description="初始版本：标准提示词模板",
    ),
}
```

升级提示词：
1. 添加新版本 `v2` 到 `PROMPT_TEMPLATES`
2. 修改 `get_active_prompt_template()` 返回 `"v2"`
3. 观察 24h 轨迹质量
4. 如质量下降，回切到 `"v1"`（见回滚手册）

### 6.2 灰度策略

建议使用环境变量控制：

```bash
# 通过 FRAME_DESC_ENABLED=false 快速关闭
# 通过 VINCI_BASE_URL 切换到备用 Vinci 实例
```
