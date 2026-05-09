# Deploy Guide

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 部署入口

| 文件 | 用途 |
|---|---|
| `run.py` | 本地开发启动 |
| `run_prod.py` | 生产类启动入口 |
| `.env.example` | 配置样例 |
| `scripts/validate_system_requirements.py` | 系统依赖验证 |

## 部署前检查

| 检查项 | 命令或说明 |
|---|---|
| Smoke test | `pytest tests/smoke/test_app_startup.py -v` |
| 编译检查 | `PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall app scripts` |
| 系统依赖 | `python scripts/validate_system_requirements.py` |
| Secrets | 确认 `.env*` 未进入提交 |

## 配置风险

| 配置 | 风险 | 建议 |
|---|---|---|
| `SECRET_KEY` | 默认开发值不可用于生产 | 生产环境必须覆盖 |
| `AUTH_ALLOW_LEGACY_USER_ID_ONLY` | 允许仅 user_id 识别用户会降低安全性 | 生产保持 `false` |
| `FRAME_DESC_CLOUD_FALLBACK_ENABLED` | 开启后可能发送抽帧到云端 API | 发布说明中标注数据流 |
| `AUTO_CREATE_TABLES` | 生产自动建表可能绕过迁移审计 | 生产保持 `false` |
