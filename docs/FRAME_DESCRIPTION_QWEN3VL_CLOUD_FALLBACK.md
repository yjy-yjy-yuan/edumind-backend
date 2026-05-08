# Frame Description Qwen3VL Cloud Fallback

> Last updated: 2026-05-08

## Target Chain

Frame Description now uses Qwen-family backends as the primary path:

```text
Local Qwen3VL
  -> Cloud Qwen3-VL API
  -> Caption Fallback
  -> Minimal Safe Response
```

Vinci is legacy-only and is used only when `FRAME_DESC_BACKEND=vinci`.

## Runtime Behavior

When `FRAME_DESC_BACKEND=qwen3vl`:

1. The router receives sampled frames or a `frame_source_url`.
2. If frames are missing and server-side frame fetch is enabled, the backend extracts one frame from the whitelisted stream URL.
3. `FrameDescriptionService` routes to `Qwen3VLRealtimeClient`.
4. If local Qwen3VL is unavailable and `FRAME_DESC_CLOUD_FALLBACK_ENABLED=true`, the same prompt and normalized frame base64 are sent to Cloud Qwen-VL.
5. If Cloud Qwen-VL also fails, the service returns caption-based text.
6. If captions are unavailable, the service returns a minimal safe response.

Empty frames are no longer treated as a hard validation failure on the Qwen3VL path. They are passed as:

```text
base64_frames=[]
```

This enables text-only reasoning from prompt, subtitle, OCR, and learning context.

## Configuration

```env
FRAME_DESC_BACKEND=qwen3vl
FRAME_DESC_AUTO_DEGRADE=true

# Local Qwen3VL
QWEN3VL_BASE_URL=http://127.0.0.1:18082
QWEN3VL_DESCRIBE_PATH=/api/v1/video/describe
QWEN3VL_STREAM_PATH=/api/v1/video/describe/stream

# Cloud Qwen-VL fallback; disabled by default because frames leave the local machine.
FRAME_DESC_CLOUD_FALLBACK_ENABLED=false
FRAME_DESC_CLOUD_PROVIDER=qwen
FRAME_DESC_CLOUD_QWEN_MODEL=qwen3-vl-plus
FRAME_DESC_CLOUD_QWEN_TIMEOUT_SECONDS=45
FRAME_DESC_CLOUD_QWEN_MAX_TOKENS=256

# DashScope/OpenAI-compatible credentials
QWEN_API_KEY=
QWEN_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
```

## Debug Log Signals

Tail the dedicated file logger during local validation:

```bash
tail -f logs/frame_description_debug.log
```

Expected routing signals:

```text
routing to qwen3vl backend
VinciAdapterService NOT instantiated
```

Expected cloud fallback signals:

```text
fallback_reason=qwen3vl_probe_unreachable:...
fallback_target=cloud_qwen_vl
cloud_qwen_vl call start
cloud_qwen_vl call done
```

If cloud fallback also fails:

```text
fallback_reason=cloud_qwen_vl_failed:...
fallback_target=subtitle_description
```

If no subtitle data exists:

```text
当前约 MM:SS，暂时无法获取画面描述或字幕内容，请继续播放后重试。
```

## Important Boundary

Cloud Qwen-VL does not replace frame extraction in the current architecture. The backend still extracts frames locally and sends only normalized frame base64 plus the prompt to Cloud Qwen-VL.

If the video stream returns `403 Forbidden` and the backend cannot obtain any frame, Cloud Qwen-VL cannot describe the visual content unless the frontend sends frames directly or the cloud model can access an authenticated media URL.
