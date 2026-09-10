"""T01：配置加载与数据目录骨架。"""

from __future__ import annotations

from pathlib import Path

from app.auth import verify_password
from app.config import AppSettings, get_settings


def test_ensure_config_files_creates_defaults(tmp_path: Path, monkeypatch) -> None:
    """app.yaml / engines.yaml 缺失时自动生成（幂等）。"""
    monkeypatch.setenv("EWB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("EWB_PASSWORD", raising=False)
    get_settings.cache_clear()

    settings = AppSettings()
    settings.ensure_config_files()

    assert settings.app_yaml_path.exists()
    assert settings.engines_path.exists()  # 从 config/engines.yaml.example 复制而来
    assert verify_password("admin123", settings.app_config()["auth"]["password_hash"])
    assert settings.low_confidence_threshold() == 0.85

    settings.ensure_config_files()  # 幂等：不覆盖已存在文件
    assert verify_password("admin123", settings.app_config()["auth"]["password_hash"])
    get_settings.cache_clear()


def test_ensure_config_files_respects_password_argument(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "d2")
    settings.ensure_config_files(password="my-own-pass")
    assert verify_password("my-own-pass", settings.app_config()["auth"]["password_hash"])


def test_read_yaml_handles_missing_and_invalid(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "d3")
    settings.ensure_directories()

    assert AppSettings.read_yaml(tmp_path / "missing.yaml") == {}

    invalid = settings.config_dir / "invalid.yaml"
    invalid.write_text("a: [1,\n", encoding="utf-8")
    assert AppSettings.read_yaml(invalid) == {}

    scalar = settings.config_dir / "scalar.yaml"
    scalar.write_text("just-a-string", encoding="utf-8")
    assert AppSettings.read_yaml(scalar) == {}


def test_low_confidence_threshold_fallback(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "d4")
    settings.ensure_directories()

    assert settings.low_confidence_threshold() == 0.85

    settings.engines_path.write_text("low_confidence_threshold: not-a-number\n", encoding="utf-8")
    assert settings.low_confidence_threshold() == 0.85

    settings.engines_path.write_text("low_confidence_threshold: 0.5\n", encoding="utf-8")
    assert settings.low_confidence_threshold() == 0.5


def test_get_settings_is_cached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EWB_DATA_DIR", str(tmp_path / "cached"))
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
