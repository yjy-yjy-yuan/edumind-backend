"""视频链接下载任务单元测试。"""

import logging

import pytest

from app.tasks import video_download
from app.tasks.video_download import (
    YOUTUBE_DEFAULT_FORMAT,
    build_download_error_message,
    build_ydl_options,
    is_youtube_forbidden_error,
    parse_browser_cookie_spec,
)

FAKE_STATUS_UPDATES = []
FAKE_RESIDUE_PATH = None


class FakeForbiddenYoutubeDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=False):
        return {"title": "Learn Sin, Cos, and Tan in 5 minutes"}

    def download(self, urls):
        FAKE_RESIDUE_PATH.write_bytes(b"partial")
        raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")


def fake_update_video_status(video_id, status, progress, step, **kwargs):
    FAKE_STATUS_UPDATES.append(
        {
            "video_id": video_id,
            "status": status.value if hasattr(status, "value") else str(status),
            "progress": progress,
            "step": step,
            "error_message": kwargs.get("error_message"),
        }
    )


def test_build_download_error_message_adds_youtube_config_hint():
    """YouTube 403/反爬失败应提示代理或浏览器 Cookie 配置。"""
    message = build_download_error_message("youtube", "HTTP Error 403: Forbidden")

    assert "HTTP Error 403" in message
    assert "YOUTUBE_DOWNLOAD_PROXY" in message
    assert "YOUTUBE_DOWNLOAD_BROWSER_COOKIE" in message
    assert "YOUTUBE_DOWNLOAD_COOKIE_FILE" in message


def test_build_download_error_message_adds_mooc_cookie_and_extractor_hint():
    """慕课直导应提示当前不支持课程页直接处理。"""
    message = build_download_error_message("mooc", "Unsupported URL: https://www.icourse163.org/learn/HIT-1001527001")

    assert "Unsupported URL" in message
    assert "MOOC_DIRECT_IMPORT_ENABLED" in message
    assert "MOOC_DOWNLOAD_COOKIE" in message
    assert "DRM/API" in message
    assert "上传本地视频/音频文件" in message


def test_build_download_error_message_keeps_other_sources_unchanged():
    """非 YouTube/MOOC 平台不追加无关配置提示。"""
    message = build_download_error_message("bilibili", "HTTP Error 403: Forbidden")

    assert message == "HTTP Error 403: Forbidden"


def test_parse_browser_cookie_spec():
    assert parse_browser_cookie_spec("chrome") == ("chrome",)
    assert parse_browser_cookie_spec("firefox:default-release") == ("firefox", "default-release")
    assert parse_browser_cookie_spec("edge:Profile 1") == ("edge", "Profile 1")
    assert parse_browser_cookie_spec("safari") == ("safari",)
    assert parse_browser_cookie_spec("opera") is None


def test_build_youtube_ydl_options_includes_runtime_config(monkeypatch, tmp_path):
    """YouTube yt-dlp 配置应支持 proxy/cookie/header/format/extractor args。"""
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_BROWSER_COOKIE", "chrome:Profile 1")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_COOKIE_FILE", "/tmp/youtube-cookies.txt")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_USER_AGENT", "Mozilla/5.0 Test")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_REFERER", "https://www.youtube.com/")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_FORMAT", "18")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_IMPERSONATE", "chrome")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_SLEEP_REQUESTS", 1.5)
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_RETRIES", 4)
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_EXTRACTOR_RETRIES", 2)
    monkeypatch.setattr(
        video_download.settings,
        "YOUTUBE_EXTRACTOR_ARGS",
        '{"youtube":{"player_client":["android","web"]}}',
    )

    options = build_ydl_options(str(tmp_path), "youtube")

    assert options["proxy"] == "http://127.0.0.1:7890"
    assert options["cookiesfrombrowser"] == ("chrome", "Profile 1")
    assert options["cookiefile"] == "/tmp/youtube-cookies.txt"
    assert options["http_headers"]["User-Agent"] == "Mozilla/5.0 Test"
    assert options["http_headers"]["Referer"] == "https://www.youtube.com/"
    assert options["format"] == "18"
    assert options["impersonate"] == "chrome"
    assert options["sleep_interval_requests"] == 1.5
    assert options["retries"] == 4
    assert options["extractor_retries"] == 2
    assert options["extractor_args"] == {"youtube": {"player_client": ["android", "web"]}}


def test_build_youtube_ydl_options_uses_default_format(monkeypatch, tmp_path):
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_PROXY", "")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_BROWSER_COOKIE", "")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_COOKIE_FILE", "")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_USER_AGENT", "")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_REFERER", "")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_FORMAT", "")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_IMPERSONATE", "")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_SLEEP_REQUESTS", 0)
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_RETRIES", 10)
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_EXTRACTOR_RETRIES", 3)
    monkeypatch.setattr(video_download.settings, "YOUTUBE_EXTRACTOR_ARGS", "")

    options = build_ydl_options(str(tmp_path), "youtube")

    assert options["format"] == YOUTUBE_DEFAULT_FORMAT
    assert options["sleep_interval_requests"] == 0.0
    assert options["retries"] == 10
    assert options["extractor_retries"] == 3


def test_invalid_youtube_extractor_args_is_logged_and_ignored(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(video_download.settings, "YOUTUBE_EXTRACTOR_ARGS", "{bad-json")

    with caplog.at_level(logging.WARNING):
        options = build_ydl_options(str(tmp_path), "youtube")

    assert "extractor_args" not in options
    assert "YOUTUBE_EXTRACTOR_ARGS 不是合法 JSON" in caplog.text


def test_invalid_youtube_numeric_options_are_logged_and_ignored(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_SLEEP_REQUESTS", -1)
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_RETRIES", "bad")
    monkeypatch.setattr(video_download.settings, "YOUTUBE_DOWNLOAD_EXTRACTOR_RETRIES", -3)

    with caplog.at_level(logging.WARNING):
        options = build_ydl_options(str(tmp_path), "youtube")

    assert "sleep_interval_requests" not in options
    assert "retries" not in options
    assert "extractor_retries" not in options
    assert "YOUTUBE_DOWNLOAD_SLEEP_REQUESTS 必须是非负数字" in caplog.text
    assert "YOUTUBE_DOWNLOAD_RETRIES 必须是非负整数" in caplog.text
    assert "YOUTUBE_DOWNLOAD_EXTRACTOR_RETRIES 必须是非负整数" in caplog.text


def test_youtube_forbidden_error_detection():
    assert is_youtube_forbidden_error("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    assert is_youtube_forbidden_error("Forbidden")
    assert not is_youtube_forbidden_error("Unsupported URL")


def test_youtube_403_task_marks_failed_and_cleans_residue(monkeypatch, tmp_path, caplog):
    """下载阶段 403 应写回失败提示并清理临时残留。"""
    pytest.importorskip("yt_dlp", reason="yt_dlp not installed")
    global FAKE_RESIDUE_PATH

    FAKE_STATUS_UPDATES.clear()
    output_title = "youtube-Learn Sin, Cos, and Tan in 5 minutes"
    FAKE_RESIDUE_PATH = tmp_path / f"{output_title}.part"

    monkeypatch.setattr(video_download.settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(video_download, "update_video_status", fake_update_video_status)
    monkeypatch.setattr(video_download, "finalize_video_record", lambda *args, **kwargs: None)
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeForbiddenYoutubeDL)

    with caplog.at_level(logging.ERROR):
        video_download.download_video_from_url_task(123, "https://www.youtube.com/watch?v=gSGbYOzjynk", "youtube")

    assert FAKE_RESIDUE_PATH.exists() is False
    assert FAKE_STATUS_UPDATES[-1]["status"] == "failed"
    assert FAKE_STATUS_UPDATES[-1]["step"] == "下载失败"
    assert "YOUTUBE_DOWNLOAD_PROXY" in FAKE_STATUS_UPDATES[-1]["error_message"]
    assert "YOUTUBE_DOWNLOAD_BROWSER_COOKIE" in FAKE_STATUS_UPDATES[-1]["error_message"]
    assert "YOUTUBE_DOWNLOAD_COOKIE_FILE" in FAKE_STATUS_UPDATES[-1]["error_message"]
    assert "HTTP Error 403" in caplog.text


def test_mooc_task_marks_failed_when_experimental_import_not_configured(monkeypatch, tmp_path):
    """慕课任务层兜底应拒绝未配置的直导请求，避免 fallback 到 yt-dlp。"""
    FAKE_STATUS_UPDATES.clear()
    monkeypatch.setattr(video_download.settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(video_download.settings, "MOOC_DIRECT_IMPORT_ENABLED", False)
    monkeypatch.setattr(video_download.settings, "MOOC_DOWNLOAD_COOKIE_FILE", "")
    monkeypatch.setattr(video_download.settings, "MOOC_DOWNLOAD_COOKIE", "")
    monkeypatch.setattr(video_download, "update_video_status", fake_update_video_status)

    video_download.download_video_from_url_task(
        123,
        "https://www.icourse163.org/course/PKU-1002534001?tid=1475372482",
        "mooc",
    )

    assert FAKE_STATUS_UPDATES[-1]["status"] == "failed"
    assert FAKE_STATUS_UPDATES[-1]["step"] == "下载失败"
    assert "MOOC_DIRECT_IMPORT_ENABLED" in FAKE_STATUS_UPDATES[-1]["error_message"]
    assert "MOOC_DOWNLOAD_COOKIE" in FAKE_STATUS_UPDATES[-1]["error_message"]
