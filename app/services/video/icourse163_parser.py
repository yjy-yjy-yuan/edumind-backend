"""中国大学慕课 icourse163 解析辅助。

该模块只提供可验证的 URL/Cookie/错误分类与受控入口。icourse163 的课时列表、
视频源接口和 DRM/加密链路需要基于已登录账号抓包确认后再补全。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger(__name__)

ICOURSE163_BASE_URL = "https://www.icourse163.org/"
ICOURSE163_API_BASE_URL = "https://www.icourse163.org/web/j"
DEFAULT_MOOC_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class MoocUrlParts:
    """从 icourse163 URL 提取出的核心参数。"""

    course_id: str = ""
    tid: str = ""
    content_id: str = ""


@dataclass
class MoocVideoInfo:
    """解析到的慕课视频源信息。"""

    video_url: str
    title: str
    duration: int = 0
    format_type: str = ""


class MoocParserError(RuntimeError):
    """icourse163 解析基础异常。"""

    code = "mooc_parser_error"

    def __init__(self, message: str, *, debug_detail: str = ""):
        super().__init__(message)
        self.debug_detail = debug_detail


class MoocAuthRequiredError(MoocParserError):
    """缺少登录态或登录态不可用。"""

    code = "mooc_auth_required"


class MoocUnsupportedDirectImportError(MoocParserError):
    """当前 URL 类型或平台能力尚不支持直导。"""

    code = "mooc_unsupported_direct_import"


class MoocVideoSourceError(MoocParserError):
    """无法解析视频源。"""

    code = "mooc_video_source_error"


class MoocApiError(MoocParserError):
    """平台接口异常。"""

    code = "mooc_api_error"


def parse_mooc_url(url: str) -> MoocUrlParts:
    """从中国大学慕课 URL 中提取课程 ID、学期 ID 和课时内容 ID。"""
    parsed = urlparse(str(url or "").strip())
    query = parse_qs(parsed.query)
    fragment_query = parse_qs(parsed.fragment.partition("?")[2])
    course_match = re.search(r"/(?:learn|course)/([^/?#]+)", parsed.path or "")
    course_id = course_match.group(1) if course_match else ""
    tid = query.get("tid", [""])[0]
    content_id = query.get("id", [""])[0] or query.get("cid", [""])[0]
    if not content_id:
        content_id = fragment_query.get("id", [""])[0] or fragment_query.get("cid", [""])[0]
    return MoocUrlParts(course_id=course_id, tid=tid, content_id=content_id)


def has_mooc_auth_config(cookie_file: str = "", cookie_header: str = "") -> bool:
    """判断是否配置了可用于 icourse163 的登录态来源。"""
    return bool(str(cookie_file or "").strip() or str(cookie_header or "").strip())


def build_mooc_session(
    *,
    cookie_file: str = "",
    cookie_header: str = "",
    user_agent: str = "",
    referer: str = "",
) -> requests.Session:
    """构建带基础请求头和 Cookie 的 icourse163 Session。"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": str(user_agent or "").strip() or DEFAULT_MOOC_USER_AGENT,
            "Referer": str(referer or "").strip() or ICOURSE163_BASE_URL,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    apply_cookie_file(session, cookie_file)
    apply_cookie_header(session, cookie_header)
    return session


def apply_cookie_file(session: requests.Session, cookie_file: str) -> None:
    """加载 Netscape cookies.txt 到请求 Session。"""
    path = str(cookie_file or "").strip()
    if not path:
        return
    if not os.path.exists(path):
        raise MoocAuthRequiredError("MOOC_DOWNLOAD_COOKIE_FILE 指向的文件不存在，请检查 Cookie 文件路径。")
    jar = MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:  # noqa: BLE001
        raise MoocAuthRequiredError("MOOC_DOWNLOAD_COOKIE_FILE 加载失败，请确认是 Netscape cookies.txt 格式。") from exc
    session.cookies.update(jar)


def apply_cookie_header(session: requests.Session, cookie_header: str) -> None:
    """解析原始 Cookie 请求头并写入 Session。"""
    header = str(cookie_header or "").strip()
    if not header:
        return
    for item in header.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            session.cookies.set(name.strip(), value.strip())


def normalize_mooc_parser_error(exc: Exception) -> MoocParserError:
    """把底层异常归一化为用户可读的 icourse163 错误。"""
    if isinstance(exc, MoocParserError):
        return exc
    if isinstance(exc, requests.HTTPError):
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        if status_code in (401, 403):
            return MoocAuthRequiredError("中国大学慕课登录态不可用或已过期，请更新 Cookie。", debug_detail=str(exc))
        return MoocApiError(f"中国大学慕课接口返回异常: HTTP {status_code}", debug_detail=str(exc))
    if isinstance(exc, requests.RequestException):
        return MoocApiError("中国大学慕课接口请求失败，请检查网络、Cookie 或平台限制。", debug_detail=str(exc))
    return MoocParserError(str(exc) or "中国大学慕课解析失败")


def parse_and_download_mooc(
    *,
    url: str,
    output_dir: str,
    cookie_file: str = "",
    cookie_header: str = "",
    user_agent: str = "",
    referer: str = "",
) -> tuple[str, str]:
    """解析并下载 icourse163 视频。

    当前只完成受控入口、登录态校验和 URL 参数校验。真实课程大纲/课时/视频源解析需要
    使用已选课账号抓包确认后补全，避免把不稳定的猜测 API 作为生产能力暴露。
    """
    parts = parse_mooc_url(url)
    if not parts.course_id:
        raise MoocUnsupportedDirectImportError("无法从中国大学慕课 URL 提取课程 ID。")
    if not has_mooc_auth_config(cookie_file, cookie_header):
        raise MoocAuthRequiredError("下载中国大学慕课视频需要配置 MOOC_DOWNLOAD_COOKIE_FILE 或 MOOC_DOWNLOAD_COOKIE。")
    build_mooc_session(
        cookie_file=cookie_file,
        cookie_header=cookie_header,
        user_agent=user_agent,
        referer=referer,
    )
    if not parts.content_id:
        raise MoocUnsupportedDirectImportError(
            "当前仅识别中国大学慕课课程页，尚未实现课程大纲到可下载课时视频源的解析；"
            "请上传本地视频/音频文件，或在完成 icourse163 抓包解析后开启直导。"
        )
    raise MoocVideoSourceError(
        "已识别中国大学慕课课时参数，但视频源 API/DRM 解析尚未完成；"
        "请上传本地视频/音频文件，或补充 icourse163 专用解析器实现。"
    )
