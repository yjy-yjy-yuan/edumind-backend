# Commands Reference

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 开发命令

| 命令 | 用途 |
|---|---|
| `python3 -m venv .venv` | 创建虚拟环境 |
| `. .venv/bin/activate` | 激活虚拟环境 |
| `pip install -r requirements.txt` | 安装依赖 |
| `cp .env.example .env` | 初始化本地配置 |
| `python run.py` | 本地启动 API |

## 验证命令

| 命令 | 用途 |
|---|---|
| `pytest tests/smoke/test_app_startup.py -v` | 默认 smoke 验证 |
| `PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts` | 编译检查 |
| `python scripts/validate_system_requirements.py` | 系统需求验证 |
| `pytest tests/unit -v` | 历史/扩展单元测试 |
| `pre-commit run --all-files` | hook 全量检查 |

## 文档维护命令

| 输入 | 目标 |
|---|---|
| 更新日志 | `docs/summaries/session-history.md` |
| 记录架构 | `docs/arch/decisions.md`, `docs/architecture/*` |
| 记录 bug | `docs/bugs/*` |
| 发布版本 | `docs/updates/*` |
| 生成周报 | `docs/summaries/weekly.md` |
