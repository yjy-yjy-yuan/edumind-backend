# Debug Prompts

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 调试工作提示词

| 场景 | 提示词 |
|---|---|
| API 报错 | 先定位 router 和 schema，再查 service 与 DB/session，最后补充回归测试 |
| Frame Description 异常 | 检查 `FRAME_DESC_*` 配置、上游 Qwen3VL/Cloud/Vinci 可达性、debug 日志、字幕 fallback |
| 搜索异常 | 检查索引状态、ChromaDB 目录、embedding 配置、软删除过滤 |
| 视频处理异常 | 检查任务状态、临时文件清理、Whisper/ffmpeg 依赖、重启恢复逻辑 |
| 鉴权异常 | 检查 Bearer token、legacy user_id 配置、`auth_deps` 行为 |

## Bug 修复记录模板

| 字段 | 说明 |
|---|---|
| Bug 原因 | 代码、配置、数据或外部依赖原因 |
| 复现步骤 | 最小可复现命令/API |
| 修复方案 | 修改点与验证 |
| 影响范围 | API、服务、数据、前端契约 |
| 是否可能回归 | 高/中/低与对应测试 |
