# Session History

更新时间：2026-05-11 21:10:00 Asia/Shanghai

## 2026-05-11 中文字幕编码乱码收敛与本地隔离验证

| 字段 | 内容 |
|---|---|
| 任务 | 修复字幕编码乱码主路径，统一字幕读取 fallback decode 与字幕输出 UTF-8-SIG（BOM），并建立可删除的本地隔离验证环境 |
| 完成内容 | 更新 `subtitle_io` fallback+mojibake 修复；统一 `video/subtitle`、`subtitles/export`、`semantic-merged` 输出 BOM；同步 QA/摘要/画面描述字幕读取链路；本地隔离环境验证通过 |
| 修改文件 | `app/utils/subtitle_io.py`, `app/routers/video.py`, `app/routers/subtitle.py`, `app/services/video_content_service.py`, `app/utils/qa_utils.py`, `app/services/frame_description_service.py`, `app/tasks/video_processing.py`, `tests/unit/test_subtitle_io.py`, `tests/api/test_video_api.py`, `tests/unit/test_qa_utils.py`, `tests/unit/test_video_content_service.py`, `tests/api/test_frame_description_api.py`, `README.md`, `CHANGELOG.md`, `docs/architecture/frontend.md`, `docs/guides/troubleshooting.md` |
| 验证结果 | 本地隔离接口 `/api/videos/9101/subtitle` 与 `export/semantic-merged` 均返回 `FIRST3_HEX=efbbbf`，`HAS_MOJIBAKE=False`，`HAS_REPAIRED_TEXT=True` |
| 结论 | `鑰佸笀...`/`ä¸­...` 类问题为编码乱码，已收敛；`平司边形`/`减几处`/`去球` 类为 ASR 误识别，不属于编码问题 |
| 后续建议 | 将字幕编码回归脚本纳入发布前 smoke 清单，避免新增读取链路再次绕过 fallback decode |

## 2026-05-09 INDEX 全局入口规则固化

| 字段 | 内容 |
|---|---|
| 任务 | 将 `docs/INDEX.md` 固化为全局上下文入口、AI 工作入口、日志路由器、Prompt Registry、Session 恢复中心 |
| 完成内容 | 重写 `docs/INDEX.md` 为强制结构；同步 `docs/prompts/workflow.md` 的恢复、Prompt、日志完整性规则；补充本 session 记录 |
| 修改文件 | `docs/INDEX.md`, `docs/prompts/workflow.md`, `docs/summaries/session-history.md`, `docs/features/completed.md`, `docs/updates/changelog.md`, `CHANGELOG.md`, `COMMIT_LOG.md` |
| 提交记录 | `903b7d1 docs: initialize project documentation system` |
| 未完成事项 | PR checklist 尚未加入 docs 完整性检查；API 示例索引尚未建立；自动化检查新增 docs 是否被 INDEX 引用仍为计划项 |
| 下次建议 | 继续任务前先读取 `docs/INDEX.md`，从 Recovery Guide 和 Pending Tasks 恢复上下文 |
| 风险 | 如果后续开发未同步 INDEX 与对应日志，功能按规则视为未完成 |
| 阻塞点 | 无 |

## 2026-05-09 Docs 初始化

| 字段 | 内容 |
|---|---|
| 任务 | 根据项目当前状态创建长期可维护 docs 日志体系 |
| 扫描范围 | `README.md`, `app/main.py`, `app/core/config.py`, `CHANGELOG.md`, `COMMIT_LOG.md`, `app/routers`, `app/services`, migrations, existing `docs/` |
| 修改文件 | `docs/INDEX.md`, `docs/MILESTONES.md`, `docs/arch/*`, `docs/architecture/*`, `docs/bugs/*`, `docs/features/*`, `docs/guides/*`, `docs/legacy/*`, `docs/prompts/*`, `docs/reference/*`, `docs/summaries/*`, `docs/testing/*`, `docs/updates/*` |
| 实现原因 | 原仓库已有多份交付/运行文档，但缺少统一、可审计、面向后续维护的日志目录 |
| 风险 | 文档体系需要后续持续更新；若功能/Bug 修复后遗漏 docs，同步状态会失真 |
| 后续优化 | 将 docs 更新要求加入 PR checklist；为 API 变更补充 request/response 样例索引 |

## 近期历史摘要

| 日期 | 类型 | 摘要 | 来源 |
|---|---|---|---|
| 2026-05-08 | feature | Cloud Qwen-VL fallback 接入 Frame Description | `CHANGELOG.md` |
| 2026-05-06 | fix | 画面描述不可用时改为更自然的字幕模式表达 | `CHANGELOG.md` |
| 2026-05-04 | feature | Qwen3-VL 本地模型后端集成与隔离验证 | `CHANGELOG.md` |
| 2026-04-27 | fix/feature | 视频删除、标签搜索、字幕、存储清理、多用户隔离加固 | `CHANGELOG.md` |
