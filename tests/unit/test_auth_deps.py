"""auth_deps 行为单测。"""

from app.utils.auth_deps import resolve_user_from_request
from app.utils.auth_token import build_auth_token


def test_resolve_user_ignores_user_id_without_legacy_flag(db, sample_user):
    """默认不信任仅传 user_id（无 Bearer）。"""
    assert resolve_user_from_request(db, sample_user.id, None) is None


def test_resolve_user_accepts_bearer_token(db, sample_user):
    token = build_auth_token(sample_user.id)
    user = resolve_user_from_request(db, None, f"Bearer {token}")
    assert user is not None
    assert user.id == sample_user.id


def test_resolve_user_legacy_user_id_when_flag_enabled(monkeypatch, db, sample_user):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_ALLOW_LEGACY_USER_ID_ONLY", True)
    user = resolve_user_from_request(db, sample_user.id, None)
    assert user is not None
    assert user.id == sample_user.id


def test_resolve_user_legacy_ignored_when_bearer_present_but_invalid(monkeypatch, db, sample_user):
    """已发送 Bearer 但 token 无效时不得回退到 legacy user_id。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_ALLOW_LEGACY_USER_ID_ONLY", True)
    user = resolve_user_from_request(
        db,
        sample_user.id,
        "Bearer 0.0.notavalidsignatureatallxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    )
    assert user is None
