"""API 契约（Pydantic 模型）与统一响应信封、领域异常。

统一信封：成功 ``{code:0, data, message}``；失败 ``{code:非0, data:null, message}``。
HTTP 状态码语义：401 未授权、404 不存在、409 未校对不可成册、422 参数校验失败。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Essay, Issue, Photo, RecognitionTask
from app.pipeline.diff import DiffService, DiffType

T = TypeVar("T")


# ---------------------------------------------------------------------------
# 枚举/类型别名
# ---------------------------------------------------------------------------
EssayStatus = Literal["uploaded", "recognizing", "review", "proofread", "failed"]
TaskStep = Literal["queued", "engine1", "judge", "engine2", "diff", "done", "engine2_failed"]


# ---------------------------------------------------------------------------
# 信封与异常
# ---------------------------------------------------------------------------
class Envelope(BaseModel, Generic[T]):
    """统一响应信封。"""

    code: int = 0
    data: T | None = None
    message: str = ""


class ApiError(Exception):
    """领域异常：由全局异常处理器转为统一错误信封。"""

    def __init__(self, message: str, *, code: int = 1, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def envelope(data: Any, message: str = "") -> dict[str, Any]:
    """构造成功信封字典（交由 response_model 校验/序列化）。"""
    return {"code": 0, "data": data, "message": message}


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    """登录请求。"""

    password: str = Field(min_length=1, description="教师口令")


class TokenOut(BaseModel):
    """登录成功返回的令牌。"""

    token: str
    token_type: str = "bearer"
    expires_in: int


# ---------------------------------------------------------------------------
# 学生
# ---------------------------------------------------------------------------
class StudentOut(BaseModel):
    """学生信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    student_no: str
    name: str
    active: int = 1


class StudentCreate(BaseModel):
    """新增学生请求。"""

    student_no: str = Field(min_length=1, max_length=50, description="学号（唯一）")
    name: str = Field(min_length=1, max_length=100, description="姓名")

    @field_validator("student_no", "name", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class StudentUpdate(BaseModel):
    """更新学生请求（字段可选）。"""

    student_no: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("student_no", "name", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class StudentImportItem(BaseModel):
    """批量导入的单条学生。"""

    student_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("student_no", "name", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class StudentImportRequest(BaseModel):
    """批量导入请求。"""

    students: list[StudentImportItem] = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# 期数
# ---------------------------------------------------------------------------
class IssueCreate(BaseModel):
    """新建期数请求。"""

    issue_no: int = Field(ge=1, description="期号")
    week_start_date: str = Field(min_length=1, description="周一日期（ISO 8601）")

    @field_validator("week_start_date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("日期必须为 YYYY-MM-DD 格式")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期不是有效的日历日期") from exc
        return value


class IssueUpdate(BaseModel):
    """更新期数请求（字段可选）。"""

    issue_no: int | None = Field(default=None, ge=1)
    week_start_date: str | None = None

    @field_validator("week_start_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("日期必须为 YYYY-MM-DD 格式")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期不是有效的日历日期") from exc
        return value


class IssueOut(BaseModel):
    """期数信息。"""

    id: int
    issue_no: int
    week_start_date: str
    created_at: str
    essay_count: int = 0


# ---------------------------------------------------------------------------
# 照片 / diff / 任务
# ---------------------------------------------------------------------------
class DiffSegment(BaseModel):
    """diff 段落（API 形态）。"""

    type: Literal["equal", "replace", "delete", "insert"]
    text_a: str = ""
    text_b: str = ""


class PhotoOut(BaseModel):
    """照片及其识别结果。

    ``engine1_text`` / ``engine2_text`` / ``diff_json`` 识别落库后只读，重跑会整体重写。
    """

    id: int
    seq: int
    file_path: str
    width: int | None = None
    height: int | None = None
    #: 画质低于门槛（短边<600 或长边<800）时为 1；尺寸无法解析时为 0。
    low_resolution: int = 0
    engine1_text: str | None = None
    engine2_text: str | None = None
    diff_json: list[DiffSegment] | None = None


class TaskOut(BaseModel):
    """识别任务状态。"""

    id: int
    step: str
    retry_count: int = 0
    error: str | None = None
    updated_at: str


# ---------------------------------------------------------------------------
# 作文
# ---------------------------------------------------------------------------
class EssayOut(BaseModel):
    """作文列表项（看板用，含画质汇总，免逐篇点开）。"""

    id: int
    issue_id: int
    student_id: int
    student_name: str | None = None
    title: str = ""
    status: str = "uploaded"
    low_confidence: int = 0
    #: 原片张数。
    photo_count: int = 0
    #: 其中画质偏低（``low_resolution=1``）的张数。
    low_resolution_count: int = 0
    created_at: str
    proofread_at: str | None = None


class EssayDetail(EssayOut):
    """作文详情：含定稿文字、照片识别结果与任务状态。"""

    final_text: str = ""
    teacher_comment: str | None = None
    score: float | None = None
    selected: int = 0
    photos: list[PhotoOut] = Field(default_factory=list)
    task: TaskOut | None = None


class EssayUpdate(BaseModel):
    """校对保存请求。"""

    final_text: str = Field(default="", description="老师定稿文字")
    proofread: bool = Field(default=False, description="是否完成校对（铁律）")
    title: str | None = Field(default=None, max_length=200, description="作文标题；不传即不改")

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        """标题去首尾空白；``null`` 表示"本次不改标题"，与"传空串清空标题"区分。"""
        return value.strip() if isinstance(value, str) else value


class UploadResult(BaseModel):
    """上传受理结果（202）。"""

    essay_id: int
    status: str
    photo_count: int
    task: TaskOut | None = None


class RecognizeRerunOut(BaseModel):
    """重跑识别受理结果（202）。"""

    essay_id: int
    status: str
    task: TaskOut | None = None


# ---------------------------------------------------------------------------
# 运维元信息（FR-13）
# ---------------------------------------------------------------------------
class MetaOut(BaseModel):
    """``GET /api/meta``：免鉴权，仅暴露班级名与版本供前端顶栏展示。"""

    class_name: str
    version: str


class HealthOut(BaseModel):
    """``GET /api/health``：健康探针（systemd / 外部拨测用）。"""

    status: Literal["ok", "degraded"] = "ok"
    version: str = ""
    db_ok: bool = True
    pending_tasks: int | None = None


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
class TemplateInfo(BaseModel):
    """成册模板清单项。"""

    key: str
    name: str
    description: str


class BookItemOut(BaseModel):
    """成册/投屏共用条目。

    ``comment`` 为二/三期评语位（FR-12 预留）：**必须在这里显式声明**，否则
    ``BookItemOut(**item)`` 会按 Pydantic 默认策略静默丢弃 ``build_items`` 透出的评语，
    投屏 JSON 与 PDF 就再也看不到它。
    """

    student_no: str = ""
    name: str = ""
    title: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    is_selected: bool = False
    comment: str = ""


class PresentOut(BaseModel):
    """投屏数据：把该期定稿作文渲染成逐篇结构。"""

    class_name: str
    issue_no: int
    week_start_date: str
    generated_at: str
    items: list[BookItemOut] = Field(default_factory=list)


class ExportRequest(BaseModel):
    """整册导出请求。"""

    template: str = Field(default="elegant", description="模板 key")
    order: str = Field(default="student_no", description="student_no | name")


# ---------------------------------------------------------------------------
# ORM -> Schema 映射
# ---------------------------------------------------------------------------
def task_to_out(task: RecognitionTask | None) -> TaskOut | None:
    """映射识别任务。"""
    if task is None:
        return None
    return TaskOut(
        id=task.id,
        step=task.step,
        retry_count=task.retry_count,
        error=task.error,
        updated_at=task.updated_at,
    )


def is_low_resolution_flag(width: int | None, height: int | None) -> int:
    """画质门槛判定（返回 0/1，与库内 int 风格一致）。

    在函数内延迟导入 ``app.images``：images 依赖本模块的 ``ApiError``，模块级互相
    导入会形成 schemas <-> images 循环。
    """
    from app.images import is_low_resolution

    return 1 if is_low_resolution(width, height) else 0


def photo_to_out(photo: Photo) -> PhotoOut:
    """映射照片（diff_json 反序列化为段落列表）。"""
    raw_segments = DiffService.from_json(photo.diff_json)
    segments = (
        [
            DiffSegment(
                type=cast(DiffType, item["type"]),
                text_a=item.get("text_a", ""),
                text_b=item.get("text_b", ""),
            )
            for item in raw_segments
        ]
        if raw_segments is not None
        else None
    )
    return PhotoOut(
        id=photo.id,
        seq=photo.seq,
        file_path=photo.file_path,
        width=photo.width,
        height=photo.height,
        low_resolution=is_low_resolution_flag(photo.width, photo.height),
        engine1_text=photo.engine1_text,
        engine2_text=photo.engine2_text,
        diff_json=segments,
    )


def _photo_counts(essay: Essay) -> tuple[int, int]:
    """统计 (原片张数, 其中低画质张数)。

    依赖 ``Essay.photos`` 的 ``lazy="selectin"``：列表查询已把照片随主查询载入，
    不会在异步上下文中触发隐式懒加载。
    """
    photos = list(essay.photos or [])
    low_count = sum(
        1 for photo in photos if is_low_resolution_flag(photo.width, photo.height)
    )
    return len(photos), low_count


def essay_to_out(essay: Essay) -> EssayOut:
    """映射作文列表项。"""
    photo_count, low_resolution_count = _photo_counts(essay)
    return EssayOut(
        id=essay.id,
        issue_id=essay.issue_id,
        student_id=essay.student_id,
        student_name=essay.student.name if essay.student is not None else None,
        title=essay.title,
        status=essay.status,
        low_confidence=essay.low_confidence,
        photo_count=photo_count,
        low_resolution_count=low_resolution_count,
        created_at=essay.created_at,
        proofread_at=essay.proofread_at,
    )


def essay_to_detail(essay: Essay) -> EssayDetail:
    """映射作文详情（照片按 seq 升序）。"""
    photos = [photo_to_out(photo) for photo in sorted(essay.photos, key=lambda p: p.seq)]
    task = essay.tasks[0] if essay.tasks else None
    return EssayDetail(
        id=essay.id,
        issue_id=essay.issue_id,
        student_id=essay.student_id,
        student_name=essay.student.name if essay.student is not None else None,
        title=essay.title,
        status=essay.status,
        low_confidence=essay.low_confidence,
        photo_count=len(photos),
        low_resolution_count=sum(1 for photo in photos if photo.low_resolution),
        created_at=essay.created_at,
        proofread_at=essay.proofread_at,
        final_text=essay.final_text,
        teacher_comment=essay.teacher_comment,
        score=essay.score,
        selected=essay.selected,
        photos=photos,
        task=task_to_out(task),
    )


def issue_to_out(issue: Issue, essay_count: int = 0) -> IssueOut:
    """映射期数。"""
    return IssueOut(
        id=issue.id,
        issue_no=issue.issue_no,
        week_start_date=issue.week_start_date,
        created_at=issue.created_at,
        essay_count=essay_count,
    )
