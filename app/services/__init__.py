"""Services Package

领域服务按业务域分组：
- video/: 视频内容分析、API 序列化、处理注册、推荐、URL 导入、站外候选
- frame_desc/: 画面描述服务、帧抽取、调试日志
- similarity/: 相似度计算、审计日志、分数解析、持久化容器
- recommendation/: 推荐运营指标
- llm_clients/: Qwen3VL、Qwen-VL Cloud、Vinci、Ollama 运行时
- whisper/: Whisper 模型管理与调试日志

跨域服务保留在 services/ 根目录：
- sleek_service.py: 设计助手
- storage_maintenance.py: 存储维护
- search/: 语义搜索（embedder、chunker、store、similarity_fusion）
"""
