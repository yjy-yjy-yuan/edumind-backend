# Setup Guide

更新时间：2026-05-09 00:00:00 Asia/Shanghai

## 本地启动

| 步骤 | 命令 |
|---|---|
| 创建虚拟环境 | `python3 -m venv .venv` |
| 激活环境 | `. .venv/bin/activate` |
| 安装依赖 | `pip install -r requirements.txt` |
| 创建本地配置 | `cp .env.example .env` |
| 启动服务 | `python run.py` |

## 默认地址

| 地址 | 用途 |
|---|---|
| `http://127.0.0.1:2004/health` | 健康检查 |
| `http://127.0.0.1:2004/docs` | Swagger UI |

## 初始化注意

| 项 | 说明 |
|---|---|
| Secrets | 不要提交 `.env`、API Key、数据库密码 |
| 数据库 | 默认 `AUTO_CREATE_TABLES=false`，使用迁移或初始化脚本 |
| 外部模型 | Qwen3VL、Ollama、Whisper 是否可用会影响健康检查细节 |
