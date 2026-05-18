# Project Overview

最近更新时间：2026-05-18 00:00:00 Asia/Shanghai

## 项目简介

| 项目 | 内容 |
|---|---|
| 名称 | EduMind Backend |
| 定位 | FastAPI 独立后端，负责认证、视频、字幕、问答、笔记、推荐、语义搜索、智能体治理与实时画面描述 |
| 代码入口 | `app/main.py` |
| 配置入口 | `app/core/config.py` |
| 默认端口 | `2004` |
| 健康检查 | `/health`, `/api/health` |

## 当前状态

| 项目 | 当前状态 | 依据 |
|---|---|---|
| API 服务 | FastAPI app 已注册 video、subtitle、note、qa、chat、design、auth、ops、recommendation、search、agent、frame_description routers | `app/main.py` |
| API 版本 | `2.0.0` | `app/main.py` |
| 数据库 | SQLAlchemy 2.0 + MySQL 默认 URL，可通过 `DATABASE_URL` 配置 | `app/core/database.py`, `app/core/config.py` |
| 表初始化 | `AUTO_CREATE_TABLES=false` 默认关闭 | `app/core/config.py`, `app/main.py` |
| Frame Description | 默认后端为 `qwen3vl`，Cloud Qwen-VL 可显式启用，Vinci 为 legacy | `app/core/config.py`, `README.md` |
| 文档治理 | `docs/INDEX.md` 是全局上下文入口、日志路由器、Prompt Registry、Session 恢复中心 | 本文件 |

## 当前版本

| 项 | 值 |
|---|---|
| API version | `2.0.0` |
| 最近提交记录 | `2e10a2e 0513 fix disable youtube mooc upload (#6)` |
| 文档体系版本 | `docs-refactor-2026-05-18` |
| 领域重构版本 | `services-ddd-2026-05-18` |

## 当前负责人

| 角色 | 负责人 |
|---|---|
| 项目维护 | 当前仓库维护者 |
| 文档维护 Agent | Codex 项目日志与文档整理 Agent |

# Current Focus

| 类别 | 当前内容 | 状态 | 关联文档 |
|---|---|---|---|
| 正在开发 | 文档同步与 git 提交 | Active | `CHANGELOG.md`, `COMMIT_LOG.md` |
| 待修复问题 | 当前未从文档扫描发现已登记但未修复 Bug | Clear | `docs/bugs/pending.md` |
| 架构任务 | 后续架构调整必须使用 ADR 并同步 architecture 文档 | Active | `docs/arch/decisions.md` |
| 验证链路 | smoke startup、compileall、system requirements | Active | `docs/testing/test-cases.md`, `docs/reference/commands.md` |

# Active Sessions

| 时间 | Session目标 | 状态 | 关联文档 |
|---|---|---|---|
| 2026-05-09 00:00:00 Asia/Shanghai | 将 `docs/INDEX.md` 固化为全局上下文入口、恢复入口与 Prompt Registry | Completed | `docs/summaries/session-history.md`, `docs/prompts/workflow.md` |
| 2026-05-18 00:00:00 Asia/Shanghai | 项目结构领域驱动重构（services/ 按业务域分组，清理死代码） | Completed | `CHANGELOG.md`, `COMMIT_LOG.md`, `AGENTS.md`, `CLAUDE.md` |
| 2026-05-09 00:00:00 Asia/Shanghai | 初始化长期可维护 docs 日志体系 | Completed | `docs/summaries/session-history.md`, `docs/MILESTONES.md` |

# Documentation Map

## Architecture

| 文件 | 用途 |
|---|---|
| `docs/arch/decisions.md` | ADR 与架构决策 |
| `docs/arch/system-design.md` | 系统边界与主要请求流 |
| `docs/arch/tech-stack.md` | 技术栈与运行时依赖 |
| `docs/architecture/backend.md` | 后端分层、中间件、生命周期 |
| `docs/architecture/frontend.md` | 前端对接边界与 API 契约关注点 |
| `docs/architecture/database.md` | 数据库配置、ORM 模型、迁移 |
| `docs/architecture/api-flow.md` | API 请求处理链路与 router map |
| `docs/VINCI_M5_ARCHITECTURE_AND_SEQUENCES.md` | Vinci M5 历史架构与序列文档 |

## Bugs

| 文件 | 用途 |
|---|---|
| `docs/bugs/resolved.md` | 已修复 Bug、原因、复现、修复、影响、回归风险 |
| `docs/bugs/pending.md` | 待处理 Bug |
| `docs/bugs/regression.md` | 回归风险清单 |

## Features

| 文件 | 用途 |
|---|---|
| `docs/features/completed.md` | 已完成功能与风险/后续优化 |
| `docs/features/in-progress.md` | 进行中功能与 session 隔离规则 |
| `docs/features/planned.md` | 计划功能 |
| `docs/MILESTONES.md` | 项目里程碑 |

## Guides

| 文件 | 用途 |
|---|---|
| `docs/guides/setup.md` | 本地启动 |
| `docs/guides/deploy.md` | 部署检查 |
| `docs/guides/release.md` | 发布流程 |
| `docs/guides/troubleshooting.md` | 排障指南 |
| `docs/FRAME_DESCRIPTION_RUNBOOK.md` | Frame Description 运行手册 |
| `docs/VINCI_RUNBOOK.md` | Vinci legacy 运行手册 |

## Testing

| 文件 | 用途 |
|---|---|
| `docs/testing/test-cases.md` | 测试目录与关键测试文件 |
| `docs/testing/e2e.md` | E2E 场景建议 |
| `docs/testing/performance.md` | 性能关注点 |
| `docs/testing/coverage.md` | 覆盖策略与风险 |
| `docs/FRAME_DESCRIPTION_ACCEPTANCE.md` | Frame Description 验收 |
| `docs/VINCI_M5_VERIFICATION_REPORT.md` | Vinci M5 历史验证报告 |

## Updates

| 文件 | 用途 |
|---|---|
| `docs/updates/changelog.md` | docs 体系内变更日志 |
| `docs/updates/version-history.md` | 版本历史 |
| `docs/updates/release-notes.md` | 发布说明 |
| `CHANGELOG.md` | 仓库根级变更日志 |
| `COMMIT_LOG.md` | 仓库根级提交日志 |

## Prompts

| 文件 | 用途 |
|---|---|
| `docs/prompts/coding.md` | 编码工作提示词 |
| `docs/prompts/debug.md` | 调试工作提示词 |
| `docs/prompts/workflow.md` | 工作流、短命令映射、INDEX 强制规则 |

## Summaries

| 文件 | 用途 |
|---|---|
| `docs/summaries/daily.md` | 日报 |
| `docs/summaries/weekly.md` | 周报 |
| `docs/summaries/session-history.md` | session 历史与恢复记录 |

## Reference

| 文件 | 用途 |
|---|---|
| `docs/reference/env.md` | 环境变量索引 |
| `docs/reference/commands.md` | 命令索引 |
| `docs/reference/dependencies.md` | 依赖索引 |
| `docs/reference/conventions.md` | 编码、测试、文档约定 |

## Legacy

| 文件 | 用途 |
|---|---|
| `docs/legacy/deprecated.md` | 废弃与兼容项 |
| `docs/legacy/migration.md` | 迁移记录 |
| `docs/FRAME_DESCRIPTION_ROLLBACK.md` | Frame Description 回滚 |
| `docs/VINCI_INTEGRATION_M1.md` | Vinci M1 历史接入 |
| `docs/VINCI_M4_INTEGRATION.md` | Vinci M4 历史联调 |
| `docs/VINCI_M5_API_CONTRACT_ERROR_CODES.md` | Vinci M5 历史错误码契约 |

# Prompt Registry

| 名称 | 用途 | 文件位置 | 最近更新时间 |
|---|---|---|---|
| Coding Prompt | 新增/修改后端功能时保持分层、测试、日志同步 | `docs/prompts/coding.md` | 2026-05-09 |
| Debug Prompt | API、Frame Description、搜索、视频处理、鉴权调试 | `docs/prompts/debug.md` | 2026-05-09 |
| Workflow Prompt | 短命令映射、session 恢复、INDEX 维护规则 | `docs/prompts/workflow.md` | 2026-05-09 |

# Recent Changes

| 时间 | 类型 | 内容 | 影响范围 |
|---|---|---|---|
| 2026-05-18 00:00:00 Asia/Shanghai | refactor | 项目结构领域驱动重构（services/ 按业务域分组，清理死代码） | `app/services/`, `AGENTS.md`, `CLAUDE.md` |
| 2026-05-13 00:00:00 Asia/Shanghai | fix | 禁用 YouTube 和中国大学慕课链接上传，修复 QA provider 路由 | `app/routers/video.py`, `app/utils/qa_utils.py` |
| 2026-05-13 00:00:00 Asia/Shanghai | feat | Whisper 运行时诊断日志 | `app/services/whisper/runtime.py` |
| 2026-05-09 00:00:00 Asia/Shanghai | docs | 将 `docs/INDEX.md` 强化为全局上下文入口、日志路由器、Prompt Registry、Session 恢复中心 | `docs/INDEX.md`, `docs/prompts/workflow.md`, `docs/summaries/session-history.md` |
| 2026-05-09 00:00:00 Asia/Shanghai | docs | 初始化长期 docs 日志体系 | `docs/**` |
| 2026-05-08 | feature | Cloud Qwen-VL fallback 接入 Frame Description | `app/services/llm_clients/qwen_vl_cloud.py` |
| 2026-05-06 | fix | 移除画面描述用户可见"降级"提示，改为字幕模式表达 | Frame Description |

# Pending Tasks

## 高优先级

| 任务 | 原因 | 关联文档 |
|---|---|---|
| 每次代码修改后先读 `docs/INDEX.md` 并补齐日志闭环 | 缺少 changelog/session/feature/INDEX 引用即视为开发未完成 | `docs/prompts/workflow.md` |
| 提交前审查 `.env*` 本地文件 | 当前工作区存在 `.env`、`.env.cloud`、`.env.local` 未跟踪以及 `.env.production` 改动 | `docs/guides/deploy.md` |

## 中优先级

| 任务 | 原因 | 关联文档 |
|---|---|---|
| 将 docs 更新要求加入 PR checklist | 防止后续文档漂移 | `docs/guides/release.md` |
| 为 API 变更补充 request/response 样例索引 | 提升前后端协作可恢复性 | `docs/architecture/api-flow.md` |

## 低优先级

| 任务 | 原因 | 关联文档 |
|---|---|---|
| 按周生成 docs 周报 | 方便长期维护回顾 | `docs/summaries/weekly.md` |

# Architecture Decisions

| 日期 | 决策 | 状态 |
|---|---|---|
| 2026-05-09 | 建立结构化 docs 日志体系 | Accepted |
| 2026-05-04 | Frame Description 默认使用 Qwen3VL 主路径 | Accepted |

# Recovery Guide

## 当前开发到哪里

| 项 | 状态 |
|---|---|
| docs 目录体系 | 已初始化 |
| `docs/INDEX.md` 全局入口 | 已固化为项目状态面板、日志路由器、Prompt Registry、Session 恢复中心 |
| 业务代码 | 本次未修改 |
| 本地配置 | `.env*` 存在既有改动/未跟踪，本次未触碰 |

## 下一步做什么

| 优先级 | 下一步 |
|---|---|
| P0 | 后续任何“继续/开始下一步/继续开发/恢复工作”先读取本文件，再检查 Active Sessions 与 Pending Tasks |
| P0 | 后续任何功能/Bug/架构/发布完成后，更新对应日志并确认本文件存在双向引用 |
| P1 | 将 docs 完整性要求补入 PR checklist 或 release checklist |
| P2 | 为新增 API 变更沉淀 request/response 示例文档 |

## 哪些文件最重要

| 文件 | 原因 |
|---|---|
| `docs/INDEX.md` | 全局上下文入口与 session 恢复中心 |
| `docs/summaries/session-history.md` | 每次 session 的完成内容、风险、未完成事项 |
| `docs/prompts/workflow.md` | 工作流规则与短命令映射 |
| `docs/updates/changelog.md` | docs 内变更记录 |
| `docs/features/completed.md` | 功能完成判定依据 |
| `docs/bugs/resolved.md` | Bug 修复审计依据 |
| `docs/arch/decisions.md` | 架构决策依据 |

## 当前风险

| 风险 | 影响 | 应对 |
|---|---|---|
| 文档未同步 | 功能视为未完成，后续 session 恢复失真 | 每次结束前更新 `session-history` 与本文件 |
| Prompt 散落在聊天记录 | 无法复用和审计 | 高质量 Prompt 必须归档到 `docs/prompts/` 并在 Prompt Registry 引用 |
| `.env*` 本地改动 | secrets 误提交 | 提交前单独审查并运行 hook/secrets 检查 |
| Frame Description 多 fallback 路径 | 回归测试复杂 | 修改时运行相关 api/unit tests |

## 未完成事项

| 事项 | 状态 |
|---|---|
| PR checklist 中强制 docs 完整性 | Pending |
| API 示例索引 | Pending |
| 自动化检查所有新增 docs 是否被 INDEX 引用 | Planned |

## 如何快速恢复上下文

| 步骤 | 操作 |
|---|---|
| 1 | 读取 `docs/INDEX.md` 的 Project Overview、Current Focus、Active Sessions |
| 2 | 查看 Pending Tasks，确认高优先级任务 |
| 3 | 读取 `docs/summaries/session-history.md` 最近一条 session |
| 4 | 按任务类型读取对应日志：feature、bug、arch、updates、prompts |
| 5 | 开始代码修改前确认是否需要新 session；新功能开发必须提醒开启新 session |
