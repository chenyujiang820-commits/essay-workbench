"""PRD v1.2 纯函数单测：配置钳制、画质门槛、标题抽取、低画质扣分封顶。

对应验收：AC-3（画质口径同源）、FR-10（并发度/扣分钳制与封顶）、FR-11（标题）、
FR-13（班级名单一来源）。本文件不碰数据库、不起应用，只验证"越界/脏输入一律收敛到
安全值"这条底线。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from app.config import (
    DEFAULT_CLASS_NAME,
    DEFAULT_LOW_RESOLUTION_PENALTY,
    DEFAULT_WORKER_CONCURRENCY,
    MAX_WORKER_CONCURRENCY,
    MIN_WORKER_CONCURRENCY,
    AppSettings,
    get_settings,
)
from app.images import is_low_resolution
from app.pipeline.confidence import (
    MAX_LOW_RESOLUTION_PENALTY,
    PENALTY_LOW_RESOLUTION,
    ConfidenceScorer,
    PhotoSample,
)
from app.pipeline.title import MAX_TITLE_CHARS, extract_title

# 足够长且干净的文本：避免触发"过短"扣分，让断言只反映画质维度。
CLEAN_TEXT = "春天的校园里玉兰花开了我们坐在树下读书风把花瓣吹到了课本上上面还有老师的批注。"


def _reload_settings(data_dir: Path, **engine_keys: Any) -> AppSettings:
    """改写 engines.yaml 后重载配置单例。

    必须走"改文件 + cache_clear"，不能直接改内存对象：钳制发生在读取路径上，
    只有真实重新解析一次才测得到它。
    """
    path = data_dir / "config" / "engines.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.update(engine_keys)
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    get_settings.cache_clear()
    return get_settings()


# ---------------------------------------------------------------------------
# worker_concurrency 钳制（FR-10）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (4, 4),
        (1, MIN_WORKER_CONCURRENCY),
        (8, MAX_WORKER_CONCURRENCY),
        # 越界一律钳到边界而不是回退默认：写 20 的人要的是"尽量快"。
        (0, MIN_WORKER_CONCURRENCY),
        (-5, MIN_WORKER_CONCURRENCY),
        (9, MAX_WORKER_CONCURRENCY),
        (999, MAX_WORKER_CONCURRENCY),
        ("3", 3),  # yaml 里被引号包住很常见
        # 无法解析成整数：回退默认。int("3.7") 会抛，故 3.7 落默认而非 3。
        (3.7, DEFAULT_WORKER_CONCURRENCY),
        ("abc", DEFAULT_WORKER_CONCURRENCY),
        (None, DEFAULT_WORKER_CONCURRENCY),
        ([], DEFAULT_WORKER_CONCURRENCY),
        (True, DEFAULT_WORKER_CONCURRENCY),
    ],
)
async def test_worker_concurrency_is_clamped(data_dir: Path, raw: Any, expected: int) -> None:
    settings = _reload_settings(data_dir, worker_concurrency=raw)
    assert settings.worker_concurrency() == expected


async def test_worker_concurrency_missing_key_falls_back_to_default(data_dir: Path) -> None:
    """engines.yaml 里干脆没有这个键（老配置文件）也要给出默认值，不能抛。"""
    assert _reload_settings(data_dir).worker_concurrency() == DEFAULT_WORKER_CONCURRENCY


# ---------------------------------------------------------------------------
# low_resolution_penalty 钳制（FR-10）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.05, 0.05),
        (0, 0.0),
        (0.2, 0.2),
        (-1, 0.0),
        (0.9, 0.2),
        ("0.1", 0.1),
        ("x", DEFAULT_LOW_RESOLUTION_PENALTY),
        (None, DEFAULT_LOW_RESOLUTION_PENALTY),
        (float("nan"), DEFAULT_LOW_RESOLUTION_PENALTY),
    ],
)
async def test_low_resolution_penalty_is_clamped(
    data_dir: Path, raw: Any, expected: float
) -> None:
    settings = _reload_settings(data_dir, low_resolution_penalty=raw)
    assert settings.low_resolution_penalty() == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 班级名（FR-13）：单一来源
# ---------------------------------------------------------------------------
async def test_class_name_defaults_when_absent(data_dir: Path) -> None:
    assert get_settings().class_name == DEFAULT_CLASS_NAME


async def test_class_name_and_render_helper_share_one_source(data_dir: Path) -> None:
    """/api/meta 与成册模板必须读同一个键、给同一个值（这里曾各抄一份实现）。"""
    from app.render import templates

    app_yaml = data_dir / "config" / "app.yaml"
    config = yaml.safe_load(app_yaml.read_text(encoding="utf-8"))
    config["class_name"] = "  高一(2)班  "
    app_yaml.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.class_name == "高一(2)班"  # 首尾空白被剥掉
    assert templates.class_name_from_settings(settings) == settings.class_name


# ---------------------------------------------------------------------------
# 画质门槛（AC-3）：长边 >=800 且短边 >=600
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("width", "height", "low"),
    [
        (800, 600, False),  # 恰好达标
        (600, 800, False),  # 竖图同样达标（横竖等价）
        (799, 600, True),  # 长边差 1
        (800, 599, True),  # 短边差 1
        (100, 100, True),
        (4000, 3000, False),
        (800, 800, False),
        (None, 600, False),  # 尺寸未知：宁可不提示，不误伤可上传的照片
        (600, None, False),
        # 非正尺寸等同于解析不出来，与 None 同处理：不打无根据的角标
        (0, 0, False),
        (-1, 800, False),
    ],
)
def test_is_low_resolution_boundaries(width: int | None, height: int | None, low: bool) -> None:
    assert is_low_resolution(width, height) is low


# ---------------------------------------------------------------------------
# 标题抽取（FR-11）
# ---------------------------------------------------------------------------
# 下面两条夹具直接取自真机 T05 照片的识别结果（原样抄录），不是编造用例：
# 旧判据（只数句末标点个数）对这两条都失手，抽取出了看着像标题的错值。
REAL_OCR_FRAGMENT: str = "\n".join(
    [
        "覆盖和服务更多的人？于是我们便开始了四川的",
        "溯源之旅，四款辣酱就此诞生，然后陆续有了酸",
        "菜鱼、樟茶鸭、萝卜干、水煮牛肉等谭六记的第一批产品",
    ]
)
REAL_NOTEBOOK_HEADER: str = "月 日 星期\n\n关于4、8教育问政、政策规划问题\n崇尚快乐、阳光教育"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("春天来了", "春天来了"),
        ("  春天来了  \n\n正文正文正文", "春天来了"),
        ("《故乡》。", "故乡"),  # 包裹符号与句读都要剥
        ("【我的妈妈】", "我的妈妈"),
        ("“童年”", "童年"),
        ("春天来了。花开了。", ""),  # 首行就是整句正文 -> 留空给老师
        ("", ""),
        ("   \n  \n", ""),
        ("。。。", ""),  # 只有标点：清洗后为空 -> 留空
        ("\n\n第一段前有若干空行", "第一段前有若干空行"),
        (MAX_TITLE_CHARS * "字", MAX_TITLE_CHARS * "字"),  # 恰好上限：保留
        (MAX_TITLE_CHARS * "字" + "多出来的部分", ""),  # 超上限：判定为正文
        (REAL_OCR_FRAGMENT, ""),  # 真机 T05：OCR 断行的正文残句（只有 1 个句中「？」）
        (REAL_NOTEBOOK_HEADER, ""),  # 真机 T05：作文本日期栏被当成首行
    ],
)
def test_extract_title_cases(text: str, expected: str) -> None:
    assert extract_title(text) == expected


def test_extract_title_never_exceeds_max_length() -> None:
    """任意输入下返回值都不超过上限（防止脏配置把标题撑爆 PDF 版式）。"""
    for text in ("a" * 500, "。" * 50, "\n" + "长" * 40 + "\n\n正文"):
        assert len(extract_title(text)) <= MAX_TITLE_CHARS


def test_extract_title_treats_single_trailing_period_as_a_title() -> None:
    """钉住一个已知取舍：句末标点只落在行尾时，首行仍判为标题。

    "今天下雨了。"更像一句话，但 FR-11 判正文要看标点**位置**而不是个数——
    落在行主体的句末标点才是正文残句，行尾单个句读仍允许（真机标题常带句号）。
    判据从">=2 个"改成"句中命中即否"时，这条断言一字未动，正是它钉住了边界。
    """
    assert extract_title("今天下雨了。") == "今天下雨了"
    assert extract_title("今天下雨了，我没带伞。") == "今天下雨了，我没带伞"


def test_extract_title_rejects_real_photo_false_positives() -> None:
    """真机 T05 的两条误判必须修掉：宁可留空让老师填，也不把错值印上整册 PDF。"""
    assert extract_title(REAL_OCR_FRAGMENT) == ""
    assert extract_title(REAL_NOTEBOOK_HEADER) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("日月同辉", "日月同辉"),  # 含「日」「月」但实义字够多：不误伤
        ("七月", "七月"),  # 只命中一个栏目词：不误伤
        ("星期天的早晨", "星期天的早晨"),  # 「星期」命中一次：不误伤
        ("我的名字", "我的名字"),  # 「名字」不等于栏目「姓名」：不误伤
        ("2024年 月 日 星期", ""),  # 日期栏：栏目词密集 + 无实义字
        ("9月12日 星期五", ""),  # 日期栏：只剩「五」一个实义字
        ("姓名 班级 学号", ""),  # 表单头
    ],
)
def test_extract_title_form_header_rule_bounds(text: str, expected: str) -> None:
    """表头判据只吃「栏目词密集 + 几乎无实义字」的整行，含日期用字的标题不受影响。"""
    assert extract_title(text) == expected


# ---------------------------------------------------------------------------
# 低画质扣分封顶（FR-10）
# ---------------------------------------------------------------------------
def _samples(count: int, low_count: int) -> list[PhotoSample]:
    return [
        PhotoSample(text=CLEAN_TEXT, self_confidence=1.0, low_resolution=index < low_count)
        for index in range(count)
    ]


def test_low_resolution_penalty_accumulates_below_cap() -> None:
    scorer = ConfidenceScorer()
    # 2 张低画质：0.05 * 2 = 0.10，未触顶
    assert scorer.score_essay(_samples(4, 2)) == pytest.approx(1.0 - 0.10)


def test_low_resolution_penalty_is_capped() -> None:
    scorer = ConfidenceScorer()
    # 6 张低画质本该扣 0.30，但累计封顶 0.15 -> 一篇全是糊图也不被扣穿
    assert MAX_LOW_RESOLUTION_PENALTY == 0.15
    assert scorer.score_essay(_samples(6, 6)) == pytest.approx(1.0 - MAX_LOW_RESOLUTION_PENALTY)


def test_low_resolution_cap_scales_with_configured_penalty() -> None:
    """自定义单张扣分时封顶仍然生效（否则调大 penalty 会把置信度直接打到 0）。"""
    scorer = ConfidenceScorer(low_resolution_penalty=0.2)
    assert scorer.score_essay(_samples(5, 5)) == pytest.approx(1.0 - MAX_LOW_RESOLUTION_PENALTY)


def test_clean_essay_without_low_resolution_is_untouched() -> None:
    assert ConfidenceScorer().score_essay(_samples(3, 0)) == pytest.approx(1.0)


def test_single_photo_penalty_is_not_shared_with_essay_cap() -> None:
    """单张口径（score_photo）各自扣一次，不受整篇累计封顶影响。"""
    scorer = ConfidenceScorer()
    blurry = PhotoSample(text=CLEAN_TEXT, self_confidence=1.0, low_resolution=True)
    assert scorer.score_photo(blurry) == pytest.approx(1.0 - PENALTY_LOW_RESOLUTION)


def test_low_resolution_pushes_a_marginal_essay_under_the_threshold() -> None:
    """设计意图验证：0.88 本不算低置信，一张糊图扣 0.05 后掉到 0.83 -> 触发复核引擎。"""
    scorer = ConfidenceScorer(low_threshold=0.85)
    clean = PhotoSample(text=CLEAN_TEXT, self_confidence=0.88)
    blurry = PhotoSample(text=CLEAN_TEXT, self_confidence=0.88, low_resolution=True)
    assert not scorer.is_low(scorer.score_essay([clean]))
    assert scorer.is_low(scorer.score_essay([blurry]))

def test_low_resolution_is_derived_from_dimensions_for_orm_shaped_input() -> None:
    """直接传 ORM ``Photo`` 形态（只有 width/height，库里没有画质列）也必须扣分。

    ``Photo`` 不存 ``low_resolution``：如果归一化时写成
    ``getattr(photo, "low_resolution", False)``，真实链路上的画质扣分会静默失效，
    而全部用 ``PhotoSample`` 的测试仍然全绿。这条断言就是钉住那个坑。
    """
    scorer = ConfidenceScorer()
    blurry = SimpleNamespace(text=CLEAN_TEXT, self_confidence=1.0, width=700, height=500)
    sharp = SimpleNamespace(text=CLEAN_TEXT, self_confidence=1.0, width=1200, height=900)
    unknown = SimpleNamespace(text=CLEAN_TEXT, self_confidence=1.0, width=None, height=None)

    assert scorer.score_essay([blurry]) == pytest.approx(1.0 - PENALTY_LOW_RESOLUTION)
    assert scorer.score_essay([sharp]) == pytest.approx(1.0)
    # 尺寸解析不出来时不扣分（与 AC-3 的 low_resolution=0 口径一致）
    assert scorer.score_essay([unknown]) == pytest.approx(1.0)
