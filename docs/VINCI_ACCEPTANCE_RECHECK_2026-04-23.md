# EduMind + Vinci 融合架构验收报告（修订版）

> 评估日期：2026-04-23  
> 评估分支：`0422-codex/edumind-vinci-integration-operable-maintainable`  
> 评估提交：`46c0d34`

## 一、结论摘要

原报告结论属于“**部分真实但明显过时**”。

- 真实：治理网关、集中式遥测、Vinci 适配降级/熔断、运维指标接口等核心能力已落地。
- 过时/不实：部分路径与类名已变化（如 `learning_flow_agent.py`、`BudgetService`、`GovernanceService`），以及“无降级/无白名单/无 compounding”的描述与当前代码不符。
- 最终判定：**不应直接作为当前版本验收依据**，应替换为本修订版。

### 1.1 本轮补强同步（2026-04-23）

本轮已补齐以下工程能力并完成回归验证：

- `PromptEngine`：三层组装与 token 感知截断（`app/agents/prompt_engine.py`）
- `SkillRegistry`：版本化技能与灰度路由（`app/agents/skill_registry.py`）
- `AgentTrajectoryRecorder`：JSONL/DB 双后端轨迹记录（`app/agents/trajectory.py`）
- `ResumableTask`：阶段级断点续传状态机（`app/tasks/resumable_state_machine.py`）
- 新增模型与迁移：
  - `app/models/agent_trajectory.py`
  - `app/models/task_checkpoint.py`
  - `alembic/versions/001_agent_tables.py`
- 关键回归：
  - `.venv/bin/pytest tests/unit/test_agent_new_modules.py ... tests/smoke/test_app_startup.py -q`
  - 结果：`72 passed`

---

## 二、逐项纠偏（原报告 vs 当前代码）

### 2.1 路径与实现映射纠偏

| 原说法 | 当前事实 | 证据 |
|---|---|---|
| `app/agents/pipelines/learning_flow_agent.py` 为主编排 | 当前主编排文件为 `learning_flow_pipeline.py` | `app/agents/pipelines/learning_flow_pipeline.py` |
| `BudgetService` | 当前为 `TokenBudget` | `app/agents/budget.py` |
| `GovernanceService` | 当前为治理网关 + 执行上下文阻断 | `app/agents/governance/gateway.py`, `app/agents/governance/context.py` |

### 2.2 关键能力纠偏

| 原说法 | 当前事实 | 证据 |
|---|---|---|
| 没有白名单工具机制 | 已有 `_TOOL_HANDLERS` 白名单 | `app/agents/governance/gateway.py` |
| 可绕过治理直接调工具 | 已有 `ensure_in_governance_context()` 阻断直调 | `app/agents/governance/context.py` |
| Vinci 不可用无降级 | 已有降级返回、错误码、熔断与恢复窗口 | `app/services/vinci_adapter_service.py` |
| 无可观测聚合接口 | 已有 `/api/ops/vinci/metrics` | `app/routers/ops.py` |
| Robust 完全缺失 | 已有重启时中断任务恢复与维护脚本 | `app/main.py`, `scripts/robust_maintenance.py` |
| Compounding 完全缺失 | 已有 compounding 导出链路与单测 | `app/compounding/`, `scripts/export_compounding_trajectories.py`, `tests/unit/test_compounding_export.py` |

---

## 三、7 项要求重新验收（按当前代码）

| 维度 | 结论 | 说明 |
|---|---|---|
| Effective（有效） | ⚠️ 部分满足 | 已有 Planner/Executor/Validator 流程分层，但仍在同一后端进程编排，不是多 agent 运行时隔离。 |
| Efficient（高效） | ⚠️ 部分满足 | 有 `TokenBudget`，并存在缓存组件；但缺统一 PromptEngine 与严格 tokenizer 级预算闭环。 |
| Safe（安全） | ✅ 基本满足 | 治理网关白名单+参数校验+审计+绕过阻断链路已完整，且有对应单测。 |
| Robust（稳健） | ⚠️ 部分满足 | 有中断恢复与维护脚本；但尚未形成“阶段级断点续跑”的通用状态机。 |
| Monitorable（可监控） | ✅ 满足 | 集中式遥测与告警、Vinci module metrics、ops 接口均已落地。 |
| Updatable（可更新） | ⚠️ 部分满足 | 有版本标识与模块化治理工具；但“提示词分段 + 技能注册中心”仍可增强。 |
| Compounding（可复利改进） | ⚠️ 部分满足 | 已有结构化导出与反馈格式；但未形成完整 OS 级在线闭环（自动标注/训练/回灌）。 |

---

## 四、验证证据

### 4.1 代码证据（关键）

- 编排主干：`app/agents/pipelines/learning_flow_pipeline.py`
- 预算系统：`app/agents/budget.py`
- 治理网关：`app/agents/governance/gateway.py`
- 绕过阻断：`app/agents/governance/context.py`
- Vinci 适配（错误映射/降级/熔断）：`app/services/vinci_adapter_service.py`
- 观测接口：`app/routers/ops.py`
- 集中式遥测：`app/analytics/pipeline.py`
- 中断恢复：`app/main.py`
- Compounding 导出：`app/compounding/export_service.py`

### 4.2 自动脚本证据

```bash
python scripts/validate_system_requirements.py
```

当前输出：`all_ok: true`。  
注意：该脚本是“结构/关键字存在性”校验，不等于完整生产成熟度评审。

### 4.3 测试证据（本次复核）

```bash
pytest tests/unit/test_agent_governance_gateway.py tests/unit/test_vinci_adapter_service.py tests/api/test_vinci_ops_api.py -q
```

结果：`22 passed, 1 warning`。

---

## 五、当前真实风险与建议

### 5.1 仍需补强的高优先级项

1. 将 Planner/Executor/Validator 进一步做运行时隔离（至少执行与验证隔离）。
2. 建立统一 PromptEngine（模板分段、tokenizer 精确预算、历史压缩策略）。
3. 补“阶段级断点续跑”而非仅重启后置失败。
4. 将 compounding 从离线导出扩展为可回灌训练/评测闭环。

### 5.2 验收使用建议

- 对外验收请使用本修订版，不再使用旧版结论。
- 旧版中“治理/降级/可观测缺失”类结论应标记为历史状态。
