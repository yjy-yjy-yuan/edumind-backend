# Local/Cloud Isolation And Verification (2026-04-27)

## 目标

- 云端服务持续正常运行，不被本地开发修改干扰。
- 本地开发修改可独立验证、独立排障。
- 本地与云端结果可对比、可按需同步。

## 本次后端改动摘要

1. 新增运行域自检接口：`GET /api/ops/runtime-scope`
1. 视频删除接口兼容增强：
   - `DELETE /api/videos/{video_id}/delete`
   - `DELETE /api/videos/{video_id}`
   - `DELETE /api/video/{video_id}/delete`
1. 字幕读取编码兼容增强 + 默认字幕输出改为 `vtt`/`utf-8`
1. 实时画面描述稳定性开关：
   - `FRAME_DESC_SKIP_STABLE_SCENE=false`
   - `FRAME_DESC_ENABLE_CONTEXT_FUSION=false`
1. 关键词搜索可选标签增强（默认关闭）：
   - 请求字段：`include_tag_match`
   - 配置：`SEARCH_TAG_MATCH_ENABLED`, `SEARCH_TAG_MATCH_WEIGHT`

## 隔离校验步骤

### 1) 检查本地运行域

```bash
curl -s http://127.0.0.1:2004/api/ops/runtime-scope | jq
```

预期：

- `scope_label == "local-runtime"`
- `local_isolation_ok == true`
- `database.is_local == true`

### 2) 检查视频删除路径兼容

```bash
VID=$(curl -s http://127.0.0.1:2004/api/videos/list | jq -r '.videos[0].id // empty')
echo "VID=$VID"
curl -i -X DELETE "http://127.0.0.1:2004/api/videos/$VID"
curl -i -X DELETE "http://127.0.0.1:2004/api/video/$VID/delete"
```

### 3) 检查字幕输出默认格式

```bash
curl -i "http://127.0.0.1:2004/api/videos/$VID/subtitle"
```

预期：`Content-Type: text/vtt; charset=utf-8`

### 4) 检查搜索标签增强（本地灰度）

先在本地 `.env` 开启：

```bash
SEARCH_TAG_MATCH_ENABLED=true
SEARCH_TAG_MATCH_WEIGHT=0.18
```

然后请求：

```bash
curl -sS -X POST "http://127.0.0.1:2004/api/search/semantic/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query":"导数定义",
    "video_ids":[],
    "limit":5,
    "threshold":0.2,
    "include_tag_match":true
  }' | jq
```

## 本地排障建议

- 如果 `videos/list` 不是 JSON，请先确认本地服务是新进程并已初始化数据库。
- 本地库初始化：

```bash
python scripts/init_db.py --create
```

- 若仍有异常，先抓原始响应头和响应体再判断（避免直接管道给 `jq` 导致误判）。

## 云端发布注意（关键词搜索）

- 本次标签部分匹配优化位于排序融合层，不改变现有向量库 schema。
- 云端拉取后通常不需要全量重建向量数据库。
- 仅在以下场景按视频重建：
  - 该视频从未建立索引（`has_semantic_index=false`）
  - 该视频索引损坏或目录异常
