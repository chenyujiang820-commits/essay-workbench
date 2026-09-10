"""字符级 diff：基于标准库 ``difflib.SequenceMatcher``。

中文无空格分词问题，字符级 diff 最稳、零依赖、确定性输出；直接产出
``equal / replace / delete / insert`` 四类段落，前端仅对后三类打「存疑」黄底。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

DiffType = Literal["equal", "replace", "delete", "insert"]
_VALID_TYPES = frozenset({"equal", "replace", "delete", "insert"})


@dataclass(frozen=True)
class DiffSegment:
    """一个 diff 段落。

    Attributes:
        type: 段落类型。
        text_a: 主引擎文本片段（原文一侧）。
        text_b: 复核引擎文本片段；``equal`` 时与 ``text_a`` 相同，``delete`` 时为空。
    """

    type: DiffType
    text_a: str = ""
    text_b: str = ""

    def to_dict(self) -> dict[str, str]:
        """转为可 JSON 序列化的字典。"""
        return {"type": self.type, "text_a": self.text_a, "text_b": self.text_b}


class DiffService:
    """字符级 diff 与分歧率计算的纯函数集合。"""

    @staticmethod
    def char_diff(text_a: str, text_b: str) -> list[DiffSegment]:
        """逐字符比较两段文本。

        Args:
            text_a: 主引擎（engine1）文本。
            text_b: 复核引擎（engine2）文本。

        Returns:
            按原文顺序排列的 ``DiffSegment`` 列表。
        """
        left = text_a or ""
        right = text_b or ""
        matcher = SequenceMatcher(None, left, right, autojunk=False)
        segments: list[DiffSegment] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            segments.append(
                DiffSegment(type=tag, text_a=left[i1:i2], text_b=right[j1:j2])
            )
        return segments

    @staticmethod
    def disagreement_rate(segments: list[DiffSegment]) -> float:
        """分歧率 = 非 equal 段落的字符数 / 两侧字符总数（0.0~1.0）。"""
        total = 0
        changed = 0
        for segment in segments:
            size = len(segment.text_a) + len(segment.text_b)
            total += size
            if segment.type != "equal":
                changed += size
        if total == 0:
            return 0.0
        return round(changed / total, 4)

    @staticmethod
    def to_json(segments: list[DiffSegment]) -> str:
        """序列化为 JSON 字符串（落库用，作为不可变审计数据）。"""
        return json.dumps([segment.to_dict() for segment in segments], ensure_ascii=False)

    @staticmethod
    def from_json(raw: str | None) -> list[dict[str, str]] | None:
        """反序列化；非法内容返回 ``None``。"""
        if not raw:
            return None
        try:
            loaded = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(loaded, list):
            return None
        segments: list[dict[str, str]] = []
        for item in loaded:
            if not isinstance(item, dict) or item.get("type") not in _VALID_TYPES:
                continue
            segments.append(
                {
                    "type": str(item["type"]),
                    "text_a": str(item.get("text_a", "")),
                    "text_b": str(item.get("text_b", "")),
                }
            )
        return segments
