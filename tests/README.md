# Backend Tests

`backend_fastapi/tests/` 是 EduMind 后端唯一测试目录。

当前按职责分层：

- `unit/`
  纯单元测试，验证 service、task、runtime、工具函数等独立逻辑
- `api/`
  HTTP 接口测试，验证 FastAPI 路由、请求参数、响应结构和主链路行为
- `smoke/`
  冒烟测试，快速验证应用能否启动、基础链路是否可用
- `integration/`
  预留给跨模块集成测试；只有当测试同时覆盖多个组件交互时才放这里
- `conftest.py`
  测试夹具、数据库会话、测试客户端和通用初始化逻辑

## 放置规则

新增后端测试时，请统一放在这里，不要散落到 `app/`、根目录或其他业务目录。

- 接口行为测试：放 `api/`
- 业务服务/工具函数测试：放 `unit/`
- 启动检查或最小全链路检查：放 `smoke/`
- 多组件协作测试：放 `integration/`

## 命名规则

- 文件名：`test_*.py`
- 类名：`Test*`
- 方法名：`test_*`

推荐示例：

- `api/test_recommendation_api.py`
- `unit/test_video_recommendation_service.py`
- `smoke/test_app_startup.py`

## 当前验证约定

当前仓库规则要求修改程序时不要用 `pytest` 作为本次验证手段；优先在仓库根目录执行：

```bash
. .venv/bin/activate
python scripts/validate_backend_smoke.py
mkdir -p .pycache-hook
PYTHONPYCACHEPREFIX="$PWD/.pycache-hook" python -m compileall backend_fastapi/app backend_fastapi/scripts scripts/hooks scripts/validate_backend_smoke.py
```

补充说明：

- 本目录仍保留历史 pytest 风格的命名与分层，方便后续维护已有回归用例。
- 若未来刷新这批历史测试，再决定是否恢复独立测试命令文档；在此之前，以当前 smoke/build/static checks 为准。
