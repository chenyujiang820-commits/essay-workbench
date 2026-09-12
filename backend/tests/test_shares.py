"""二期家长分享（FR-07）与海报/文集导出的真库真 HTTP 测。

分两类：

* 分享链路全部走 HTTP（含**不带 token 的匿名请求**），因为"免鉴权面有没有扩大"
  只能在真实路由表上验证，单元测试说不清；
* 导出侧只做 400 分支与 PDF 页数断言（AC-17），HTML 版式在 ``test_render.py`` 里查。
"""

from __future__ import annotations

import asyncio
import re
import sys

from app.models import ShareLink
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Playwright 在 Windows 需要 Proactor 事件循环（子进程能力）。
if sys.platform == "win32":  # pragma: no cover - 平台相关
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from tests.test_phase2_api import add_essay, add_issue, add_student  # noqa: E402


def _pdf_page_count(data: bytes) -> int:
    """数 PDF 页数：Chromium 每页写一个 ``/Type /Page`` 对象、页树写 ``/Type /Pages``。

    刻意不引入 pypdf 这类新依赖（新增依赖需要单独授权）：这里只验证"是不是单页"
    这一个二值判据，两条独立口径（Page 对象数 + 页树 /Count）互相对上就够用了。
    """
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", data))
    counts = [int(item) for item in re.findall(rb"/Count\s+(\d+)", data)]
    assert counts, "PDF 里找不到 /Count，说明解析口径失效，断言不可信"
    assert max(counts) == pages, f"页数口径不一致：/Count={counts} vs Page 对象={pages}"
    return pages


async def _make_issue_with_essays(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    issue_no: int = 1,
    students: tuple[tuple[str, str], ...] = (("S001", "甲"), ("S002", "乙")),
    comment: str | None = "书写工整，结尾有力。",
    score: float | None = 92.0,
) -> tuple[int, list[int], list[int]]:
    """建 1 期 + N 个学生各 1 篇已定稿作文，返回 (issue_id, student_ids, essay_ids)。"""
    issue_id = await add_issue(session_factory, issue_no)
    student_ids: list[int] = []
    essay_ids: list[int] = []
    for student_no, name in students:
        student_id = await add_student(session_factory, student_no, name)
        student_ids.append(student_id)
        essay_ids.append(
            await add_essay(
                session_factory,
                issue_id=issue_id,
                student_id=student_id,
                title=f"{name}的作文",
                text=f"{name}的第一段\n{name}的第二段",
                comment=comment,
                score=score,
            )
        )
    return issue_id, student_ids, essay_ids


# ---------------------------------------------------------------------------
# 建链 / 列表 / 撤销
# ---------------------------------------------------------------------------
async def test_create_share_issue_scope(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    response = await client.post(
        f"/api/issues/{issue_id}/shares",
        json={"days": 7, "label": "四年级2班家长群"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["scope"] == "issue"
    assert data["url"] == f"/share/{data['token']}"
    assert data["label"] == "四年级2班家长群"
    assert data["essay_count"] == 2
    assert len(data["token"]) >= 40


async def test_create_share_requires_proofread_content(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """该期一篇都没定稿时不给建链接：分享出去只会让家长看到空白（GAP-08 同一类误解）。"""
    issue_id = await add_issue(session_factory, 9)
    student_id = await add_student(session_factory, "S001", "甲")
    await add_essay(session_factory, issue_id=issue_id, student_id=student_id, status="review", text="")
    response = await client.post(
        f"/api/issues/{issue_id}/shares", json={"days": 7}, headers=auth_headers
    )
    assert response.status_code == 400
    assert "已定稿" in response.json()["message"]


async def test_share_days_bounds_validated(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    for days in (0, 91):
        response = await client.post(
            f"/api/issues/{issue_id}/shares", json={"days": days}, headers=auth_headers
        )
        assert response.status_code == 422, days


async def test_list_and_revoke_shares(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    created = await client.post(
        f"/api/issues/{issue_id}/shares", json={"days": 7}, headers=auth_headers
    )
    token = created.json()["data"]["token"]

    listed = await client.get(f"/api/shares?issue_id={issue_id}", headers=auth_headers)
    assert listed.status_code == 200
    items = listed.json()["data"]
    assert [item["token"] for item in items] == [token]
    assert items[0]["revoked"] == 0

    revoked = await client.delete(f"/api/shares/{token}", headers=auth_headers)
    assert revoked.status_code == 200
    after = await client.get(f"/api/shares?issue_id={issue_id}", headers=auth_headers)
    assert after.json()["data"][0]["revoked"] == 1  # 仍在列表里：能解释"我撤过这条"

    missing = await client.delete("/api/shares/不存在的令牌", headers=auth_headers)
    assert missing.status_code == 404


async def test_revoke_keeps_row_for_diagnosis(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """撤销是**标记**不是删行（留一行可诊断记录）。"""
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={}, headers=auth_headers)
    ).json()["data"]["token"]
    await client.delete(f"/api/shares/{token}", headers=auth_headers)

    async with session_factory() as session:
        link = await session.get(ShareLink, token)
        assert link is not None
        assert int(link.revoked) == 1


# ---------------------------------------------------------------------------
# 公网只读（AC-16）
# ---------------------------------------------------------------------------
async def test_public_share_view_is_readable_without_token(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3}, headers=auth_headers)
    ).json()["data"]["token"]

    anonymous = await client.get(f"/api/share/{token}")  # 不带 Authorization
    assert anonymous.status_code == 200, anonymous.text
    assert anonymous.headers["cache-control"] == "no-store"
    data = anonymous.json()["data"]
    assert data["issue_no"] == 1
    assert [item["name"] for item in data["items"]] == ["甲", "乙"]
    assert data["items"][0]["comment"] == "书写工整，结尾有力。"
    assert data["items"][0]["stars"] == 5
    # 数据最小化：家长视图不给学号
    assert data["items"][0]["student_no"] == ""
    # v1.3：分数同样不下发到免鉴权接口 —— 家长看星级，星级是公开口径，分数不是。
    assert data["items"][0]["score"] is None


async def test_public_share_payload_carries_no_score_at_all(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """家长 JSON 的每一项都不该带分数：前端不渲染不等于不发，链接一旦外传就是明文泄露。"""
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3}, headers=auth_headers)
    ).json()["data"]["token"]

    anonymous = await client.get(f"/api/share/{token}")
    assert anonymous.status_code == 200, anonymous.text
    for item in anonymous.json()["data"]["items"]:
        assert item["score"] is None
        assert item["student_no"] == ""
        # 星级仍然要给：家长页唯一的评价格式就是它。
        assert item["stars"] >= 0


async def test_public_share_never_leaks_unproofread(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """分享链接不是绕过校道的第二条路：未定稿稿件一律不出现（AC-19）。"""
    issue_id, student_ids, _ = await _make_issue_with_essays(session_factory)
    await add_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_ids[0],
        title="没校对的稿子",
        status="review",
        text="",
    )
    token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3}, headers=auth_headers)
    ).json()["data"]["token"]
    data = (await client.get(f"/api/share/{token}")).json()["data"]
    titles = [item["title"] for item in data["items"]]
    assert "没校对的稿子" not in titles
    assert len(titles) == 2


async def test_student_scope_link_excludes_others(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, student_ids, _ = await _make_issue_with_essays(session_factory)
    token = (
        await client.post(
            f"/api/issues/{issue_id}/shares",
            json={"days": 3, "student_id": student_ids[1]},
            headers=auth_headers,
        )
    ).json()["data"]["token"]

    data = (await client.get(f"/api/share/{token}")).json()["data"]
    assert data["scope"] == "student"
    assert data["student_name"] == "乙"
    assert [item["name"] for item in data["items"]] == ["乙"]

    html = await client.get(f"/api/share/{token}/preview")
    assert html.status_code == 200
    assert "乙的作文" in html.text
    assert "甲的第一段" not in html.text


async def test_all_three_failure_modes_share_one_message(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """不存在 / 已撤销 / 已过期：同一 410、同一文案（不做枚举预言机）。"""
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    revoked_token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3}, headers=auth_headers)
    ).json()["data"]["token"]
    await client.delete(f"/api/shares/{revoked_token}", headers=auth_headers)

    expired_token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3}, headers=auth_headers)
    ).json()["data"]["token"]
    async with session_factory() as session:
        link = await session.get(ShareLink, expired_token)
        assert link is not None
        link.expires_at = "2000-01-01T00:00:00+00:00"
        await session.commit()

    messages = set()
    for token in ("nonsense-token", revoked_token, expired_token):
        for path in (f"/api/share/{token}", f"/api/share/{token}/preview"):
            response = await client.get(path)
            assert response.status_code == 410, path
            messages.add(response.json()["message"])
    assert messages == {"链接已失效或不存在，请向老师重新获取"}


async def test_share_preview_html_is_book_template(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3}, headers=auth_headers)
    ).json()["data"]["token"]
    response = await client.get(f"/api/share/{token}/preview?template=formal")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "甲的作文" in response.text
    assert "书写工整" in response.text  # 评语跟着出（家长看的就是 PDF 版式）
    assert "S001" not in response.text  # 学号被抹掉


async def test_share_of_deleted_issue_goes_410(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """期数被删时链接随之失效（外键 CASCADE），不留死链也不 500。"""
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    token = (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3}, headers=auth_headers)
    ).json()["data"]["token"]
    deleted = await client.delete(f"/api/issues/{issue_id}?confirm=true", headers=auth_headers)
    assert deleted.status_code == 200, deleted.text
    assert (await client.get(f"/api/share/{token}")).status_code == 410
    async with session_factory() as session:
        assert await session.get(ShareLink, token) is None


async def test_auth_endpoints_still_guarded(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    assert (
        await client.post(f"/api/issues/{issue_id}/shares", json={"days": 3})
    ).status_code == 401
    assert (await client.get("/api/shares")).status_code == 401
    assert (
        await client.put(f"/api/issues/{issue_id}/selection", json={"essay_ids": []})
    ).status_code == 401


# ---------------------------------------------------------------------------
# 海报 / 个人文集导出（AC-17）
# ---------------------------------------------------------------------------
async def test_poster_requires_selection(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await _make_issue_with_essays(session_factory)
    response = await client.post(f"/api/exports/{issue_id}/poster", headers=auth_headers)
    assert response.status_code == 400
    assert "精选" in response.json()["message"]


async def test_poster_is_single_page_even_with_ten_selected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """10 篇精选也必须只有 1 页：海报要发家长群，翻页就没人看完。"""
    long_text = "这是一段很长很长用来撑页数的正文。" * 30
    issue_id = await add_issue(session_factory, 3)
    for index in range(10):
        student_id = await add_student(session_factory, f"S0{index:02d}", f"学生{index}")
        await add_essay(
            session_factory,
            issue_id=issue_id,
            student_id=student_id,
            title=f"标题{index}",
            text=long_text,
            comment="评语内容也比较长，用来验证摘要截断后海报仍然只出一页。" * 4,
            score=float(60 + index),
            selected=1,
        )

    response = await client.post(f"/api/exports/{issue_id}/poster", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.content[:5] == b"%PDF-"
    assert _pdf_page_count(response.content) == 1


async def test_portfolio_export_pdf(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, student_ids, _ = await _make_issue_with_essays(session_factory)
    await add_essay(
        session_factory, issue_id=issue_id, student_id=student_ids[0], status="review", text=""
    )
    response = await client.post(
        f"/api/exports/students/{student_ids[0]}/portfolio?template=elegant&order=issue_no",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.content[:5] == b"%PDF-"
    assert _pdf_page_count(response.content) >= 1

    empty = await client.post(
        f"/api/exports/students/{student_ids[0]}/portfolio", headers=auth_headers
    )
    assert empty.status_code != 404  # 上面已经出过一次，说明该生有稿
    unknown = await client.post("/api/exports/students/9999/portfolio", headers=auth_headers)
    assert unknown.status_code == 404