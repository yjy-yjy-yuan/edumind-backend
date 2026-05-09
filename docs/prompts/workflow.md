# Workflow Prompts

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## INDEX 强制规则

| 规则 | 要求 |
|---|---|
| 全局入口 | `docs/INDEX.md` 是全局记忆中心、AI 工作入口、日志路由器、Prompt Registry、Session 恢复中心、项目状态面板 |
| 代码修改前后 | 每次代码修改、功能开发、Bug 修复、架构调整、版本发布之后，必须主动读取并检查 `docs/INDEX.md` |
| 完整性检查 | 检查对应日志、提示词、架构记录、session 记录、变更记录是否存在 |
| 自动补齐 | 若记录不存在，自动创建、补全引用、建立双向链接 |
| 引用要求 | 所有新增文档必须被 `docs/INDEX.md` 引用 |
| 完成定义 | 任何功能如果缺少 changelog、session-history、feature 记录或 INDEX 引用，视为开发未完成 |

## 用户短命令映射

| 用户输入 | 执行动作 |
|---|---|
| 更新日志 | 更新 `docs/summaries/session-history.md` |
| 记录架构 | 更新 `docs/arch/decisions.md` 与 `docs/architecture/*` |
| 记录 bug | 更新 `docs/bugs/resolved.md` 或 `docs/bugs/pending.md` |
| 发布版本 | 更新 `docs/updates/changelog.md` 与 `docs/updates/release-notes.md` |
| 生成周报 | 汇总 `docs/summaries/weekly.md` |
| 继续 | 先读取 `docs/INDEX.md`，恢复最近 session，检查 Pending Tasks，再开始工作 |
| 开始下一步 | 先读取 `docs/INDEX.md`，恢复最近 session，检查 Pending Tasks，再开始工作 |
| 继续开发 | 先读取 `docs/INDEX.md`，恢复最近 session，检查 Pending Tasks，再开始工作 |
| 恢复工作 | 先读取 `docs/INDEX.md`，恢复最近 session，检查 Pending Tasks，再开始工作 |

## Session 结束规则

| 必须更新 | 记录内容 |
|---|---|
| `docs/summaries/session-history.md` | 本次完成内容、修改文件、未完成事项、下次建议、风险、阻塞点 |
| `docs/INDEX.md` | Active Sessions、Recent Changes、Pending Tasks、Recovery Guide |

结束时必须提醒：“建议开启新的 session 继续下一阶段开发，避免上下文污染与历史推理干扰。”

## Prompt 管理规则

| 规则 | 要求 |
|---|---|
| 归档位置 | 所有高质量 Prompt 必须归档到 `docs/prompts/` |
| INDEX 引用 | Prompt 必须出现在 `docs/INDEX.md` 的 Prompt Registry |
| 元数据 | 必须记录用途、适用场景、更新时间 |
| 禁止散落 | Prompt 不允许只存在于聊天记录中 |

## 新功能开发提醒

| 触发 | 必须提醒 |
|---|---|
| 用户开启新功能开发 | 请开启新的 session 继续开发，避免上下文污染 |

## 文档质量要求

| 要求 | 当前执行方式 |
|---|---|
| 不允许空文档 | 每个初始化文件包含真实项目状态或明确“未发现/无待办”的审计记录 |
| 统一标题层级 | 使用 `#`, `##`, `###` |
| 时间戳 | 每个文件包含更新时间 |
| 表格记录变更 | 状态、记录、风险均使用表格 |
