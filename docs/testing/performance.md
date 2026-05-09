# Performance Testing

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 性能关注点

| 领域 | 风险 | 当前控制 |
|---|---|---|
| 视频处理 | CPU/IO 密集，ffmpeg/Whisper 耗时 | 后台任务与可配置 executor |
| Frame Description | 上游视觉模型首 token/请求慢 | 超时、探测、fallback、采样间隔 |
| 搜索 | embedding 与 Chroma 查询耗时 | chunk 配置、索引状态、搜索开关 |
| 推荐 | 候选扫描过多 | `RECOMMENDATION_MAX_CANDIDATES_SCAN` |
| 存储 | 临时文件和 Chroma backup 增长 | `storage_maintenance` |

## 建议记录指标

| 指标 | 来源 |
|---|---|
| API elapsed_ms | `RequestTimingMiddleware` |
| Frame Description fallback rate | frame description metrics/debug logs |
| 视频处理耗时 | task logs/status |
| 搜索延迟 | search logs/telemetry |
| 推荐返回数量与耗时 | recommendation ops events |
