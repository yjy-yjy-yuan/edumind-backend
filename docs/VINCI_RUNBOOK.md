# Vinci Integration Runbook（M1-4）

本 Runbook 面向 EduMind 后端的 Vinci 适配层运维，覆盖常见故障、排查步骤、恢复与回滚建议。

## 1. 观测指标

建议基于 `app.analytics.telemetry` 中 `module=vinci` 的事件做聚合，核心指标如下：

- 成功率：`success_rate = ok / total`
- 错误率：`error_rate = error / total`
- 超时率：`timeout_rate = timeout / total`
- 降级触发计数：`degraded_count = count(status=degraded)`
- P95 延迟：`p95_latency_ms`

可通过 `AnalyticsTelemetry.module_metrics("vinci")` 在进程内获取窗口快照：

- `total`
- `success_count` / `error_count` / `timeout_count` / `degraded_count`
- `success_rate` / `error_rate` / `timeout_rate` / `degraded_rate`
- `p95_latency_ms`

## 2. 告警建议阈值

默认阈值来自 `.env`：

- `ANALYTICS_ALERT_MAX_FAILURE_RATE=0.15`
- `ANALYTICS_ALERT_MAX_TIMEOUT_RATE=0.10`
- `ANALYTICS_ALERT_MAX_P95_LATENCY_MS=12000`
- `ANALYTICS_ALERT_LATENCY_TIMEOUT_MS=30000`
- `ANALYTICS_ALERT_MIN_INTERVAL_SEC=60`
- `VINCI_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3`
- `VINCI_CIRCUIT_BREAKER_RECOVERY_SECONDS=30`

建议告警策略：

1. 错误率连续 5 分钟 > 15% 报警。
2. 超时率连续 5 分钟 > 10% 报警。
3. P95 连续 5 分钟 > 12s 报警。
4. `degraded_count` 在 10 分钟窗口超过 20 次时报警（提示上游不稳定）。
5. 若 `vinci_circuit_opened` 持续触发，优先检查上游可用性并评估阈值是否过于激进。

实际平台接入（Grafana/Loki）：

- 规则模板文件：`docs/monitoring/grafana_loki_vinci_alert_rules.yaml`
- 覆盖：错误率、超时率、P95 延迟、降级突增四类告警
- 接入方式：将模板导入 Grafana Alerting（Unified Alerting），并按你们环境替换 `datasourceUid` 与标签过滤条件。
- 实接入验收记录：`docs/monitoring/VINCI_ALERTING_ACCEPTANCE_M3.md`

## 3. 故障现象

- 用户请求返回降级文案（`VINCI_UNAVAILABLE`）显著增多。
- 日志出现 `vinci_request_timeout` 或 `vinci_request_error` 密集事件。
- 日志出现 `vinci_circuit_opened`，短时间内大量 `VINCI_CIRCUIT_OPEN` 降级响应。
- SSE 流异常中断，仅出现 `error` + `done` 事件。
- P95 延迟突增，且超时率提升。

## 4. 排查步骤

1. 确认 Vinci 服务地址配置：
   - `VINCI_ENABLED`
   - `VINCI_BASE_URL`
   - `VINCI_CHAT_PATH`
   - `VINCI_STREAM_PATH`
2. 检查网络连通性与 DNS：
   - 从后端节点 curl Vinci 健康检查/接口。
3. 检查鉴权：
   - `VINCI_API_KEY` 是否过期或缺失。
4. 查看治理与遥测日志：
   - `agent_tool_failed`（治理失败）
   - `vinci_request_error` / `vinci_request_timeout` / `vinci_request_degraded`
   - `vinci_circuit_opened` / `vinci_circuit_recovered`
5. 检查上游资源：
   - Vinci 服务 CPU/内存/连接数
   - 上游模型服务是否超时

## 5. 恢复步骤

1. 若 Vinci 瞬时不可用：
   - 允许系统继续降级，观察 `degraded_count` 回落。
2. 若 Vinci 长时间不可用：
   - 临时将 `VINCI_ENABLED=false`，避免持续超时拖慢主链路。
3. 若仅超时高：
   - 适度提升 `VINCI_REQUEST_TIMEOUT_SECONDS` / `VINCI_STREAM_TIMEOUT_SECONDS`
   - 同时评估 Vinci 侧容量和限流策略。
4. 若错误率高（非超时）：
   - 重点检查接口契约变更与鉴权配置。

## 6. 回滚步骤

1. 回滚到上一稳定后端版本（包含已验证的 Vinci 接入代码）。
2. 保留治理路径，不要绕过 `execute_tool` 直接调用 Vinci。
3. 回滚后确认：
   - `tests/unit/test_vinci_adapter_service.py`
   - `tests/unit/test_agent_governance_gateway.py`
   - `tests/unit/test_analytics_alerting.py`
   全部通过。

## 6.1 回滚触发条件（建议）

满足任一条件建议立即触发回滚或降级开关：

1. 连续 10 分钟 `error_rate > 20%` 且无回落趋势。
2. 连续 10 分钟 `timeout_rate > 15%` 且 `vinci_circuit_opened` 高频触发。
3. 连续 10 分钟 `p95_latency_ms > 20s`，明显影响主流程时延。
4. `degraded_count` 在 10 分钟窗口持续 > 50 且用户可感知失败激增。
5. 发现治理绕过风险（存在未经过 `execute_tool` 的 Vinci 调用路径）。

建议回滚优先级：

- 一级：先将 `VINCI_ENABLED=false`，确保主流程继续可用。
- 二级：回滚到上一稳定版本并执行最小回归测试。

## 7. 验证命令（变更后）

```bash
pytest tests/unit/test_vinci_adapter_service.py -v
pytest tests/unit/test_agent_governance_gateway.py -v
pytest tests/unit/test_analytics_alerting.py tests/unit/test_analytics_pipeline.py -v
```

Grafana/Loki 本地验收（规则导入 + 触发演练）：

```bash
docker compose -f docs/monitoring/local/docker-compose.grafana-loki.yaml up -d
curl -u admin:admin http://localhost:3000/api/health
curl -u admin:admin http://localhost:3000/api/v1/provisioning/alert-rules
curl -u admin:admin http://localhost:3000/api/prometheus/grafana/api/v1/rules
```

预发布/生产接线准备（生成演练 payload + 命令清单）：

```bash
. .venv/bin/activate
python scripts/run_vinci_alerting_acceptance_prep.py \
  --grafana-url "https://grafana.pre.example.com" \
  --loki-url "https://loki.pre.example.com" \
  --grafana-user "${GRAFANA_USER}" \
  --grafana-password "${GRAFANA_PASSWORD}" \
  --event-count 40 \
  --trace-prefix "vinci-preprod-drill" \
  --output-dir "docs/monitoring/evidence/m3/preprod_prep"
```

脚本会生成：

- `vinci_degraded_drill_payload.json`（注入 Loki 的降级演练事件）
- `vinci_alerting_acceptance_commands.sh`（健康检查、规则导入校验、告警状态校验命令）

执行命令清单前请先设置鉴权环境变量：

```bash
export GRAFANA_USER="your_user"
export GRAFANA_PASSWORD="your_password"
```

## 8. 上线风险清单（M5 收口）

1. **上游依赖波动风险**：Vinci 不可用或高时延会触发降级，需确保降级文案与产品预期一致。
2. **阈值配置风险**：熔断阈值过敏可能导致过早降级，阈值过宽可能导致错误扩散。
3. **观测盲区风险**：若日志采集链路异常，将影响告警及时性。
4. **治理策略漂移风险**：白名单/参数边界变更需同步测试与文档，避免线上误拒绝。
5. **发布窗口风险**：上线后前 30 分钟需重点观察 `/api/ops/vinci/metrics` 与告警状态。
