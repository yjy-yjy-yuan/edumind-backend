# EduMind Backend 提交日志

> 说明：
> - 按日期倒序排列，仅记录每日提交信息。
> - 分支创建记录单独列出，不计入当日提交条目。
> - 云端服务器上的提交一并记录。

## 分支创建记录

- `main` 基线：`cad1c23` Initial commit: EduMind Backend FastAPI service

---

### 2026-06-03

- (pending) fix(video): block MOOC direct import and expand yt-dlp env config

### 2026-06-02

- (pending) fix(ollama): bypass proxies for local runtime status

### 2026-06-01

- (pending) feat(frame-description): tune local runtime config and reduce realtime latency
- (pending) feat(frame-description): optimize realtime latency and suppress duplicate requests
### 2026-06-02

- `1c98096` fix(video): enforce soft-delete filter across all video access paths
### 2026-06-01

- `d7395ed` fix(recommendation): enforce user scope and sync docs
  - 修复 `load_candidate_videos_for_recommendation` 缺少 `user_id` / `is_deleted` 过滤导致的跨用户视频泄漏
  - 修复 `related` 场景 `seed_video` 校验未过滤 `user_id` 的问题
  - 新增 `tests/api/test_recommendation_user_scope.py` 覆盖用户隔离、软删除隔离、seed 归属校验
  - 同步 CHANGELOG.md、COMMIT_LOG.md 及相关文档

### 2026-05-20

- `c74fc98` docs: add async architecture status and blocking points to CLAUDE.md and AGENTS.md
- `9f1068a` feat(ai-serving): asyncize qa/chat path with admission control and ops metrics

### 2026-05-18

- `2e10a2e` 0513 fix disable youtube mooc upload (#6)
- `c960ea4` fix(video-upload): disable YouTube and MOOC link imports (#5)
- `6da6d64` docs(log): sync commit log for subtitle encoding hardening

### 2026-05-13

- `07f14da` feat(whisper): add runtime debug logging
- `2a244b1` fix(qa): route direct DeepSeek requests correctly
- `80032f9` docs(log): sync video upload source disable commit
- `c4ef920` fix(video-upload): disable youtube and mooc link imports

### 2026-05-11

- `a188cd4` fix(subtitles): unify utf-8-sig output and fallback decode across subtitle flows

### 2026-05-09

- `903b7d1` docs: initialize project documentation system

### 2026-05-08

- `6e2d2e3` feat(frame-description): add cloud qwen-vl fallback and sync docs

### 2026-05-06

- `daa9949` fix(frame-description): remove "degraded" UI text and simplify chat system for cloud deployment

### 2026-05-04

- `ea601e7` feat(frame-description): integrate Qwen3-VL local model with dual-backend architecture

### 2026-04-27

- `91c74a4` fix(frame-description): subtitle-driven degraded output when vinci unavailable
- `f631361` fix: stabilize frame-description/delete flow and improve partial tag search
- `(unknown)` docs: sync commit log docs and add commit log sync rule to AGENTS.md/CLAUDE.md; add COMMIT_LOG.md to .gitignore
- `66367ec` fix(subtitles): use RFC5987 filename for non-ascii export headers
- `6258064` fix(subtitles): harden chinese charset handling and vtt conversion
- `3b92163` fix(storage): ensure temp audio cleanup on processing failures
- `4d75d2e` feat(storage): auto-clean runtime residue and stale temp artifacts
- `fddd69c` chore: stop tracking chroma runtime index files
- `d7e4f01` fix: enforce semantic index purge on video soft-delete and validate end-to-end cloud integration (#4)
- `a8b6c52` fix: purge semantic index when soft deleting video
- `8e4b2e3` fix: deliver soft-delete flow, default-user rebind, and docs sync
- `dcddbd4` chore(merge): integrate multi-user session isolation into main
- `8235913` feat: enforce multi-user isolation and sync docs (#3)

### 2026-04-26

- `063064d` fix: handle AV1/unsupported codec videos in processing pipeline
- `5871df9` feat: add video compression and enhance storage cleanup
- `b2dbdfe` feat: enforce multi-user isolation and sync docs
- `ee5a0c0` Integrate Vinci-enhanced frame description into EduMind with governance, resilience, and observability (#2)
- `b30b00f` chore(repo): sync docs and apply hooked fixes for vinci frame description integration
- `f454080` chore(db): support sqlite engine compatibility and document DATABASE_URL modes

### 2026-04-24

- `31f6a6d` fix(demo): support streamed httpx response parsing in frame description script
- `42e53a9` test(frame-desc): extend api coverage and add demo runner
- `59f5c7c` docs(frame-desc): align acceptance numbers and move release docs to docs
- `e38a4d1` docs(frame-desc): sync delivery report and governance closure evidence
- `7504514` feat(frame-desc): enforce governance gateway path and add frame description pipeline

### 2026-04-23

- `34841c4` fix(vinci): isolate circuit-breaker state and sync verification docs
- `0dd5ab9` docs(m5): sync delivery docs and acceptance recheck
- `46c0d34` feat(m4): align vinci integration contracts and interop docs
- `daffcca` feat: add vinci alerting acceptance prep tooling
- `c6e5e3f` docs: add M3 alerting acceptance evidence and sync vinci docs
- `c48cb0d` feat(vinci): add circuit breaker and graceful fallback in learning flow
- `4e4c3c2` docs(monitoring): align vinci alert templates with runbook thresholds
- `cbf4c41` test(agent): assert denied audit event for vinci whitelist block
- `d2f6076` test(agent): add vinci whitelist denial api regression
- `e137fba` fix(agent): enforce governance context in vinci adapter
- `066da7f` feat(agent): route vinci summary through governance pipeline
- `b3faf6a` feat: add vinci ops metrics endpoint and alert rule templates
- `39627d3` feat(agent): add prompt/skill/trajectory/resumable foundations with alembic
- `dabaa5d` feat: add vinci m1-4 observability metrics and runbook

### 2026-04-22

- `f4b3a9b` docs: sync backend docs and harden hook pipeline for reliable local/CI checks (#1)
- `55e4031` docs: sync backend docs and hook checks
- `9c20a22` feat: add vinci adapter baseline and sync m1 docs
- `04e3546` feat: enforce vinci governance path and sync m1-3 docs
- `822946a` Harden backend hooks and search auth consistency
- `a641b16` Harden governance and analytics compliance
- `cad1c23` Initial commit: EduMind Backend FastAPI service
