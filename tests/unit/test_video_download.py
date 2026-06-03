"""视频链接下载任务单元测试。"""

from app.tasks.video_download import build_download_error_message


def test_build_download_error_message_adds_youtube_config_hint():
    """YouTube 403/反爬失败应提示代理或浏览器 Cookie 配置。"""
    message = build_download_error_message("youtube", "HTTP Error 403: Forbidden")

    assert "HTTP Error 403" in message
    assert "请检查 YOUTUBE_DOWNLOAD_PROXY 或 YOUTUBE_DOWNLOAD_BROWSER_COOKIE 配置" in message


def test_build_download_error_message_adds_mooc_cookie_and_extractor_hint():
    """慕课不支持或登录态失败应提示 Cookie 与 yt-dlp 页面支持限制。"""
    message = build_download_error_message("mooc", "Unsupported URL: https://www.icourse163.org/learn/HIT-1001527001")

    assert "Unsupported URL" in message
    assert "MOOC_DOWNLOAD_COOKIE_FILE" in message
    assert "yt-dlp 可能不支持该页面" in message


def test_build_download_error_message_keeps_other_sources_unchanged():
    """非 YouTube/MOOC 平台不追加无关配置提示。"""
    message = build_download_error_message("bilibili", "HTTP Error 403: Forbidden")

    assert message == "HTTP Error 403: Forbidden"
