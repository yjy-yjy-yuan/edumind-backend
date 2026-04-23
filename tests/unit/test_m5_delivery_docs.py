"""M5 交付文档契约测试。"""

from pathlib import Path


def test_m5_delivery_docs_exist_with_required_sections():
    required = {
        "docs/VINCI_M5_ARCHITECTURE_AND_SEQUENCES.md": ["融合前", "融合后", "时序"],
        "docs/VINCI_M5_API_CONTRACT_ERROR_CODES.md": ["接口", "错误码", "Runbook"],
        "docs/VINCI_M5_VERIFICATION_REPORT.md": ["unit", "api", "smoke", "DoD"],
        "docs/VINCI_M5_MILESTONE_COMMITS.md": ["Milestone", "Commit"],
    }

    for rel_path, tokens in required.items():
        path = Path(rel_path)
        assert path.exists(), f"缺少 M5 交付文档: {rel_path}"
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, f"文档缺少关键内容 '{token}': {rel_path}"
