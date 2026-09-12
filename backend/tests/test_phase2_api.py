"""二期 API 测（真库真 HTTP）：评语/评分/精选写入、三榜、成长档案。

覆盖 PRD v1.3 的 AC-11 / AC-13 / AC-14 / AC-15，以及最要紧的一条回归：
**写评语/评分/精选绝不能改变状态机**（未定稿就是未定稿）。
"""

from __future__ import annotations

from app.models import Essay, Issue, Student, utcnow_iso
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


# ---------------------------------------------------------------------------
# 夹具工具
# ---------------------------------------------------------------------------
async def add_student(session_factory: async_sessionmaker[AsyncSession], no: str, name: str) -> int:
    async with session_factory() as session:
        student = Student(student_no=no, name=name, active=1, created_at=utcnow_iso())
        session.add(student)
        await session.commit()
        await session.refresh(student)
        return int(student.id)


async def add_issue(session_factory: async_sessionmaker[AsyncSession], no: int) -> int:
    async with session_factory() as session:
        issue = Issue(issue_no=no, week_start_date="2026-09-07", created_at=utcnow_iso())
        session.add(issue)
        await session.commit()
        await session.refresh(issue)
        return int(issue.id)


async def add_essay(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    issue_id: int,
    student_id: int,
    title: str = "春天",
    text: str = "第一段\n第二段",
    status: str = "proofread",
    score: float | None = None,
    comment: str | None = None,
    selected: int = 0,
) -> int:
    async with session_factory() as session:
        essay = Essay(
            issue_id=issue_id,
            student_id=student_id,
            title=title,
            final_text=text if status == "proofread" else "",
            status=status,
            low_confidence=0,
            score=score,
            teacher_comment=comment,
            selected=selected,
            created_at=utcnow_iso(),
            proofread_at=utcnow_iso() if status == "proofread" else None,
        )
        session.add(essay)
        await session.commit()
        await session.refresh(essay)
        return int(essay.id)


async def patch_essay(
    client: AsyncClient,
    auth_headers: dict[str, str],
    essay_id: int,
    payload: dict[str, object],
) -> object:
    body: dict[str, object] = {"final_text": "第一段\n第二段", **payload}
    return await client.patch(f"/api/essays/{essay_id}", json=body, headers=auth_headers)


async def selected_ids(
    session_factory: async_sessionmaker[AsyncSession], issue_id: int
) -> set[int]:
    async with session_factory() as session:
        rows = (
            await session.execute(select(Essay.id).where(Essay.issue_id == issue_id, Essay.selected == 1))
        ).scalars().all()
        return {int(item) for item in rows}


# ---------------------------------------------------------------------------
# AC-11 评语与评分写入
# ---------------------------------------------------------------------------
async def test_comment_does_not_change_status(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """**本文件最重要的一条**：只写评语绝不会把 review 推成 proofread。"""
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "张三")
    essay_id = await add_essay(
        session_factory, issue_id=issue_id, student_id=student_id, status="review", text=""
    )

    response = await patch_essay(client, auth_headers, essay_id, {"teacher_comment": "书写工整。"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "review"
    assert data["teacher_comment"] == "书写工整。"
    assert data["proofread_at"] is None


async def test_comment_three_states(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "张三")
    essay_id = await add_essay(
        session_factory, issue_id=issue_id, student_id=student_id, comment="原有评语"
    )

    # 不传 = 不改
    data = (await patch_essay(client, auth_headers, essay_id, {})).json()["data"]
    assert data["teacher_comment"] == "原有评语"

    # 传空串 = 清空
    data = (
        await patch_essay(client, auth_headers, essay_id, {"teacher_comment": "   "})
    ).json()["data"]
    assert data["teacher_comment"] in (None, "")

    # 传字符串 = 覆盖（正文里的换行保留）
    data = (
        await patch_essay(
            client, auth_headers, essay_id, {"teacher_comment": "内容充实\n结构清晰"}
        )
    ).json()["data"]
    assert data["teacher_comment"] == "内容充实\n结构清晰"


async def test_score_requires_proofread_and_reports_stars(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "张三")
    draft_id = await add_essay(
        session_factory, issue_id=issue_id, student_id=student_id, status="review", text=""
    )
    proofread_id = await add_essay(
        session_factory, issue_id=issue_id, student_id=student_id
    )

    denied = await patch_essay(client, auth_headers, draft_id, {"score": 90})
    assert denied.status_code == 400
    assert "未定稿" in denied.json()["message"]

    ok = await patch_essay(client, auth_headers, proofread_id, {"score": 92})
    assert ok.status_code == 200
    data = ok.json()["data"]
    assert data["score"] == 92.0
    assert data["stars"] == 5  # 默认阈值 [60,70,80,90]

    out_of_range = await patch_essay(client, auth_headers, proofread_id, {"score": 120})
    assert out_of_range.status_code == 422  # Pydantic 层直接拒，不做静默钳制


async def test_list_essays_carries_comment_score_stars(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """看板一次性拿到评语/评分/星级，不用逐篇点开（也不需要前端再算一遍星级）。"""
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "张三")
    await add_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_id,
        score=65.0,
        comment="有进步",
        selected=1,
    )
    response = await client.get(f"/api/issues/{issue_id}/essays", headers=auth_headers)
    item = response.json()["data"][0]
    assert item["teacher_comment"] == "有进步"
    assert item["score"] == 65.0
    assert item["stars"] == 2
    assert item["selected"] == 1


# ---------------------------------------------------------------------------
# AC-14 每周精选
# ---------------------------------------------------------------------------
async def _detail(
    client: AsyncClient, auth_headers: dict[str, str], essay_id: int
) -> dict[str, object]:
    """读一篇作文的当前落库状态（用于验证"响应好看"之外真的写进去了）。"""
    response = await client.get(f"/api/essays/{essay_id}", headers=auth_headers)
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])

# ---------------------------------------------------------------------------
# AC-11 评分的三态：不传=不改、传值=覆盖、**显式 null=清空**
# ---------------------------------------------------------------------------
async def test_explicit_null_clears_score_absent_keeps_it(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """把""不传""与""传 null""当成两件事：老师要能取消一个打错的分数。

    只用一个 `score: float | None` 字段是分不清这两种意图的 —— Pydantic 把缺字段
    和显式 null 都折叠成 None，所以判据必须落在 ``model_fields_set`` 上。
    """
    issue_id = await add_issue(session_factory, 7)
    student_id = await add_student(session_factory, "S007", "张三")
    essay_id = await add_essay(
        session_factory, issue_id=issue_id, student_id=student_id, score=61.0
    )
    assert (await _detail(client, auth_headers, essay_id))["stars"] == 2

    # 1) 只动评语：评分与星级都不许变
    untouched = await patch_essay(
        client, auth_headers, essay_id, {"teacher_comment": "开头略快。"}
    )
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["data"]["score"] == 61
    assert untouched.json()["data"]["stars"] == 2

    # 2) 显式 null：清空评分，星级一并归零（留""4 星但 0 分""是最难看的半套状态）
    cleared = await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": "第一段", "score": None},
        headers=auth_headers,
    )
    assert cleared.status_code == 200, cleared.text
    detail = cleared.json()["data"]
    assert detail["score"] is None
    assert detail["stars"] == 0

    # 3) 清空必须真的落库，而不是只在响应里好看
    assert (await _detail(client, auth_headers, essay_id))["score"] is None

    # 4) 未定稿时清空仍合法：取消一个误填的分数不该先要求定稿
    draft_id = await add_essay(
        session_factory, issue_id=issue_id, student_id=student_id, status="review"
    )
    cleared_draft = await client.patch(
        f"/api/essays/{draft_id}",
        json={"final_text": "第一段", "score": None},
        headers=auth_headers,
    )
    assert cleared_draft.status_code == 200, cleared_draft.text
    assert cleared_draft.json()["data"]["status"] == "review"


async def test_selection_sets_whole_issue(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "张三")
    ids = [
        await add_essay(session_factory, issue_id=issue_id, student_id=student_id, selected=1)
        for _ in range(4)
    ]

    response = await client.put(
        f"/api/issues/{issue_id}/selection",
        json={"essay_ids": ids[1:3]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert sorted(data["selected_ids"]) == sorted(ids[1:3])
    assert data["limit"] == 10
    assert await selected_ids(session_factory, issue_id) == {ids[1], ids[2]}


async def test_selection_readback_carries_scored_counts(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """保存精选一次就带回"本期已评/未评"两个数，且与三榜同一判据。

    看板存完要立刻刷新"还有 N 篇没评分"的提示条；若为此再打一次 ranking，两次请求之间
    老师一改别的作文，界面就会出现两个互相矛盾的 N —— 所以由后端在同一次回读里给全。
    """
    issue_id = await add_issue(session_factory, 3)
    first = await add_student(session_factory, "S001", "甲")
    second = await add_student(session_factory, "S002", "乙")
    third = await add_student(session_factory, "S003", "丙")
    scored = await add_essay(session_factory, issue_id=issue_id, student_id=first, score=88)
    unscored = await add_essay(
        session_factory, issue_id=issue_id, student_id=second, score=None
    )
    draft = await add_essay(
        session_factory, issue_id=issue_id, student_id=third, status="review"
    )

    response = await client.put(
        f"/api/issues/{issue_id}/selection",
        json={"essay_ids": [scored, unscored, draft][:2]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["selected_ids"] == [scored, unscored] or data["selected_ids"] == [unscored, scored]
    assert data["scored_count"] == 1
    # 未定稿的那篇连"未评分"都不算：它还没进评价口径
    assert data["unscored_count"] == 1

    board = (
        await client.get(f"/api/issues/{issue_id}/ranking", headers=auth_headers)
    ).json()["data"]
    assert board["scored_count"] == data["scored_count"]
    assert board["unscored_count"] == data["unscored_count"]

async def test_selection_over_limit_rolls_back_everything(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """第 11 篇 -> 400 且**数据库零变更**（整单校验先于写入，PRD Q28）。"""
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "张三")
    ids = [await add_essay(session_factory, issue_id=issue_id, student_id=student_id) for _ in range(11)]
    await client.put(
        f"/api/issues/{issue_id}/selection", json={"essay_ids": ids[:2]}, headers=auth_headers
    )

    response = await client.put(
        f"/api/issues/{issue_id}/selection", json={"essay_ids": ids}, headers=auth_headers
    )
    assert response.status_code == 400
    assert "最多 10 篇" in response.json()["message"]
    assert await selected_ids(session_factory, issue_id) == set(ids[:2])


async def test_selection_rejects_foreign_and_draft(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_a = await add_issue(session_factory, 1)
    issue_b = await add_issue(session_factory, 2)
    student_id = await add_student(session_factory, "S001", "张三")
    mine = await add_essay(session_factory, issue_id=issue_a, student_id=student_id)
    theirs = await add_essay(session_factory, issue_id=issue_b, student_id=student_id)
    draft = await add_essay(
        session_factory, issue_id=issue_a, student_id=student_id, status="review", text=""
    )

    foreign = await client.put(
        f"/api/issues/{issue_a}/selection",
        json={"essay_ids": [mine, theirs]},
        headers=auth_headers,
    )
    assert foreign.status_code == 400
    assert "不属于本期" in foreign.json()["message"]

    draft_response = await client.put(
        f"/api/issues/{issue_a}/selection", json={"essay_ids": [draft]}, headers=auth_headers
    )
    assert draft_response.status_code == 400
    assert "未定稿" in draft_response.json()["message"]
    assert await selected_ids(session_factory, issue_a) == set()


async def test_single_essay_selected_respects_cap(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "张三")
    full = [
        await add_essay(session_factory, issue_id=issue_id, student_id=student_id, selected=1)
        for _ in range(10)
    ]
    extra = await add_essay(session_factory, issue_id=issue_id, student_id=student_id)

    response = await patch_essay(client, auth_headers, extra, {"selected": 1})
    assert response.status_code == 400
    assert "上限 10 篇" in response.json()["message"]

    cancel = await patch_essay(client, auth_headers, full[0], {"selected": 0})
    assert cancel.status_code == 200
    assert cancel.json()["data"]["selected"] == 0


# ---------------------------------------------------------------------------
# AC-13 三榜端点
# ---------------------------------------------------------------------------
async def _write_app_ranking(client: AsyncClient, body: dict[str, object]) -> None:
    """把 ranking 段写进当前测试数据目录的 app.yaml（配置是热读的）。"""
    from app.config import get_settings

    settings = get_settings()
    import yaml

    existing = settings.app_config()
    existing["ranking"] = body
    settings.app_yaml_path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


async def test_ranking_endpoint_returns_three_boards(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue1 = await add_issue(session_factory, 1)
    issue2 = await add_issue(session_factory, 2)
    s1 = await add_student(session_factory, "S001", "甲")
    s2 = await add_student(session_factory, "S002", "乙")
    s3 = await add_student(session_factory, "S003", "丙")
    await add_essay(session_factory, issue_id=issue1, student_id=s1, score=60.0)
    await add_essay(session_factory, issue_id=issue1, student_id=s2, score=90.0)
    await add_essay(session_factory, issue_id=issue2, student_id=s1, score=85.0)
    await add_essay(session_factory, issue_id=issue2, student_id=s2, score=70.0)
    await add_essay(session_factory, issue_id=issue2, student_id=s3, score=95.0)  # 无上期

    response = await client.get(f"/api/issues/{issue2}/ranking", headers=auth_headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert [row["name"] for row in data["work"]] == ["丙", "甲", "乙"]
    assert [row["rank"] for row in data["work"]] == [1, 2, 3]
    assert data["work"][0]["stars"] == 5

    # 进步榜：甲 +25、乙 -20；丙无上期不入榜
    assert [row["name"] for row in data["progress"]] == ["甲", "乙"]
    assert data["progress"][0]["delta"] == 25.0
    assert data["progress"][1]["previous_score"] == 90.0

    # 星级榜按学号，且不含 rank/score
    assert [row["student_no"] for row in data["star"]] == ["S001", "S002", "S003"]
    assert "rank" not in data["star"][0]
    assert "score" not in data["star"][0]

    assert data["scored_count"] == 3
    assert data["config"]["work"]["visibility"] == "teacher"
    assert data["disabled"] == []


async def test_ranking_respects_disabled_boards(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "甲")
    await add_essay(session_factory, issue_id=issue_id, student_id=student_id, score=75.0)
    await _write_app_ranking(
        client, {"work": {"enabled": False}, "stars_from_score": [50, 60, 70, 80]}
    )

    data = (await client.get(f"/api/issues/{issue_id}/ranking", headers=auth_headers)).json()["data"]
    assert data["work"] == []
    assert data["disabled"] == ["work"]
    assert data["thresholds"] == [50, 60, 70, 80]
    assert data["star"][0]["stars"] == 4  # 阈值改了，星级跟着变（后端一处算）


async def test_ranking_unscored_and_unproofread_excluded(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "甲")
    await add_essay(session_factory, issue_id=issue_id, student_id=student_id, score=None)
    await add_essay(session_factory, issue_id=issue_id, student_id=student_id, score=None)
    # 有人绕过接口直接往库里塞了分，但未定稿 —— 依然不进榜
    await add_essay(
        session_factory, issue_id=issue_id, student_id=student_id, score=99.0, status="review", text=""
    )

    data = (await client.get(f"/api/issues/{issue_id}/ranking", headers=auth_headers)).json()["data"]
    assert data["work"] == []
    assert data["scored_count"] == 0
    assert data["unscored_count"] == 2


# ---------------------------------------------------------------------------
# AC-15 成长档案
# ---------------------------------------------------------------------------
async def test_portfolio_lists_only_proofread_with_stats(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue1 = await add_issue(session_factory, 1)
    issue2 = await add_issue(session_factory, 2)
    mine = await add_student(session_factory, "S001", "甲")
    other = await add_student(session_factory, "S002", "乙")
    await add_essay(
        session_factory, issue_id=issue1, student_id=mine, title="第一期", score=62.0, selected=1
    )
    await add_essay(
        session_factory, issue_id=issue2, student_id=mine, title="第二期", score=93.0
    )
    await add_essay(
        session_factory, issue_id=issue2, student_id=mine, status="review", text="", title="未定稿"
    )
    for index in range(3):  # 乙在第 2 期分数更高，占掉佳作榜前 3，甲因此不上榜
        await add_essay(
            session_factory,
            issue_id=issue2,
            student_id=other,
            title=f"乙{index}",
            score=97.0,
        )

    data = (
        await client.get(f"/api/students/{mine}/portfolio", headers=auth_headers)
    ).json()["data"]
    assert data["student"]["name"] == "甲"
    assert [entry["title"] for entry in data["entries"]] == ["第二期", "第一期"]  # 期号倒序
    assert data["stats"]["essay_count"] == 2
    assert data["stats"]["selected_count"] == 1
    assert data["stats"]["top_stars"] == 5
    assert data["stats"]["avg_score"] == 77.5
    assert data["stats"]["issue_count"] == 2
    assert data["stats"]["last_issue_no"] == 2
    assert data["stats"]["honoured_count"] == 1  # 第 1 期只有甲有分 -> 进前 3


async def test_portfolio_rejects_unknown_student(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/students/9999/portfolio", headers=auth_headers)
    assert response.status_code == 404


async def test_portfolio_requires_auth(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """免鉴权面**只有** /api/share/*：档案必须登录才能读（AC-16）。"""
    issue_id = await add_issue(session_factory, 1)
    student_id = await add_student(session_factory, "S001", "甲")
    await add_essay(session_factory, issue_id=issue_id, student_id=student_id)
    anonymous = await client.get(f"/api/students/{student_id}/portfolio")
    assert anonymous.status_code == 401
    anonymous_ranking = await client.get(f"/api/issues/{issue_id}/ranking")
    assert anonymous_ranking.status_code == 401
    anonymous_selection = await client.put(
        f"/api/issues/{issue_id}/selection", json={"essay_ids": []}
    )
    assert anonymous_selection.status_code == 401