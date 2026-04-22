#!/usr/bin/env bash

set -euo pipefail

MODEL_NAME="${OLLAMA_MODEL_NAME:-qwen-3.5:9b}"
MODEL_SOURCE="${1:-${GGUF_PATH:-${OLLAMA_MODEL_SOURCE:-}}}"

if [[ -z "${MODEL_SOURCE}" ]]; then
  echo "用法:"
  echo "  bash backend_fastapi/scripts/import_qwen35_gguf_to_ollama.sh /absolute/path/to/model.gguf"
  echo "  bash backend_fastapi/scripts/import_qwen35_gguf_to_ollama.sh hf.co/owner/repo:tag"
  echo "可选环境变量:"
  echo "  OLLAMA_MODEL_NAME=qwen-3.5:9b"
  echo "  OLLAMA_MODEL_SOURCE=hf.co/owner/repo:tag"
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "未检测到 ollama，请先安装并启动 Ollama。"
  exit 1
fi

if ollama show "${MODEL_NAME}" >/dev/null 2>&1; then
  echo "检测到已有同名模型别名，正在替换: ${MODEL_NAME}"
  ollama rm "${MODEL_NAME}" >/dev/null
fi

if [[ -f "${MODEL_SOURCE}" ]]; then
  ABS_GGUF_PATH="$(cd "$(dirname "${MODEL_SOURCE}")" && pwd)/$(basename "${MODEL_SOURCE}")"
  TMPFILE="$(mktemp)"
  trap 'rm -f "${TMPFILE}"' EXIT

  cat > "${TMPFILE}" <<EOF
FROM ${ABS_GGUF_PATH}
PARAMETER temperature 0.2
PARAMETER num_ctx 8192
EOF

  echo "正在导入本地 GGUF 到 Ollama: ${MODEL_NAME}"
  ollama create "${MODEL_NAME}" -f "${TMPFILE}"
elif [[ "${MODEL_SOURCE}" == hf.co/* ]]; then
  echo "正在从 Hugging Face 拉取远程模型: ${MODEL_SOURCE}"
  ollama pull "${MODEL_SOURCE}"
  if [[ "${MODEL_SOURCE}" != "${MODEL_NAME}" ]]; then
    echo "正在复制远程模型到项目别名: ${MODEL_NAME}"
    ollama cp "${MODEL_SOURCE}" "${MODEL_NAME}"
  fi
else
  echo "暂只支持本地 GGUF 路径或 hf.co/... 远程模型引用。"
  echo "收到参数: ${MODEL_SOURCE}"
  exit 1
fi

ollama show "${MODEL_NAME}" >/dev/null
echo "导入完成。当前模型别名: ${MODEL_NAME}"
