"""T04：模板渲染 + 导出/投屏路由 + PDF 冒烟。

* 纯渲染单测（不依赖数据库）：用轻量 ORM 实例直接渲染三套模板。
* 路由测（client fixture）：templates / preview / present / 整册 PDF / 单篇 PDF / 400 / 409。
* PDF 冒烟标记为 ``slow``（启动无头浏览器）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from app.models import Essay, Student, utcnow_iso
from app.render import templates as tpl
from app.schemas import ApiError
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Playwright 在 Windows 需要 Proactor 事件循环（子进程能力）。
if sys.platform == "win32":  # pragma: no cover - 平台相关
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


# ---------------------------------------------------------------------------
# 纯渲染单测
# ---------------------------------------------------------------------------
def make_essay(
    *,
    student_no: str = "S001",
    name: str = "张三",
    title: str = "春天的校园",
    text: str = "第一段文字\n第二段文字",
    selected: int = 0,
) -> Essay:
    """构造带学生的轻量作文实例（无需数据库）。"""
    student = Student(student_no=student_no, name=name, active=1, created_at=utcnow_iso())
    essay = Essay(
        issue_id=1,
        student_id=1,
        title=title,
        final_text=text,
        status="proofread",
        low_confidence=0,
        selected=selected,
        created_at=utcnow_iso(),
    )
    essay.student = student
    return essay


def make_meta(template: str = "elegant") -> dict[str, str | int]:
    return tpl.build_meta(
        class_name="高一(1)班",
        issue_no=3,
        week_start_date="2026-09-07",
        generated_at="2026-09-10 20:00",
        template=template,
        order="student_no",
    )


@pytest.mark.parametrize("template", ["elegant", "playful", "formal"])
def test_template_renders_fields(template: str) -> None:
    html = tpl.render_book_html([make_essay()], template, make_meta(template))
    assert "高一(1)班" in html
    assert "第 3 期" in html
    assert "张三" in html
    assert "春天的校园" in html
    assert "第一段文字" in html
    assert "第二段文字" in html
    assert "@page" in html  # 分页规则
    assert "12pt" in html  # 正文打印字号 ≥ 12pt


@pytest.mark.parametrize("template", ["elegant", "playful", "formal"])
def test_selected_badge_branch(template: str) -> None:
    selected_html = tpl.render_book_html([make_essay(selected=1)], template, make_meta(template))
    assert 'class="badge"' in selected_html

    plain_html = tpl.render_book_html([make_essay(selected=0)], template, make_meta(template))
    assert 'class="badge"' not in plain_html


def test_single_render_has_no_cover() -> None:
    html = tpl.render_single_html(make_essay(), "elegant", make_meta("elegant"))
    assert "春天的校园" in html
    assert "目 录" not in html
    assert 'class="cover"' not in html


def test_sort_essays_by_student_no_and_name() -> None:
    first = make_essay(student_no="S002", name="李四")
    second = make_essay(student_no="S001", name="张三")

    by_no = tpl.sort_essays([first, second], "student_no")
    assert [essay.student.student_no for essay in by_no] == ["S001", "S002"]

    by_name = tpl.sort_essays([first, second], "name")
    assert [essay.student.name for essay in by_name] == ["张三", "李四"]


def test_resolve_order_rejects_score() -> None:
    assert tpl.resolve_order(None) == "student_no"
    assert tpl.resolve_order("name") == "name"

    with pytest.raises(ApiError) as excinfo:
        tpl.resolve_order("score")
    assert excinfo.value.status_code == 400
    assert "二期" in excinfo.value.message

    with pytest.raises(ApiError):
        tpl.resolve_order("unknown")


def test_validate_template_rejects_unknown() -> None:
    assert tpl.validate_template(None) == tpl.DEFAULT_TEMPLATE
    for key in tpl.VALID_TEMPLATES:
        assert tpl.validate_template(key) == key
    with pytest.raises(ApiError) as excinfo:
        tpl.validate_template("nope")
    assert excinfo.value.status_code == 400


def test_split_paragraphs_drops_blank_lines() -> None:
    assert tpl.split_paragraphs("甲\n\n  乙  \n丙\n") == ["甲", "乙", "丙"]
    assert tpl.split_paragraphs(None) == []


# ---------------------------------------------------------------------------
# 路由辅助
# ---------------------------------------------------------------------------
async def insert_student(
    session_factory: async_sessionmaker[AsyncSession], student_no: str, name: str
) -> int:
    async with session_factory() as session:
        student = Student(student_no=student_no, name=name, active=1, created_at=utcnow_iso())
        session.add(student)
        await session.commit()
        await session.refresh(student)
        return student.id


async def insert_essay(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    issue_id: int,
    student_id: int,
    title: str = "无题之作",
    text: str = "第一段\n第二段",
    status: str = "proofread",
    selected: int = 0,
) -> int:
    async with session_factory() as session:
        essay = Essay(
            issue_id=issue_id,
            student_id=student_id,
            title=title,
            final_text=text,
            status=status,
            low_confidence=0,
            selected=selected,
            created_at=utcnow_iso(),
        )
        session.add(essay)
        await session.commit()
        await session.refresh(essay)
        return essay.id


async def create_issue(client: AsyncClient, auth_headers: dict[str, str], issue_no: int) -> int:
    response = await client.post(
        "/api/issues",
        json={"issue_no": issue_no, "week_start_date": "2026-09-07"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["data"]["id"])


async def seed_two_essays(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    issue_no: int = 1,
) -> tuple[int, int, int]:
    issue_id = await create_issue(client, auth_headers, issue_no)
    student_a = await insert_student(session_factory, "S001", "张三")
    student_b = await insert_student(session_factory, "S002", "李四")
    await insert_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_a,
        title="春天",
        text="春天来了。\n玉兰花开了。",
    )
    essay_b = await insert_essay(
        session_factory, issue_id=issue_id, student_id=student_b, title="秋天", text="秋天到了。"
    )
    return issue_id, student_a, essay_b


# ---------------------------------------------------------------------------
# 路由测
# ---------------------------------------------------------------------------
async def test_templates_endpoint(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get("/api/exports/templates", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["key"] for item in data] == ["elegant", "playful", "formal"]
    assert all(item["name"] and item["description"] for item in data)


async def test_templates_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/exports/templates")).status_code == 401


async def test_preview_returns_html(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await seed_two_essays(client, auth_headers, session_factory)

    response = await client.get(
        f"/api/exports/{issue_id}/preview",
        params={"template": "playful", "order": "name"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "张三" in response.text
    assert "李四" in response.text


async def test_present_data(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await seed_two_essays(client, auth_headers, session_factory)

    response = await client.get(f"/api/exports/{issue_id}/present", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["items"]) == 2
    assert data["items"][0]["name"] == "张三"
    assert data["items"][0]["paragraphs"] == ["春天来了。", "玉兰花开了。"]


async def test_preview_empty_issue_returns_400(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    issue_id = await create_issue(client, auth_headers, 5)
    response = await client.get(f"/api/exports/{issue_id}/preview", headers=auth_headers)
    assert response.status_code == 400


async def test_export_empty_issue_returns_400(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    issue_id = await create_issue(client, auth_headers, 6)
    response = await client.post(
        f"/api/exports/{issue_id}", json={"template": "elegant", "order": "student_no"}, headers=auth_headers
    )
    assert response.status_code == 400


async def test_export_blocked_when_not_all_proofread(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await create_issue(client, auth_headers, 7)
    student_id = await insert_student(session_factory, "S001", "张三")
    await insert_essay(
        session_factory, issue_id=issue_id, student_id=student_id, status="proofread"
    )
    await insert_essay(
        session_factory, issue_id=issue_id, student_id=student_id, status="review"
    )

    response = await client.post(
        f"/api/exports/{issue_id}", json={"template": "elegant", "order": "student_no"}, headers=auth_headers
    )
    assert response.status_code == 409
    assert "未校对" in response.json()["message"]


async def test_export_score_order_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, _ = await seed_two_essays(client, auth_headers, session_factory, issue_no=8)
    response = await client.post(
        f"/api/exports/{issue_id}", json={"template": "elegant", "order": "score"}, headers=auth_headers
    )
    assert response.status_code == 400
    assert "二期" in response.json()["message"]


async def test_single_export_requires_proofread(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id = await create_issue(client, auth_headers, 9)
    student_id = await insert_student(session_factory, "S001", "张三")
    essay_id = await insert_essay(
        session_factory, issue_id=issue_id, student_id=student_id, status="review"
    )
    response = await client.post(
        f"/api/exports/{issue_id}/single/{essay_id}", headers=auth_headers
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# PDF 冒烟（较慢）
# ---------------------------------------------------------------------------
@pytest.mark.slow
async def test_export_book_pdf_smoke(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    issue_id, _, _ = await seed_two_essays(client, auth_headers, session_factory, issue_no=21)

    response = await client.post(
        f"/api/exports/{issue_id}",
        json={"template": "elegant", "order": "student_no"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]

    data = response.content
    assert data[:5] == b"%PDF-"
    assert len(data) > 10_000

    (tmp_path / "book.pdf").write_bytes(data)


@pytest.mark.slow
async def test_export_single_pdf_smoke(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    issue_id, _, essay_b = await seed_two_essays(
        client, auth_headers, session_factory, issue_no=22
    )

    response = await client.post(
        f"/api/exports/{issue_id}/single/{essay_b}",
        params={"template": "formal"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.content
    assert data[:5] == b"%PDF-"
    assert len(data) > 5_000
