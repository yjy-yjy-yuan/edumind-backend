# Coverage Notes

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 当前策略

| 项 | 说明 |
|---|---|
| 固定覆盖率阈值 | 当前仓库指南未强制固定阈值 |
| 日常默认验证 | smoke startup + compileall + system requirements |
| 风险驱动测试 | 变更 router/service/task 时补充或更新测试 |

## 覆盖风险

| 领域 | 建议 |
|---|---|
| 异常路径 | 视频处理、外部模型调用、搜索索引清理需覆盖失败分支 |
| 鉴权 | 新 API 必须覆盖 Bearer 与 legacy 兼容边界 |
| 数据隔离 | 新模型/查询必须覆盖跨用户访问 |
| fallback | Frame Description 每个 fallback 分支都应有单测 |
