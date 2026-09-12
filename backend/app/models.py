"""ORM 模型：students / issues / essays / photos / recognition_tasks。

设计要点（严格对齐 docs/architecture.md 第 3 节）：
* 所有时间字段以 ISO 8601 UTC 字符串存储，展示层再转本地时区。
* ``photos.engine1_text`` / ``engine2_text`` / ``diff_json`` 为**审计数据**：
  识别落库后只读（老师编辑只写 ``essays.final_text``）；``POST /api/essays/{id}/recognize``
  重跑会整体重写这三列（旧文本随重跑丢弃，不做增量合并）。
* ``essays.title`` 一期生效：识别 Worker 抽取正文首行自动写入（仅当老师未填），
  老师在 ``PATCH /api/essays/{id}`` 传 ``title`` 时以其为准（人工优先）。
* ``teacher_comment`` / ``score`` / ``selected`` 三列一期预留不写入；v1.2 起成册/投屏按
  当前值渲染评语位与徽标，**v1.3（二期）开放写入**（口径见 ``app/ranking.py``）。
  三列都**不参与状态机**：写评语/评分/精选不会把 review 推成 proofread。
* v1.3 新增表 ``share_links``（家长查看的只读链接）：令牌即能力凭证，明文入库是刻意的
  （理由见该模型 docstring 与 PRD v1.3 §5.1）。
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
    """一张原片及其双引擎识别结果。

    ``engine1_text`` / ``engine2_text`` / ``diff_json`` **识别落库后只读**，
    重跑（``POST /api/essays/{id}/recognize``）会整体重写这三列。
    """

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
# ---------------------------------------------------------------------------
class ShareLink(Base):
    """家长查看链接（v1.3 / FR-07）：带随机令牌、可过期、可撤销的只读入口。

    设计要点：
    * ``token`` 为 32 字节 ``secrets.token_urlsafe``，**主键即令牌**，无需额外索引；
    * 令牌明文入库是刻意的：它是"持有即授权"的能力凭证（capability URL），而数据库本身
      已按 0600 落在备份加密链路内。哈希化只会让老师丢失链接后无法在界面上复查"我发过
      哪几条"，安全性却没有实质提升（令牌不是从口令派生的，无从猜起）；
    * ``student_id`` 为空 = 整期链接；非空 = 单生专属链接（只回该生稿件）；
    * 外键 ``ondelete=CASCADE``：删期数时链接随之消失，不留指向已删数据的死链；
    * 撤销用 ``revoked`` 标记而非删行 —— 删了就无法解释"这条链接为什么突然打不开"。
    """

    __tablename__ = "share_links"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=True, default=None
    )
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utcnow_iso)
    revoked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        # 刻意不回显 token：日志里出现完整令牌等于把链接外泄到日志文件。
        return f"ShareLink(token=<hidden>, issue_id={self.issue_id!r}, revoked={self.revoked!r})"
