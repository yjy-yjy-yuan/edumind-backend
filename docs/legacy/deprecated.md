# Deprecated Components

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 废弃或历史兼容项

| 项 | 状态 | 当前替代 | 证据 |
|---|---|---|---|
| Vinci 作为默认 Frame Description 后端 | Legacy | `FRAME_DESC_BACKEND=qwen3vl` | `app/core/config.py`, `README.md` |
| `/api/video/*` 单数前缀 | Compatibility | `/api/videos/*` | `app/main.py` |
| 用户可见“降级模式”文案 | Removed | 字幕模式/自然 fallback 文案 | `CHANGELOG.md` 2026-05-06 |
| 自动建表作为默认初始化方式 | Disabled by default | migration/init scripts | `AUTO_CREATE_TABLES=false` |

## 维护要求

| 要求 | 说明 |
|---|---|
| 保留兼容说明 | 删除 legacy 前需更新 release notes |
| 增加迁移说明 | 兼容路径下线前必须补充 `docs/legacy/migration.md` |
