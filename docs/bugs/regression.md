# Regression Risks

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 回归风险清单

| 领域 | 风险 | 推荐回归 |
|---|---|---|
| 多用户隔离 | ~~新接口遗漏 user_id 过滤导致跨用户数据暴露~~ ✅ 2026-06-01 已修复：推荐系统 `load_candidate_videos_for_recommendation` 与 seed 校验已增加 `user_id` 过滤 | `tests/api/test_recommendation_user_scope.py` |
| 视频软删除 | ~~查询、搜索、推荐仍返回 deleted 视频~~ ✅ 2026-06-01 已修复：推荐候选加载与 seed 校验已增加 `is_deleted` 过滤 | `tests/api/test_recommendation_user_scope.py`, `tests/api/test_video_api.py` |
| Frame Description | Qwen3VL、Cloud fallback、字幕 fallback 分支漂移 | `tests/api/test_frame_description_api.py`, `tests/unit/test_frame_description_service.py` |
| 字幕编码 | 新字幕来源出现乱码或导出 header 不兼容 | subtitle API/unit tests |
| 搜索索引 | 删除或重建后 ChromaDB 状态不一致 | search API tests, vector indexing tests |
| 推荐契约 | 响应字段版本与前端预期不一致 | `tests/api/test_recommendation_api.py` |
