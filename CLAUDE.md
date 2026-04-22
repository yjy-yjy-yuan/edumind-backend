# Backend FastAPI CLAUDE.md

FastAPI + SQLAlchemy 2.0 + ProcessPoolExecutor 后端服务

## Bash Commands

```bash
# 启动服务
python run.py
uvicorn app.main:app --reload --port 2004

# 验证
python ../scripts/validate_backend_smoke.py
mkdir -p ../.pycache-hook && PYTHONPYCACHEPREFIX="$PWD/../.pycache-hook" python -m compileall app scripts ../scripts/hooks ../scripts/validate_backend_smoke.py

# 数据库迁移
alembic revision --autogenerate -m "描述"
alembic upgrade head

# 代码格式化
pre-commit run --all-files
black app/ tests/
isort app/ tests/
```

## Core Files

| 文件 | 说明 |
|------|------|
| `app/main.py` | FastAPI 应用入口，路由注册 |
| `app/core/config.py` | Pydantic Settings 配置 |
| `app/core/database.py` | SQLAlchemy 2.0 连接和 get_db 依赖 |
| `app/core/executor.py` | ProcessPoolExecutor 后台任务 |
| `app/routers/*.py` | API 路由 (video, subtitle, note, qa, chat, auth) |
| `app/models/*.py` | SQLAlchemy 2.0 模型 (Mapped[] 类型注解) |
| `app/schemas/*.py` | Pydantic 请求/响应模型 |
| `app/tasks/video_processing.py` | 视频处理后台任务 |
| `app/utils/*.py` | 工具函数 (chat, qa, semantic) |

## Code Style

- **IMPORTANT**: 行宽 120 字符
- **IMPORTANT**: 使用 black + isort 格式化
- 异步路由使用 `async def`
- 类型注解: Pydantic + SQLAlchemy `Mapped[]`
- Import 顺序: stdlib → third-party → local

```python
# 路由示例
@router.get("/{video_id}")
async def get_video(video_id: int, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    return video.to_dict()
```

## API Endpoints

| 路由前缀 | 说明 |
|---------|------|
| `/api/videos` | 视频上传、处理、列表、流式传输 |
| `/api/subtitles` | 字幕提取、语义合并、导出 |
| `/api/notes` | 笔记 CRUD、时间戳、批量操作 |
| `/api/qa` | AI 问答 (流式响应) |
| `/api/chat` | 聊天系统 (流式响应) |
| `/api/auth` | 用户注册、登录、信息更新 |
## Testing

```bash
# 当前仓库要求修改程序时不要使用 pytest 做验证
python ../scripts/validate_backend_smoke.py
mkdir -p ../.pycache-hook && PYTHONPYCACHEPREFIX="$PWD/../.pycache-hook" python -m compileall app scripts ../scripts/hooks ../scripts/validate_backend_smoke.py
```

## Development Environment

```bash
# 激活环境
conda activate ai-edvision

# 端口
# Backend: 2004
# Frontend: 328
```

## Key Differences from Flask

| Flask | FastAPI |
|-------|---------|
| `@bp.route('/path', methods=['POST'])` | `@router.post('/path')` |
| `request.get_json()` | Pydantic Model 参数 |
| `jsonify({...})` | 直接返回 dict |
| `current_app.config['KEY']` | `settings.KEY` |
| `Response(generate())` | `StreamingResponse(generate())` |
| `Video.query.get(id)` | `db.query(Video).filter(Video.id == id).first()` |

## Background Tasks

**IMPORTANT**: 使用 ProcessPoolExecutor 替代 Celery

```python
from app.core.executor import submit_task
from app.tasks.video_processing import process_video_task

# 提交后台任务
submit_task(process_video_task, video_id, language, model)

# 任务内创建独立数据库连接
def process_video_task(video_id: int, language: str, model: str):
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    # ...
```

## Warnings

- **YOU MUST** 在任务函数内创建新的数据库连接 (ProcessPoolExecutor 限制)
- **YOU MUST** 使用 `Depends(get_db)` 注入数据库会话
- **IMPORTANT**: 流式响应使用 `StreamingResponse`，不要用 `Response`
- **IMPORTANT**: 验证错误返回 422，不是 400
