# Release Notes

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 未发布

| 日期 | 类型 | 说明 |
|---|---|---|
| 2026-05-09 | docs | 初始化长期 docs 日志体系，不改变运行时代码 |

## 2026-05-08

| 类型 | 说明 |
|---|---|
| Added | Frame Description 可配置 Cloud Qwen-VL fallback |
| Changed | 文档同步默认主路径为 Qwen3VL，Vinci 标记为 legacy |
| Risk | 开启 cloud fallback 后，抽帧数据会发送到配置的云端模型服务 |

## 2026-05-06

| 类型 | 说明 |
|---|---|
| Fixed | 画面描述不可用时不再向用户展示“降级”字样 |
| Impact | 用户看到更自然的字幕模式表达 |
