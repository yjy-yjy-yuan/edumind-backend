# Troubleshooting Guide

更新时间：2026-05-11 21:10:00 Asia/Shanghai

## 常见问题

| 症状 | 可能原因 | 排查路径 |
|---|---|---|
| `/health` 返回模型不可用 | Whisper/Ollama/Qwen3VL 未启动或配置不匹配 | 查看 `app/core/config.py` 对应 URL/路径，检查运行时日志 |
| 视频处理卡住 | 服务重启或后台任务中断 | 启动时 `recover_interrupted_video_tasks` 会将 pending/processing/downloading 置为 failed |
| 字幕乱码 | 源字幕编码复杂或链路绕过 fallback decode | 检查 `app/utils/subtitle_io.py` fallback 逻辑、字幕接口是否为 `utf-8-sig` + BOM、前端 `TextDecoder` 解码路径 |
| 实时画面描述无内容 | Qwen3VL/Cloud/Vinci 不可用且无字幕 fallback | 查看 `FRAME_DESC_*` 配置与 debug 日志 |
| 搜索结果缺失 | 视频未建索引、索引损坏或软删除过滤 | 检查 search index status endpoint 与 ChromaDB 数据目录 |

## 日志与证据

| 文件/目录 | 用途 |
|---|---|
| `logs/frame_description_debug.log` | Frame Description debug 日志默认相对路径 |
| `docs/monitoring/evidence/m3/` | Vinci M3 告警验收历史证据 |
| `CHANGELOG.md` | 根级变更记录 |
| `docs/summaries/session-history.md` | session 级操作记录 |
