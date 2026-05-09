# Release Guide

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 发布流程

| 步骤 | 说明 |
|---|---|
| 1 | 确认 `git status`，识别业务改动和本地配置改动 |
| 2 | 运行默认验证链路 |
| 3 | 更新 `CHANGELOG.md`、`COMMIT_LOG.md`、`docs/updates/changelog.md`、`docs/updates/release-notes.md` |
| 4 | 若涉及架构，更新 `docs/arch/decisions.md` 与 `docs/architecture/*` |
| 5 | 若涉及 Bug，更新 `docs/bugs/resolved.md` |
| 6 | 提交后按仓库规则同步 commit hash 到 `COMMIT_LOG.md` |

## 默认验证链

| 命令 | 目的 |
|---|---|
| `pytest tests/smoke/test_app_startup.py -v` | 启动导入与关键配置 smoke |
| `mkdir -p .pycache-hook` | 准备编译缓存目录 |
| `PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts` | Python 编译检查 |
| `python scripts/validate_system_requirements.py` | 系统依赖检查 |
