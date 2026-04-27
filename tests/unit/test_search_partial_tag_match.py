"""搜索标签部分匹配单元测试。"""

from app.services.search.search import _partial_tag_match_score


def test_partial_tag_match_score_hits_substring():
    score = _partial_tag_match_score("单调", ["函数单调性", "导数", "极值"])
    assert score > 0.9


def test_partial_tag_match_score_hits_bigram_overlap():
    score = _partial_tag_match_score("导函数", ["导数定义", "函数图像"])
    assert score > 0.2


def test_partial_tag_match_score_empty_query_returns_zero():
    assert _partial_tag_match_score("", ["函数单调性"]) == 0.0
