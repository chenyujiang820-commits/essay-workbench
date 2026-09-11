"""置信度计算口径。

* 单张：以引擎 ``self_confidence`` 为基准，叠加启发式扣减
  （不可打印/生僻字符占比 > 2% 扣分；单图有效字数 < 20 扣分；出现「未确认」标记扣分；
  **原片画质低于门槛**扣分）。
* 整篇：取各张文本质量得分的 **最小值**（任一张不可靠即整篇存疑），再叠加**低画质按张
  累计扣分**，累计封顶 ``MAX_LOW_RESOLUTION_PENALTY``（一篇多张低画质不会无限扣分）。
* 判定：``overall < low_threshold``（默认 0.85，来自 engines.yaml）即为低置信。

低画质扣分是 PRD v1.2 FR-10 的"画质门槛参与置信度"落点：让糊图自然更容易掉进
低置信区间，从而触发复核引擎，而不是静默出一篇低质量稿子。
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

#: 单张低画质默认扣分（可被 engines.yaml 的 ``low_resolution_penalty`` 覆盖）。
PENALTY_LOW_RESOLUTION = 0.05

#: 整篇低画质累计扣分上限：避免"整组糊图"把置信度打到毫无信息量的低位。
MAX_LOW_RESOLUTION_PENALTY = 0.15


@dataclass(frozen=True)
class PhotoSample:
    """单张照片的识别文本、引擎自评置信度与画质标记。

    Attributes:
        text: 主引擎识别文本。
        self_confidence: 引擎自评置信度（0~1）。
        low_resolution: 原片是否低于画质门槛（来自 ``app.images.is_low_resolution``）。
    """

    text: str
    self_confidence: float = 1.0
    low_resolution: bool = False


class ConfidenceScorer:
    """置信度打分器（纯计算，无 IO）。"""

    def __init__(
        self,
        low_threshold: float = DEFAULT_LOW_THRESHOLD,
        *,
        low_resolution_penalty: float = PENALTY_LOW_RESOLUTION,
        max_low_resolution_penalty: float = MAX_LOW_RESOLUTION_PENALTY,
    ) -> None:
        self.low_threshold = float(low_threshold)
        self.low_resolution_penalty = float(low_resolution_penalty)
        self.max_low_resolution_penalty = float(max_low_resolution_penalty)

    # -- 单张 ---------------------------------------------------------------
    def score_photo(self, sample: PhotoSample) -> float:
        """计算单张照片的置信度（0.0~1.0，含该张自身的低画质扣分）。"""
        score = self._score_text(sample)
        if sample.low_resolution:
            score -= self.low_resolution_penalty
        return _clamp(score)

    # -- 整篇 ---------------------------------------------------------------
    def score_essay(self, photos: Sequence[Any]) -> float:
        """整篇置信度。

        口径：各张**文本质量**得分的 min，再减去按张累计的低画质扣分
        （累计封顶 ``max_low_resolution_penalty``）。单张口径仍由 :meth:`score_photo`
        表达，整篇累计只在本题实现，保持两个函数各自职责单一、可独立单测。
        """
        samples = [self._to_sample(photo) for photo in photos]
        if not samples:
            return 0.0
        base = min(self._score_text(sample) for sample in samples)
        low_count = sum(1 for sample in samples if sample.low_resolution)
        penalty = min(self.max_low_resolution_penalty, self.low_resolution_penalty * low_count)
        return _clamp(base - penalty)

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

    def _score_text(self, sample: PhotoSample) -> float:
        """只看识别文本质量的得分（不含画质维度）。"""
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

        return score

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
        return PhotoSample(
            text=str(text),
            self_confidence=confidence_value,
            low_resolution=ConfidenceScorer._low_resolution_of(photo),
        )

    @staticmethod
    def _low_resolution_of(photo: Any) -> bool:
        """画质判定：有显式标记就用，没有就**由宽高现算**。

        ``Photo`` 表并不存 ``low_resolution`` 列（它是宽度的派生值）。若这里只写
        ``getattr(photo, "low_resolution", False)``，那么任何直接把 ORM 对象传进来的
        调用点都会**静默丢掉 FR-10 的画质扣分**——分数看起来正常，糊图却不再触发复核。
        回落到与前端/接口同一判据，保证「同源判定」不因调用点而变。
        """
        marked = getattr(photo, "low_resolution", None)
        if marked is not None:
            return bool(marked)
        # 延迟导入：app.images 依赖 app.schemas，模块级互导易成环，与 schemas 同一处理。
        from app.images import is_low_resolution

        return bool(
            is_low_resolution(getattr(photo, "width", None), getattr(photo, "height", None))
        )


def _clamp(score: float) -> float:
    """把得分收敛到 0.0~1.0 并保留 4 位小数。"""
    return round(max(0.0, min(1.0, score)), 4)
