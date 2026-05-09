# Milestones

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 里程碑总览

| 日期 | 状态 | 里程碑 | 证据 |
|---|---|---|---|
| 2026-05-09 | Completed | 初始化长期 docs 日志体系 | `docs/INDEX.md` |
| 2026-05-08 | Completed | Frame Description Cloud Qwen-VL fallback | `CHANGELOG.md`, `docs/FRAME_DESCRIPTION_QWEN3VL_CLOUD_FALLBACK.md` |
| 2026-05-06 | Completed | Frame Description 用户可见降级文案优化 | `CHANGELOG.md` |
| 2026-05-04 | Completed | Qwen3-VL 本地模型双后端架构 | `CHANGELOG.md`, `docs/FRAME_DESCRIPTION_RUNBOOK.md` |
| 2026-04-27 | Completed | 视频软删除、删除流稳定、标签匹配搜索增强 | `CHANGELOG.md` |
| 2026-04-26 | Completed | 多用户会话隔离 | `docs/MULTI_USER_SESSION_ISOLATION_DELIVERY_2026-04-26.md` |
| 2026-04-23 | Completed | Vinci M3 告警、M2 断路器、M5 验证文档 | `docs/VINCI_M5_VERIFICATION_REPORT.md` |
| 2026-04-21 | Completed | EduMind Backend 初始服务 | `COMMIT_LOG.md` |

## 当前里程碑风险

| 风险 | 影响 | 当前控制 |
|---|---|---|
| Frame Description 依赖本地或云端视觉模型 | 上游不可用时实时描述质量下降 | 已存在字幕 fallback 与 minimal safe response |
| 历史 Vinci 文档与新 Qwen3VL 主路径并存 | 维护者可能误读默认链路 | 本次 docs 初始化在 `legacy/` 与 `architecture/` 中明确标注 legacy |
| `.env*` 本地文件存在未跟踪/改动 | 误提交 secrets 风险 | 文档继续强调不得提交 secrets，当前任务未触碰 `.env*` |
