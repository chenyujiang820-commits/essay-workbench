"""进程内异步识别 Worker：队列消费 + 状态机 + 降级。

状态机：
* ``essay.status`` ∈ {uploaded, recognizing, review, proofread, failed}
* ``task.step``   ∈ {queued, engine1, judge, engine2, diff, done, engine2_failed}

关键行为：
* **篇级有界并发**（FR-10）：``concurrency`` 个消费者协程共用一个 asyncio 队列，每个消费者
  处理一篇作文、且每篇独占一个 AsyncSession。张级仍按 ``seq`` 串行（seq 决定正文拼接顺序，
  张级并行会引入乱序写入风险）。并发度来自 engines.yaml 的 ``worker_concurrency``。
* 逐张串行：主引擎(engine1) -> 置信度判定(judge) -> 低置信才调复核(engine2) -> diff。
* **复核引擎失败（超时/504）必须降级**：记录 ``engine2_failed`` + ``error``，
  不抛异常、不阻塞，照常进入 ``review``。
* 识别完成时自动抽取候选标题（FR-11），**仅当 ``essay.title`` 为空时写入**，
  老师手工填写优先级永远更高。
* 进程重启后 ``recover_pending()`` 从 DB 恢复未完成任务；已落库的 ``engine1_text``
  会被复用（不重复计费）。

SQLite 写入纪律：每个阶段结束即 ``commit``（而非攒到整篇结束）。WAL 模式下写锁
按事务独占，若在事务内 ``await`` 引擎返回（数秒~数十秒），并发消费者会互相阻塞甚至
触发 ``busy_timeout``——篇级并发就退化成串行。分阶段短事务把写锁占用压到毫秒级，
同时让"崩在半路"的稿子留下可续跑的中间进度。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import AppSettings
from app.images import is_low_resolution
from app.models import Essay, Photo, RecognitionTask, utcnow_iso
from app.pipeline.confidence import ConfidenceScorer, PhotoSample
from app.pipeline.diff import DiffService
from app.pipeline.engine_adapter import EngineResult, OcrEngineAdapter, OcrEngineError
from app.pipeline.title import extract_title

logger = logging.getLogger(__name__)

TERMINAL_STEPS = frozenset({"done"})

#: 篇级并发默认值（与 AppSettings.worker_concurrency 默认一致）。
DEFAULT_CONCURRENCY = 4

ImageLoader = Callable[[Photo], "tuple[bytes, str]"]


class RecognitionWorker:
    """单进程 asyncio Worker：有界并发消费 essay 识别任务（篇内逐张串行）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        primary: OcrEngineAdapter | None,
        review: OcrEngineAdapter | None,
        scorer: ConfidenceScorer,
        *,
        settings: AppSettings | None = None,
        image_loader: ImageLoader | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._session_factory = session_factory
        self._primary = primary
        self._review = review
        self._scorer = scorer
        self._settings = settings
        self._image_loader = image_loader
        # 至少 1 个消费者，避免配置成 0 时队列无人消费。
        self._concurrency = max(1, int(concurrency))

        self._queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._consumer_tasks: list[asyncio.Task[None]] = []
        self._running = False

        self.processed = 0
        self.failures = 0

    # -- 生命周期 -----------------------------------------------------------
    @property
    def running(self) -> bool:
        """Worker 是否在运行。"""
        return self._running

    @property
    def concurrency(self) -> int:
        """当前篇级并发度。"""
        return self._concurrency

    def start(self) -> None:
        """启动消费者循环（幂等）：按并发度拉起固定数量的篇级处理协程。"""
        if self._running:
            return
        self._running = True
        self._consumer_tasks = [
            asyncio.create_task(self.run_loop(), name=f"recognition-worker-{index}")
            for index in range(self._concurrency)
        ]

    async def stop(self) -> None:
        """停止消费循环（每个消费者投放一个哨兵并等待全部退出）。"""
        if not self._running:
            return
        self._running = False
        tasks = self._consumer_tasks
        self._consumer_tasks = []
        for _ in tasks:
            await self._queue.put(None)
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def enqueue(self, essay_id: int) -> None:
        """把作文加入识别队列。"""
        await self._queue.put(essay_id)

    async def join(self) -> None:
        """等待队列内所有任务处理完毕（测试与关停用）。"""
        await self._queue.join()

    # -- 主循环 -------------------------------------------------------------
    async def run_loop(self) -> None:
        """单个消费者的取任务循环：取到 None 哨兵即退出。"""
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                await self._run_one(item)
            finally:
                self._queue.task_done()

    async def _run_one(self, essay_id: int) -> None:
        """处理一篇并把成败计入统计；单篇异常绝不允许拖垮消费者循环。"""
        try:
            await self._process(essay_id)
            self.processed += 1
        except Exception as exc:  # noqa: BLE001 - 篇级隔离
            self.failures += 1
            logger.exception("作文 %s 识别失败：%s", essay_id, exc)
            await self._mark_failed(essay_id, exc)

    async def recover_pending(self) -> int:
        """从 DB 恢复未完成任务并入队，返回恢复数量。"""
        async with self._session_factory() as session:
            stmt = (
                select(RecognitionTask.essay_id)
                .join(Essay, Essay.id == RecognitionTask.essay_id)
                .where(RecognitionTask.step.notin_(list(TERMINAL_STEPS)))
            )
            rows = (await session.execute(stmt)).all()
        for row in rows:
            await self.enqueue(int(row[0]))
        return len(rows)

    # -- 单篇处理 -----------------------------------------------------------
    async def _process(self, essay_id: int) -> None:
        """处理单篇作文：逐张串行识别 -> 判定 -> 复核 -> diff -> 抽标题 -> review。

        每篇独占一个 AsyncSession：篇级并发下不同 session 之间不共享 ORM 状态，
        因此不会产生交叉污染。
        """
        async with self._session_factory() as session:
            essay = await session.get(Essay, essay_id, options=[selectinload(Essay.photos)])
            if essay is None:
                logger.warning("作文 %s 不存在，跳过", essay_id)
                return
            if essay.status == "proofread":
                logger.info("作文 %s 已定稿，跳过识别", essay_id)
                return

            task = await self._get_or_create_task(session, essay_id)
            essay.status = "recognizing"
            await self._set_step(session, task, "engine1")

            samples: list[PhotoSample] = []
            photos = sorted(essay.photos, key=lambda photo: photo.seq)

            for photo in photos:
                sample = await self._stage_engine1(session, task, photo)
                samples.append(sample)

                await self._set_step(session, task, "judge")
                if self._scorer.is_low(self._scorer.score_photo(sample)) and self._review is not None:
                    await self._stage_engine2(session, task, photo)

            self._apply_auto_title(essay, samples)
            overall = self._scorer.score_essay(samples)
            essay.low_confidence = 1 if self._scorer.is_low(overall) else 0
            essay.status = "review"
            await self._set_step(session, task, "done")

            logger.info(
                "作文 %s 识别完成：%s 张，低置信=%s",
                essay_id,
                len(photos),
                essay.low_confidence,
            )

    @staticmethod
    def _apply_auto_title(essay: Essay, samples: Sequence[PhotoSample]) -> None:
        """从定稿候选文本抽首行写入标题——**仅在老师还没填时**（FR-11 人工优先）。"""
        if (essay.title or "").strip():
            return
        # 候选文本：各张主引擎结果按 seq 用空行拼接，与校对页看到的段落顺序一致。
        candidate = "\n\n".join(sample.text or "" for sample in samples)
        essay.title = extract_title(candidate)

    async def _stage_engine1(
        self, session: AsyncSession, task: RecognitionTask, photo: Photo
    ) -> PhotoSample:
        """主引擎识别单张（已落库则复用，避免重复调用）。"""
        low_resolution = is_low_resolution(photo.width, photo.height)
        if photo.engine1_text is not None:
            return PhotoSample(
                text=photo.engine1_text, self_confidence=1.0, low_resolution=low_resolution
            )

        if self._primary is None:
            raise OcrEngineError("主识别引擎未配置", retryable=False)

        image_bytes, mime = self._load_image(photo)
        result: EngineResult = await self._primary.recognize(image_bytes, mime)
        photo.engine1_text = result.text
        await session.commit()
        return PhotoSample(
            text=result.text,
            self_confidence=result.self_confidence,
            low_resolution=low_resolution,
        )

    async def _stage_engine2(
        self, session: AsyncSession, task: RecognitionTask, photo: Photo
    ) -> None:
        """复核引擎识别单张；失败则降级（不抛异常），成功后转入 ``_stage_diff``。"""
        if self._review is None:  # pragma: no cover - 由调用点保证
            return
        await self._set_step(session, task, "engine2")
        try:
            image_bytes, mime = self._load_image(photo)
            review_result = await self._review.recognize(image_bytes, mime)
        except OcrEngineError as exc:
            # 降级：记录 error，不阻塞，照常进入 review。
            await self._set_step(session, task, "engine2_failed")
            task.error = f"复核引擎失败，已降级（无 diff 标注）：{exc}"
            logger.warning("复核降级：essay=%s photo_seq=%s err=%s", task.essay_id, photo.seq, exc)
            await session.commit()
            return

        photo.engine2_text = review_result.text
        await self._stage_diff(session, task, photo, review_result.text)

    async def _stage_diff(
        self,
        session: AsyncSession,
        task: RecognitionTask,
        photo: Photo,
        review_text: str,
    ) -> None:
        """字符级 diff：主引擎文本 vs 复核文本，落库为审计数据（重跑会整体重写）。"""
        segments = DiffService.char_diff(photo.engine1_text or "", review_text)
        photo.diff_json = DiffService.to_json(segments)
        await self._set_step(session, task, "diff")

    # -- 辅助 ---------------------------------------------------------------
    def _load_image(self, photo: Photo) -> tuple[bytes, str]:
        """读取原片字节与 MIME。"""
        if self._image_loader is not None:
            return self._image_loader(photo)
        if self._settings is None:  # pragma: no cover - 构造保证
            raise OcrEngineError("Worker 未配置数据目录", retryable=False)
        path = self._settings.data_dir / photo.file_path
        return path.read_bytes(), self._mime_for(photo.file_path)

    @staticmethod
    def _mime_for(file_path: str) -> str:
        suffix = Path(file_path).suffix.lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".heic": "image/heic",
        }.get(suffix, "image/jpeg")

    @staticmethod
    async def _get_or_create_task(session: AsyncSession, essay_id: int) -> RecognitionTask:
        stmt = select(RecognitionTask).where(RecognitionTask.essay_id == essay_id)
        task = (await session.execute(stmt)).scalars().first()
        if task is None:
            task = RecognitionTask(
                essay_id=essay_id,
                step="queued",
                retry_count=0,
                error=None,
                updated_at=utcnow_iso(),
            )
            session.add(task)
            await session.flush()
        return task

    @staticmethod
    async def _set_step(session: AsyncSession, task: RecognitionTask, step: str) -> None:
        """推进任务状态并提交（短事务：不要在事务内 ``await`` 引擎）。"""
        task.step = step
        task.updated_at = utcnow_iso()
        await session.commit()

    async def _mark_failed(self, essay_id: int, exc: BaseException) -> None:
        """单篇彻底失败：置 essay.status=failed 并记录错误。"""
        try:
            async with self._session_factory() as session:
                essay = await session.get(Essay, essay_id)
                if essay is not None and essay.status != "proofread":
                    essay.status = "failed"
                task = await self._get_or_create_task(session, essay_id)
                task.step = "done"
                task.error = f"识别失败：{exc}"
                task.updated_at = utcnow_iso()
                await session.commit()
        except Exception:  # noqa: BLE001 - 记录失败本身不应再抛
            logger.exception("标记作文 %s 失败时出错", essay_id)


def build_worker(
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
    primary: OcrEngineAdapter | None = None,
    review: OcrEngineAdapter | None = None,
    *,
    concurrency: int | None = None,
) -> RecognitionWorker:
    """从应用配置构造 Worker（适配器/并发度可注入，便于测试）。

    Args:
        settings: 应用配置；提供数据目录与 engines.yaml 运行参数。
        session_factory: Session 工厂（Worker 每篇开一个独立 session）。
        primary: 主引擎适配器；缺省时按 engines.yaml 构造。
        review: 复核引擎适配器；缺省时按 engines.yaml 构造。
        concurrency: 篇级并发度；缺省取 ``settings.worker_concurrency()``（已钳制）。
    """
    if primary is None or review is None:
        from app.pipeline.engine_adapter import AdapterFactory

        config: dict[str, Any] = settings.engines_config()
        primary = primary or AdapterFactory.from_config(config, "primary")
        review = review or AdapterFactory.from_config(config, "review")
    scorer = ConfidenceScorer(
        settings.low_confidence_threshold(),
        low_resolution_penalty=settings.low_resolution_penalty(),
    )
    resolved_concurrency = settings.worker_concurrency() if concurrency is None else concurrency
    return RecognitionWorker(
        session_factory,
        primary,
        review,
        scorer,
        settings=settings,
        concurrency=resolved_concurrency,
    )
