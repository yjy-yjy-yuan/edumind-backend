"""站外候选轻量元数据服务。

站外结果缓存键由 build_external_cache_key 生成，结构为
(normalized_query, normalized_subject, tuple(归一化标签), limit)；
归一化规则与 clean_text / dedupe_keep_order 一致，内存 TTL 见 EXTERNAL_CANDIDATE_CACHE_TTL_SECONDS。
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from time import monotonic
from time import perf_counter
from typing import Optional
from urllib.parse import parse_qs
from urllib.parse import quote_plus
from urllib.parse import unquote
from urllib.parse import urlparse

import requests
from app.core.config import settings
from app.services.video_content_service import build_subject_enriched_tags
from app.services.video_content_service import fallback_primary_topic_name
from app.services.video_content_service import infer_subject_from_text

logger = logging.getLogger(__name__)

EXTERNAL_CANDIDATE_CACHE_TTL_SECONDS = 120
COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    )
}
VIDEO_LINK_RE = re.compile(r"href=\"//www\.bilibili\.com/video/([^\"]+)\"")
VIDEO_TITLE_RE = re.compile(r"bili-video-card__info--tit[^>]*title=\"([^\"]+)\"")
VIDEO_AUTHOR_RE = re.compile(r"<span class=\"bili-video-card__info--author\"[^>]*>(.*?)</span>", re.S)
VIDEO_DATE_RE = re.compile(r"<span class=\"bili-video-card__info--date\"[^>]*>(.*?)</span>", re.S)
YOUTUBE_INITIAL_DATA_RE = re.compile(r"var ytInitialData = (\{.*?\});", re.S)
DUCKDUCKGO_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S,
)
EXTERNAL_CANDIDATE_REPORT_CACHE: dict[
    tuple[str, str, tuple[str, ...], str, int], tuple[float, "ExternalCandidateFetchReport"]
] = {}


def http_timeout_seconds() -> float:
    """单次 HTTP 请求超时（秒），与并行抓取总等待预算配合使用。"""
    return float(getattr(settings, "RECOMMENDATION_EXTERNAL_TIMEOUT_SECONDS", 8.0))


@dataclass
class ExternalCandidate:
    """站外候选元数据。"""

    id: str
    provider: str
    source_label: str
    title: str
    external_url: str
    summary: str = ""
    tags: list[str] | None = None
    author: str = ""
    upload_time: Optional[datetime] = None
    subject: str = ""
    primary_topic: str = ""
    cluster_key: str = ""
    can_import: bool = True
    import_hint: str = ""

    def __post_init__(self):
        self.tags = list(self.tags or [])


@dataclass
class ExternalProviderFetchSummary:
    """单个 provider 的抓取摘要。"""

    provider: str
    source_label: str
    status: str
    candidate_count: int = 0
    error_message: str = ""
    latency_ms: int = 0


@dataclass
class ExternalCandidateFetchReport:
    """站外候选抓取总报告。"""

    candidates: list[ExternalCandidate]
    providers: list[ExternalProviderFetchSummary]


def build_external_cache_key(
    query_text: str,
    *,
    subject_hint: str = "",
    preferred_tags: Optional[list[str]] = None,
    preferred_provider: str = "",
    limit: int = 3,
) -> tuple[str, str, tuple[str, ...], str, int]:
    """构建站外候选抓取缓存键。"""
    normalized_query = clean_text(query_text)
    normalized_subject = clean_text(subject_hint)
    normalized_tags = tuple(dedupe_keep_order(list(preferred_tags or [])))
    normalized_provider = clean_text(preferred_provider).lower()
    normalized_limit = max(1, int(limit))
    return normalized_query, normalized_subject, normalized_tags, normalized_provider, normalized_limit


def clone_external_candidate(candidate: ExternalCandidate) -> ExternalCandidate:
    """复制站外候选，避免缓存对象被调用方修改。"""
    return ExternalCandidate(
        id=candidate.id,
        provider=candidate.provider,
        source_label=candidate.source_label,
        title=candidate.title,
        external_url=candidate.external_url,
        summary=candidate.summary,
        tags=list(candidate.tags or []),
        author=candidate.author,
        upload_time=candidate.upload_time,
        subject=candidate.subject,
        primary_topic=candidate.primary_topic,
        cluster_key=candidate.cluster_key,
        can_import=candidate.can_import,
        import_hint=candidate.import_hint,
    )


def clone_provider_summary(summary: ExternalProviderFetchSummary) -> ExternalProviderFetchSummary:
    """复制 provider 抓取摘要。"""
    return ExternalProviderFetchSummary(
        provider=summary.provider,
        source_label=summary.source_label,
        status=summary.status,
        candidate_count=summary.candidate_count,
        error_message=summary.error_message,
        latency_ms=summary.latency_ms,
    )


def clone_external_candidate_fetch_report(report: ExternalCandidateFetchReport) -> ExternalCandidateFetchReport:
    """复制抓取报告，避免缓存对象被外部修改。"""
    return ExternalCandidateFetchReport(
        candidates=[clone_external_candidate(item) for item in report.candidates],
        providers=[clone_provider_summary(item) for item in report.providers],
    )


def clear_external_candidate_report_cache() -> None:
    """清空站外候选抓取缓存。"""
    EXTERNAL_CANDIDATE_REPORT_CACHE.clear()


def get_cached_external_candidate_report(
    query_text: str,
    *,
    subject_hint: str = "",
    preferred_tags: Optional[list[str]] = None,
    preferred_provider: str = "",
    limit: int = 3,
) -> Optional[ExternalCandidateFetchReport]:
    """读取有效的站外候选抓取缓存。"""
    cache_key = build_external_cache_key(
        query_text,
        subject_hint=subject_hint,
        preferred_tags=preferred_tags,
        preferred_provider=preferred_provider,
        limit=limit,
    )
    cached = EXTERNAL_CANDIDATE_REPORT_CACHE.get(cache_key)
    if not cached:
        return None
    cached_at, report = cached
    if monotonic() - cached_at > EXTERNAL_CANDIDATE_CACHE_TTL_SECONDS:
        EXTERNAL_CANDIDATE_REPORT_CACHE.pop(cache_key, None)
        return None
    logger.debug("站外候选抓取命中缓存 | query=%s | subject=%s | limit=%s", cache_key[0], cache_key[1], cache_key[3])
    return clone_external_candidate_fetch_report(report)


def set_cached_external_candidate_report(
    query_text: str,
    *,
    subject_hint: str = "",
    preferred_tags: Optional[list[str]] = None,
    preferred_provider: str = "",
    limit: int = 3,
    report: ExternalCandidateFetchReport,
) -> None:
    """写入站外候选抓取缓存。"""
    cache_key = build_external_cache_key(
        query_text,
        subject_hint=subject_hint,
        preferred_tags=preferred_tags,
        preferred_provider=preferred_provider,
        limit=limit,
    )
    EXTERNAL_CANDIDATE_REPORT_CACHE[cache_key] = (monotonic(), clone_external_candidate_fetch_report(report))


def build_provider_success_summary(
    adapter: "ExternalCandidateAdapter",
    *,
    candidate_count: int,
    latency_ms: int,
) -> ExternalProviderFetchSummary:
    """构建 provider 成功摘要。"""
    status = "success" if candidate_count > 0 else "empty"
    return ExternalProviderFetchSummary(
        provider=adapter.provider,
        source_label=adapter.source_label,
        status=status,
        candidate_count=candidate_count,
        latency_ms=latency_ms,
    )


def build_provider_failure_summary(
    adapter: "ExternalCandidateAdapter",
    *,
    error_message: str,
    latency_ms: int,
) -> ExternalProviderFetchSummary:
    """构建 provider 失败摘要。"""
    return ExternalProviderFetchSummary(
        provider=adapter.provider,
        source_label=adapter.source_label,
        status="failed",
        candidate_count=0,
        error_message=clean_text(error_message),
        latency_ms=latency_ms,
    )


def log_provider_fetch_start(adapter: "ExternalCandidateAdapter", query_text: str) -> None:
    """记录 provider 抓取开始日志。"""
    logger.debug("站外候选抓取开始 | provider=%s | query=%s", adapter.provider, query_text)


def log_provider_fetch_success(
    adapter: "ExternalCandidateAdapter",
    *,
    query_text: str,
    candidate_count: int,
    latency_ms: int,
) -> None:
    """记录 provider 抓取成功日志。"""
    logger.debug(
        "站外候选抓取完成 | provider=%s | query=%s | count=%s | latency_ms=%s",
        adapter.provider,
        query_text,
        candidate_count,
        latency_ms,
    )


def log_provider_fetch_failure(
    adapter: "ExternalCandidateAdapter",
    *,
    query_text: str,
    error_message: str,
    latency_ms: int,
) -> None:
    """记录 provider 抓取失败日志。"""
    logger.debug(
        "站外候选抓取失败 | provider=%s | query=%s | latency_ms=%s | error=%s",
        adapter.provider,
        query_text,
        latency_ms,
        error_message,
    )
    logger.warning(
        "站外候选抓取失败 | provider=%s | query=%s | error=%s",
        adapter.provider,
        query_text,
        error_message,
    )


def clean_text(value: str) -> str:
    """清理 HTML/文本中的空白。"""
    text = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def parse_date_text(value: str) -> Optional[datetime]:
    """尝试解析页面里出现的日期文本。"""
    text = clean_text(value).strip("· ").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def dedupe_keep_order(values: list[str]) -> list[str]:
    """按原顺序去重。"""
    seen = set()
    ordered = []
    for value in values:
        normalized = clean_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def decode_duckduckgo_result_url(raw_url: str) -> str:
    """解析 DuckDuckGo 跳转 URL 中的真实目标。"""
    parsed = urlparse(str(raw_url or "").strip())
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target).strip()
    if raw_url.startswith("//duckduckgo.com/l/?"):
        parsed = urlparse(f"https:{raw_url}")
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target).strip()
    return str(raw_url or "").strip()


def parse_duckduckgo_result_entries(html: str, *, limit: int) -> list[tuple[str, str]]:
    """提取 DuckDuckGo HTML 搜索结果中的标题和真实链接。"""
    entries = []
    seen_urls = set()
    for matched in DUCKDUCKGO_RESULT_RE.finditer(str(html or "")):
        external_url = decode_duckduckgo_result_url(matched.group("href"))
        title = clean_text(matched.group("title"))
        if not external_url or not title or external_url in seen_urls:
            continue
        seen_urls.add(external_url)
        entries.append((title, external_url))
        if len(entries) >= max(1, limit):
            break
    return entries


def build_mooc_search_query(
    query_text: str, *, subject_hint: str = "", preferred_tags: Optional[list[str]] = None
) -> str:
    """构建中国大学慕课课程页检索词。"""
    parts = ["site:icourse163.org/learn"]
    if subject_hint:
        parts.append(subject_hint)
    if preferred_tags:
        parts.extend(preferred_tags[:2])
    parts.append(query_text)
    return " ".join(part for part in parts if clean_text(part))


def enrich_candidate_tags(
    *,
    title: str,
    summary: str,
    tags: list[str],
    subject_hint: str = "",
) -> tuple[list[str], str, str, str]:
    """统一站外候选的科目与主题画像。"""
    seed_tags = [subject_hint, *tags] if subject_hint else tags
    normalized_tags = build_subject_enriched_tags(seed_tags, title=title, summary=summary, max_tags=8)
    subject = infer_subject_from_text(tags=normalized_tags, title=title, summary=summary) or subject_hint
    primary_topic = fallback_primary_topic_name(summary, tags=normalized_tags, title=title, max_length=24)
    cluster_basis = primary_topic or (normalized_tags[1] if len(normalized_tags) > 1 else subject)
    cluster_key = "::".join([part for part in [subject, cluster_basis] if part])
    return normalized_tags, subject, primary_topic, cluster_key


class ExternalCandidateAdapter:
    """站外候选适配器基类。"""

    provider = "external"
    source_label = "站外候选"

    def search(
        self,
        query_text: str,
        *,
        subject_hint: str = "",
        preferred_tags: Optional[list[str]] = None,
        limit: int = 2,
    ) -> list[ExternalCandidate]:
        raise NotImplementedError

    def build_candidate(
        self,
        *,
        raw_id: str,
        title: str,
        external_url: str,
        summary: str = "",
        author: str = "",
        upload_time: Optional[datetime] = None,
        tags: Optional[list[str]] = None,
        subject_hint: str = "",
        can_import: bool = True,
        import_hint: str = "",
    ) -> ExternalCandidate:
        normalized_tags, subject, primary_topic, cluster_key = enrich_candidate_tags(
            title=title,
            summary=summary,
            tags=list(tags or []),
            subject_hint=subject_hint,
        )
        return ExternalCandidate(
            id=f"{self.provider}:{raw_id}",
            provider=self.provider,
            source_label=self.source_label,
            title=clean_text(title),
            external_url=external_url,
            summary=clean_text(summary),
            tags=normalized_tags,
            author=clean_text(author),
            upload_time=upload_time,
            subject=subject,
            primary_topic=primary_topic,
            cluster_key=cluster_key,
            can_import=can_import,
            import_hint=clean_text(import_hint),
        )


class BilibiliExternalCandidateAdapter(ExternalCandidateAdapter):
    """B 站搜索结果适配器。"""

    provider = "bilibili"
    source_label = "B站"
    search_url = "https://search.bilibili.com/all"

    def search(
        self,
        query_text: str,
        *,
        subject_hint: str = "",
        preferred_tags: Optional[list[str]] = None,
        limit: int = 2,
    ) -> list[ExternalCandidate]:
        response = requests.get(
            self.search_url,
            params={"keyword": query_text},
            headers=COMMON_HEADERS,
            timeout=http_timeout_seconds(),
        )
        response.raise_for_status()
        html = response.text
        titles = [clean_text(item) for item in VIDEO_TITLE_RE.findall(html)]
        links = dedupe_keep_order(VIDEO_LINK_RE.findall(html))
        authors = [clean_text(item) for item in VIDEO_AUTHOR_RE.findall(html)]
        dates = [parse_date_text(item) for item in VIDEO_DATE_RE.findall(html)]

        candidates = []
        for index, title in enumerate(titles[: max(1, limit)]):
            video_path = links[index] if index < len(links) else ""
            if not video_path:
                continue
            author = authors[index] if index < len(authors) else ""
            upload_time = dates[index] if index < len(dates) else None
            summary = f"来自 B站 的 {query_text} 相关视频。"
            if author:
                summary = f"{summary} 作者：{author}。"
            candidate = self.build_candidate(
                raw_id=video_path.strip("/"),
                title=title,
                external_url=f"https://www.bilibili.com/video/{video_path.strip('/')}",
                summary=summary,
                author=author,
                upload_time=upload_time,
                tags=list(preferred_tags or []),
                subject_hint=subject_hint,
            )
            candidates.append(candidate)
        return candidates


class YouTubeExternalCandidateAdapter(ExternalCandidateAdapter):
    """YouTube 搜索结果适配器。"""

    provider = "youtube"
    source_label = "YouTube"
    search_url = "https://www.youtube.com/results"

    def search(
        self,
        query_text: str,
        *,
        subject_hint: str = "",
        preferred_tags: Optional[list[str]] = None,
        limit: int = 2,
    ) -> list[ExternalCandidate]:
        response = requests.get(
            self.search_url,
            params={"search_query": query_text},
            headers=COMMON_HEADERS,
            timeout=http_timeout_seconds(),
        )
        response.raise_for_status()
        matched = YOUTUBE_INITIAL_DATA_RE.search(response.text)
        if not matched:
            return []

        try:
            payload = json.loads(matched.group(1))
        except json.JSONDecodeError:
            logger.warning("YouTube 搜索结果解析失败 | query=%s", query_text)
            return []

        renderers = []
        self._walk_video_renderers(payload, renderers)
        candidates = []
        for renderer in renderers[: max(1, limit)]:
            video_id = str(renderer.get("videoId") or "").strip()
            title = clean_text("".join(run.get("text", "") for run in renderer.get("title", {}).get("runs", [])))
            if not video_id or not title:
                continue
            author = clean_text("".join(run.get("text", "") for run in renderer.get("ownerText", {}).get("runs", [])))
            description = clean_text(
                "".join(run.get("text", "") for run in (renderer.get("descriptionSnippet") or {}).get("runs", []))
            )
            if not description:
                description = clean_text(
                    "".join(
                        run.get("text", "")
                        for run in (renderer.get("detailedMetadataSnippets") or [{}])[0]
                        .get("snippetText", {})
                        .get("runs", [])
                    )
                )
            published_text = clean_text((renderer.get("publishedTimeText") or {}).get("simpleText", ""))
            summary = description or f"来自 YouTube 的 {query_text} 相关视频。"
            if published_text:
                summary = f"{summary} 发布时间：{published_text}。"
            candidate = self.build_candidate(
                raw_id=video_id,
                title=title,
                external_url=f"https://www.youtube.com/watch?v={video_id}",
                summary=summary,
                author=author,
                tags=list(preferred_tags or []),
                subject_hint=subject_hint,
            )
            candidates.append(candidate)
        return candidates

    def _walk_video_renderers(self, node, renderers: list[dict]):
        if isinstance(node, dict):
            if "videoRenderer" in node:
                renderers.append(node["videoRenderer"])
            for value in node.values():
                self._walk_video_renderers(value, renderers)
        elif isinstance(node, list):
            for item in node:
                self._walk_video_renderers(item, renderers)


class MoocExternalCandidateAdapter(ExternalCandidateAdapter):
    """中国大学慕课搜索页适配器。"""

    provider = "icourse163"
    source_label = "中国大学慕课"
    search_url = "https://www.icourse163.org/search.htm"
    course_search_url = "https://html.duckduckgo.com/html/"

    def search_course_pages(
        self,
        query_text: str,
        *,
        subject_hint: str = "",
        preferred_tags: Optional[list[str]] = None,
        limit: int = 1,
    ) -> list[ExternalCandidate]:
        search_query = build_mooc_search_query(
            query_text,
            subject_hint=subject_hint,
            preferred_tags=preferred_tags,
        )
        response = requests.get(
            self.course_search_url,
            params={"q": search_query},
            headers=COMMON_HEADERS,
            timeout=http_timeout_seconds(),
        )
        response.raise_for_status()
        entries = parse_duckduckgo_result_entries(response.text, limit=max(1, limit * 3))
        candidates = []
        for title, external_url in entries:
            if "icourse163.org/learn/" not in external_url:
                continue
            candidate = self.build_candidate(
                raw_id=external_url.rstrip("/").split("/")[-1].split("?")[0] or quote_plus(query_text),
                title=title,
                external_url=external_url,
                summary=f"来自中国大学慕课的 {query_text} 相关课程，可直接进入课程页后导入学习。",
                tags=list(preferred_tags or []),
                subject_hint=subject_hint,
                can_import=True,
            )
            candidates.append(candidate)
            if len(candidates) >= max(1, limit):
                break
        return candidates

    def search(
        self,
        query_text: str,
        *,
        subject_hint: str = "",
        preferred_tags: Optional[list[str]] = None,
        limit: int = 1,
    ) -> list[ExternalCandidate]:
        course_candidates = self.search_course_pages(
            query_text,
            subject_hint=subject_hint,
            preferred_tags=preferred_tags,
            limit=limit,
        )
        if course_candidates:
            return course_candidates

        response = requests.get(
            self.search_url,
            params={"search": query_text},
            headers=COMMON_HEADERS,
            timeout=http_timeout_seconds(),
        )
        response.raise_for_status()
        title_match = re.search(r"<title>\s*([^<]+)\s*</title>", response.text)
        page_title = clean_text(title_match.group(1) if title_match else "搜索课程_中国大学MOOC(慕课)")
        if not page_title:
            return []

        candidate = self.build_candidate(
            raw_id=quote_plus(query_text),
            title=f"中国大学慕课 · {query_text} 相关课程",
            external_url=f"{self.search_url}?search={quote_plus(query_text)}",
            summary=f"打开中国大学慕课搜索页，继续查看 {query_text} 相关课程。页面标题：{page_title}。",
            tags=list(preferred_tags or []),
            subject_hint=subject_hint,
            can_import=False,
            import_hint="当前候选为中国大学慕课搜索页，需先进入具体课程页后才能导入。",
        )
        return [candidate][: max(1, limit)]


EXTERNAL_CANDIDATE_ADAPTERS: tuple[ExternalCandidateAdapter, ...] = (
    BilibiliExternalCandidateAdapter(),
    YouTubeExternalCandidateAdapter(),
    MoocExternalCandidateAdapter(),
)


def get_ordered_external_candidate_adapters(preferred_provider: str = "") -> list[ExternalCandidateAdapter]:
    """按期望平台排序 provider，优先把更贴近 seed 来源的平台提前。"""
    normalized_provider = clean_text(preferred_provider).lower()
    adapters = list(EXTERNAL_CANDIDATE_ADAPTERS)
    if not normalized_provider:
        return adapters
    adapters.sort(key=lambda adapter: (0 if adapter.provider == normalized_provider else 1, adapter.provider))
    return adapters


def _run_adapter_search(
    adapter: ExternalCandidateAdapter,
    normalized_query: str,
    *,
    subject_hint: str,
    preferred_tags: Optional[list[str]],
    per_provider_limit: int,
) -> tuple[ExternalProviderFetchSummary, list[ExternalCandidate]]:
    """单 provider 抓取，带有限次重试。"""
    start = perf_counter()
    log_provider_fetch_start(adapter, normalized_query)
    retries = max(0, int(getattr(settings, "RECOMMENDATION_EXTERNAL_FETCH_RETRIES", 1)))
    for attempt in range(retries + 1):
        try:
            provider_items = adapter.search(
                normalized_query,
                subject_hint=subject_hint,
                preferred_tags=list(preferred_tags or []),
                limit=per_provider_limit,
            )
            latency_ms = int((perf_counter() - start) * 1000)
            provider_added = sum(1 for item in provider_items if item.external_url)
            log_provider_fetch_success(
                adapter,
                query_text=normalized_query,
                candidate_count=provider_added,
                latency_ms=latency_ms,
            )
            return (
                build_provider_success_summary(
                    adapter,
                    candidate_count=provider_added,
                    latency_ms=latency_ms,
                ),
                provider_items,
            )
        except Exception as exc:
            if attempt < retries:
                time.sleep(0.25)
                continue
            latency_ms = int((perf_counter() - start) * 1000)
            log_provider_fetch_failure(
                adapter,
                query_text=normalized_query,
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            return (
                build_provider_failure_summary(adapter, error_message=str(exc), latency_ms=latency_ms),
                [],
            )


def _fetch_report_parallel(
    normalized_query: str,
    *,
    subject_hint: str,
    preferred_tags: Optional[list[str]],
    preferred_provider: str,
    limit: int,
) -> ExternalCandidateFetchReport:
    """并行抓取各 provider，总等待时间有上限。"""
    per_provider_limit = 1 if limit <= 3 else 2
    wall_timeout = float(getattr(settings, "RECOMMENDATION_EXTERNAL_TIMEOUT_SECONDS", 8.0)) + 0.5
    ordered_adapters = get_ordered_external_candidate_adapters(preferred_provider)
    results_by_provider: dict[str, tuple[ExternalProviderFetchSummary, list[ExternalCandidate]]] = {}

    with ThreadPoolExecutor(max_workers=len(ordered_adapters)) as executor:
        future_to_adapter = {
            executor.submit(
                _run_adapter_search,
                adapter,
                normalized_query,
                subject_hint=subject_hint,
                preferred_tags=preferred_tags,
                per_provider_limit=per_provider_limit,
            ): adapter
            for adapter in ordered_adapters
        }
        done, not_done = wait(future_to_adapter.keys(), timeout=wall_timeout)
        for future in done:
            adapter = future_to_adapter[future]
            try:
                results_by_provider[adapter.provider] = future.result()
            except Exception as exc:
                results_by_provider[adapter.provider] = (
                    build_provider_failure_summary(adapter, error_message=str(exc), latency_ms=0),
                    [],
                )
        for future in not_done:
            future.cancel()
            adapter = future_to_adapter[future]
            results_by_provider[adapter.provider] = (
                build_provider_failure_summary(
                    adapter,
                    error_message="等待站外候选响应超时",
                    latency_ms=int(wall_timeout * 1000),
                ),
                [],
            )

    provider_summaries: list[ExternalProviderFetchSummary] = []
    candidates: list[ExternalCandidate] = []
    seen_urls: set[str] = set()
    for adapter in ordered_adapters:
        summary, provider_items = results_by_provider.get(
            adapter.provider,
            (
                build_provider_failure_summary(adapter, error_message="未执行抓取", latency_ms=0),
                [],
            ),
        )
        provider_summaries.append(summary)
        for item in provider_items:
            if not item.external_url or item.external_url in seen_urls:
                continue
            seen_urls.add(item.external_url)
            candidates.append(item)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    return ExternalCandidateFetchReport(candidates=candidates[:limit], providers=provider_summaries)


def _fetch_report_sequential(
    normalized_query: str,
    *,
    subject_hint: str,
    preferred_tags: Optional[list[str]],
    preferred_provider: str,
    limit: int,
) -> ExternalCandidateFetchReport:
    """串行抓取（与旧行为一致，便于对照）。"""
    candidates: list[ExternalCandidate] = []
    provider_summaries: list[ExternalProviderFetchSummary] = []
    seen_urls: set[str] = set()
    per_provider_limit = 1 if limit <= 3 else 2
    for adapter in get_ordered_external_candidate_adapters(preferred_provider):
        summary, provider_items = _run_adapter_search(
            adapter,
            normalized_query,
            subject_hint=subject_hint,
            preferred_tags=preferred_tags,
            per_provider_limit=per_provider_limit,
        )
        provider_summaries.append(summary)
        for item in provider_items:
            if not item.external_url or item.external_url in seen_urls:
                continue
            seen_urls.add(item.external_url)
            candidates.append(item)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    return ExternalCandidateFetchReport(candidates=candidates[:limit], providers=provider_summaries)


def serialize_provider_summary(summary: ExternalProviderFetchSummary) -> dict:
    """序列化 provider 摘要。"""
    return {
        "provider": summary.provider,
        "source_label": summary.source_label,
        "status": summary.status,
        "candidate_count": summary.candidate_count,
        "error_message": summary.error_message,
        "latency_ms": summary.latency_ms,
    }


def fetch_external_candidates_report(
    query_text: str,
    *,
    subject_hint: str = "",
    preferred_tags: Optional[list[str]] = None,
    preferred_provider: str = "",
    limit: int = 3,
) -> ExternalCandidateFetchReport:
    """按统一接口抓取站外候选元数据并返回 provider 摘要。"""
    normalized_query = clean_text(query_text)
    if not normalized_query:
        return ExternalCandidateFetchReport(candidates=[], providers=[])
    cached_report = get_cached_external_candidate_report(
        normalized_query,
        subject_hint=subject_hint,
        preferred_tags=preferred_tags,
        preferred_provider=preferred_provider,
        limit=limit,
    )
    if cached_report is not None:
        return cached_report

    if getattr(settings, "RECOMMENDATION_EXTERNAL_FETCH_PARALLEL", True):
        report = _fetch_report_parallel(
            normalized_query,
            subject_hint=subject_hint,
            preferred_tags=preferred_tags,
            preferred_provider=preferred_provider,
            limit=limit,
        )
    else:
        report = _fetch_report_sequential(
            normalized_query,
            subject_hint=subject_hint,
            preferred_tags=preferred_tags,
            preferred_provider=preferred_provider,
            limit=limit,
        )
    set_cached_external_candidate_report(
        normalized_query,
        subject_hint=subject_hint,
        preferred_tags=preferred_tags,
        preferred_provider=preferred_provider,
        limit=limit,
        report=report,
    )
    return clone_external_candidate_fetch_report(report)


def fetch_external_candidates(
    query_text: str,
    *,
    subject_hint: str = "",
    preferred_tags: Optional[list[str]] = None,
    limit: int = 3,
) -> list[ExternalCandidate]:
    """兼容旧调用方，只返回站外候选列表。"""
    report = fetch_external_candidates_report(
        query_text,
        subject_hint=subject_hint,
        preferred_tags=preferred_tags,
        limit=limit,
    )
    return report.candidates
