"""置信度计算口径。

* 单张：以引擎 ``self_confidence`` 为基准，叠加启发式扣减
  （不可打印/生僻字符占比 > 2% 扣分；单图有效字数 < 20 扣分；出现「未确认」标记扣分）。
* 整篇：取各张得分的 **最小值**（任一张不可靠即整篇存疑）。
* 判定：``overall < low_threshold``（默认 0.85，来自 engines.yaml）即为低置信。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_LOW_THRESHOLD = 0.85
MIN_PHOTO_CHARS = 20
ABNORMAL_RATIO_LIMIT = 0.02
UNCONFIRMED_MARKER = "【?】"

# 扣分权重
PENALTY_ABNORMAL_RATIO = 0.25
PENALTY_TOO_SHORT = 0.20
PENALTY_UNCONFIRMED_MARKER = 0.10


@dataclass(frozen=True)
class PhotoSample:
    """单张照片的识别文本与引擎自评置信度。"""

    text: str
    self_confidence: float = 1.0


class ConfidenceScorer:
    """置信度打分器（纯计算，无 IO）。"""

    def __init__(self, low_threshold: float = DEFAULT_LOW_THRESHOLD) -> None:
        self.low_threshold = float(low_threshold)

    # -- 单张 ---------------------------------------------------------------
    def score_photo(self, sample: PhotoSample) -> float:
        """计算单张照片的置信度（0.0~1.0）。"""
        text = sample.text or ""
        stripped = text.strip()
        if not stripped:
            return 0.0

        score = max(0.0, min(1.0, float(sample.self_confidence)))

        total = len(text)
        abnormal = self._abnormal_count(text)
        if total and abnormal / total > ABNORMAL_RATIO_LIMIT:
            score -= PENALTY_ABNORMAL_RATIO

        if len(stripped) < MIN_PHOTO_CHARS:
            score -= PENALTY_TOO_SHORT

        if self.unconfirmed_marker_count(text) > 0:
            score -= PENALTY_UNCONFIRMED_MARKER

        return round(max(0.0, min(1.0, score)), 4)

    # -- 整篇 ---------------------------------------------------------------
    def score_essay(self, photos: Sequence[Any]) -> float:
        """整篇置信度 = 各张得分的 min；无照片时为 0.0。"""
        samples = [self._to_sample(photo) for photo in photos]
        if not samples:
            return 0.0
        return min(self.score_photo(sample) for sample in samples)

    def is_low(self, overall: float) -> bool:
        """是否判定为低置信。"""
        return float(overall) < self.low_threshold

    # -- 辅助 ---------------------------------------------------------------
    @staticmethod
    def unconfirmed_marker_count(text: str) -> int:
        """统计「【?】」未确认标记数量。"""
        if not text:
            return 0
        return text.count(UNCONFIRMED_MARKER)

    @classmethod
    def _abnormal_count(cls, text: str) -> int:
        """统计异常字符数（不可打印 / 替换符 / 私用区）。"""
        count = 0
        for char in text:
            if cls._is_abnormal(char):
                count += 1
        return count

    @staticmethod
    def _is_abnormal(char: str) -> bool:
        if char in "\n\r\t\f":
            return False
        if char == "\ufffd":  # U+FFFD 替换字符
            return True
        if not char.isprintable():
            return True
        codepoint = ord(char)
        # 私用区 & 补充私用区
        if 0xE000 <= codepoint <= 0xF8FF:
            return True
        return 0xF0000 <= codepoint <= 0x10FFFF

    @staticmethod
    def _to_sample(photo: Any) -> PhotoSample:
        """把任意照片对象/样本归一化为 ``PhotoSample``。"""
        if isinstance(photo, PhotoSample):
            return photo
        text = getattr(photo, "text", None)
        if text is None:
            text = getattr(photo, "engine1_text", "") or ""
        confidence = getattr(photo, "self_confidence", 1.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 1.0
        return PhotoSample(text=str(text), self_confidence=confidence_value)
