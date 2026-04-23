"""M4 联调文档契约测试。"""

from __future__ import annotations

from pathlib import Path


def test_m4_integration_doc_exists_and_has_repro_steps():
    doc_path = Path("docs/VINCI_M4_INTEGRATION.md")
    assert doc_path.exists(), "缺少 M4 联调文档：docs/VINCI_M4_INTEGRATION.md"

    text = doc_path.read_text(encoding="utf-8")
    required_tokens = [
        "后端启动",
        "前端启动",
        "契约联调步骤",
        "/api/agent/execute",
        "/api/qa/ask",
        "pytest tests/api/test_agent_api.py",
        "pytest tests/api/test_qa_api.py",
    ]
    for token in required_tokens:
        assert token in text, f"M4 联调文档缺少必要内容: {token}"
