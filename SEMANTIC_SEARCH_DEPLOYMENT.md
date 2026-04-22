# EduMind 语义搜索后端落地说明

更新日期：2026-04-10

本文档记录 `backend_fastapi/` 中已经落地的语义搜索后端能力、当前限制和最小部署步骤。它描述的是当前仓库状态，不再把计划项写成“已完成”。

## 当前已落地

### 代码入口

- 路由：`app/routers/search.py`
- 视频流路由：`app/routers/video.py`
- Schema：`app/schemas/search.py`
- 搜索服务：`app/services/search/search.py`
- 分片：`app/services/search/chunker.py`
- 向量工厂：`app/services/search/embedder.py`
- Gemini 嵌入：`app/services/search/gemini_embedder.py`
- 本地文本嵌入：`app/services/search/local_embedder.py`
- ChromaDB 存储：`app/services/search/store.py`
- Chroma 遥测适配：`app/services/search/chroma_telemetry.py`
- 后台索引任务：`app/tasks/vector_indexing.py`
- 数据模型：`app/models/vector_index.py`、`app/models/semantic_search_log.py`
- 迁移脚本：`scripts/migrations_semantic_search.py`（向量索引相关历史脚本）；全局检索日志表：`migrations/add_semantic_search_logs.sql`

### 已实现能力

- 支持 `mp4` / `mov` 视频切片。
- 支持可配置的切片时长、切片重叠、分辨率和 FPS 预处理。
- 支持将视频片段写入 ChromaDB 持久化目录。
- 支持 Gemini 文本查询嵌入与视频分片嵌入。
- 支持本地 `local` 后端对字幕时间窗分块做文本向量索引与搜索。
- 支持按 `user_{user_id}_video_{video_id}_chunks` 规则隔离集合。
- 支持搜索接口、手动触发索引接口、索引状态接口。
- 支持 iOS/WKWebView 播放预检：`/api/videos/{video_id}/stream` 同时支持 `HEAD` 与 `Range`（206）响应。
- 支持后台索引任务状态流转：`pending -> processing -> completed/failed`。
- 支持在视频处理完成后按配置自动提交索引任务。
- 搜索结果已回填字幕预览文本 `preview_text`。
- 当前本地默认链路会先做字幕语义召回，再结合查询词与 `preview_text` / 视频标题的词面命中做融合重排；接口返回的 `similarity_score` 与 `threshold` 都以融合后的分数为准，不再是“纯向量相似度”。
- Chroma 集合元数据损坏时（典型为 `_decode_seq_id` / `max_seqid` / `object of type 'int' has no len()`）会触发自愈重建；若检索请求中所有目标视频都失败，接口返回 `503`，提示先重建索引，避免把后端存储故障误报成“无结果”。
- 默认关闭 Chroma 匿名遥测（`SEARCH_CHROMA_ANONYMIZED_TELEMETRY=false`），减少本地运行 `posthog` 噪声日志干扰排障。
- **全局语义搜索落库**：当请求体未携带 `video_ids`（或为空列表）时，即跨「当前用户全部已索引视频」检索，每次检索会在 `semantic_search_logs` 写入一条记录（含查询文本、实际参与检索的视频 ID 列表、命中条数、耗时、`limit`/`threshold`）；无可搜视频时也会写入（`result_count=0`）。写库失败仅打日志，不影响搜索接口。实现见 `app/services/search/search_log.py`。

### 数据库：`semantic_search_logs`

- 推荐路径：
  - 现有 MySQL 库增量部署：优先执行 `backend_fastapi/migrations/add_semantic_search_logs.sql`
  - 本地开发或新库初始化：可使用 `python backend_fastapi/scripts/init_db.py --create`
  - 仅在明确允许自动建表时，才依赖 `AUTO_CREATE_TABLES=true`
- `scripts/migrations_semantic_search.py` 主要覆盖早期向量索引相关结构；`semantic_search_logs` 这张表请以 `backend_fastapi/migrations/add_semantic_search_logs.sql` 为准。
- 若库中**尚未执行**建表（迁移脚本或 `init_db.py --create` 未跑），则属于**部署动作未完成**，不是代码缺陷：全局搜索仍会返回结果，但审计落库失败；进程内表现为「首次全局搜索一条 WARNING、之后同类失败为 DEBUG」，直至表真正创建。完整修复请执行上述 SQL 或 `init_db.py --create`。

## 索引覆盖与数据预期（诊断结论）

以下结论依赖具体库内数据，用于纠正过时的「全盘否定」诊断，**不作为代码自动保证**：

- **「所有视频都未切片、全局搜索完全无结果」** 在环境已正确配置 Chroma 与向量索引时通常**不成立**：多数已完成语义索引的视频可以返回片段。
- **「全量已上传视频都可搜」** 不一定成立：处理失败、字幕路径失效、或 `COMPLETED` 但 `subtitle_filepath` 为空等情况会导致**无法建字幕索引**，从而无法参与搜索。
- 若库内长期存在“失败且无可恢复资产”的历史视频（例如无可用视频文件且无字幕），建议直接清理该类记录，避免持续污染索引成功率与运维告警观察面。

## 当前限制

下面这些点已经在代码或文档中明确为限制，不应再按“已完成”验收：

- 认证尚未完全接入搜索路由；当前用户解析优先取请求头 `X-User-ID`，否则回退到默认用户 `1`。
- 当前默认打通方案更偏向字幕语义搜索，并不是生产级视频视觉 embedding 方案。
- 本地 `local` 后端以字幕为主：当视频文件缺失但字幕可用时，会降级为“字幕索引”；若字幕不可用，则该视频无法参与本地语义搜索。
- 当前本地链路对字幕识别质量仍然敏感；若字幕 OCR/ASR 本身有错字或口语化噪声，融合重排只能缓解结果排序，不能替代字幕清洗或别名归一。
- 本文档不把“视频片段裁剪导出”列为已完成能力，仓库当前没有完整落地 `trimmer.py`。
- 本文档不把前端搜索 UI 或 iOS 搜索交互列为已完成能力；当前提交范围主要是后端链路。

## 需要的配置

在 `backend_fastapi/.env` 中补齐语义搜索配置：

```bash
SEARCH_ENABLED=true
SEARCH_BACKEND=local
SEARCH_LOCAL_MODEL=hashing-char-ngrams-zh
SEARCH_GEMINI_API_KEY=
SEARCH_CHROMA_DB_DIR=./data/chroma
SEARCH_CHROMA_ANONYMIZED_TELEMETRY=false
SEARCH_ADAPTIVE_CHUNKING=true
SEARCH_CHUNK_DURATION=30
SEARCH_CHUNK_OVERLAP=5
SEARCH_PREPROCESS=true
SEARCH_PREPROCESS_RESOLUTION=480
SEARCH_PREPROCESS_FPS=5
SEARCH_AUTO_INDEX_NEW_VIDEOS=true
```

说明：

- 启用 `SEARCH_ADAPTIVE_CHUNKING=true` 后，索引会按视频总时长自动选择切片参数，短视频切得更细，长视频切得更粗。
- 当前默认规则是：`<=3min -> 12s/2s`、`<=10min -> 20s/4s`、`<=30min -> 45s/8s`、`<=60min -> 60s/10s`、`>60min -> 75s/12s`。
- 规则表当前使用“单值上限”匹配，像 `180.5s`、`600.5s` 这类浮点时长会自动落入下一档，不会再错误回退到固定 `30s/5s`。
- 字幕分块在 overlap 场景下会以重叠窗口起点作为后续 chunk 的开始时间，避免长字幕把多个片段错误锚定到更早时间。
- 本地默认打通方案使用 `SEARCH_BACKEND=local`，优先把字幕聚合成时间窗分块后再做文本向量。
- 若切回 `SEARCH_BACKEND=gemini`，需要保证 `SEARCH_GEMINI_API_KEY` 或 Gemini SDK 默认环境变量可用。
- 开发环境的后台执行器默认是线程池；生产环境可通过 `BACKGROUND_TASK_EXECUTOR=process` 切到进程池。

## 最小部署步骤

### 1. 安装依赖

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend_fastapi/requirements.txt
```

### 2. 创建数据库字段

当前仓库提供的是 SQL 脚本而不是完整 Alembic revision。至少需要落下：

- `vector_indexes` 表
- `videos.has_semantic_index`
- `videos.vector_index_id`
- 全局检索审计：`semantic_search_logs`（见 `backend_fastapi/migrations/add_semantic_search_logs.sql`
  或 `python backend_fastapi/scripts/init_db.py --create`）
- 若现有 `videos` 表尚无 `user_id`，还需要执行 `backend_fastapi/migrations/add_user_id_to_videos.sql`

### 3. 准备 ChromaDB 目录

```bash
mkdir -p backend_fastapi/data/chroma
```

### 4. 启动后端

```bash
python backend_fastapi/run.py
```

### 5. 验证

```bash
. .venv/bin/activate
python scripts/validate_backend_smoke.py
python -m compileall backend_fastapi/app backend_fastapi/scripts scripts/hooks scripts/validate_backend_smoke.py
```

## 当前接口

### 语义搜索

`POST /api/search/semantic/search`

请求体示例：

```json
{
  "query": "讲了牛顿第二定律的部分",
  "video_ids": [1, 2],
  "limit": 10,
  "threshold": 0.5
}
```

### 手动触发索引

`POST /api/search/videos/{video_id}/index`

### 查看索引状态

`GET /api/search/videos/{video_id}/index/status`

### 视频流播放（iOS/WKWebView 关键链路）

- `GET /api/videos/{video_id}/stream`
- `HEAD /api/videos/{video_id}/stream`
- 支持 `Range: bytes=start-end`，返回 `206 Partial Content` 与 `Content-Range`，兼容拖动与分段加载。

## 验收建议

若要确认这条链路不是“只有骨架”，建议至少验证：

1. 视频处理完成后，`Video.has_semantic_index` 能被更新。
2. ChromaDB 数据目录里能看到对应集合数据。
3. 同一查询对多个视频搜索时能返回按相似度排序、带 `preview_text` 的片段。
4. 索引失败时，`vector_indexes.status` 和 `error_message` 会落库。
