# Vinci 告警平台实接入验收记录（M3）

## 1. 验收目标

在本地真实 Grafana/Loki 环境完成以下闭环：

1. 导入 Vinci 告警规则并确认生效。
2. 触发一次降级告警演练。
3. 留存截图与日志/API 证据。
4. 给出可执行回滚步骤。

## 2. 验收时间与环境

- 验收时间（UTC）：2026-04-23 04:30 ~ 04:40
- 验收时间（Asia/Shanghai）：2026-04-23 12:30 ~ 12:40
- 仓库：`/Users/yuan/final-work/edumind-backend`
- 本地组件：
  - `grafana/grafana:11.1.4`
  - `grafana/loki:2.9.8`
- 启动编排文件：
  - `docs/monitoring/local/docker-compose.grafana-loki.yaml`

镜像拉取补充（网络受限环境）：

- 若 Docker Hub 拉取超时，可先使用镜像加速源拉取并本地打 tag：
  - `docker pull docker.m.daocloud.io/grafana/grafana:11.1.4`
  - `docker pull docker.m.daocloud.io/grafana/loki:2.9.8`
  - `docker tag docker.m.daocloud.io/grafana/grafana:11.1.4 grafana/grafana:11.1.4`
  - `docker tag docker.m.daocloud.io/grafana/loki:2.9.8 grafana/loki:2.9.8`

## 3. 生效阈值（与 Runbook 对齐）

- 错误率连续 5 分钟 > 15%
- 超时率连续 5 分钟 > 10%
- P95 连续 5 分钟 > 12000ms
- `degraded_count` 10 分钟窗口 > 20

规则文件：`docs/monitoring/grafana_loki_vinci_alert_rules.yaml`

## 4. 验收步骤与结果

### 4.1 规则导入

- Grafana 健康检查通过：`docs/monitoring/evidence/m3/grafana_health_after_fix.json`
- 规则导入数量 = 4：`docs/monitoring/evidence/m3/grafana_provisioning_alert_rules.json`
- 数据源与告警 provisioning 完成日志：`docs/monitoring/evidence/m3/grafana_logs_final.log`

说明：首次导入时发现规则兼容问题（`relativeTimeRange` 与表达式语法），已在同一规则文件完成修复后重新导入通过。

### 4.2 触发演练（降级突增）

- 向 Loki 注入 40 条 `status=degraded` 遥测事件：
  - payload：`docs/monitoring/evidence/m3/loki_push_payload_degraded_round2.json`
  - push 响应：`docs/monitoring/evidence/m3/loki_push_response_degraded_round2.json`
- Loki 查询验证已入库（示例批次 30 条）：
  - `docs/monitoring/evidence/m3/loki_query_after_push.json`
- 规则状态达到 `firing`：
  - `docs/monitoring/evidence/m3/prometheus_rules_degraded_firing.json`
- Alertmanager 中告警实例激活：
  - `docs/monitoring/evidence/m3/alerts_after_reduce_fix.json`
  - 关键字段：`alertname = Vinci Degraded Burst`，`state = active`，`startsAt = 2026-04-23T04:39:10.000Z`

### 4.3 截图证据

- 告警列表页截图：
  - `docs/monitoring/evidence/m3/grafana_alerting_list.png`
- 告警详情页截图：
  - `docs/monitoring/evidence/m3/grafana_alert_vinci_degraded_burst.png`

## 5. 验收结论

- 规则导入：通过
- 触发演练：通过（`Vinci Degraded Burst` 成功触发）
- 证据留存：通过（日志/API/截图齐全）

当前可以确认：Vinci 告警规则已可在真实 Grafana/Loki 链路中导入、评估并触发。

## 6. 回滚步骤

1. 停止本地验收栈：
   - `docker compose -f docs/monitoring/local/docker-compose.grafana-loki.yaml down -v`
2. 回滚规则文件到上一稳定版本（如需）：
   - `git checkout -- docs/monitoring/grafana_loki_vinci_alert_rules.yaml`
3. 删除本轮验收证据（如需清理）：
   - `rm -rf docs/monitoring/evidence/m3/*`
4. 按 Runbook 恢复业务侧配置：
   - 参考 `docs/VINCI_RUNBOOK.md` 的“恢复步骤/回滚步骤”。

## 7. 复现实验命令（本轮实操）

```bash
docker compose -f docs/monitoring/local/docker-compose.grafana-loki.yaml up -d
curl -u admin:admin http://localhost:3000/api/health
curl -u admin:admin http://localhost:3000/api/v1/provisioning/alert-rules
curl -u admin:admin http://localhost:3000/api/prometheus/grafana/api/v1/rules
curl -X POST "http://localhost:3100/loki/api/v1/push" -H "Content-Type: application/json" --data-binary @docs/monitoring/evidence/m3/loki_push_payload_degraded_round2.json
curl -u admin:admin http://localhost:3000/api/alertmanager/grafana/api/v2/alerts
```

预发布/生产接线建议：

- 使用 `scripts/run_vinci_alerting_acceptance_prep.py` 生成演练 payload 与命令清单。
- 生成物示例路径：`docs/monitoring/evidence/m3/preprod_prep/`。
- 生成的命令模板默认使用 `GRAFANA_USER` / `GRAFANA_PASSWORD` 环境变量，不会把口令写入文件。
