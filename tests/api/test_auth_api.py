"""API 测试 - 认证接口"""

import io
import os

import pytest
from app.models.user import User
from app.utils.auth_token import build_auth_token


@pytest.mark.api
class TestAuthAPI:
    """认证 API 测试"""

    def test_get_current_user_requires_auth(self, client):
        """测试获取当前用户时必须登录。"""
        response = client.get("/api/auth/user")
        assert response.status_code == 401
        assert response.json()["detail"] == "未登录"

    def test_update_username_persists_to_users_table(self, client, db, sample_user):
        """测试更新用户名会真实写入 users 表。"""
        headers = {"Authorization": f"Bearer {build_auth_token(sample_user.id)}"}

        response = client.post(
            "/api/auth/user/update",
            json={"username": "学习搭子"},
            headers=headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["user"]["username"] == "学习搭子"

        db.refresh(sample_user)
        assert sample_user.username == "学习搭子"

    def test_update_username_rejects_duplicate_value(self, client, db, sample_user):
        """测试用户名冲突时返回 400。"""
        another_user = User(
            username="existing_user",
            email="another@example.com",
            phone="13900139000",
            password="Other#123A",
        )
        db.add(another_user)
        db.commit()

        headers = {"Authorization": f"Bearer {build_auth_token(sample_user.id)}"}
        response = client.post(
            "/api/auth/user/update",
            json={"username": another_user.username},
            headers=headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "用户名已存在"

    def test_upload_avatar_updates_users_table_and_serves_file(self, client, db, sample_user, tmp_path, monkeypatch):
        """测试头像上传后会写回 users.avatar，并可通过接口读取。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
        headers = {"Authorization": f"Bearer {build_auth_token(sample_user.id)}"}
        avatar_bytes = b"fake-avatar-content"

        response = client.post(
            "/api/auth/user/avatar",
            headers=headers,
            files={"file": ("avatar.png", io.BytesIO(avatar_bytes), "image/png")},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["user"]["avatar"].startswith("/api/auth/avatar-files/")

        db.refresh(sample_user)
        assert sample_user.avatar == payload["user"]["avatar"]

        filename = payload["user"]["avatar"].rsplit("/", 1)[-1]
        file_path = tmp_path / "uploads" / "avatars" / filename
        assert os.path.exists(file_path)
        assert file_path.read_bytes() == avatar_bytes

        fetch_response = client.get(payload["user"]["avatar"])
        assert fetch_response.status_code == 200
        assert fetch_response.content == avatar_bytes
