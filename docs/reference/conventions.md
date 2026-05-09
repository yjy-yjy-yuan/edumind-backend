# Conventions

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 代码约定

| 项 | 规则 |
|---|---|
| 缩进 | 4 spaces |
| 行宽 | 120 characters |
| 函数/模块 | `snake_case` |
| 类 | `PascalCase` |
| 常量 | `UPPER_SNAKE_CASE` |
| async route | 路由处理器优先使用 `async def` |
| imports | stdlib, third-party, local |

## 测试约定

| 项 | 规则 |
|---|---|
| 文件 | `test_*.py` |
| 类 | `Test*` |
| 函数 | `test_*` |
| 目录 | `tests/unit`, `tests/api`, `tests/smoke`, `tests/integration` |

## 文档约定

| 项 | 规则 |
|---|---|
| 时间戳 | 每个 docs 初始化文件保留更新时间 |
| 表格 | 状态、风险、变更使用表格 |
| 空文档 | 不允许；无数据时记录“未发现”及证据 |
| 架构决策 | 使用 ADR 格式 |
