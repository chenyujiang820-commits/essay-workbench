"""二期榜单纯函数与配置解析单测（FR-04 / AC-12 / AC-13）。

这里全部是**无数据库**的纯函数测试：榜单口径是教育判断，必须能被穷举到边界
（并列、缺上期、未评分、配置写错），而不是靠真库跑出个大概。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.config import AppSettings
from app.ranking import (
    DEFAULT_STAR_THRESHOLDS,
    MAX_SELECTED_PER_ISSUE,
    ScoredEssay,
    honour_pairs,
    progress_board,
    ranking_config,
    resolve_star_thresholds,
    star_board,
    stars_from_score,
    work_board,
)
from app.share import (
    SHARE_GONE_MESSAGE,
    expiry_iso,
    is_active,
    is_expired,
    new_share_token,
    share_path,
)


def entry(
    *,
    essay_id: int = 1,
    issue_id: int = 1,
    issue_no: int = 1,
    student_id: int = 1,
    student_no: str = "S001",
    name: str = "张三",
    title: str = "春天",
    score: float = 85.0,
    selected: int = 0,
) -> ScoredEssay:
    return ScoredEssay(
        essay_id=essay_id,
        issue_id=issue_id,
        issue_no=issue_no,
        student_id=student_id,
        student_no=student_no,
        name=name,
        title=title,
        score=score,
        selected=selected,
    )


# ---------------------------------------------------------------------------
# 星级换算（AC-12）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("score", "stars"),
    [(0, 1), (59, 1), (60, 2), (69, 2), (70, 3), (79, 3), (80, 4), (89, 4), (90, 5), (100, 5)],
)
def test_stars_from_default_thresholds(score: int, stars: int) -> None:
    assert stars_from_score(float(score)) == stars


def test_stars_none_means_unscored() -> None:
    """未评分返回 0 而不是 1 星：模板据此**不渲染**星级，免得给家长看成一个最低的星。"""
    assert stars_from_score(None) == 0


def test_resolve_thresholds_rejects_bad_shapes() -> None:
    bad_cases = [
        None,
        "60,70,80,90",
        [],
        [60, 70, 80],  # 少一个
        [60, 70, 80, 90, 95],  # 多一个
        [60, 90, 70, 80],  # 非递增
        [60, 60, 70, 80],  # 相等
        [-5, 70, 80, 90],  # 越界
        [60, 70, 80, 999],  # 越界
        [60, True, 80, 90],  # bool 不当数字用
        ["a", 70, 80, 90],  # 非数字
    ]
    for case in bad_cases:
        assert resolve_star_thresholds(case) == DEFAULT_STAR_THRESHOLDS, case

    assert resolve_star_thresholds(["60", "70", "80", "90"]) == (60, 70, 80, 90)
    assert resolve_star_thresholds([50, 60, 70, 80]) == (50, 60, 70, 80)


def test_ranking_config_defaults_and_overrides() -> None:
    empty = ranking_config({})
    assert empty["thresholds"] == DEFAULT_STAR_THRESHOLDS
    assert empty["boards"]["work"] == {"enabled": True, "visibility": "teacher"}
    assert empty["boards"]["progress"]["visibility"] == "public"
    assert empty["boards"]["star"]["visibility"] == "public"

    custom = ranking_config(
        {
            "ranking": {
                "stars_from_score": [50, 60, 70, 80],
                "work": {"enabled": False},
                "star": {"visibility": "teacher"},
                "progress": {"visibility": "乱写"},
            }
        }
    )
    assert custom["thresholds"] == (50, 60, 70, 80)
    assert custom["boards"]["work"]["enabled"] is False
    assert custom["boards"]["star"]["visibility"] == "teacher"
    # 非法可见性回默认（public），而不是把整个榜关掉：老师要能看出"配置没生效"而不是"榜没了"
    assert custom["boards"]["progress"] == {"enabled": True, "visibility": "public"}


def test_settings_ranking_config_reads_app_yaml(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置项走 app.yaml 且**每次重读**（老师改完刷新即生效，FR-13 同一套热读纪律）。"""
    import yaml

    root = tmp_path  # type: ignore[assignment]
    settings = AppSettings(data_dir=root)  # type: ignore[arg-type]
    settings.ensure_directories()
    assert settings.ranking_config()["thresholds"] == DEFAULT_STAR_THRESHOLDS

    settings.app_yaml_path.write_text(
        yaml.safe_dump({"ranking": {"stars_from_score": [50, 60, 70, 80]}}, allow_unicode=True),
        encoding="utf-8",
    )
    assert settings.ranking_config()["thresholds"] == (50, 60, 70, 80)


# ---------------------------------------------------------------------------
# 三榜（AC-13）
# ---------------------------------------------------------------------------
def test_work_board_orders_by_score_then_student_no() -> None:
    rows = work_board(
        [
            entry(essay_id=1, student_no="S003", score=88.0),
            entry(essay_id=2, student_no="S001", score=95.0),
            entry(essay_id=3, student_no="S002", score=88.0),
        ],
        DEFAULT_STAR_THRESHOLDS,
    )
    assert [row["essay_id"] for row in rows] == [2, 3, 1]  # 同分按学号，不随机
    assert [row["rank"] for row in rows] == [1, 2, 3]
    assert rows[0]["stars"] == 5


def test_star_board_has_no_rank_and_no_score() -> None:
    """星级榜**不返回** rank/score 字段：弱化名次不能只靠前端不显示。"""
    rows = star_board(
        [entry(student_no="S002", score=99.0), entry(student_no="S001", score=61.0)],
        DEFAULT_STAR_THRESHOLDS,
    )
    assert [row["student_no"] for row in rows] == ["S001", "S002"]  # 按学号，不按分数
    assert set(rows[0]) == {"student_id", "student_no", "name", "title", "stars"}
    assert rows[1]["stars"] == 5


def test_progress_board_requires_previous_issue() -> None:
    current = [
        entry(essay_id=10, issue_id=2, issue_no=2, student_id=1, student_no="S001", score=90.0),
        entry(essay_id=11, issue_id=2, issue_no=2, student_id=2, student_no="S002", score=70.0),
        entry(essay_id=12, issue_id=2, issue_no=2, student_id=3, student_no="S003", score=80.0),
    ]
    history = [
        # S001：第 1 期 60 分、第 2 期之前还有第 3? —— 用两期验证"取最近一期"而不是"取最大增量"
        entry(essay_id=1, issue_id=1, issue_no=1, student_id=1, student_no="S001", score=50.0),
        entry(essay_id=2, issue_id=1, issue_no=1, student_id=2, student_no="S002", score=85.0),
    ]
    rows = progress_board(current, history, DEFAULT_STAR_THRESHOLDS)
    assert [row["student_no"] for row in rows] == ["S001", "S002"]
    assert [row["delta"] for row in rows] == [40.0, -15.0]
    assert rows[1]["previous_issue_no"] == 1
    assert [row["rank"] for row in rows] == [1, 2]
    # S003 没有上期 -> 不入榜（不补 0、不显示假进步）
    assert all(row["student_no"] != "S003" for row in rows)


def test_progress_board_picks_latest_previous_issue() -> None:
    current = [entry(essay_id=3, issue_id=3, issue_no=3, score=80.0)]
    history = [
        entry(essay_id=1, issue_id=1, issue_no=1, score=50.0),
        entry(essay_id=2, issue_id=2, issue_no=2, score=78.0),
    ]
    rows = progress_board(current, history, DEFAULT_STAR_THRESHOLDS)
    assert rows[0]["previous_issue_no"] == 2
    assert rows[0]["delta"] == 2.0


def test_progress_board_empty_current_returns_empty() -> None:
    assert progress_board([], [entry()], DEFAULT_STAR_THRESHOLDS) == []


def test_honour_pairs_counts_top_three_per_issue() -> None:
    entries = [
        entry(essay_id=i, issue_id=1, student_id=i, student_no=f"S00{i}", score=float(100 - i))
        for i in range(1, 6)
    ]
    pairs = honour_pairs(entries)
    assert pairs == {(1, 1), (2, 1), (3, 1)}


def test_max_selected_constant_matches_prd() -> None:
    assert MAX_SELECTED_PER_ISSUE == 10


# ---------------------------------------------------------------------------
# 分享令牌（FR-07 / AC-16）
# ---------------------------------------------------------------------------
def test_share_token_shape_and_uniqueness() -> None:
    tokens = {new_share_token() for _ in range(50)}
    assert len(tokens) == 50
    token = tokens.pop()
    assert len(token) >= 40 and "/" not in token and "+" not in token
    assert share_path(token) == f"/share/{token}"


def test_expiry_iso_days_and_bounds() -> None:
    expires = datetime.fromisoformat(expiry_iso(14))
    days = (expires - datetime.now(UTC)).days
    assert 13 <= days <= 14
    with pytest.raises(ValueError):
        expiry_iso(0)
    with pytest.raises(ValueError):
        expiry_iso(999)


def test_is_expired_compares_real_moments() -> None:
    now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    assert is_expired("2026-09-11T12:00:00+00:00", now=now) is True
    assert is_expired("2026-09-13T12:00:00+00:00", now=now) is False
    # naive 值按 UTC 解释，不因 naive/aware 比较抛异常
    assert is_expired("2026-09-13T12:00:00", now=now) is False
    # 解析不了的一律按已过期：宁可拒绝对外可读
    assert is_expired("昨天", now=now) is True


def test_is_active_covers_revoked_and_expiry() -> None:
    future = "2099-01-01T00:00:00+00:00"
    past = "2000-01-01T00:00:00+00:00"
    assert is_active(0, future) is True
    assert is_active(1, future) is False
    assert is_active(0, past) is False


def test_gone_message_does_not_leak_reason() -> None:
    """三种失效原因共用一条文案，避免给令牌做枚举预言机。"""
    assert "已失效" in SHARE_GONE_MESSAGE
    assert "过期" not in SHARE_GONE_MESSAGE and "撤销" not in SHARE_GONE_MESSAGE