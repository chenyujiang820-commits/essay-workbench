"""作文标题自动抽取（FR-11）。

识别完成后从「定稿候选文本」抽取首行作为候选标题，写入 ``essays.title``。

设计取向：**宁缺勿错**。标题会出现在整册 PDF、看板与投屏上，误把正文首句当成
标题比留空更伤信任分，因此只在首行足够"像标题"时才返回结果，否则返回空串交给
老师手填（校对页标题输入框优先级更高：``PATCH`` 显式覆盖，Worker 不写非空标题）。

判定规则（PRD v1.2 §5 FR-11，rev.4 起补充真机判据）：

1. **显式标签行优先**：开头若干非空行内若有「（作文/习作）题目：X」「标题：X」
   「题目是 X」（允许前置题号，如「23. 题目：…」），取冒号后的内容判定；
   冒号后为空则取下一非空行。命中标签行即以此结果为准，不再回退到规则 2。
2. 否则**只看首个非空行**（不跨行找，避免把正文误当标题）；
3. 反复剥离包裹符号（书名号/引号/括号/方头括号）、首尾空白与行尾中英文句读；
4. 句末标点（``。！？.!?``）出现在**行主体内部** -> 该行是正文片段，返回空串。
   只看行尾的单个句末标点仍算标题（如「今天下雨了。」）；
5. 首行是噪声 -> 返回空串。噪声四类：
   * 作文本表头/表单栏目（「月 日 星期」「姓名 班级」）；
   * 大题号行（「四、写作」「三、」）；
   * 题号开头行（「23. …」「第 23 题」）；
   * 署名/水印行（「逸云手写」「@某某公众号」）。
6. 清洗后长度 > ``MAX_TITLE_CHARS`` -> 判定为正文，返回空串；
7. 否则返回截断到 ``MAX_TITLE_CHARS`` 的标题。

规则 4/5 的表头部分来自真机 T05 回灌（GAP-11 第一轮）；规则 1 与规则 5 的题号/署名
部分来自真机第二轮回灌（GAP-11）：
第二轮回灌：三张真实照片的识别结果首行分别是「逸云手写」（页眉署名）、「四、写作」
（大题号）和一行正文，旧判据把前两条当成了标题，老师只能两次手填纠正。
"""

from __future__ import annotations

import re

#: 标题最大长度（字符）：超过即判定为首行是正文。
MAX_TITLE_CHARS = 30

#: 句末标点：出现在行主体内部即视为一整句正文。
SENTENCE_END_PUNCTUATION: frozenset[str] = frozenset("。！？.!?")

#: 行尾可剥离的中英文句读（规范标题不带句读）。
TRAILING_PUNCTUATION_CHARS = "。！？，、；：.:;,"

#: 允许剥离的首/尾包裹符号（成对使用，逐层剥离）。
_LEADING_WRAPPERS = "《「『“‘\"'【（([<《"
_TRAILING_WRAPPERS = "》」』”’\"'】）)]>》"

#: 作文本表头/表单栏目词（先按多字词匹配，再按单字匹配，避免误伤「日月同辉」）。
_FORM_LABEL_WORDS: tuple[str, ...] = ("星期", "班级", "姓名", "学号", "题目", "标题", "天气")
_FORM_LABEL_CHARS = "年月日"

#: 至少命中这么多个栏目词才判定为表头（单个「月」也可能是标题用字）。
_FORM_MIN_LABEL_HITS = 2

#: 剥掉栏目词与数字后，允许残留的实义字符数：<=1 即认为整行都是表格文字。
_FORM_MAX_MEANINGFUL_CHARS = 1

#: 显式标签行：可选题号前缀 + 题目/标题 + 冒号或「是」+ 标题内容（可能为空）。
_TITLE_LABEL_RE = re.compile(
    r"^(?:[0-9０-９一二三四五六七八九十]{1,4}\s*[、.．:：]?\s*)?"
    r"(?:作文|习作)?\s*(?:题\s*目|标\s*题)\s*(?:[：:]|是)\s*(.*)$"
)

#: 标签行只在开头这么多行里找：再往后就是正文，不值得冒险。
_LABEL_SCAN_LINES = 5

#: 大题号行：「四、写作」「三、」（中文序号 + 顿号/点）。
_SECTION_NUMBER_RE = re.compile(r"^[一二三四五六七八九十]+\s*[、.．]")

#: 题号行：「23. 题目…」「23、」（数字 + 顿号/点）。
_QUESTION_NUMBER_RE = re.compile(r"^[0-9０-９]{1,3}\s*[、.．]")

#: 试卷题头：「第 23 题」「第3篇」。
_EXAM_QUESTION_RE = re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]{1,4}\s*[题次篇]")

#: 署名/水印行后缀与长度上限（真机「逸云手写」4 字）。
_SIGNATURE_SUFFIXES: tuple[str, ...] = (
    "手写", "手记", "录入", "校对", "来源", "摘自", "公众号", "水印", "署名",
)
_SIGNATURE_MAX_CHARS = 8


def _clean_line(line: str) -> str:
    """固定点迭代清洗：剥包裹符号 -> 去行尾句读 -> 去空白，直到不再变化。

    单趟清洗无法处理 ``《故乡》。`` 这类"句读在包裹符号之外"的写法，故循环到稳定。
    """
    text = line.strip()
    while text:
        cleaned = text.rstrip(TRAILING_PUNCTUATION_CHARS).strip()
        if cleaned[:1] in _LEADING_WRAPPERS and len(cleaned) > 1:
            cleaned = cleaned[1:].strip()
        elif cleaned[-1:] in _TRAILING_WRAPPERS and len(cleaned) > 1:
            cleaned = cleaned[:-1].strip()
        if cleaned == text:
            return text
        text = cleaned
    return ""


def _line_body(line: str) -> str:
    """剥掉行尾空白/句读/右包裹符号后的「行主体」，用于判断句末标点是否落在句中。"""
    text = line.strip()
    while text:
        stripped = text.rstrip(TRAILING_PUNCTUATION_CHARS).strip()
        if stripped != text:
            text = stripped
            continue
        if stripped[-1:] in _TRAILING_WRAPPERS and len(stripped) > 1:
            text = stripped[:-1].strip()
            continue
        return stripped
    return ""


def _has_mid_sentence_punctuation(line: str) -> bool:
    """行主体内部仍含句末标点 -> 首行是正文片段（OCR 断行时标点不会在行尾）。"""
    body = _line_body(line)
    return any(char in SENTENCE_END_PUNCTUATION for char in body)


def _is_form_header_line(line: str) -> bool:
    """整行都是作文本表头/表单栏目时判定为噪声。

    只认「栏目词密集 + 几乎没有实义字」的组合：``月 日 星期`` 命中 3 个栏目词、
    实义字 0 个 -> 噪声；``日月同辉`` 虽含「日」「月」但实义字 2 个 -> 保留为标题。
    """
    remainder = line
    hits = 0
    for label in _FORM_LABEL_WORDS:
        count = remainder.count(label)
        if count:
            hits += count
            remainder = remainder.replace(label, "")
    for char in _FORM_LABEL_CHARS:
        count = remainder.count(char)
        if count:
            hits += count
            remainder = remainder.replace(char, "")
    if hits < _FORM_MIN_LABEL_HITS:
        return False
    meaningful = sum(1 for char in remainder if char.isalpha())
    return meaningful <= _FORM_MAX_MEANINGFUL_CHARS


def _is_numbering_line(line: str) -> bool:
    """大题号/题号/试卷题头（「四、写作」「23. …」「第 23 题」）不是标题。"""
    return bool(
        _SECTION_NUMBER_RE.match(line)
        or _QUESTION_NUMBER_RE.match(line)
        or _EXAM_QUESTION_RE.match(line)
    )


def _is_signature_line(line: str) -> bool:
    """页眉署名与水印行（「逸云手写」「@xxx」）不是标题：短行 + 命中署名后缀。"""
    text = line.strip()
    if text.startswith("@"):
        return True
    if len(text) > _SIGNATURE_MAX_CHARS:
        return False
    return any(text.endswith(suffix) for suffix in _SIGNATURE_SUFFIXES)


def _is_noise_line(line: str) -> bool:
    """四类噪声行的总入口。"""
    return _is_form_header_line(line) or _is_numbering_line(line) or _is_signature_line(line)


def _nonempty_lines(text: str) -> list[str]:
    """按顺序返回所有非空白行（已 strip）。"""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _judge_line(line: str) -> str:
    """对一行候选标题文本套用全部判据：可信则返回清洗截断后的标题，否则空串。"""
    if not line:
        return ""
    if _has_mid_sentence_punctuation(line) or _is_noise_line(line):
        return ""
    title = _clean_line(line)
    if not title or len(title) > MAX_TITLE_CHARS:
        return ""
    return title[:MAX_TITLE_CHARS]


def _extract_labeled_title(lines: list[str]) -> str | None:
    """在开头若干非空行里找显式「题目：/标题：」标签行。

    真机照片里试卷本就写着「作文题目：《…》」，这是比"猜首行"强得多的信号，因此
    标签行优先；但**只在标签存在时**才跨行取内容——无标签时跨行找下一行会把
    ``月 日 星期`` 后面的正文残句抽成标题（真机 T05 既有断言正是这么钉住的）。

    Returns:
        ``None`` 表示没有标签行（调用方回退到「只看首个非空行」）；
        否则返回标签内容的判定结果（命中标签行即以它为准，不再回退）。
    """
    for position, line in enumerate(lines[:_LABEL_SCAN_LINES]):
        match = _TITLE_LABEL_RE.match(line)
        if match is None:
            continue
        candidate = match.group(1).strip()
        if not candidate and position + 1 < len(lines):
            # 冒号后换行书写：「作文题目：」下一行才是标题。
            candidate = lines[position + 1]
        return _judge_line(candidate)
    return None


def extract_title(text: str) -> str:
    """从识别文本抽取候选标题。

    Args:
        text: 定稿候选文本（各张识别结果按 ``seq`` 顺序拼接）。

    Returns:
        清洗并截断到 30 字的标题；判定"不像标题"时返回 ````（由老师填写）。
    """
    lines = _nonempty_lines(text)
    if not lines:
        return ""

    labeled = _extract_labeled_title(lines)
    if labeled is not None:
        return labeled
    return _judge_line(lines[0])
