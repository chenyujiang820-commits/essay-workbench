"""T04：模板渲染 + 导出/投屏路由 + PDF 冒烟。

* 纯渲染单测（不依赖数据库）：用轻量 ORM 实例直接渲染五套模板。
* 路由测（client fixture）：templates / preview / present / 整册 PDF / 单篇 PDF / 400 / 409。
* PDF 冒烟标记为 ``slow``（启动无头浏览器）。
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from app.models import Essay, Issue, Photo, Student, utcnow_iso
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
    score: float | None = None,
    photos: Sequence[str] | None = None,
    status: str = "proofread",
) -> Essay:
    """构造带学生的轻量作文实例（无需数据库）。"""
    student = Student(student_no=student_no, name=name, active=1, created_at=utcnow_iso())
    essay = Essay(
        issue_id=1,
        student_id=1,
        title=title,
        final_text=text,
        status=status,
        low_confidence=0,
        selected=selected,
        teacher_comment=comment,
        score=score,
        created_at=utcnow_iso(),
    )
    if photos:
        essay.photos = [
            Photo(
                essay_id=1,
                seq=seq,
                file_path=f"photos/{seq}.jpg",
                engine1_text=text,
                created_at=utcnow_iso(),
            )
            for seq, text in enumerate(photos, start=1)
        ]
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


TEMPLATES_ALL: tuple[str, ...] = ("elegant", "playful", "formal", "clean", "reading")


@pytest.mark.parametrize("template", TEMPLATES_ALL)
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


@pytest.mark.parametrize("template", TEMPLATES_ALL)
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


def test_resolve_order_accepts_score_order() -> None:
    """v1.4 支持 score 与 selected_score 两种排序，未知值仍直接 400。"""
    assert tpl.resolve_order(None) == "student_no"
    assert tpl.resolve_order("name") == "name"
    assert tpl.resolve_order("score") == "score"
    assert "score" in tpl.VALID_ORDERS

    with pytest.raises(ApiError):
        tpl.resolve_order("unknown")


def test_sort_essays_by_score_puts_unscored_last() -> None:
    """佳作序：分数降序，**未评分排最后并按学号升序**。"""
    low = make_essay(student_no="S001", name="甲", score=62.0)
    high = make_essay(student_no="S002", name="乙", score=95.0)
    missing_b = make_essay(student_no="S004", name="丁", score=None)
    missing_a = make_essay(student_no="S003", name="丙", score=None)

    ordered = tpl.sort_essays([missing_a, low, missing_b, high], "score")
    assert [essay.student.name for essay in ordered] == ["乙", "甲", "丙", "丁"]


def test_sort_essays_selected_first_then_score() -> None:
    ordinary_high = make_essay(student_no="S001", name="甲", score=99.0, selected=0)
    selected_low = make_essay(student_no="S002", name="乙", score=61.0, selected=1)
    selected_high = make_essay(student_no="S003", name="丙", score=95.0, selected=1)
    selected_unscored = make_essay(student_no="S004", name="丁", score=None, selected=1)

    ordered = tpl.sort_essays(
        [ordinary_high, selected_unscored, selected_low, selected_high], "selected_score"
    )
    assert [essay.student.name for essay in ordered] == ["丙", "乙", "丁", "甲"]


def test_build_items_carries_stars() -> None:
    """星级由 build_items 按阈值算好下发，模板只负责渲染。"""
    items = tpl.build_items([make_essay(score=95.0), make_essay(score=None, student_no="S002")])
    assert [item["stars"] for item in items] == [5, 0]
    assert [item["score"] for item in items] == [95.0, None]

    custom = tpl.build_items([make_essay(score=95.0)], thresholds=(96, 97, 98, 99))
    assert custom[0]["stars"] == 1


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
# 五套模板各自的评语特征文案；无评语时这些字样一个都不许出现
COMMENT_MARKERS: dict[str, str] = {
    "elegant": "教师评语",
    "playful": "老师想说",
    "formal": "师评",
    "clean": "教师评语",
    "reading": "教师评语",
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
        # rev.4 投屏改造新增 is_draft；本断言仍守住「不多不少」的键集合。
        "is_draft",
        # v1.3（二期）新增两键：评分与后端算好的星级。
        "score",
        "stars",
        # v1.3：档案/家长共用同一份条目，期数由 build_items 透出（模板不再自己找关系）。
        "issue_no",
    }
    # 默认路径（成册/预览）绝不把未定稿稿件的识别初稿混进来。
    assert [item["is_draft"] for item in items] == [False, False]


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_no_comment_renders_no_comment_dom(template: str) -> None:
    """无评语（None / 空串 / 纯空白）时五套模板都不产生评语 DOM，一期视觉零回归。"""
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


# ---------------------------------------------------------------------------
# v1.3（二期）：封面文案位 / 星级 / share 与 portfolio 模式
# ---------------------------------------------------------------------------
# 一期封面写死「第 N 期作文集」「周一起 X」，二期改成可覆盖的文案位；但**默认值下必须
# 逐字不变** —— PDF 版式是一期真机验证过的基线，任何一处文字漂移都算回归（PRD v1.3 §9）。
COVER_DEFAULT_LINES: tuple[str, ...] = ("<h1>第 3 期作文集</h1>", "周一起 2026-09-07 · 共 2 篇")


def make_meta_v13(template: str, **overrides: object) -> dict[str, object]:
    """带二期文案位/可见性覆盖的 meta（默认值与一期完全一致）。"""
    base: dict[str, object] = {
        "class_name": "高一(1)班",
        "issue_no": 3,
        "week_start_date": "2026-09-07",
        "generated_at": "2026-09-10 20:00",
        "template": template,
        "order": "student_no",
    }
    base.update(overrides)
    return tpl.build_meta(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_cover_defaults_render_exactly_as_phase_one(template: str) -> None:
    html = tpl.render_book_html(
        [make_essay(), make_essay(student_no="S002", name="李四")], template, make_meta(template)
    )
    for line in COVER_DEFAULT_LINES:
        assert line in html


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_cover_honours_meta_overrides(template: str) -> None:
    html = tpl.render_book_html(
        [make_essay()],
        template,
        make_meta_v13(
            template,
            book_title="张三 的作文成长档案",
            subtitle="高一(1)班 · 第 1~3 期",
        ),
    )
    assert "<h1>张三 的作文成长档案</h1>" in html
    assert "高一(1)班 · 第 1~3 期 · 共 1 篇" in html
    assert COVER_DEFAULT_LINES[0] not in html


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_stars_render_only_when_scored(template: str) -> None:
    """星跟着后端算出的 stars 出：未评分既不出星、也不出星级容器（空容器会白占一行版式）。"""
    scored = tpl.render_book_html([make_essay(score=82.0)], template, make_meta(template))
    assert tpl.STAR_CHAR * 4 in scored
    if template == "playful":
        # 活泼版只给实心星（暖色徽章），素雅/正式版补足空心星。
        assert tpl.EMPTY_STAR_CHAR not in scored
    else:
        assert tpl.EMPTY_STAR_CHAR in scored
        # 满分不补足空心星：满排五颗就够，多一个空心符号反而像在提示"还差一颗"。
        top = tpl.render_book_html([make_essay(score=95.0)], template, make_meta(template))
        assert tpl.STAR_CHAR * 5 in top

    unscored = tpl.render_book_html([make_essay()], template, make_meta(template))
    assert tpl.STAR_CHAR not in unscored
    assert 'class="stars"' not in unscored


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_stars_line_does_not_impersonate_comment_label(template: str) -> None:
    """有星级、无评语时不得出现任何评语特征词：formal 的星级行曾写作「师评星级」，
    它会把「无评语即无评语 DOM」这条回归判定污染成假绿（CSS 注释同理）。"""
    html = tpl.render_book_html([make_essay(score=95.0)], template, make_meta(template))
    assert not any(keyword in html for keyword in COMMENT_KEYWORDS)


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_share_mode_blanks_student_no_and_shows_expiry(template: str) -> None:
    """家长只读预览：学号被抹掉、封面标出链接有效期（数据最小化的两条落点都在模板里）。"""
    meta = make_meta_v13(
        template,
        hide_student_no=True,
        book_title="张三 的作文",
        subtitle="高一(1)班 · 周一起 2026-09-07",
    )
    meta["expires_at"] = "2026-09-24T08:00:00+00:00"
    html = tpl.render_share_html([make_essay()], template, meta)
    assert "S001" not in html
    assert "张三" in html
    assert "有效期至" in html


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_portfolio_mode_marks_student_and_per_essay_issue(template: str) -> None:
    """个人文集：正文行标出该篇来自哪一期；封面学生名只在标题没带上时才补一行。"""
    essay = make_essay(score=82.0)
    essay.issue = Issue(issue_no=5, week_start_date="2026-09-07")

    html = tpl.render_portfolio_html(
        [essay],
        template,
        make_meta_v13(
            template,
            student_name="张三",
            book_title="高一(1)班 · 作文成长档案",
            subtitle="第 5 期",
        ),
    )
    assert re.search("张三[^<]*第 5 期", html), "档案的正文行要标出该篇来自哪一期"
    assert "张三 的成长文集" in html

    merged = tpl.render_portfolio_html(
        [essay],
        template,
        make_meta_v13(template, student_name="张三", book_title="张三 的作文成长档案"),
    )
    assert "的成长文集" not in merged


@pytest.mark.parametrize("template", TEMPLATES_ALL)
def test_comment_renders_with_template_marker(template: str) -> None:
    """有评语时五套模板都渲染评语原文，并各自带上特征前缀/标签。"""
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
# 投屏正文取法（GAP-14）
# ---------------------------------------------------------------------------
def test_present_body_prefers_final_text() -> None:
    """已定稿稿件取 ``final_text``，不标初稿。"""
    essay = make_essay(text="定稿文字", photos=["识别文字"])
    assert tpl.present_body(essay) == ("定稿文字", False)


def test_present_body_falls_back_to_recognition_draft() -> None:
    """未定稿但有识别文字 → 回退识别初稿并标 ``is_draft``（投屏不再静默丢篇）。"""
    essay = make_essay(text="", photos=["识别第一段"], status="review")
    assert tpl.present_body(essay) == ("识别第一段", True)


def test_present_body_empty_without_any_text() -> None:
    """定稿与识别文字都为空 → 空串且不标初稿，由调用方排除并计数。"""
    assert tpl.present_body(make_essay(text="")) == ("", False)


def test_recognition_draft_joins_photos_by_seq() -> None:
    """多张照片按 ``seq`` 升序拼接，空白文字跳过，段之间空行分隔。"""
    essay = make_essay(text="")
    essay.photos = [
        Photo(essay_id=1, seq=2, file_path="photos/2.jpg", engine1_text="第二页", created_at=utcnow_iso()),
        Photo(
            essay_id=1, seq=1, file_path="photos/1.jpg", engine1_text="第一页\n还有第二行", created_at=utcnow_iso()
        ),
        Photo(essay_id=1, seq=3, file_path="photos/3.jpg", engine1_text="   ", created_at=utcnow_iso()),
    ]
    assert tpl.recognition_draft(essay) == "第一页\n还有第二行\n\n第二页"


def test_build_items_draft_fallback_is_opt_in() -> None:
    """``include_draft_fallback`` 默认关闭：成册/预览绝不混入识别初稿（校对铁律）。"""
    essay = make_essay(text="", photos=["识别初稿"], status="review")
    strict = tpl.build_items([essay])
    assert strict[0]["paragraphs"] == []
    assert strict[0]["is_draft"] is False
    loose = tpl.build_items([essay], include_draft_fallback=True)
    assert loose[0]["paragraphs"] == ["识别初稿"]
    assert loose[0]["is_draft"] is True


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
    score: float | None = None,
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
            score=score,
            created_at=utcnow_iso(),
        )
        session.add(essay)
        await session.commit()
        await session.refresh(essay)
        return essay.id


async def insert_photo(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    essay_id: int,
    seq: int,
    engine1_text: str,
) -> int:
    """给作文挂一张带识别文字的照片（GAP-14：未定稿投屏要能回退到识别初稿）。"""
    async with session_factory() as session:
        photo = Photo(
            essay_id=essay_id,
            seq=seq,
            file_path=f"photos/{essay_id}_{seq}.jpg",
            engine1_text=engine1_text,
            created_at=utcnow_iso(),
        )
        session.add(photo)
        await session.commit()
        await session.refresh(photo)
        return photo.id


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
    assert [item["key"] for item in data] == ["elegant", "playful", "formal", "clean", "reading"]
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

async def test_present_includes_unproofread_draft(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """投屏列出全部有文字的稿件（GAP-14）。

    真机反馈：老师拍完 3 篇只定稿 1 篇，投屏里就只剩那 1 篇，原话是「只能看到一篇文章，
    另外一篇看不到」。旧实现按 status=proofread 过滤，把未定稿的静默丢弃了。
    现在的判据改成「有没有文字」：未定稿的用识别初稿顶上并标 is_draft。
    """
    issue_id = await create_issue(client, auth_headers, 8)
    student_a = await insert_student(session_factory, "S001", "张三")
    student_b = await insert_student(session_factory, "S002", "李四")
    student_c = await insert_student(session_factory, "S003", "王五")
    await insert_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_a,
        title="春天",
        text="定稿的一段。",
    )
    draft = await insert_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_b,
        title="秋天",
        text="",
        status="review",
    )
    await insert_photo(session_factory, essay_id=draft, seq=1, engine1_text="秋风吹起来了。")
    await insert_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_c,
        title="冬天",
        text="",
        status="review",
    )

    response = await client.get(f"/api/exports/{issue_id}/present", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]
    # 学号序：张三（定稿）→ 李四（识别初稿）；王五既无定稿也无识别文字 → 排除并计数
    assert [item["name"] for item in data["items"]] == ["张三", "李四"]
    assert [item["is_draft"] for item in data["items"]] == [False, True]
    assert data["items"][1]["paragraphs"] == ["秋风吹起来了。"]
    assert data["draft_count"] == 1
    assert data["excluded_no_text"] == 1
    # 校对铁律不受影响：同一期整册导出仍然 409
    blocked = await client.post(
        f"/api/exports/{issue_id}",
        json={"template": "elegant", "order": "student_no"},
        headers=auth_headers,
    )
    assert blocked.status_code == 409


async def test_present_rejects_when_no_text_at_all(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """全期都没有文字时返回 400 并说明原因，不给老师一个空轮播（GAP-14）。"""
    issue_id = await create_issue(client, auth_headers, 9)
    student_id = await insert_student(session_factory, "S001", "张三")
    await insert_essay(
        session_factory,
        issue_id=issue_id,
        student_id=student_id,
        text="",
        status="review",
    )

    response = await client.get(f"/api/exports/{issue_id}/present", headers=auth_headers)
    assert response.status_code == 400
    assert "暂无可投屏" in response.json()["message"]



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


async def test_preview_orders_by_score_with_unscored_last(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``order=score`` 走真库真 HTTP：佳作序把未评分排到最后（v1.3 / FR-04）。"""
    issue_id = await create_issue(client, auth_headers, 8)
    student_a = await insert_student(session_factory, "S001", "甲未评分")
    student_b = await insert_student(session_factory, "S002", "乙九十三")
    await insert_essay(
        session_factory, issue_id=issue_id, student_id=student_a, title="无分之作", score=None
    )
    await insert_essay(
        session_factory, issue_id=issue_id, student_id=student_b, title="高分之作", score=93.0
    )
    response = await client.get(
        f"/api/exports/{issue_id}/preview?order=score", headers=auth_headers
    )
    assert response.status_code == 200
    html = response.text
    assert html.index("乙九十三") < html.index("甲未评分")


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
