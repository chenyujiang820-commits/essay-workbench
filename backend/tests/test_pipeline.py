"""T02：识别流水线（状态机全路径 + 降级 + 重启恢复）+ diff/置信度单测。"""

from __future__ import annotations

from typing import Any

from app.models import Essay, Issue, Photo, RecognitionTask, Student, utcnow_iso
from app.pipeline.confidence import ConfidenceScorer, PhotoSample
from app.pipeline.diff import DiffService
from app.pipeline.engine_adapter import EngineResult, OcrEngineError
from app.pipeline.worker import RecognitionWorker, build_worker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

LONG_TEXT = "春天的校园里，玉兰花开了，我们坐在树下读书，风把花瓣吹到课本上。"


# ---------------------------------------------------------------------------
# 测试替身与工具
# ---------------------------------------------------------------------------
class FakeEngine:
    """按序返回预设结果（可为 EngineResult 或异常）的假引擎。"""

    def __init__(self, name: str, results: list[Any]) -> None:
        self.name = name
        self._results = results
        self.calls = 0

    async def recognize(self, image_bytes: bytes, mime: str = "image/jpeg") -> EngineResult:
        index = min(self.calls, len(self._results) - 1)
        self.calls += 1
        outcome = self._results[index]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_worker(
    session_factory: async_sessionmaker[AsyncSession],
    primary: Any,
    review: Any,
    *,
    threshold: float = 0.85,
) -> RecognitionWorker:
    return RecognitionWorker(
        session_factory,
        primary,
        review,
        ConfidenceScorer(threshold),
        image_loader=lambda _photo: (b"fake-image", "image/jpeg"),
    )


async def create_essay(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    photo_count: int = 1,
    issue_no: int = 1,
    status: str = "uploaded",
    step: str = "queued",
    engine1_text: str | None = None,
) -> int:
    async with session_factory() as session:
        student = Student(student_no=f"S{issue_no:03d}", name="张三", created_at=utcnow_iso())
        issue = Issue(issue_no=issue_no, week_start_date="2026-09-07", created_at=utcnow_iso())
        session.add_all([student, issue])
        await session.flush()

        essay = Essay(
            issue_id=issue.id, student_id=student.id, status=status, created_at=utcnow_iso()
        )
        session.add(essay)
        await session.flush()

        for seq in range(1, photo_count + 1):
            session.add(
                Photo(
                    essay_id=essay.id,
                    seq=seq,
                    file_path=f"photos/{issue.id}/{essay.id}/{seq}.jpg",
                    engine1_text=engine1_text,
                    created_at=utcnow_iso(),
                )
            )
        session.add(
            RecognitionTask(essay_id=essay.id, step=step, updated_at=utcnow_iso())
        )
        await session.commit()
        return essay.id


async def load_essay(session_factory: async_sessionmaker[AsyncSession], essay_id: int) -> Essay:
    async with session_factory() as session:
        stmt = (
            select(Essay)
            .where(Essay.id == essay_id)
            .options(selectinload(Essay.photos), selectinload(Essay.tasks))
        )
        return (await session.execute(stmt)).scalars().one()


async def load_task(
    session_factory: async_sessionmaker[AsyncSession], essay_id: int
) -> RecognitionTask:
    async with session_factory() as session:
        stmt = select(RecognitionTask).where(RecognitionTask.essay_id == essay_id)
        return (await session.execute(stmt)).scalars().one()


# ---------------------------------------------------------------------------
# 状态机：happy path
# ---------------------------------------------------------------------------
async def test_process_high_confidence_skips_review(session_factory) -> None:
    essay_id = await create_essay(session_factory)
    primary = FakeEngine("primary", [EngineResult(text=LONG_TEXT, self_confidence=1.0)])
    review = FakeEngine("review", [EngineResult(text="不该被调用")])
    worker = make_worker(session_factory, primary, review)

    await worker._process(essay_id)

    essay = await load_essay(session_factory, essay_id)
    task = await load_task(session_factory, essay_id)

    assert essay.status == "review"
    assert essay.low_confidence == 0
    assert task.step == "done"
    assert task.error is None
    assert review.calls == 0
    assert essay.photos[0].engine1_text == LONG_TEXT
    assert essay.photos[0].engine2_text is None
    assert essay.photos[0].diff_json is None


async def test_process_low_confidence_calls_review_and_builds_diff(session_factory) -> None:
    essay_id = await create_essay(session_factory)
    primary = FakeEngine("primary", [EngineResult(text="太短", self_confidence=1.0)])
    review = FakeEngine("review", [EngineResult(text="太短了呀", self_confidence=1.0)])
    worker = make_worker(session_factory, primary, review)

    await worker._process(essay_id)

    essay = await load_essay(session_factory, essay_id)
    task = await load_task(session_factory, essay_id)

    assert review.calls == 1
    assert essay.status == "review"
    assert essay.low_confidence == 1
    assert task.step == "done"
    assert task.error is None
    assert essay.photos[0].engine2_text == "太短了呀"
    assert essay.photos[0].diff_json is not None
    assert DiffService.from_json(essay.photos[0].diff_json) is not None


# ---------------------------------------------------------------------------
# 状态机：复核引擎降级（不阻塞）
# ---------------------------------------------------------------------------
async def test_process_degrades_when_review_fails(session_factory) -> None:
    essay_id = await create_essay(session_factory)
    primary = FakeEngine("primary", [EngineResult(text="太短", self_confidence=1.0)])
    review = FakeEngine(
        "review", [OcrEngineError("引擎返回可重试状态码 504", status_code=504, retryable=True)]
    )
    worker = make_worker(session_factory, primary, review)

    await worker._process(essay_id)  # 不应抛异常

    essay = await load_essay(session_factory, essay_id)
    task = await load_task(session_factory, essay_id)

    assert essay.status == "review"  # 降级后照常进入 review
    assert essay.low_confidence == 1
    assert task.step == "done"  # engine2_failed -> done
    assert task.error is not None and "降级" in task.error
    assert essay.photos[0].engine1_text == "太短"
    assert essay.photos[0].engine2_text is None
    assert essay.photos[0].diff_json is None


# ---------------------------------------------------------------------------
# 多张串行
# ---------------------------------------------------------------------------
async def test_process_multiple_photos_serial(session_factory) -> None:
    essay_id = await create_essay(session_factory, photo_count=3)
    primary = FakeEngine("primary", [EngineResult(text=LONG_TEXT, self_confidence=1.0)])
    review = FakeEngine("review", [EngineResult(text="x")])
    worker = make_worker(session_factory, primary, review)

    await worker._process(essay_id)

    essay = await load_essay(session_factory, essay_id)
    assert primary.calls == 3
    assert review.calls == 0
    assert [photo.engine1_text for photo in essay.photos] == [LONG_TEXT, LONG_TEXT, LONG_TEXT]
    assert [photo.seq for photo in essay.photos] == [1, 2, 3]


async def test_process_reuses_engine1_text_on_resume(session_factory) -> None:
    """已落库的 engine1_text 复用（不重复调用主引擎）。"""
    essay_id = await create_essay(session_factory, engine1_text=LONG_TEXT)
    primary = FakeEngine("primary", [EngineResult(text="不该被调用")])
    review = FakeEngine("review", [EngineResult(text="x")])
    worker = make_worker(session_factory, primary, review)

    await worker._process(essay_id)

    essay = await load_essay(session_factory, essay_id)
    assert primary.calls == 0
    assert essay.photos[0].engine1_text == LONG_TEXT


async def test_process_skips_proofread_essay(session_factory) -> None:
    essay_id = await create_essay(session_factory, status="proofread", step="done")
    primary = FakeEngine("primary", [EngineResult(text=LONG_TEXT)])
    worker = make_worker(session_factory, primary, FakeEngine("review", []))

    await worker._process(essay_id)

    essay = await load_essay(session_factory, essay_id)
    assert essay.status == "proofread"
    assert primary.calls == 0


async def test_process_missing_essay_is_noop(session_factory) -> None:
    worker = make_worker(
        session_factory,
        FakeEngine("primary", [EngineResult(text=LONG_TEXT)]),
        FakeEngine("review", []),
    )
    await worker._process(999999)  # 不应抛异常


# ---------------------------------------------------------------------------
# run_loop / 恢复 / 失败
# ---------------------------------------------------------------------------
async def test_run_loop_processes_enqueued_essay(session_factory) -> None:
    essay_id = await create_essay(session_factory)
    primary = FakeEngine("primary", [EngineResult(text=LONG_TEXT, self_confidence=1.0)])
    worker = make_worker(session_factory, primary, FakeEngine("review", []))

    worker.start()
    await worker.enqueue(essay_id)
    await worker.join()
    await worker.stop()

    essay = await load_essay(session_factory, essay_id)
    assert essay.status == "review"
    assert worker.processed == 1


async def test_run_loop_marks_failed_when_primary_fails(session_factory) -> None:
    essay_id = await create_essay(session_factory)
    primary = FakeEngine(
        "primary", [OcrEngineError("引擎返回错误状态码 400", status_code=400, retryable=False)]
    )
    worker = make_worker(session_factory, primary, FakeEngine("review", []))

    worker.start()
    await worker.enqueue(essay_id)
    await worker.join()
    await worker.stop()

    essay = await load_essay(session_factory, essay_id)
    task = await load_task(session_factory, essay_id)
    assert essay.status == "failed"
    assert task.error is not None
    assert worker.failures == 1


async def test_recover_pending_reenqueues_unfinished(session_factory) -> None:
    essay_id = await create_essay(session_factory, step="engine1", status="recognizing")
    primary = FakeEngine("primary", [EngineResult(text=LONG_TEXT, self_confidence=1.0)])
    worker = make_worker(session_factory, primary, FakeEngine("review", []))

    worker.start()
    recovered = await worker.recover_pending()
    await worker.join()
    await worker.stop()

    assert recovered == 1
    essay = await load_essay(session_factory, essay_id)
    assert essay.status == "review"


def test_build_worker_from_settings(settings, session_factory) -> None:
    worker = build_worker(settings, session_factory)
    assert worker is not None
    assert worker._primary.base_url == "https://primary.example/v1"
    assert worker._review.timeout_s == 120.0


# ---------------------------------------------------------------------------
# diff 单测
# ---------------------------------------------------------------------------
def test_char_diff_replace() -> None:
    segments = DiffService.char_diff("今天很好", "今天很糟")
    assert [segment.type for segment in segments] == ["equal", "replace"]
    assert segments[0].text_a == "今天很"
    assert segments[1].text_a == "好"
    assert segments[1].text_b == "糟"


def test_char_diff_delete_and_insert() -> None:
    deleted = DiffService.char_diff("abc", "ac")
    assert "delete" in [segment.type for segment in deleted]
    inserted = DiffService.char_diff("ac", "abc")
    assert "insert" in [segment.type for segment in inserted]


def test_disagreement_rate() -> None:
    assert DiffService.disagreement_rate(DiffService.char_diff("今天很好", "今天很糟")) == 0.25
    assert DiffService.disagreement_rate(DiffService.char_diff("相同", "相同")) == 0.0
    assert DiffService.disagreement_rate([]) == 0.0


def test_diff_json_roundtrip_and_invalid() -> None:
    segments = DiffService.char_diff("甲乙丙", "甲乙丁")
    raw = DiffService.to_json(segments)
    restored = DiffService.from_json(raw)
    assert restored == [segment.to_dict() for segment in segments]
    assert DiffService.from_json(None) is None
    assert DiffService.from_json("not json") is None
    assert DiffService.from_json('{"a":1}') is None
    assert DiffService.from_json('[{"type":"bogus"}]') == []


# ---------------------------------------------------------------------------
# 置信度单测
# ---------------------------------------------------------------------------
def test_confidence_empty_text_is_zero() -> None:
    scorer = ConfidenceScorer()
    assert scorer.score_photo(PhotoSample(text="")) == 0.0
    assert scorer.score_photo(PhotoSample(text="   ")) == 0.0


def test_confidence_short_text_penalty() -> None:
    scorer = ConfidenceScorer()
    assert scorer.score_photo(PhotoSample(text="短")) == 0.8


def test_confidence_long_text_full() -> None:
    scorer = ConfidenceScorer()
    assert scorer.score_photo(PhotoSample(text=LONG_TEXT)) == 1.0


def test_confidence_abnormal_ratio_penalty() -> None:
    scorer = ConfidenceScorer()
    text = "\ufffd" * 3 + "正常内容" * 10
    assert scorer.score_photo(PhotoSample(text=text)) == 0.75


def test_confidence_unconfirmed_marker_penalty() -> None:
    scorer = ConfidenceScorer()
    text = "这是一篇足够长的作文，但有一处无法确认【?】的字。"
    assert scorer.score_photo(PhotoSample(text=text)) == 0.9
    assert scorer.unconfirmed_marker_count(text) == 1


def test_confidence_essay_uses_min() -> None:
    scorer = ConfidenceScorer()
    overall = scorer.score_essay(
        [PhotoSample(text=LONG_TEXT, self_confidence=1.0), PhotoSample(text="短", self_confidence=1.0)]
    )
    assert overall == 0.8
    assert scorer.is_low(overall) is True
    assert scorer.is_low(0.9) is False


def test_confidence_essay_empty_is_zero() -> None:
    scorer = ConfidenceScorer()
    assert scorer.score_essay([]) == 0.0


def test_confidence_accepts_orm_like_object() -> None:
    scorer = ConfidenceScorer()
    photo = Photo(essay_id=1, seq=1, file_path="p", engine1_text=LONG_TEXT)
    assert scorer.score_essay([photo]) == 1.0
