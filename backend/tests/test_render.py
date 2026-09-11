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
    comment: str | None = None,
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
        teacher_comment=comment,
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
# v1.2 / FR-12：评语位（条件渲染）+ FR-11 标题兜底
# ---------------------------------------------------------------------------
TEMPLATES_ALL: tuple[str, ...] = ("elegant", "playful", "formal")
# 三套模板各自的评语特征文案；无评语时这些字样一个都不许出现
COMMENT_MARKERS: dict[str, str] = {
    "elegant": "教师评语",
    "playful": "老师想说",
    "formal": "师评",
}
COMMENT_KEYWORDS: tuple[str, ...] = ("教师评语", "老师想说", "师评")


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_build_items_carries_comment(template: str) -> None:
    """统一数据结构新增 comment 键，且只做 strip，不改其它键。"""
    items = tpl.build_items([make_essay(comment="  结尾有力。  "), make_essay()])
    assert [item["comment"] for item in items] == ["结尾有力。", ""]
    assert set(items[0]) == {
        "student_no",
        "name",
        "title",
        "paragraphs",
        "is_selected",
        "comment",
    }


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_no_comment_renders_no_comment_dom(template: str) -> None:
    """无评语（None / 空串 / 纯空白）时三套模板都不产生评语 DOM，一期视觉零回归。"""
    meta = make_meta(template)
    for comment in (None, "", "   \n  "):
        essay = make_essay(comment=comment)
        for rendered in (
            tpl.render_book_html([essay], template, meta),
            tpl.render_single_html(essay, template, meta),
        ):
            assert not any(keyword in rendered for keyword in COMMENT_KEYWORDS)
            assert 'class="comment"' not in rendered
            assert 'class="comment-text"' not in rendered
            assert 'class="comment-label"' not in rendered
            assert 'class="flag"' not in rendered


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_comment_renders_with_template_marker(template: str) -> None:
    """有评语时三套模板都渲染评语原文，并各自带上特征前缀/标签。"""
    comment = "观察仔细，用词准确。"
    meta = make_meta(template)
    essay = make_essay(comment=comment)
    for rendered in (
        tpl.render_book_html([essay], template, meta),
        tpl.render_single_html(essay, template, meta),
    ):
        assert comment in rendered
        assert COMMENT_MARKERS[template] in rendered
        assert 'class="comment"' in rendered


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_comment_is_escaped(template: str) -> None:
    """评语走自动转义：脚本/标签不破版，徽标字样只作为文字出现。"""
    comment = '<script>x</script> <b class="badge">精选</b>'
    meta = make_meta(template)
    essay = make_essay(comment=comment)
    for rendered in (
        tpl.render_book_html([essay], template, meta),
        tpl.render_single_html(essay, template, meta),
    ):
        assert "<script>" not in rendered
        assert '<b class="badge">' not in rendered
        assert "class=&#34;badge&#34;" in rendered
        assert "&lt;script&gt;" in rendered
        # 文本里的"精选"不生成真的徽标 DOM
        assert 'class="badge"' not in rendered


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_comment_newline_preserved(template: str) -> None:
    """多行评语用 white-space: pre-wrap 保留换行（不拼接、不吞行）。"""
    comment = "第一段评语\n第二段评语"
    meta = make_meta(template)
    essay = make_essay(comment=comment)
    for rendered in (
        tpl.render_book_html([essay], template, meta),
        tpl.render_single_html(essay, template, meta),
    ):
        assert comment in rendered
        assert "white-space: pre-wrap" in rendered


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_badge_and_comment_coexist(template: str) -> None:
    """精选徽标与评语同时存在时两者都渲染。"""
    meta = make_meta(template)
    rendered = tpl.render_book_html(
        [make_essay(selected=1, comment="层次清楚。")], template, meta
    )
    assert 'class="badge"' in rendered
    assert COMMENT_MARKERS[template] in rendered
    assert "层次清楚。" in rendered
    assert 'class="flag"' in rendered  # 目录同步标记该篇有评语


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_title_fallback_is_unnamed(template: str) -> None:
    """标题兜底改为"未命名"（FR-11），目录/正文/单篇一致。"""
    meta = make_meta(template)
    for title in ("", "   "):
        essay = make_essay(title=title)
        book = tpl.render_book_html([essay], template, meta)
        single = tpl.render_single_html(essay, template, meta)
        assert "无题" not in book
        assert "无题" not in single
        assert book.count("未命名") >= 2  # 目录一处 + 正文一处
        assert "未命名" in single


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_toc_shows_book_fields(template: str) -> None:
    """目录页字段可见性与正文一致：班级名/期数/周起始日/姓名/标题/徽标/评语标记。"""
    html = tpl.render_book_html(
        [make_essay(selected=1, comment="有评语")], template, make_meta(template)
    )
    start = html.index('class="toc"')
    section = html[start : html.index("</section>", start)]
    assert "高一(1)班" in section
    assert "第 3 期" in section
    assert "2026-09-07" in section
    assert "张三" in section
    assert "春天的校园" in section
    assert 'class="badge"' in section
    assert 'class="flag"' in section


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
    comment: str | None = None,
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
            teacher_comment=comment,
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


async def test_preview_renders_teacher_comment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """落库的 teacher_comment 能经导出链路出现在成册 HTML 评语位。"""
    issue_id = await create_issue(client, auth_headers, 30)
    student_id = await insert_student(session_factory, "S001", "张三")
    await insert_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_id,
        title="春天",
        comment="结尾有力。",
    )

    response = await client.get(
        f"/api/exports/{issue_id}/preview",
        params={"template": "formal"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "师评：结尾有力。" in response.text


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
