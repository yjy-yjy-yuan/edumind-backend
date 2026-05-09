# Test Cases

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 测试目录

| 目录 | 用途 |
|---|---|
| `tests/unit/` | 服务、工具、模型、解析器等隔离测试 |
| `tests/api/` | 路由行为与响应契约 |
| `tests/smoke/` | 启动与最小路径检查 |
| `tests/integration/` | 多组件流程 |

## 关键测试文件

| 文件 | 覆盖领域 |
|---|---|
| `tests/smoke/test_app_startup.py` | 应用启动 smoke |
| `tests/api/test_frame_description_api.py` | Frame Description API |
| `tests/unit/test_frame_description_service.py` | Frame Description service |
| `tests/unit/test_qwen3vl_realtime_client.py` | Qwen3VL client |
| `tests/api/test_user_scope_isolation.py` | 用户隔离 |
| `tests/api/test_search_api.py` | 搜索 API |
| `tests/api/test_recommendation_api.py` | 推荐 API |
| `tests/unit/test_video_processing_task.py` | 视频处理任务 |
