# Local/Cloud Isolation And Verification (2026-04-27)

## Status

Rebuilt on 2026-05-04.

The hard runtime guard (`GET /api/ops/runtime-scope`) was removed from
`app/routers/ops.py`. Local/cloud isolation is now handled entirely by
explicit environment files and a static verification script, so local
debugging does not fail during app configuration loading.

## Local Isolation Guardrails

- Backend local `.env` uses `APP_ENV=local`.
- Backend local `.env` points `DATABASE_URL` to a local/private database.
- Backend local `.env` points `VINCI_BASE_URL` to local Vinci
  (`127.0.0.1` / `localhost`).
- Backend local `.env` points `QWEN3VL_BASE_URL` to local Qwen3-VL
  (`127.0.0.1` / `localhost`); Qwen3-VL is the preferred local model for
  real-time frame description.
- Frontend local `.env` points API/proxy targets to local backend addresses.
- Cloud deployment keeps its own production environment files and process
  manager configuration; local tests must not edit or restart cloud services.

## Verification Command

Run before local debugging:

```bash
python scripts/verify_local_cloud_isolation.py
```

- Exit code `0`: local/cloud isolation is valid.
- Exit code `1`: one or more settings still point to cloud or non-local endpoints.

## Important Rule

Do not implement isolation by raising exceptions inside `app/core/config.py`.
Configuration loading must stay side-effect free so local debugging, tests, and
cloud deployment cannot block each other accidentally.
