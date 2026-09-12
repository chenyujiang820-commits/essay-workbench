"""二期榜单：星级换算与三榜计算（FR-04）。

本模块是**纯函数层**：不导入 config / db / render，输入是普通数据结构，输出是可直接
序列化的 dict。理由有二：

* 榜单口径是产品里最容易和教学制度绑死的部分，必须能被单测穷举（含并列、缺上期、
  未评分这些边界），不依赖数据库才谈得上快；
* 星级只在这里算一次。前端与 PDF 模板都消费后端下发的 ``stars``，避免同一判据在前后端
  各写一份、迟早漂移（一期画质判定 ``low_resolution`` 已经确立过这条纪律）。

三榜定义（v1.1 拍板、v1.3 §3 落地）：
* **佳作榜 work**：绝对水平，分数降序，默认仅老师可见；
* **进步榜 progress**：与"该生最近一次有评分的更早一期"比较，增量降序，可公开；
* **星级榜 star**：全员呈现星级，**不含名次也不含分数**，且按学号排序 —— 榜内顺序本身
  不能泄露排名，否则"弱化名次"形同虚设。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.models import Essay

# ---------------------------------------------------------------------------
# 常量：口径的唯一来源
# ---------------------------------------------------------------------------

#: 星级 = MIN_STARS + 达到的阈值个数，故阈值表长度必须是 ``MAX_STARS - MIN_STARS``。
MIN_STARS = 1
MAX_STARS = 5

#: 默认阈值（分）：59->1 星、60->2 星、70->3 星、80->4 星、90->5 星。
#: 这是**待老师核对的工程默认值**（PRD v1.3 Q25），不是教学结论；非递增/越界一律回落。
DEFAULT_STAR_THRESHOLDS: tuple[int, ...] = (60, 70, 80, 90)

SCORE_MIN = 0
SCORE_MAX = 100

#: 评语长度上限（整册 PDF 一篇一页，超长按 400 拒，不静默截断老师写的字）。
MAX_COMMENT_LENGTH = 2000

#: 每期精选上限（v1.1 原文"默认 5 篇，可在 1~10 调整"）。
MAX_SELECTED_PER_ISSUE = 10
#: 看板预勾选的篇数（只是省点击，不是硬约束）。
DEFAULT_SELECTED_SUGGEST = 5

#: 佳作榜前 N 名记为"上榜"（档案统计用，不落列，口径变了档案自动跟着变）。
HONOUR_RANKS = 3

#: 三榜的键，固定顺序（前端按此渲染卡片，不给它再发明一遍）。
BOARD_KEYS: tuple[str, ...] = ("work", "progress", "star")

#: 可见范围只有两种取值：写错的值回默认，不让服务起不来。
VISIBILITIES: tuple[str, ...] = ("teacher", "public")
DEFAULT_VISIBILITY: dict[str, str] = {
    "work": "teacher",
    "progress": "public",
    "star": "public",
}


@dataclass(frozen=True)
class ScoredEssay:
    """榜单输入：一篇**已评分**的稿件（未评分者不进任何榜）。

    字段刻意只放榜单要用的，不把 ORM 对象一路带进纯函数层。
    """

    essay_id: int
    issue_id: int
    issue_no: int
    student_id: int
    student_no: str
    name: str
    title: str
    score: float
    selected: int = 0
    comment: str = ""


# ---------------------------------------------------------------------------
# 星级换算
# ---------------------------------------------------------------------------
def resolve_star_thresholds(raw: object) -> tuple[int, ...]:
    """把配置里的阈值表归一化；非法形状整体回落默认。

    合法条件：长度恰为 ``MAX_STARS - MIN_STARS``、全部可转 int、严格递增、且落在
    ``[SCORE_MIN, SCORE_MAX]`` 内。任何一条不满足都整体回默认 —— 半套阈值算出来的
    星级比"用了默认值"更难解释。
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != MAX_STARS - MIN_STARS:
        return DEFAULT_STAR_THRESHOLDS
    values: list[int] = []
    for item in raw:
        if isinstance(item, bool):  # bool 是 int 子类，True 不该被当成 1 分
            return DEFAULT_STAR_THRESHOLDS
        try:
            value = int(str(item))
        except (TypeError, ValueError):
            return DEFAULT_STAR_THRESHOLDS
        if not SCORE_MIN <= value <= SCORE_MAX:
            return DEFAULT_STAR_THRESHOLDS
        values.append(value)
    if any(values[i] >= values[i + 1] for i in range(len(values) - 1)):
        return DEFAULT_STAR_THRESHOLDS
    return tuple(values)


def stars_from_score(
    score: float | None, thresholds: Sequence[int] = DEFAULT_STAR_THRESHOLDS
) -> int:
    """分数 -> 星级；``None`` 表示未评分，返回 0（模板据此不渲染星级）。"""
    if score is None:
        return 0
    stars = MIN_STARS
    for threshold in thresholds:
        if score >= threshold:
            stars += 1
    return min(stars, MAX_STARS)


# ---------------------------------------------------------------------------
# 配置解析（app.yaml 的 ranking 段）
# ---------------------------------------------------------------------------
def ranking_config(app_config: dict[str, Any]) -> dict[str, Any]:
    """从 ``app.yaml`` 全量配置里解析榜单设置，缺失段按默认补齐。

    Returns:
        ``{"thresholds": (...), "boards": {"work": {"enabled": bool, "visibility":
        "teacher"|"public"}, ...}}``。
    """
    raw = app_config.get("ranking") if isinstance(app_config, dict) else None
    section = raw if isinstance(raw, dict) else {}
    boards: dict[str, dict[str, Any]] = {}
    for key in BOARD_KEYS:
        board = section.get(key)
        board = board if isinstance(board, dict) else {}
        enabled = board.get("enabled")
        visibility = board.get("visibility")
        boards[key] = {
            # 只认显式关闭（False / "false"）；其余一律视为开启 —— 老师改了配置后
            # 最不该发生的事是"榜悄悄消失了但没人能解释"。
            "enabled": enabled is not False and enabled != "false",
            "visibility": (
                visibility
                if isinstance(visibility, str) and visibility in VISIBILITIES
                else DEFAULT_VISIBILITY[key]
            ),
        }
    return {
        "thresholds": resolve_star_thresholds(section.get("stars_from_score")),
        "boards": boards,
    }


# ---------------------------------------------------------------------------
# 三榜计算
# ---------------------------------------------------------------------------
def work_board(entries: Sequence[ScoredEssay], thresholds: Sequence[int]) -> list[dict[str, Any]]:
    """佳作榜：分数降序；同分按学号升序（并列不能随机排，否则每次刷新都在重排名次）。"""
    ordered = sorted(entries, key=lambda item: (-item.score, item.student_no, item.essay_id))
    return [
        {
            "rank": index,
            "essay_id": entry.essay_id,
            "student_id": entry.student_id,
            "student_no": entry.student_no,
            "name": entry.name,
            "title": entry.title,
            "score": entry.score,
            "stars": stars_from_score(entry.score, thresholds),
            "selected": bool(entry.selected),
        }
        for index, entry in enumerate(ordered, start=1)
    ]


def progress_board(
    current: Sequence[ScoredEssay],
    history: Sequence[ScoredEssay],
    thresholds: Sequence[int],
) -> list[dict[str, Any]]:
    """进步榜：与"该生最近一次有评分的更早一期"比增量。

    没有可比较上期者**不入榜**（不补 0、不显示假进步，PRD Q27）；增量并列按学号升序。
    """
    if not current:
        return []
    issue_no = min(item.issue_no for item in current)
    previous = _previous_scores(history, issue_no)
    rows: list[dict[str, Any]] = []
    for entry in current:
        prev = previous.get(entry.student_id)
        if prev is None:
            continue
        rows.append(
            {
                "essay_id": entry.essay_id,
                "student_id": entry.student_id,
                "student_no": entry.student_no,
                "name": entry.name,
                "title": entry.title,
                "score": entry.score,
                "stars": stars_from_score(entry.score, thresholds),
                "previous_score": prev.score,
                "previous_issue_no": prev.issue_no,
                "delta": round(entry.score - prev.score, 2),
            }
        )
    rows.sort(key=lambda row: (-row["delta"], row["student_no"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def _previous_scores(
    history: Sequence[ScoredEssay], before_issue_no: int
) -> dict[int, ScoredEssay]:
    """每位学生"期号小于 ``before_issue_no`` 的最近一次有评分稿件"。"""
    latest: dict[int, ScoredEssay] = {}
    for entry in history:
        if entry.issue_no >= before_issue_no:
            continue
        known = latest.get(entry.student_id)
        if known is None or entry.issue_no > known.issue_no:
            latest[entry.student_id] = entry
    return latest


def star_board(entries: Sequence[ScoredEssay], thresholds: Sequence[int]) -> list[dict[str, Any]]:
    """星级榜：全员呈现星级；**不含 rank 也不含 score**，按学号排序。"""
    ordered = sorted(entries, key=lambda item: (item.student_no, item.essay_id))
    return [
        {
            "student_id": entry.student_id,
            "student_no": entry.student_no,
            "name": entry.name,
            "title": entry.title,
            "stars": stars_from_score(entry.score, thresholds),
        }
        for entry in ordered
    ]


def honour_pairs(entries: Sequence[ScoredEssay]) -> set[tuple[int, int]]:
    """所有"上榜"记录：返回 ``(student_id, issue_id)`` 集合（每期佳作榜前 N 名）。"""
    by_issue: dict[int, list[ScoredEssay]] = {}
    for entry in entries:
        by_issue.setdefault(entry.issue_id, []).append(entry)
    result: set[tuple[int, int]] = set()
    for issue_id, group in by_issue.items():
        ranked = sorted(group, key=lambda item: (-item.score, item.student_no, item.essay_id))
        for entry in ranked[:HONOUR_RANKS]:
            result.add((entry.student_id, issue_id))
    return result


def to_scored_essays(essays: Sequence[Essay]) -> list[ScoredEssay]:
    """把 ORM 作文序列映射成榜单输入，**丢掉未评分者**。"""
    rows: list[ScoredEssay] = []
    for essay in essays:
        if essay.score is None:
            continue
        student = essay.student
        rows.append(
            ScoredEssay(
                essay_id=int(essay.id),
                issue_id=int(essay.issue_id),
                issue_no=int(essay.issue.issue_no) if essay.issue is not None else 0,
                student_id=int(essay.student_id),
                student_no=student.student_no if student is not None else "",
                name=student.name if student is not None else "",
                title=(essay.title or "").strip(),
                score=float(essay.score),
                selected=int(essay.selected or 0),
                comment=(essay.teacher_comment or "").strip(),
            )
        )
    return rows