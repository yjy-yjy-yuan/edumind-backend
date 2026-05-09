# Completed Features

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 已完成功能

| 日期 | 功能 | 修改文件 | 实现原因 | 风险 | 后续优化 |
|---|---|---|---|---|---|
| 2026-05-09 | INDEX 全局上下文入口规则固化 | `docs/INDEX.md`, `docs/prompts/workflow.md`, `docs/summaries/session-history.md` | 确保后续 session 能从单一入口恢复，并强制日志完整性 | 后续若未执行 INDEX 检查，规则会停留在文档层 | 增加自动化检查所有新增 docs 均被 INDEX 引用 |
| 2026-05-09 | 初始化 docs 长期日志体系 | `docs/**` | 建立可审计、可维护的项目日志结构 | 文档需持续维护，否则会漂移 | 将 docs 更新加入 PR checklist |
| 2026-05-08 | Cloud Qwen-VL fallback | `app/services/qwen_vl_cloud_client.py`, `app/services/frame_description_service.py`, `app/core/config.py`, `app/main.py`, `docs/FRAME_DESCRIPTION_QWEN3VL_CLOUD_FALLBACK.md` | 本地 Qwen3VL 不可用时提供同族云端补偿 | 开启后会向云端发送抽帧结果 | 增加更多端到端可观测样例 |
| 2026-05-04 | Qwen3VL 本地模型双后端 | `app/services/qwen3vl_realtime_client.py`, `app/services/frame_description_service.py`, `app/routers/frame_description.py` | 降低对重型 Vinci 服务的默认依赖 | 本地服务未启动会触发 fallback | 明确本地服务启动脚本与健康检查 |
| 2026-04-27 | 视频软删除与删除流优化 | `app/routers/video.py`, `app/tasks/video_cleanup.py`, `migrations/add_video_soft_delete_and_user_rebind.sql` | 删除后前端立即可见，同时保留数据库记录 | 关联查询必须过滤软删除 | 增加软删除全域过滤检查 |
| 2026-04-27 | 搜索标签部分匹配增强 | `app/services/search/search.py`, `app/routers/search.py`, `app/schemas/search.py` | 提升关键词搜索对 tags 的召回/排序 | 排序可能影响历史预期 | 按 telemetry 评估排序效果 |
| 2026-04-26 | 多用户会话隔离 | `app/routers/`, `app/services/`, `app/utils/auth_deps.py`, migrations | 防止跨用户数据访问 | 新增接口可能遗漏过滤 | 为新增 router 强制添加隔离测试 |
| 2026-04-23 | Vinci 治理、断路器与告警 | `app/agents/`, `app/analytics/`, `docs/monitoring/` | 让外部画面描述能力具备治理与观测 | Vinci 当前为 legacy，文档需标注 | 保留历史验收文档，避免误导默认路径 |
