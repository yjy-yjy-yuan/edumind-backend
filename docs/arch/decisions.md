# Architecture Decisions

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## ADR-2026-05-09-001: 建立结构化 docs 日志体系

| 字段 | 内容 |
|---|---|
| 状态 | Accepted |
| 日期 | 2026-05-09 |
| 决策者 | 项目维护流程 |
| 影响范围 | `docs/` |

### Context

仓库已存在运行手册、Frame Description/Vinci 交付文档、根级 `CHANGELOG.md` 与 `COMMIT_LOG.md`，但缺少统一的长期索引、功能/Bug/发布/测试/参考分类日志。后续开发需要可审计、可增量维护的文档入口。

### Decision

在 `docs/` 下建立固定目录：`arch/`, `architecture/`, `bugs/`, `features/`, `guides/`, `legacy/`, `prompts/`, `reference/`, `summaries/`, `testing/`, `updates/`，并使用 `docs/INDEX.md` 作为统一导航与状态页。

### Consequences

| 类型 | 说明 |
|---|---|
| 正向影响 | 后续功能、Bug、架构和发布记录有固定落点，降低历史信息散落风险 |
| 维护成本 | 每次功能或修复需要同步更新对应 docs 日志 |
| 风险 | 如果开发结束时未同步文档，`docs/` 与代码状态可能漂移 |

### Follow-up

| 后续动作 | 状态 |
|---|---|
| 将 docs 更新纳入提交检查清单 | Planned |
| 后续架构调整继续使用 ADR 格式 | Active |

## ADR-2026-05-04-001: Frame Description 默认使用 Qwen3VL 主路径

| 字段 | 内容 |
|---|---|
| 状态 | Accepted |
| 日期 | 2026-05-04 |
| 影响范围 | `app/services/frame_description_service.py`, `app/services/qwen3vl_realtime_client.py`, `app/core/config.py` |

### Context

历史 Vinci 微服务链路较重，当前配置中 `FRAME_DESC_BACKEND` 默认值为 `qwen3vl`，并保留 `vinci` 作为 legacy 兼容路径。

### Decision

Frame Description 主链路采用 Local Qwen3VL，云端 Qwen-VL 可作为显式启用的 fallback，Vinci 仅在 `FRAME_DESC_BACKEND=vinci` 时使用。

### Consequences

| 类型 | 说明 |
|---|---|
| 正向影响 | 本地开发与云端部署可以解耦，默认路径更轻 |
| 风险 | Qwen3VL 本地服务未启动时需要依赖 fallback 或字幕模式 |
| 监控 | `app/routers/frame_description.py` 健康检查与 debug 日志辅助定位 |
