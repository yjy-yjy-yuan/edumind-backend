# Pending Bugs

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 当前待处理问题

| 日期 | 问题 | 证据 | 影响 | 当前处理 |
|---|---|---|---|---|
| 2026-05-09 | 未从当前代码扫描发现已登记但未修复的 Bug | `CHANGELOG.md` 近期条目均为已完成修复或功能 | 无直接待办 | 后续输入“记录 bug”时追加 |

## 观察项

| 观察项 | 风险 | 建议 |
|---|---|---|
| `.env*` 存在本地改动/未跟踪 | secrets 误提交 | 提交前运行 secrets/hook 检查 |
| Frame Description 上游多路径 | fallback 组合复杂 | 每次改动运行 frame description 相关 unit/api tests |
