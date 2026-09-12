"""应用配置：环境变量 + 数据目录下的 YAML 配置。

约定（详见 docs/architecture.md 第 7 节）：
* 数据目录由环境变量 ``EWB_DATA_DIR`` 指定，默认 ``./data``。
* 代码与数据彻底分离：SQLite、照片、导出、配置全部位于数据目录内。
* 数据目录结构::

      <EWB_DATA_DIR>/
      ├── essay.db                 # SQLite（WAL）
      ├── photos/{issue}/{essay}/  # 原片永久留存
      ├── exports/                 # PDF 导出产物
      └── config/
          ├── app.yaml             # 口令哈希 / token 密钥
          └── engines.yaml         # 双引擎配置（含 API Key，gitignore）
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ranking import ranking_config as parse_ranking_config

DEFAULT_DATA_DIR = "./data"
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.85

#: 篇级并发 Worker 数（engines.yaml ``worker_concurrency``）。
DEFAULT_WORKER_CONCURRENCY = 4
MIN_WORKER_CONCURRENCY = 1
MAX_WORKER_CONCURRENCY = 8

#: 单张低画质置信度扣分（engines.yaml ``low_resolution_penalty``）及其合法区间。
DEFAULT_LOW_RESOLUTION_PENALTY = 0.05
MIN_LOW_RESOLUTION_PENALTY = 0.0
MAX_LOW_RESOLUTION_PENALTY = 0.2

#: 班级名兜底值。**与 ``app/render/templates.py`` 的同名常量刻意重复**：config 不能
#: 反向依赖 render（render 依赖 config），班级名单一来源由集成阶段收敛（见交付报告）。
DEFAULT_CLASS_NAME = "班级"


class AppSettings(BaseSettings):
    """运行时配置（仅来自环境变量）。

    Attributes:
        data_dir: 数据根目录，来自 ``EWB_DATA_DIR``，默认 ``./data``。
        worker_enabled: 是否启动进程内识别 Worker（测试可置为 false），
            来自 ``EWB_WORKER_ENABLED``。
    """

    model_config = SettingsConfigDict(env_prefix="EWB_", extra="ignore", env_file=None)

    data_dir: Path = Path(DEFAULT_DATA_DIR)
    worker_enabled: bool = True

    # -- 派生路径 ----------------------------------------------------------
    @property
    def config_dir(self) -> Path:
        """数据目录下的 config 子目录。"""
        return self.data_dir / "config"

    @property
    def photos_dir(self) -> Path:
        """原片照片根目录。"""
        return self.data_dir / "photos"

    @property
    def exports_dir(self) -> Path:
        """导出产物目录。"""
        return self.data_dir / "exports"

    @property
    def db_path(self) -> Path:
        """SQLite 数据库文件路径。"""
        return self.data_dir / "essay.db"

    @property
    def engines_path(self) -> Path:
        """engines.yaml 路径。"""
        return self.config_dir / "engines.yaml"

    @property
    def app_yaml_path(self) -> Path:
        """app.yaml 路径。"""
        return self.config_dir / "app.yaml"

    # -- 目录与配置 --------------------------------------------------------
    def ensure_directories(self) -> None:
        """确保数据目录骨架存在（幂等）。"""
        for path in (self.data_dir, self.config_dir, self.photos_dir, self.exports_dir):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def read_yaml(path: Path) -> dict[str, Any]:
        """读取 YAML 文件；文件缺失或内容非法时返回空字典。"""
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def app_config(self) -> dict[str, Any]:
        """读取 app.yaml（每次调用重新读取，便于热改口令）。"""
        return self.read_yaml(self.app_yaml_path)

    def engines_config(self) -> dict[str, Any]:
        """读取 engines.yaml。"""
        return self.read_yaml(self.engines_path)

    def low_confidence_threshold(self) -> float:
        """整篇置信度判定阈值，默认 0.85，可由 engines.yaml 覆盖。"""
        raw = self.engines_config().get("low_confidence_threshold")
        if raw is None:
            return DEFAULT_LOW_CONFIDENCE_THRESHOLD
        try:
            return float(raw)
        except (TypeError, ValueError):
            return DEFAULT_LOW_CONFIDENCE_THRESHOLD

    # -- v1.2 运行参数（非法/越界一律钳制，避免配置写错就起不来）-------------
    def worker_concurrency(self) -> int:
        """篇级并发 Worker 数，钳制到 1~8（默认 4）。"""
        return _clamp_int(
            self.engines_config().get("worker_concurrency"),
            DEFAULT_WORKER_CONCURRENCY,
            MIN_WORKER_CONCURRENCY,
            MAX_WORKER_CONCURRENCY,
        )

    def low_resolution_penalty(self) -> float:
        """单张低画质的置信度扣分，钳制到 0~0.2（默认 0.05）。"""
        return _clamp_float(
            self.engines_config().get("low_resolution_penalty"),
            DEFAULT_LOW_RESOLUTION_PENALTY,
            MIN_LOW_RESOLUTION_PENALTY,
            MAX_LOW_RESOLUTION_PENALTY,
        )

    def ranking_config(self) -> dict[str, Any]:
        """二期榜单配置（``app.yaml`` 的 ``ranking:`` 段），每次调用重读，改完刷新即生效。

        解析与非法值回落全在 ``app.ranking.ranking_config`` 里（纯函数，可直接单测）；
        这里只负责"从哪读"。配置写错**不让服务起不来**，与 worker 并发度同一套纪律。
        """
        return parse_ranking_config(self.app_config())

    @property
    def class_name(self) -> str:
        """班级名（``app.yaml`` 顶层 ``class_name``），供 ``GET /api/meta`` 展示。

        本属性是班级名的唯一实现：app.render.templates.class_name_from_settings
        已改为转发到这里（依赖方向只有 render -> config，不成环），改兜底文案只需改这一处。
        """
        raw = self.app_config().get("class_name")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return DEFAULT_CLASS_NAME

    def _repo_config_dir(self) -> Path:
        """代码库内的配置示例目录（backend/config）。"""
        return Path(__file__).resolve().parents[1] / "config"

    def ensure_config_files(self, *, password: str | None = None) -> None:
        """确保 app.yaml / engines.yaml 就位；缺失时按规则生成（幂等）。

        Args:
            password: 显式指定初始口令；优先级：参数 > ``EWB_PASSWORD`` > 默认。
        """
        # 延迟导入，避免 config <-> auth 的导入环。
        from app.auth import generate_secret, hash_password

        self.ensure_directories()

        if not self.app_yaml_path.exists():
            initial = password or os.environ.get("EWB_PASSWORD") or "admin123"
            payload = {
                "auth": {
                    "password_hash": hash_password(initial),
                    "token_secret": generate_secret(),
                    "token_ttl_seconds": 604800,
                }
            }
            with self.app_yaml_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)

        if not self.engines_path.exists():
            example = self._repo_config_dir() / "engines.yaml.example"
            if example.exists():
                self.engines_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")


def _clamp_int(raw: object, default: int, low: int, high: int) -> int:
    """把配置值归一化为 ``[low, high]`` 内的整数；非法值回退默认。"""
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _clamp_float(raw: object, default: float, low: float, high: float) -> float:
    """把配置值归一化为 ``[low, high]`` 内的浮点数；非法值回退默认。"""
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        value = default
    if value != value:  # NaN 比较恒 False，这里显式回退，避免污染扣分计算
        return default
    return max(low, min(high, value))


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """返回进程级单例配置（测试中可用 ``get_settings.cache_clear()`` 重置）。"""
    settings = AppSettings()
    settings.ensure_directories()
    return settings
