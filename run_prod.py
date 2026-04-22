"""
EduMind FastAPI 生产环境启动脚本

用法:
    # 直接运行（开发/调试用）
    python run_prod.py

    # systemd 调用时无需额外参数，使用环境变量中的配置
    # ExecStart=/var/www/edumind/.venv/bin/python run_prod.py

本脚本在 uvicorn 启动前完成两个关键操作：
1. 将 pysqlite3 注入 sys.modules，使 chromadb 等依赖 sqlite>=3.35 的库在
   内置 sqlite3 受限的环境（某些 Linux 发行版的 Python 打包）中正常工作。
2. 强制将 DEBUG 置为 False，确保 reload=False 在生产环境生效。
"""

from __future__ import annotations

import logging
import os
import sys

# ── 1. pysqlite3 注入（必须在任何模块 import 之前执行）───────────────────────────
try:
    import pysqlite3 as _pysqlite3

    sys.modules["sqlite3"] = _pysqlite3  # type: ignore[assignment]
    del _pysqlite3
except ImportError:
    # pysqlite3 未安装时记录警告但不阻断（某些环境可能使用系统 sqlite>=3.35）
    sys.stderr.write(
        "[run_prod.py] WARNING: pysqlite3 not found in site-packages. "
        "If chromadb raises 'sqlite3.OperationalError: not an error', "
        "run: pip install pysqlite3-binary\n"
    )

# ── 2. 强制生产模式 ────────────────────────────────────────────────────────────
#   防止用户在 .env 中误设 DEBUG=true 导致 uvicorn reload=True 造成进程分裂
_env_debug = os.environ.get("DEBUG", "").strip().lower()
_is_explicit_debug = _env_debug in ("true", "1", "yes")
if _is_explicit_debug:
    sys.stderr.write(
        "[run_prod.py] WARNING: DEBUG=true is ignored in production mode. "
        "Set APP_ENV=production instead.\n"
    )
os.environ["DEBUG"] = "false"

# ── 3. 启动 uvicorn ───────────────────────────────────────────────────────────
import uvicorn

from app.core.config import settings

_log_level = "info"
_access_log = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
)

uvicorn.run(
    "app.main:app",
    host=settings.HOST,
    port=settings.PORT,
    reload=False,          # 生产环境必须关闭，systemd 管理生命周期
    log_level=_log_level,
    access_log=_access_log,
    # 以下为生产推荐参数
    workers=1,              # Gunicorn 模式下由 Gunicorn 管理 workers；单进程时保持 1
    limit_concurrency=100,
    limit_max_requests=8192,
    timeout_keep_alive=60,
    proxy_headers=True,    # 从 Nginx 读取 X-Forwarded-* 头
    forwarded_allow_ips="*",
)
