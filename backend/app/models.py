"""ORM 模型：students / issues / essays / photos / recognition_tasks。

设计要点（严格对齐 docs/architecture.md 第 3 节）：
* 所有时间字段以 ISO 8601 UTC 字符串存储，展示层再转本地时区。
* ``photos.engine1_text`` / ``engine2_text`` / ``diff_json`` 为**不可变审计数据**，
  落库后只读；老师编辑只会写入 ``essays.final_text``。
* 二/三期预留字段（``teacher_comment`` / ``score`` / ``selected``）一期不写入。
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串（秒精度，带 +00:00 偏移）。"""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""


class Student(Base):
    """学生（一期由 deploy/seed_students.py 从 CSV 导入）。"""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)

    essays: Mapped[list["Essay"]] = relationship(back_populates="student")

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Student(id={self.id!r}, student_no={self.student_no!r}, name={self.name!r})"


class Issue(Base):
    """期数：一期一册。"""

    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_no: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    week_start_date: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)

    essays: Mapped[list["Essay"]] = relationship(
        back_populates="issue", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Issue(id={self.id!r}, issue_no={self.issue_no!r})"


class Essay(Base):
    """一篇作文：状态机载体，串起照片与识别任务。"""

    __tablename__ = "essays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    final_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # uploaded -> recognizing -> review -> proofread | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    low_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 二/三期预留（一期不写入）
    teacher_comment: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)
    proofread_at: Mapped[str | None] = mapped_column(String(40), nullable=True, default=None)

    issue: Mapped["Issue"] = relationship(back_populates="essays", lazy="selectin")
    student: Mapped["Student"] = relationship(back_populates="essays", lazy="selectin")
    photos: Mapped[list["Photo"]] = relationship(
        back_populates="essay",
        lazy="selectin",
        order_by="Photo.seq",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list["RecognitionTask"]] = relationship(
        back_populates="essay", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Essay(id={self.id!r}, status={self.status!r})"


class Photo(Base):
    """一张原片及其双引擎识别结果（审计数据，落库只读）。"""

    __tablename__ = "photos"
    __table_args__ = (UniqueConstraint("essay_id", "seq", name="uq_photo_essay_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    essay_id: Mapped[int] = mapped_column(ForeignKey("essays.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    engine1_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    engine2_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    diff_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)

    essay: Mapped["Essay"] = relationship(back_populates="photos")

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Photo(id={self.id!r}, essay_id={self.essay_id!r}, seq={self.seq!r})"


class RecognitionTask(Base):
    """识别任务状态机：queued -> engine1 -> judge -> engine2 -> diff -> done。

    复核引擎失败时进入 ``engine2_failed`` 并记录 ``error``，随后照常落到 ``done``
    （降级不阻塞）。
    """

    __tablename__ = "recognition_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    essay_id: Mapped[int] = mapped_column(ForeignKey("essays.id"), nullable=False)
    step: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)

    essay: Mapped["Essay"] = relationship(back_populates="tasks")

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"RecognitionTask(id={self.id!r}, essay_id={self.essay_id!r}, step={self.step!r})"
