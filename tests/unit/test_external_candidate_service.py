"""站外候选元数据服务单测。"""

import json
import logging

from app.services import external_candidate_service as service


class FakeResponse:
    """最小响应对象。"""

    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise service.requests.HTTPError(f"status={self.status_code}")


class FakeSequenceResponse:
    """按顺序返回固定响应。"""

    def __init__(self, responses):
        self.responses = list(responses)

    def __call__(self, *args, **kwargs):
        if not self.responses:
            raise AssertionError("no fake responses left")
        return self.responses.pop(0)


class BrokenAdapter(service.ExternalCandidateAdapter):
    """抛出请求异常的 provider。"""

    provider = "broken"
    source_label = "坏源"

    def search(self, *args, **kwargs):
        raise service.requests.RequestException("boom")


class GoodAdapter(service.ExternalCandidateAdapter):
    """返回单条候选的 provider。"""

    provider = "good"
    source_label = "好源"

    def search(self, *args, **kwargs):
        return [
            self.build_candidate(
                raw_id="good-1",
                title="中国大学慕课 · 数学相关课程",
                external_url="https://www.icourse163.org/search.htm?search=%E6%95%B0%E5%AD%A6",
                summary="打开中国大学慕课搜索页继续学习。",
                tags=["数学", "导数"],
                subject_hint="数学",
            )
        ]


class CountingAdapter(service.ExternalCandidateAdapter):
    """记录调用次数的 provider。"""

    provider = "counting"
    source_label = "计数源"

    def __init__(self):
        self.call_count = 0

    def search(self, *args, **kwargs):
        self.call_count += 1
        return [
            self.build_candidate(
                raw_id=f"count-{self.call_count}",
                title="导数专题复盘",
                external_url=f"https://example.com/{self.call_count}",
                summary="缓存测试使用的站外候选。",
                tags=["数学", "导数"],
                subject_hint="数学",
            )
        ]


def test_bilibili_adapter_parses_search_results(monkeypatch):
    """B 站适配器应能提取标题、作者与链接。"""
    html = """
    <a href="//www.bilibili.com/video/BV1abc123/"></a>
    <h3 class="bili-video-card__info--tit" title="导数与函数单调性综合串讲"></h3>
    <span class="bili-video-card__info--author">宋老师</span>
    <span class="bili-video-card__info--date"> · 2025-03-01</span>
    """

    monkeypatch.setattr(service.requests, "get", lambda *args, **kwargs: FakeResponse(html))

    adapter = service.BilibiliExternalCandidateAdapter()
    items = adapter.search("数学 导数", subject_hint="数学", preferred_tags=["导数"], limit=1)

    assert len(items) == 1
    assert items[0].source_label == "B站"
    assert items[0].title == "导数与函数单调性综合串讲"
    assert items[0].external_url == "https://www.bilibili.com/video/BV1abc123"
    assert items[0].subject == "数学"
    assert items[0].tags[0] == "数学"


def test_youtube_adapter_parses_yt_initial_data(monkeypatch):
    """YouTube 适配器应能从 ytInitialData 中提取视频信息。"""
    payload = {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [
                            {
                                "itemSectionRenderer": {
                                    "contents": [
                                        {
                                            "videoRenderer": {
                                                "videoId": "abc123xyz",
                                                "title": {"runs": [{"text": "Python Data Structures Crash Review"}]},
                                                "ownerText": {"runs": [{"text": "Computer Teacher"}]},
                                                "descriptionSnippet": {
                                                    "runs": [{"text": "Quick review for list and dict."}]
                                                },
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        }
    }
    html = f"<script>var ytInitialData = {json.dumps(payload)};</script>"

    monkeypatch.setattr(service.requests, "get", lambda *args, **kwargs: FakeResponse(html))

    adapter = service.YouTubeExternalCandidateAdapter()
    items = adapter.search("计算机 Python", subject_hint="计算机", preferred_tags=["Python"], limit=1)

    assert len(items) == 1
    assert items[0].source_label == "YouTube"
    assert items[0].external_url == "https://www.youtube.com/watch?v=abc123xyz"
    assert items[0].author == "Computer Teacher"
    assert items[0].subject == "计算机"
    assert items[0].tags[0] == "计算机"


def test_fetch_external_candidates_isolates_provider_failures(monkeypatch):
    """单个 provider 失败时，不应阻断其他站外候选返回。"""
    service.clear_external_candidate_report_cache()

    monkeypatch.setattr(service, "EXTERNAL_CANDIDATE_ADAPTERS", (BrokenAdapter(), GoodAdapter()))

    items = service.fetch_external_candidates("数学 导数", subject_hint="数学", preferred_tags=["导数"], limit=2)

    assert len(items) == 1
    assert items[0].source_label == "好源"
    assert items[0].subject == "数学"


def test_fetch_external_candidates_report_tracks_provider_failures(monkeypatch, caplog):
    """站外抓取报告应包含 provider 失败摘要，并输出 DEBUG 日志。"""
    service.clear_external_candidate_report_cache()
    monkeypatch.setattr(service, "EXTERNAL_CANDIDATE_ADAPTERS", (BrokenAdapter(), GoodAdapter()))

    with caplog.at_level(logging.DEBUG):
        report = service.fetch_external_candidates_report(
            "数学 导数",
            subject_hint="数学",
            preferred_tags=["导数"],
            limit=2,
        )

    assert len(report.candidates) == 1
    assert len(report.providers) == 2
    failed_summary = next(item for item in report.providers if item.provider == "broken")
    success_summary = next(item for item in report.providers if item.provider == "good")
    assert failed_summary.status == "failed"
    assert failed_summary.error_message == "boom"
    assert success_summary.status == "success"
    assert success_summary.candidate_count == 1
    assert "站外候选抓取失败 | provider=broken" in caplog.text


def test_mooc_adapter_marks_search_candidate_as_non_importable(monkeypatch):
    """慕课搜索页候选应显式标记为暂不可直接导入。"""
    service.clear_external_candidate_report_cache()
    html = "<title>搜索课程_中国大学MOOC(慕课)</title>"
    monkeypatch.setattr(service.requests, "get", lambda *args, **kwargs: FakeResponse(html))

    adapter = service.MoocExternalCandidateAdapter()
    items = adapter.search("数学 导数", subject_hint="数学", preferred_tags=["导数"], limit=1)

    assert len(items) == 1
    assert items[0].source_label == "中国大学慕课"
    assert items[0].can_import is False
    assert "搜索页" in items[0].import_hint


def test_mooc_adapter_resolves_course_page_from_search_result(monkeypatch):
    """慕课适配器应优先把搜索词解析成具体课程页链接。"""
    service.clear_external_candidate_report_cache()
    duckduckgo_html = """
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.icourse163.org%2Flearn%2FZJU-1003315004%3Ftid%3D1472024446">
       高等数学 - 中国大学慕课
    </a>
    """
    monkeypatch.setattr(service.requests, "get", lambda *args, **kwargs: FakeResponse(duckduckgo_html))

    adapter = service.MoocExternalCandidateAdapter()
    items = adapter.search("数学 导数", subject_hint="数学", preferred_tags=["导数"], limit=1)

    assert len(items) == 1
    assert items[0].source_label == "中国大学慕课"
    assert items[0].can_import is True
    assert items[0].external_url == "https://www.icourse163.org/learn/ZJU-1003315004?tid=1472024446"


def test_fetch_external_candidates_report_uses_short_ttl_cache(monkeypatch):
    """同一检索条件应短时间命中缓存，避免重复打外部源。"""
    service.clear_external_candidate_report_cache()
    adapter = CountingAdapter()
    monkeypatch.setattr(service, "EXTERNAL_CANDIDATE_ADAPTERS", (adapter,))

    first = service.fetch_external_candidates_report("数学 导数", subject_hint="数学", preferred_tags=["导数"], limit=2)
    second = service.fetch_external_candidates_report(
        "数学 导数", subject_hint="数学", preferred_tags=["导数"], limit=2
    )

    assert adapter.call_count == 1
    assert first.candidates[0].external_url == "https://example.com/1"
    assert second.candidates[0].external_url == "https://example.com/1"
    assert first.candidates[0] is not second.candidates[0]
