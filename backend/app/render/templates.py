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


#: 段内软换行合并时认定的「行末句读」：中文句末标点 + 常见收尾符号（引号/括号/省略号）。
_PARAGRAPH_END_CHARS = "。！？…?!\"'”’」』】〉）)"

#: 一行至少这么长才算「被写满后折行」，从而与下一行合并。
#: 真机数据定的阈值：张伟 15 行 OCR 断行 12~23 字/行（全是写满折行），
#: 陈静称呼行「亲爱的同学们：」7 字（作者有意断行）—— 10 字阈值恰好分开两者。
_SOFT_WRAP_MIN_CHARS = 10

#: 段落边界：一个空行（含只由空白字符组成的行）。
_BLANK_LINE_RE = re.compile(r"\r?\n[ \t]*(?:\r?\n[ \t]*)+")


_ASCII_WORD_CHARS = re.compile(r"[0-9A-Za-z]$")


def _join_wrapped_lines(left: str, right: str) -> str:
    """拼接被折行切断的两截文本。

    中文折行处不能补空格；英文单词之间必须补一个空格，否则 "hello" + "world"
    会粘成 "helloworld"。故只在两侧都是 ASCII 字母/数字时补空格。
    """
    if not left:
        return right
    if _ASCII_WORD_CHARS.search(left) and _ASCII_WORD_CHARS.match(right[:1]):
        return left + " " + right
    return left + right


def _ends_paragraph(line: str) -> bool:
    """行末是句末标点/收尾符号 -> 视为作者有意结束一段（不是折行）。"""
    return line[-1:] in _PARAGRAPH_END_CHARS


def _is_soft_wrap(line: str) -> bool:
    """行末不是句读，且这一行被写满到接近折行宽度 -> 判定为 OCR/手写折行。"""
    return len(line) >= _SOFT_WRAP_MIN_CHARS and not _ends_paragraph(line)


def split_paragraphs(text: str | None) -> list[str]:
    """切分正文为段落：空行分段 + 段内软换行合并。

    旧实现「每个换行都算一段」在真机上翻车（GAP-09）：手写照片 OCR 出来一行就是一行，
    张伟 323 字被切成 15「段」（平均 20.5 字/段），投屏一页因此只放得下几行。
    新规则：

    * 空行永远是段落边界（老师在校对页敲的空行照常生效）；
    * 段内一行末尾没有句末标点、且该行长度达到 ``_SOFT_WRAP_MIN_CHARS`` 字
      （即"被写满后折行"），则与下一行拼回同一段；
    * 短行（称呼、落款、小标题）与句末标点结尾的行仍各自成段。
    """
    paragraphs: list[str] = []
    for block in _BLANK_LINE_RE.split(text or ""):
        buffer = ""
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if buffer and not _is_soft_wrap(buffer):
                paragraphs.append(buffer)
                buffer = line
            else:
                buffer = _join_wrapped_lines(buffer, line)
        if buffer:
            paragraphs.append(buffer)
    return paragraphs


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


# 投屏正文的取法（GAP-14）。上一轮为了消灭「投出空白页」，把投屏列表限定成 status=proofread，
# 结果换来一个新观感：拍完 3 篇、只定稿了 1 篇时投屏里就只剩那 1 篇，老师以为「另一篇看不到」。
# 空白页至少还在轮播里，静默消失反而更难查 —— 正确做法是让它进来并**标明是识别初稿**。
def recognition_draft(essay: Essay) -> str:
    """未定稿稿件的投屏正文：按 ``seq`` 顺序拼接各张主引擎识别文字（``\n\n`` 分段）。"""
    parts: list[str] = []
    for photo in sorted(essay.photos, key=lambda item: item.seq):
        text = (photo.engine1_text or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def present_body(essay: Essay) -> tuple[str, bool]:
    """投屏用正文 ``(text, is_draft)``：定稿优先，未定稿回退识别初稿。

    ``is_draft`` 为真表示这篇尚未定稿、投屏看到的是识别初稿，前端据此挂徽标。
    """
    final = (essay.final_text or "").strip()
    if final:
        return final, False
    draft = recognition_draft(essay)
    return draft, bool(draft)


def build_items(
    essays: Sequence[Essay], *, include_draft_fallback: bool = False
) -> list[dict[str, Any]]:
    """把 ORM 作文序列映射为统一渲染条目（含评语位 ``comment``）。

    ``include_draft_fallback`` 只给投屏用：真为 ``True`` 时未定稿稿件回退到识别初稿并标
    ``is_draft``；成册与预览走默认 ``False``，**正文仍然只认 ``final_text``**（校对铁律）。
    """
    items: list[dict[str, Any]] = []
    for essay in essays:
        student = essay.student
        body, is_draft = present_body(essay) if include_draft_fallback else (essay.final_text, False)
        items.append(
            {
                "student_no": student.student_no if student is not None else "",
                "name": student.name if student is not None else "",
                "title": (essay.title or "").strip(),
                "paragraphs": split_paragraphs(body),
                "is_draft": is_draft,
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
