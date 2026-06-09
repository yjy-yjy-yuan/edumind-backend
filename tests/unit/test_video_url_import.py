"""远程视频 URL 导入服务单元测试。"""

import pytest
from fastapi import HTTPException

from app.services.video.url_import import (
    MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE,
    detect_remote_video_source,
    extract_mooc_course_id,
    import_remote_video_from_url,
    is_mooc_video_url,
)


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


def test_mooc_direct_import_is_rejected_before_download_queue(db):
    with pytest.raises(HTTPException) as exc_info:
        import_remote_video_from_url(
            db,
            user_id=1,
            video_url="https://www.icourse163.org/course/PKU-1002534001?tid=1475372482",
            process_options={"model": "base", "language": "zh"},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == MOOC_UNSUPPORTED_DIRECT_IMPORT_MESSAGE
