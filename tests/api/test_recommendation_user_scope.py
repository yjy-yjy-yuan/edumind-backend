"""API 测试 - 推荐系统用户作用域与软删除隔离。"""

import datetime

import pytest

from app.models.video import Video, VideoStatus
from app.utils.auth_token import build_auth_token


class TestRecommendationUserScope:
    """验证推荐系统不会泄漏跨用户视频或已删除视频。"""

    def test_home_recommendation_excludes_other_user_videos(self, client, db, sample_user):
        """首页推荐不应包含其他用户的视频。"""
        from app.models.user import User

        other_user = User(
            username="other",
            email="other@example.com",
            password="password",
        )
        db.add(other_user)
        db.commit()

        own_video = Video(
            user_id=sample_user.id,
            title="我的视频",
            filename="own.mp4",
            filepath="/tmp/own.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
        )
        other_video = Video(
            user_id=other_user.id,
            title="别人的视频",
            filename="other.mp4",
            filepath="/tmp/other.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
        )
        db.add_all([own_video, other_video])
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 10},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200
        payload = response.json()
        item_ids = [item["id"] for item in payload["items"]]
        assert own_video.id in item_ids
        assert other_video.id not in item_ids

    def test_home_recommendation_excludes_deleted_videos(self, client, db, sample_user):
        """首页推荐不应包含已软删除的视频。"""
        active_video = Video(
            user_id=sample_user.id,
            title="正常视频",
            filename="active.mp4",
            filepath="/tmp/active.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
        )
        deleted_video = Video(
            user_id=sample_user.id,
            title="已删除视频",
            filename="deleted.mp4",
            filepath="/tmp/deleted.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            is_deleted=True,
            deleted_at=datetime.datetime.now(),
        )
        db.add_all([active_video, deleted_video])
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 10},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 200
        payload = response.json()
        item_ids = [item["id"] for item in payload["items"]]
        assert active_video.id in item_ids
        assert deleted_video.id not in item_ids

    def test_related_recommendation_seed_must_belong_to_user(self, client, db, sample_user):
        """related 场景的 seed_video 必须是当前用户的视频。"""
        from app.models.user import User

        other_user = User(
            username="other2",
            email="other2@example.com",
            password="password",
        )
        db.add(other_user)
        db.commit()

        other_video = Video(
            user_id=other_user.id,
            title="别人的种子视频",
            filename="seed.mp4",
            filepath="/tmp/seed.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
        )
        db.add(other_video)
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "related", "seed_video_id": other_video.id, "limit": 4},
            headers={"Authorization": f"Bearer {build_auth_token(sample_user.id)}"},
        )
        assert response.status_code == 404
        assert "无权访问" in response.json()["detail"] or "不存在" in response.json()["detail"]

    def test_unauthenticated_user_can_still_see_recommendations(self, client, db, sample_user):
        """未登录用户仍能看到推荐（不过滤 user_id，但过滤 is_deleted）。"""
        active_video = Video(
            user_id=sample_user.id,
            title="公共可见视频",
            filename="public.mp4",
            filepath="/tmp/public.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
        )
        deleted_video = Video(
            user_id=sample_user.id,
            title="已删除视频",
            filename="deleted.mp4",
            filepath="/tmp/deleted.mp4",
            status=VideoStatus.COMPLETED,
            process_progress=100,
            is_deleted=True,
            deleted_at=datetime.datetime.now(),
        )
        db.add_all([active_video, deleted_video])
        db.commit()

        response = client.get(
            "/api/recommendations/videos",
            params={"scene": "home", "limit": 10},
        )
        assert response.status_code == 200
        payload = response.json()
        item_ids = [item["id"] for item in payload["items"]]
        assert active_video.id in item_ids
        assert deleted_video.id not in item_ids
