"""T01 验收：口令哈希与 Token 签发/校验。"""

from __future__ import annotations

import pytest
from app.auth import (
    AuthManager,
    generate_secret,
    hash_password,
    verify_password,
)
from app.config import AppSettings


def test_hash_password_roundtrip() -> None:
    stored = hash_password("s3cret-口令")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-口令", stored) is True


def test_hash_password_is_salted() -> None:
    first = hash_password("same")
    second = hash_password("same")
    assert first != second
    assert verify_password("same", first) and verify_password("same", second)


def test_verify_password_rejects_wrong_and_malformed() -> None:
    stored = hash_password("right")
    assert verify_password("wrong", stored) is False
    assert verify_password("right", "not-a-valid-hash") is False
    assert verify_password("", stored) is False
    assert verify_password("right", "") is False


def test_hash_password_empty_raises() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_generate_secret_is_unique() -> None:
    assert generate_secret() != generate_secret()
    assert len(generate_secret()) > 20


def test_issue_and_verify_token() -> None:
    manager = AuthManager(hash_password("pw"), generate_secret(), ttl_seconds=100)
    token = manager.issue_token("teacher", now=1000.0)
    claims = manager.verify_token(token, now=1050.0)
    assert claims is not None
    assert claims.subject == "teacher"
    assert claims.expires_at == 1100


def test_verify_token_rejects_expired() -> None:
    manager = AuthManager(hash_password("pw"), generate_secret(), ttl_seconds=100)
    token = manager.issue_token(now=1000.0)
    assert manager.verify_token(token, now=1200.0) is None


def test_verify_token_rejects_tampered_and_foreign_secret() -> None:
    manager = AuthManager(hash_password("pw"), generate_secret(), ttl_seconds=100)
    token = manager.issue_token(now=1000.0)
    payload, signature = token.split(".")
    assert manager.verify_token(f"{payload}.{signature[:-2]}xx", now=1001.0) is None
    assert manager.verify_token("garbage", now=1001.0) is None
    assert manager.verify_token("", now=1001.0) is None

    other = AuthManager(hash_password("pw"), generate_secret(), ttl_seconds=100)
    assert other.verify_token(token, now=1001.0) is None


def test_verify_login() -> None:
    manager = AuthManager(hash_password("correct"), generate_secret())
    assert manager.verify_login("correct") is True
    assert manager.verify_login("nope") is False


def test_auth_manager_from_settings(settings: AppSettings) -> None:
    """从 app.yaml 构造（conftest 写入的测试口令）。"""
    manager = AuthManager.from_settings(settings)
    assert manager.verify_login("test-pass-123") is True
    assert manager.ttl_seconds == 3600


def test_auth_manager_from_settings_defaults(tmp_path, monkeypatch) -> None:
    """app.yaml 缺失时回退默认口令与随机密钥。"""
    monkeypatch.setenv("EWB_DATA_DIR", str(tmp_path / "empty-data"))
    manager = AuthManager.from_settings(AppSettings())
    assert manager.verify_login("admin123") is True
