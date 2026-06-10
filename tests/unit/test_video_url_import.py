"""远程视频 URL 导入服务单元测试。"""

import pytest
from fastapi import HTTPException

import app.services.video.url_import as url_import
from app.services.video.url_import import (
    MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE,
    detect_remote_video_source,
    extract_mooc_course_id,
    import_remote_video_from_url,
    is_mooc_video_url,
)

FAKE_SUBMITTED_REMOTE_DOWNLOAD = {}


def fake_submit_remote_video_download(db, *, video, video_url, source_type, process_options, request_source):
    FAKE_SUBMITTED_REMOTE_DOWNLOAD["video_id"] = video.id
    FAKE_SUBMITTED_REMOTE_DOWNLOAD["video_url"] = video_url
    FAKE_SUBMITTED_REMOTE_DOWNLOAD["source_type"] = source_type


@pytest.mark.parametrize(
    "url",
    [
        "https://www.icourse163.org/course/PKU-1002534001?tid=1475372482",
        "https://icourse163.org/course/PKU-1002534001",
        "https://study.icourse163.org/learn/PKU-1002534001",
    ],
)
def test_mooc_urls_are_detected(url):
    assert is_mooc_video_url(url) is True

    source_type, placeholder = detect_remote_video_source(url)

    assert source_type == "mooc"
    assert placeholder.startswith("mooc-")


def test_mooc_detection_does_not_match_lookalike_domain():
    assert is_mooc_video_url("https://evil-icourse163.org/course/PKU-1002534001") is False

    with pytest.raises(HTTPException) as exc_info:
        detect_remote_video_source("https://evil-icourse163.org/course/PKU-1002534001")

    assert exc_info.value.status_code == 400


def test_mooc_course_url_placeholder_uses_course_id_for_course_path():
    source_type, placeholder = detect_remote_video_source(
        "https://www.icourse163.org/course/PKU-1002534001?tid=1475372482"
    )

    assert source_type == "mooc"
    assert placeholder == "mooc-PKU-1002534001"
    assert extract_mooc_course_id("https://study.icourse163.org/learn/PKU-1002534001") == "PKU-1002534001"


def test_mooc_direct_import_is_rejected_before_download_queue(db, monkeypatch):
    monkeypatch.setattr(url_import.settings, "MOOC_DIRECT_IMPORT_ENABLED", False)
    monkeypatch.setattr(url_import.settings, "MOOC_DOWNLOAD_COOKIE_FILE", "")
    monkeypatch.setattr(url_import.settings, "MOOC_DOWNLOAD_COOKIE", "")

    with pytest.raises(HTTPException) as exc_info:
        import_remote_video_from_url(
            db,
            user_id=1,
            video_url="https://www.icourse163.org/course/PKU-1002534001?tid=1475372482",
            process_options={"model": "base", "language": "zh"},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE


def test_mooc_direct_import_is_allowed_when_experimental_config_is_complete(db, monkeypatch):
    FAKE_SUBMITTED_REMOTE_DOWNLOAD.clear()
    monkeypatch.setattr(url_import.settings, "MOOC_DIRECT_IMPORT_ENABLED", True)
    monkeypatch.setattr(url_import.settings, "MOOC_DOWNLOAD_COOKIE_FILE", "")
    monkeypatch.setattr(url_import.settings, "MOOC_DOWNLOAD_COOKIE", "NTESSTUDYSI=fake")
    monkeypatch.setattr(url_import, "submit_remote_video_download", fake_submit_remote_video_download)

    result = import_remote_video_from_url(
        db,
        user_id=1,
        video_url="https://www.icourse163.org/course/PKU-1002534001?tid=1475372482",
        process_options={"model": "base", "language": "zh"},
    )

    assert result.status == "downloading"
    assert result.video.title == "mooc-PKU-1002534001"
    assert FAKE_SUBMITTED_REMOTE_DOWNLOAD["source_type"] == "mooc"
