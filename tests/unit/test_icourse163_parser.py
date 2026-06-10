"""icourse163 解析器单元测试。"""

import pytest
import requests

from app.services.video.icourse163_parser import (
    MoocAuthRequiredError,
    apply_cookie_file,
    apply_cookie_header,
    has_mooc_auth_config,
    parse_mooc_url,
)


@pytest.mark.parametrize(
    "url,expected_course_id,expected_tid",
    [
        ("https://www.icourse163.org/course/PKU-1002534001?tid=1475372482", "PKU-1002534001", "1475372482"),
        ("https://study.icourse163.org/learn/PKU-1002534001", "PKU-1002534001", ""),
        (
            "https://www.icourse163.org/learn/ZJU-93001?tid=1003997005#/learn/content"
            "?type=detail&id=1244068605&cid=1244068605",
            "ZJU-93001",
            "1003997005",
        ),
    ],
)
def test_parse_mooc_url_extracts_course_and_term(url, expected_course_id, expected_tid):
    result = parse_mooc_url(url)

    assert result.course_id == expected_course_id
    assert result.tid == expected_tid


def test_parse_mooc_url_with_content_id():
    result = parse_mooc_url("https://www.icourse163.org/learn/ZJU-93001#/learn/content?type=detail&id=12345")

    assert result.course_id == "ZJU-93001"
    assert result.content_id == "12345"


def test_parse_mooc_url_invalid_search_page():
    result = parse_mooc_url("https://www.icourse163.org/search.htm?search=python")

    assert result.course_id == ""
    assert result.tid == ""
    assert result.content_id == ""


def test_has_mooc_auth_config():
    assert has_mooc_auth_config("", "") is False
    assert has_mooc_auth_config("/tmp/cookies.txt", "") is True
    assert has_mooc_auth_config("", "NTESSTUDYSI=abc") is True


def test_apply_cookie_header_sets_cookie():
    session = requests.Session()

    apply_cookie_header(session, "NTESSTUDYSI=abc; EDUWEBDEVICE=device")

    assert session.cookies.get("NTESSTUDYSI") == "abc"
    assert session.cookies.get("EDUWEBDEVICE") == "device"


def test_apply_cookie_file_missing_path_raises_auth_error():
    session = requests.Session()

    with pytest.raises(MoocAuthRequiredError) as exc_info:
        apply_cookie_file(session, "/path/not/exist/cookies.txt")

    assert "MOOC_DOWNLOAD_COOKIE_FILE" in str(exc_info.value)
