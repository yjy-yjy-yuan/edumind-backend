"""用户隔离与切换场景测试。"""

import pytest

from app.models.note import Note
from app.models.user import User
from app.models.video import Video, VideoStatus
from app.utils.auth_token import build_auth_token


def _auth_headers(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {build_auth_token(user_id)}"}


@pytest.mark.api
def test_switch_user_should_isolate_and_restore_records(client, db, sample_user):
    user_a = sample_user
    user_b = User(
        username="switch_user_b",
        email="switch_user_b@example.com",
        phone="13800138001",
        password="Strong#456",
    )
    db.add(user_b)
    db.commit()
    db.refresh(user_b)

    video_a = Video(
        user_id=user_a.id,
        filename="ua.mp4",
        filepath="/tmp/ua.mp4",
        title="用户A视频",
        status=VideoStatus.COMPLETED,
    )
    video_b = Video(
        user_id=user_b.id,
        filename="ub.mp4",
        filepath="/tmp/ub.mp4",
        title="用户B视频",
        status=VideoStatus.COMPLETED,
    )
    db.add_all([video_a, video_b])
    db.commit()
    db.refresh(video_a)
    db.refresh(video_b)

    db.add_all(
        [
            Note(
                title="A-note",
                content="A content",
                note_type="text",
                video_id=video_a.id,
                user_id=user_a.id,
            ),
            Note(
                title="B-note",
                content="B content",
                note_type="text",
                video_id=video_b.id,
                user_id=user_b.id,
            ),
        ]
    )
    db.commit()

    headers_a = _auth_headers(user_a.id)
    headers_b = _auth_headers(user_b.id)

    # 用户 A：仅看到自己的视频/笔记
    a_video_resp = client.get("/api/videos/list", headers=headers_a)
    assert a_video_resp.status_code == 200
    assert [item["id"] for item in a_video_resp.json()["videos"]] == [video_a.id]

    a_note_resp = client.get("/api/notes/notes", headers=headers_a)
    assert a_note_resp.status_code == 200
    assert [item["title"] for item in a_note_resp.json()["data"]] == ["A-note"]

    # 切换到用户 B：应“清空”A上下文（看不到A数据）
    b_video_resp = client.get("/api/videos/list", headers=headers_b)
    assert b_video_resp.status_code == 200
    assert [item["id"] for item in b_video_resp.json()["videos"]] == [video_b.id]

    b_note_resp = client.get("/api/notes/notes", headers=headers_b)
    assert b_note_resp.status_code == 200
    assert [item["title"] for item in b_note_resp.json()["data"]] == ["B-note"]

    # 切回用户 A：历史可恢复
    a_video_resp_again = client.get("/api/videos/list", headers=headers_a)
    assert a_video_resp_again.status_code == 200
    assert [item["id"] for item in a_video_resp_again.json()["videos"]] == [video_a.id]

    a_note_resp_again = client.get("/api/notes/notes", headers=headers_a)
    assert a_note_resp_again.status_code == 200
    assert [item["title"] for item in a_note_resp_again.json()["data"]] == ["A-note"]

    # 处理/搜索相关接口应拒绝越权访问
    forbidden_status = client.get(f"/api/videos/{video_a.id}/status", headers=headers_b)
    assert forbidden_status.status_code == 404

    forbidden_index_status = client.get(f"/api/search/videos/{video_a.id}/index/status", headers=headers_b)
    assert forbidden_index_status.status_code == 403
