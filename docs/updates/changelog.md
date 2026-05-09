# Docs Changelog

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 2026-05-09

| 类型 | 模块 | 文件 | 影响范围 |
|---|---|---|---|
| docs | INDEX 全局入口 | `docs/INDEX.md`, `docs/prompts/workflow.md`, `docs/summaries/session-history.md` | 固化全局上下文入口、Prompt Registry、Session 恢复中心和日志完整性规则 |
| docs | 文档体系初始化 | `docs/INDEX.md`, `docs/MILESTONES.md` | 新增长期文档入口与里程碑 |
| docs | 架构 | `docs/arch/*`, `docs/architecture/*` | 记录系统设计、技术栈、后端/API/数据库/前端对接边界 |
| docs | 功能与 Bug | `docs/features/*`, `docs/bugs/*` | 建立功能完成、计划、Bug 修复和回归风险日志 |
| docs | 参考与指南 | `docs/guides/*`, `docs/reference/*` | 汇总 setup、deploy、release、commands、env、conventions |
| docs | 测试与总结 | `docs/testing/*`, `docs/summaries/*` | 记录测试范围、性能关注、daily/weekly/session 历史 |
| docs | 发布 | `docs/updates/*` | 建立版本历史与 release notes 入口 |

## 2026-05-08

| 类型 | 模块 | 文件 | 影响范围 |
|---|---|---|---|
| feature | Frame Description | `app/services/qwen_vl_cloud_client.py`, `app/services/frame_description_service.py`, `docs/FRAME_DESCRIPTION_QWEN3VL_CLOUD_FALLBACK.md` | 本地 Qwen3VL 不可用时可显式启用 Cloud Qwen-VL fallback |
