"""进程内异步识别 Worker：队列消费 + 状态机 + 降级。

状态机：
* ``essay.status`` ∈ {uploaded, recognizing, review, proofread, failed}
* ``task.step``   ∈ {queued, engine1, judge, engine2, diff, done, engine2_failed}

关键行为：
* 逐张串行：主引擎(engine1) -> 置信度判定(judge) -> 低置信才调复核(engine2) -> diff。
* **复核引擎失败（超时/504）必须降级**：记录 ``engine2_failed`` + ``error``，
  不抛异常、不阻塞，照常进入 ``review``。
* 进程重启后 ``recover_pending()`` 从 DB 恢复未完成任务；已落库的 ``engine1_text``
  会被复用（不重复计费）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import AppSettings
from app.models import Essay, Photo, RecognitionTask, utcnow_iso
from app.pipeline.confidence import ConfidenceScorer, PhotoSample
from app.pipeline.diff import DiffService
from app.pipeline.engine_adapter import EngineResult, OcrEngineAdapter, OcrEngineError

logger = logging.getLogger(__name__)

TERMINAL_STEPS = frozenset({"done"})

ImageLoader = Callable[[Photo], "tuple[bytes, str]"]


class RecognitionWorker:
    """单进程 asyncio Worker：串行消费 essay 识别任务。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        primary: OcrEngineAdapter | None,
        review: OcrEngineAdapter | None,
        scorer: ConfidenceScorer,
        *,
        settings: AppSettings | None = None,
        image_loader: ImageLoader | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._primary = primary
        self._review = review
        self._scorer = scorer
        self._settings = settings
        self._image_loader = image_loader

        self._queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._loop_task: asyncio.Task[None] | None = None
        self._running = False

        self.processed = 0
        self.failures = 0

    # -- 生命周期 -----------------------------------------------------------
    @property
    def running(self) -> bool:
        """Worker 是否在运行。"""
        return self._running

    def start(self) -> None:
        """启动消费循环（幂等）。"""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self.run_loop(), name="recognition-worker")

    async def stop(self) -> None:
        """停止消费循环（投放哨兵并等待退出）。"""
        if not self._running:
            return
        self._running = False
        await self._queue.put(None)
        if self._loop_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

    async def enqueue(self, essay_id: int) -> None:
        """把作文加入识别队列。"""
        await self._queue.put(essay_id)

    async def join(self) -> None:
        """等待队列内所有任务处理完毕（测试与关停用）。"""
        await self._queue.join()

    # -- 主循环 -------------------------------------------------------------
    async def run_loop(self) -> None:
        """消费循环：取任务 -> 处理 -> 记录成败。"""
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                try:
                    await self._process(item)
                    self.processed += 1
                except Exception as exc:  # noqa: BLE001 - 单篇失败不可拖垮循环
                    self.failures += 1
                    logger.exception("作文 %s 识别失败：%s", item, exc)
                    await self._mark_failed(item, exc)
            finally:
                self._queue.task_done()

    async def recover_pending(self) -> int:
        """从 DB 恢复未完成任务并入队，返回恢复数量。"""
        async with self._session_factory() as session:
            stmt = (
                select(RecognitionTask.essay_id)
                .join(Essay, Essay.id == RecognitionTask.essay_id)
                .where(RecognitionTask.step.notin_(list(TERMINAL_STEPS)))
                .where(Essay.status != "proofread")
            )
            rows = (await session.execute(stmt)).scalars().all()

        for essay_id in rows:
            await self.enqueue(int(essay_id))
        return len(rows)

    # -- 单篇处理 -----------------------------------------------------------
    async def _process(self, essay_id: int) -> None:
        """处理单篇作文：逐张串行识别 -> 判定 -> 复核 -> diff -> review。"""
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
                await session.flush()

            overall = self._scorer.score_essay(samples)
            essay.low_confidence = 1 if self._scorer.is_low(overall) else 0
            essay.status = "review"
            await self._set_step(session, task, "done")
            await session.commit()

            logger.info(
                "作文 %s 识别完成：%s 张，低置信=%s",
                essay_id,
                len(photos),
                essay.low_confidence,
            )

    async def _stage_engine1(
        self, session: AsyncSession, task: RecognitionTask, photo: Photo
    ) -> PhotoSample:
        """主引擎识别单张（已落库则复用，避免重复调用）。"""
        if photo.engine1_text is not None:
            return PhotoSample(text=photo.engine1_text, self_confidence=1.0)

        if self._primary is None:
            raise OcrEngineError("主识别引擎未配置", retryable=False)

        image_bytes, mime = self._load_image(photo)
        result: EngineResult = await self._primary.recognize(image_bytes, mime)
        photo.engine1_text = result.text
        await session.flush()
        return PhotoSample(text=result.text, self_confidence=result.self_confidence)

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
            await session.flush()
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
        """字符级 diff：主引擎文本 vs 复核文本，落库为不可变审计数据。"""
        segments = DiffService.char_diff(photo.engine1_text or "", review_text)
        photo.diff_json = DiffService.to_json(segments)
        await self._set_step(session, task, "diff")
        await session.flush()

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
        task.step = step
        task.updated_at = utcnow_iso()
        await session.flush()

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
) -> RecognitionWorker:
    """从应用配置构造 Worker（适配器可注入，便于测试）。"""
    if primary is None or review is None:
        from app.pipeline.engine_adapter import AdapterFactory

        config: dict[str, Any] = settings.engines_config()
        primary = primary or AdapterFactory.from_config(config, "primary")
        review = review or AdapterFactory.from_config(config, "review")
    scorer = ConfidenceScorer(settings.low_confidence_threshold())
    return RecognitionWorker(
        session_factory, primary, review, scorer, settings=settings
    )
