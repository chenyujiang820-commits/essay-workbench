"""成册渲染：Jinja2 环境 + 统一数据结构 + 三套模板。

设计要点
* 模板目录：默认取代码库 ``backend/assets/templates/``；若数据目录存在
  ``{EWB_DATA_DIR}/templates/`` 则**优先**（同名模板可被老师覆盖微调）。
* 统一数据结构（三套模板共用）::

      {
        class_name, issue_no, week_start_date, generated_at, template, order,
        items: [ {student_no, name, title, paragraphs: [...], is_selected, comment} ]
      }

  ``comment`` 取自 ``essay.teacher_comment``（v1.2 / FR-12 评语位）：一期一般为空，
  三套模板用 ``{% if item.comment %}`` 条件渲染，空值不产生任何 DOM 与占位。

* 中文字体栈覆盖 Windows 开发机与 Linux 服务器，落到系统字体即可。
* 三/二类排序：``student_no``（默认）/ ``name``；``score`` 为二期预留，一期返回 400。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import AppSettings
from app.models import Essay
from app.schemas import ApiError

# 中文优先字体栈：Windows / Linux 均可落到系统字体
FONT_STACK = (
    '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", '
    '"Source Han Sans SC", "WenQuanYi Micro Hei", sans-serif'
)
DEFAULT_CLASS_NAME = "班级"

TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "key": "elegant",
        "name": "素雅校刊风",
        "description": "封面 + 目录 + 每篇独立起页，留白克制，适合正式发布。",
    },
    {
        "key": "playful",
        "name": "活泼童趣风",
        "description": "彩色标题条与圆角卡片、姓名徽章，适合低年级与亲子阅读。",
    },
    {
        "key": "formal",
        "name": "正式文集风",
        "description": "标题分隔线、规范落款与编委会页，适合校内文集。",
    },
)
DEFAULT_TEMPLATE = "elegant"
VALID_TEMPLATES: tuple[str, ...] = tuple(item["key"] for item in TEMPLATES)

VALID_ORDERS: tuple[str, ...] = ("student_no", "name")
_RESERVED_ORDERS: dict[str, str] = {"score": "评分排序将在二期开放"}


def repo_templates_dir() -> Path:
    """代码库内置模板目录（``backend/assets/templates``）。"""
    return Path(__file__).resolve().parents[2] / "assets" / "templates"


def build_environment(settings: AppSettings | None = None) -> Environment:
    """构造 Jinja2 环境；数据目录模板优先于代码库内置模板。"""
    search_paths: list[str] = []
    if settings is not None:
        search_paths.append(str(settings.data_dir / "templates"))
    search_paths.append(str(repo_templates_dir()))

    env = Environment(
        loader=FileSystemLoader(search_paths),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["font_stack"] = FONT_STACK
    return env


def validate_template(template: str | None) -> str:
    """校验模板 key，返回合法值。

    Raises:
        ApiError: 400 未知模板。
    """
    key = template or DEFAULT_TEMPLATE
    if key not in VALID_TEMPLATES:
        raise ApiError(f"未知模板：{key}", code=400, status_code=400)
    return key


def resolve_order(order: str | None) -> str:
    """校验排序方式，返回合法值。

    Raises:
        ApiError: 400 评分排序（二期）或不支持的排序方式。
    """
    if not order:
        return "student_no"
    if order in _RESERVED_ORDERS:
        raise ApiError(_RESERVED_ORDERS[order], code=400, status_code=400)
    if order not in VALID_ORDERS:
        raise ApiError(f"不支持的排序方式：{order}", code=400, status_code=400)
    return order


def split_paragraphs(text: str | None) -> list[str]:
    """按行切分正文为段落（去空行、去首尾空白）。"""
    return [line.strip() for line in re.split(r"\r?\n", text or "") if line.strip()]


def sort_essays(essays: Sequence[Essay], order: str) -> list[Essay]:
    """按学号（默认）或姓名排序作文。"""
    if order == "name":
        return sorted(
            essays,
            key=lambda essay: (
                essay.student.name if essay.student is not None else "",
                essay.student.student_no if essay.student is not None else "",
            ),
        )
    return sorted(
        essays,
        key=lambda essay: (essay.student.student_no if essay.student is not None else ""),
    )


def build_items(essays: Sequence[Essay]) -> list[dict[str, Any]]:
    """把 ORM 作文序列映射为统一渲染条目（含评语位 ``comment``）。"""
    items: list[dict[str, Any]] = []
    for essay in essays:
        student = essay.student
        items.append(
            {
                "student_no": student.student_no if student is not None else "",
                "name": student.name if student is not None else "",
                "title": (essay.title or "").strip(),
                "paragraphs": split_paragraphs(essay.final_text),
                "is_selected": bool(essay.selected),
                "comment": (essay.teacher_comment or "").strip(),
            }
        )
    return items


def class_name_from_settings(settings: AppSettings) -> str:
    """读取班级名：唯一实现在 ``AppSettings.class_name``，这里只做转发。

    曾经这里抄了一份同样的读取逻辑，注释理由是"避免 config <-> render 导入环"；
    但本模块本来就 ``from app.config import AppSettings``，依赖是单向的，不存在环。
    两处实现意味着"改兜底文案要记得改两个地方"，故收敛成单一来源。
    """
    return settings.class_name


def build_meta(
    *,
    class_name: str,
    issue_no: int,
    week_start_date: str,
    generated_at: str,
    template: str,
    order: str,
) -> dict[str, Any]:
    """构造三套模板共用的元数据。"""
    return {
        "class_name": class_name,
        "issue_no": issue_no,
        "week_start_date": week_start_date,
        "generated_at": generated_at,
        "template": template,
        "order": order,
    }


def render_book_html(
    essays: Sequence[Essay],
    template: str,
    meta: dict[str, Any],
    *,
    settings: AppSettings | None = None,
) -> str:
    """渲染整册 HTML（与 PDF 同一套模板，所见即所得）。"""
    env = build_environment(settings)
    template_obj = env.get_template(f"{validate_template(template)}.html")
    items = build_items(essays)
    context = {
        **meta,
        "items": items,
        "selected_count": sum(1 for item in items if item["is_selected"]),
        "mode": "book",
    }
    return template_obj.render(**context)


def render_single_html(
    essay: Essay,
    template: str,
    meta: dict[str, Any],
    *,
    settings: AppSettings | None = None,
) -> str:
    """渲染单篇版式 HTML（打印张贴用）。"""
    env = build_environment(settings)
    template_obj = env.get_template(f"{validate_template(template)}.html")
    item = build_items([essay])[0]
    context = {**meta, "item": item, "items": [item], "mode": "single"}
    return template_obj.render(**context)
