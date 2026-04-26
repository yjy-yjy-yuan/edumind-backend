# Vinci Integration - Milestone Commit List (M1~M5)

## 1. 已完成里程碑提交

| Milestone | Commit | Subject |
|---|---|---|
| M1-1/M1-2 基线接入 | `9c20a22` | feat: add vinci adapter baseline and sync m1 docs |
| M1-3 治理接入 | `04e3546` | feat: enforce vinci governance path and sync m1-3 docs |
| M1-4 可观测与运维 | `dabaa5d` | feat: add vinci m1-4 observability metrics and runbook |
| M1-4 运维接口补齐 | `b3faf6a` | feat: add vinci ops metrics endpoint and alert rule templates |
| M2 编排接入 | `066da7f` | feat(agent): route vinci summary through governance pipeline |
| M2 治理防绕过修复 | `e137fba` | fix(agent): enforce governance context in vinci adapter |
| M2 回归测试增强 | `d2f6076` | test(agent): add vinci whitelist denial api regression |
| M2 审计测试增强 | `cbf4c41` | test(agent): assert denied audit event for vinci whitelist block |
| M3 告警阈值同步 | `4e4c3c2` | docs(monitoring): align vinci alert templates with runbook thresholds |
| M3 稳定性策略 | `c48cb0d` | feat(vinci): add circuit breaker and graceful fallback in learning flow |
| M3 验收证据归档 | `c6e5e3f` | docs: add M3 alerting acceptance evidence and sync vinci docs |
| M3 预发布准备脚本 | `daffcca` | feat: add vinci alerting acceptance prep tooling |
| M4 联调收口 | `46c0d34` | feat(m4): align vinci integration contracts and interop docs |
| M3 稳定性补丁回归 | 待本次提交生成 | fix(vinci): isolate circuit breaker state per adapter instance and sync docs |

## 2. M5 提交建议（本轮）

建议将 M5 交付收口文档作为独立单一职责提交：

- 建议 commit message：`docs(m5): finalize vinci delivery pack and release DoD`
