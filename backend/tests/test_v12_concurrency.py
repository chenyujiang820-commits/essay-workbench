"""PRD v1.2 FR-10 / AC-7：识别 Worker 篇级有界并发。

两件事必须同时成立，缺一不可：
1. 并发确实缩短墙钟（否则 FR-10 只是把风险引进来没换到收益）；
2. 篇与篇互不串台（各自的识别文本、task.step、essay.status 都对得上自己）。
第 2 条比第 1 条重要——共享 session 或写错 essay_id 会造成静默的数据污染。

测法：假引擎每张固定 sleep，用"图片字节里带 essay_id、引擎原样回显"的方式
给每篇作文打上身份戳，这样一旦串台就会被断言抓住。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.models import Essay, Issue, Photo, RecognitionTask, Student, utcnow_iso
from app.pipeline.confidence import ConfidenceScorer
from app.pipeline.engine_adapter import EngineResult
from app.pipeline.worker import RecognitionWorker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

#: 单次识别调用的固定耗时（秒）。8 篇串行约 0.4s，4 并发约 0.1s，留足判别余量。
DELAY = 0.05


class SlowEngine:
    """每次调用固定耗时、并把收到的图片字节原样回显成文本的假引擎。"""

    name = "slow"

    def __init__(self) -> None:
        self.calls = 0

    async def recognize(self, image_bytes: bytes, mime: str = "image/jpeg") -> EngineResult:
        self.calls += 1
        await asyncio.sleep(DELAY)
        return EngineResult(text=image_bytes.decode("utf-8"), self_confidence=1.0)


def echo_loader(photo: Any) -> tuple[bytes, str]:
    """图片内容 = 该照片所属作文的身份证据（essay_id 编码在字节里）。"""
    return (f"essay-{photo.essay_id}".encode(), "image/jpeg")


async def create_essays(
    session_factory: async_sessionmaker[AsyncSession],
    count: int = 0,
    photos_per_essay: int = 1,
    *,
    photo_counts: list[int] | None = None,
) -> list[int]:
    """建 count 篇作文（共用一期一名学生）；photo_counts 可逐篇指定张数。

    学生与期数只能建一次（student_no / issue_no 上有唯一约束），所以压测要一次性
    造完整班级，不能循环调用本函数。
    """
    per_essay = photo_counts if photo_counts is not None else [photos_per_essay] * count
    essay_ids: list[int] = []
    async with session_factory() as session:
        student = Student(student_no="S900", name="并发测试", created_at=utcnow_iso())
        issue = Issue(issue_no=900, week_start_date="2026-09-07", created_at=utcnow_iso())
        session.add_all([student, issue])
        await session.flush()

        for photos in per_essay:
            essay = Essay(
                issue_id=issue.id,
                student_id=student.id,
                status="uploaded",
                created_at=utcnow_iso(),
            )
            session.add(essay)
            await session.flush()
            for seq in range(1, photos + 1):
                session.add(
                    Photo(
                        essay_id=essay.id,
                        seq=seq,
                        file_path=f"photos/{issue.id}/{essay.id}/{seq}.jpg",
                        created_at=utcnow_iso(),
                    )
                )
            session.add(
                RecognitionTask(essay_id=essay.id, step="queued", updated_at=utcnow_iso())
            )
            essay_ids.append(int(essay.id))
        await session.commit()
    return essay_ids


async def run_all(session_factory: async_sessionmaker[AsyncSession], essay_ids: list[int], concurrency: int) -> float:
    """按给定并发度跑完全部作文，返回墙钟秒数。"""
    worker = RecognitionWorker(
        session_factory,
        SlowEngine(),
        SlowEngine(),
        ConfidenceScorer(0.85),
        image_loader=echo_loader,
        concurrency=concurrency,
    )
    started = time.perf_counter()
    worker.start()
    try:
        for essay_id in essay_ids:
            await worker.enqueue(essay_id)
        await worker.join()
    finally:
        await worker.stop()
    return time.perf_counter() - started


async def load_rows(
    session_factory: async_sessionmaker[AsyncSession], essay_ids: list[int]
) -> dict[int, tuple[str, str, str]]:
    """返回 essay_id -> (status, task.step, 首张 engine1_text)。"""
    async with session_factory() as session:
        stmt = (
            select(Essay)
            .where(Essay.id.in_(essay_ids))
            .options(selectinload(Essay.photos), selectinload(Essay.tasks))
        )
        rows = (await session.execute(stmt)).scalars().all()
    result: dict[int, tuple[str, str, str]] = {}
    for essay in rows:
        step = essay.tasks[-1].step if essay.tasks else ""
        text = essay.photos[0].engine1_text or "" if essay.photos else ""
        result[int(essay.id)] = (essay.status, step, text)
    return result


# ---------------------------------------------------------------------------
# 1. 并发缩短墙钟
# ---------------------------------------------------------------------------
async def test_concurrency_four_is_materially_faster_than_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    essay_ids = await create_essays(session_factory, count=8)

    serial = await run_all(session_factory, essay_ids, concurrency=1)
    parallel = await run_all(session_factory, essay_ids, concurrency=4)

    # 8 篇各 1 张：串行 8 次调用，4 并发约 2 轮 -> 理论 25%。
    # 机器负载有抖动，只要求显著更快（<60%），不卡 AC-7 的 25% 理论值。
    assert parallel < serial * 0.6, f"串行 {serial:.3f}s，并发4 {parallel:.3f}s"


async def test_concurrency_one_matches_serial_expectation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """concurrency=1 是回滚开关：它必须等价于逐篇串行，不能悄悄并发。"""
    essay_ids = await create_essays(session_factory, count=4)
    elapsed = await run_all(session_factory, essay_ids, concurrency=1)
    assert elapsed >= DELAY * 4 * 0.9


# ---------------------------------------------------------------------------
# 2. 篇级隔离：不串台
# ---------------------------------------------------------------------------
async def test_concurrent_runs_do_not_cross_contaminate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    essay_ids = await create_essays(session_factory, count=6, photos_per_essay=2)
    await run_all(session_factory, essay_ids, concurrency=4)

    rows = await load_rows(session_factory, essay_ids)
    assert len(rows) == 6
    for essay_id, (status, step, text) in rows.items():
        # 每篇的识别文本必须是自己的身份戳，不能被兄弟篇覆盖
        assert text == f"essay-{essay_id}", f"作文 {essay_id} 拿到的是 {text!r}"
        assert status == "review", f"作文 {essay_id} 状态异常：{status}"
        assert step == "done", f"作文 {essay_id} 任务步异常：{step}"


async def test_one_failing_essay_does_not_stall_its_siblings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """单篇异常只置该篇 failed，消费者继续吃后面的任务（篇级隔离的核心承诺）。"""

    class ExplodingEngine:
        name = "boom"

        def __init__(self) -> None:
            self.calls = 0

        async def recognize(self, image_bytes: bytes, mime: str = "image/jpeg") -> EngineResult:
            self.calls += 1
            await asyncio.sleep(DELAY)
            if image_bytes == b"essay-BOOM":
                raise RuntimeError("引擎炸了")
            return EngineResult(text=image_bytes.decode("utf-8"), self_confidence=1.0)

    boom_ids = await create_essays(session_factory, count=3)
    # 把第一篇伪装成会炸的：让 echo_loader 对它返回固定字节
    async with session_factory() as session:
        photo = (
            (await session.execute(select(Photo).where(Photo.essay_id == boom_ids[0])))
            .scalars()
            .first()
        )
        assert photo is not None
        photo.file_path = "photos/boom/0/1.jpg"
        await session.commit()

    def loader(photo: Any) -> tuple[bytes, str]:
        if photo.essay_id == boom_ids[0]:
            return (b"essay-BOOM", "image/jpeg")
        return echo_loader(photo)

    worker = RecognitionWorker(
        session_factory,
        ExplodingEngine(),
        ExplodingEngine(),
        ConfidenceScorer(0.85),
        image_loader=loader,
        concurrency=3,
    )
    worker.start()
    try:
        for essay_id in boom_ids:
            await worker.enqueue(essay_id)
        await worker.join()
    finally:
        await worker.stop()

    assert worker.failures == 1
    assert worker.processed == 2

    rows = await load_rows(session_factory, boom_ids)
    statuses = {essay_id: status for essay_id, (status, _step, _text) in rows.items()}
    assert statuses[boom_ids[0]] == "failed"
    assert statuses[boom_ids[1]] == "review"
    assert statuses[boom_ids[2]] == "review"


# ---------------------------------------------------------------------------
# 3. 并发度来自配置且被钳制
# ---------------------------------------------------------------------------
async def test_worker_takes_its_concurrency_from_settings(
    session_factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    from app.pipeline.worker import build_worker

    worker = build_worker(session_factory=session_factory, settings=settings, primary=None, review=None)
    assert worker.concurrency == settings.worker_concurrency()

    forced = build_worker(
        session_factory=session_factory, settings=settings, primary=None, review=None, concurrency=0
    )
    assert forced.concurrency == 1, "配置成 0 也要至少留一个消费者，否则队列永远没人吃"


# ---------------------------------------------------------------------------
# 4. AC-7 形状的全班压测（45 篇 x 平均 2.5 张）
# ---------------------------------------------------------------------------
#: 压测用的单张延迟：比 DELAY 再小一档，保证整条用例 ~3 秒内跑完。
logger = logging.getLogger(__name__)

STRESS_DELAY = 0.02
CLASS_SIZE = 45


class CallMeter:
    """记录引擎调用的在飞峰值（结构化证据，比墙钟比值稳定）。

    并发是不是真的发生了、有没有越过上限，用「同一时刻最多几个 in-flight」判定是确定的；
    用墙钟比值判定则每台机器、每次负载都不一样（同一条实现实测 0.323 与 0.351）。
    """

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.total = 0

    def enter(self) -> None:
        self.in_flight += 1
        self.total += 1
        self.peak = max(self.peak, self.in_flight)

    def leave(self) -> None:
        self.in_flight -= 1


class TimedEngine:
    """固定耗时 + 原样回显图片字节的假引擎，顺带把在飞数记进 meter。"""

    name = "timed"

    def __init__(self, meter: CallMeter | None = None) -> None:
        self._meter = meter

    async def recognize(self, image_bytes: bytes, mime: str = "image/jpeg") -> EngineResult:
        if self._meter is not None:
            self._meter.enter()
        try:
            await asyncio.sleep(STRESS_DELAY)
            return EngineResult(text=image_bytes.decode(), self_confidence=1.0)
        finally:
            if self._meter is not None:
                self._meter.leave()


async def run_stress(
    session_factory: async_sessionmaker[AsyncSession],
    essay_ids: list[int],
    concurrency: int,
    meter: CallMeter | None = None,
) -> float:
    counter = meter or CallMeter()
    worker = RecognitionWorker(
        session_factory,
        TimedEngine(counter),
        TimedEngine(counter),
        ConfidenceScorer(0.85),
        image_loader=echo_loader,
        concurrency=concurrency,
    )
    started = time.perf_counter()
    worker.start()
    try:
        for essay_id in essay_ids:
            await worker.enqueue(essay_id)
        await worker.join()
    finally:
        await worker.stop()
    elapsed = time.perf_counter() - started
    assert worker.failures == 0, "压测中出现识别失败，墙钟数字不可用"
    return elapsed


async def test_class_of_45_runs_in_parallel_without_mixing_up(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """AC-7 的形状：45 篇、每篇 2~3 张（合计 112 张原片）冷跑两遍做同量对比。

    两条判据分主次，是踩坑之后改的：

    1. **结构化判据（主，精确）**：并发跑时引擎调用的在飞峰值必须 > 1 且 ≤ 并发上限；
       串行跑时峰值只能是 1。这条不受机器负载影响，能准确抓住「并发被悄悄关掉」「加了全局
       锁」「越过并发上限」三种回归。不钉「峰值 == 4」：每篇还要开独立 DB session，篇与篇
       的「引擎等待 / DB 写入」相位会互相错开，实测峰值常在 3——那是采样相位，不是并发失效。
    2. **墙钟比值（辅，只做粗判）**：阈值 60%。同量冷跑实测比值稳定落在 **0.48~0.51**，
       也就是并发 4 只买到约 **2 倍**加速，不是 4 倍——每篇的多次 commit 要在 SQLite 的
       单写锁上排队，篇内 CPU 部分也共用同一个事件循环。把门禁压在理论值上只会偶发红；
       真实吞吐（45 篇 ≤5 分钟）由 AC-10 真机计时负责。

    ⚠️ 两轮必须**各自冷跑**：曾把这同 45 篇先串行跑一遍、再并发跑一遍，第二遍因为
    `photo.engine1_text` 已落库而走「已识别则复用」分支（少打一半引擎调用），于是
    量出来 224 vs 112 次调用、「比值 0.32」——**那是缓存命中，不是并发收益**。现在一次
    造 90 篇（两组各 45 篇，张数分布完全相同），串行用第一组、并发用第二组。
    """
    # 2/3 张交替，平均 2.5 张；两组同分布，保证比的是同一份工作
    counts = [2 if index % 2 == 0 else 3 for index in range(CLASS_SIZE)]
    essay_ids = await create_essays(session_factory, photo_counts=counts + counts)
    assert len(essay_ids) == CLASS_SIZE * 2
    serial_ids = essay_ids[:CLASS_SIZE]
    parallel_ids = essay_ids[CLASS_SIZE:]

    serial_meter = CallMeter()
    parallel_meter = CallMeter()
    serial = await run_stress(session_factory, serial_ids, concurrency=1, meter=serial_meter)
    parallel = await run_stress(session_factory, parallel_ids, concurrency=4, meter=parallel_meter)

    # 同量：两轮的引擎调用次数必须一致，否则墙钟比值是在比两份不同负载
    assert serial_meter.total == parallel_meter.total, (
        f"两轮调用次数不同：串行 {serial_meter.total} vs 并发4 {parallel_meter.total}"
    )
    assert serial_meter.total == 2 * sum(counts), (
        f"每张应有主引擎 + 复核各一次（文本短、必低置信），实测 {serial_meter.total}"
    )
    assert serial_meter.peak == 1, f"并发度 1 时不该出现并行调用，实测峰值 {serial_meter.peak}"
    assert 1 < parallel_meter.peak <= 4, (
        f"并发度 4 没有在飞重叠或越过上限，实测峰值 {parallel_meter.peak}"
    )

    ratio = parallel / serial
    logger.info(
        "AC-7 压测：%d 篇 / %d 张 / 引擎调用 %d 次（两轮同量）；串行 %.2fs -> 并发4 %.2fs",
        CLASS_SIZE,
        sum(counts),
        parallel_meter.total,
        serial,
        parallel,
    )
    logger.info(
        "AC-7 墙钟比值 %.3f（上限 0.600，实测带宽 0.48~0.51）；在飞峰值 串行=%d / 并发4=%d。"
        "比值含每篇独立 DB session 的固定开销，只作粗判，真实吞吐看 AC-10。",
        ratio,
        serial_meter.peak,
        parallel_meter.peak,
    )
    # 实测带宽 0.48~0.51；退回串行会是 ~1.0，加全局锁同理，这条足够抓住大倒退且不误报。
    assert ratio <= 0.60, f"串行 {serial:.2f}s / 并发4 {parallel:.2f}s = {ratio:.2f}，并发收益大幅倒退"

    rows = await load_rows(session_factory, essay_ids)
    assert len(rows) == CLASS_SIZE * 2
    crossed = [
        essay_id for essay_id, (_status, _step, text) in rows.items() if text != f"essay-{essay_id}"
    ]
    assert not crossed, f"并发下有作文拿到了别篇的识别结果：{crossed[:5]}"
    assert all(step == "done" for _status, step, _text in rows.values())
