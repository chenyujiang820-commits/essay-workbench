"""作文标题自动抽取（FR-11）。

识别完成后从「定稿候选文本」抽取首行作为候选标题，写入 ``essays.title``。

设计取向：**宁缺勿错**。标题会出现在整册 PDF、看板与投屏上，误把正文首句当成
标题比留空更伤信任分，因此只在首行足够"像标题"时才返回结果，否则返回空串交给
老师手填（校对页标题输入框优先级更高：``PATCH`` 显式覆盖，Worker 不写非空标题）。

判定规则（PRD v1.2 rev.2 §5 FR-11）：

1. 取首个非空行；
2. 反复剥离包裹符号（书名号/引号/括号/方头括号）、首尾空白与行尾中英文句读；
3. 句末标点（``。！？.!?``）出现在**行主体内部** -> 该行是正文片段，返回空串。
   只看行尾的单个句末标点仍算标题（如「今天下雨了。」）；
4. 首行是作文本表头/表单栏目（如「月 日 星期」「姓名 班级」）-> 返回空串，
   不把表格文字带进整册 PDF；
5. 清洗后长度 > ``MAX_TITLE_CHARS`` -> 判定为正文，返回空串；
6. 否则返回截断到 ``MAX_TITLE_CHARS`` 的标题。

规则 3/4 来自真机数据回灌：T05 真实照片里「覆盖和服务更多的人？于是我们便开始了
四川的」是 OCR 断行的正文残句（只有 1 个句末标点，旧判据统计个数漏掉），
「月 日 星期」是作文本日期栏（无标点、长度合规，旧判据完全没防御）。
"""

from __future__ import annotations

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


def _first_nonempty_line(text: str) -> str:
    """返回首个非空白行（已 strip）；全空白时返回空串。"""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def extract_title(text: str) -> str:
    """从识别文本抽取候选标题。

    Args:
        text: 定稿候选文本（各张识别结果按 ``seq`` 顺序拼接）。

    Returns:
        清洗并截断到 30 字的标题；判定"不像标题"时返回 ``""``（由老师填写）。
    """
    first_line = _first_nonempty_line(text)
    if not first_line:
        return ""

    if _has_mid_sentence_punctuation(first_line):
        return ""
    if _is_form_header_line(first_line):
        return ""

    title = _clean_line(first_line)
    if not title or len(title) > MAX_TITLE_CHARS:
        return ""
    return title[:MAX_TITLE_CHARS]
