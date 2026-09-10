"""API 契约（Pydantic 模型）与统一响应信封、领域异常。

统一信封：成功 ``{code:0, data, message}``；失败 ``{code:非0, data:null, message}``。
HTTP 状态码语义：401 未授权、404 不存在、409 未校对不可成册、422 参数校验失败。
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field

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


# ---------------------------------------------------------------------------
# 期数
# ---------------------------------------------------------------------------
class IssueCreate(BaseModel):
    """新建期数请求。"""

    issue_no: int = Field(ge=1, description="期号")
    week_start_date: str = Field(min_length=1, description="周一日期（ISO 8601）")


class IssueUpdate(BaseModel):
    """更新期数请求（字段可选）。"""

    issue_no: int | None = Field(default=None, ge=1)
    week_start_date: str | None = None


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
    """照片及其识别结果（engine 文本与 diff 为只读审计数据）。"""

    id: int
    seq: int
    file_path: str
    width: int | None = None
    height: int | None = None
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
    """作文列表项。"""

    id: int
    issue_id: int
    student_id: int
    student_name: str | None = None
    title: str = ""
    status: str = "uploaded"
    low_confidence: int = 0
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


class UploadResult(BaseModel):
    """上传受理结果（202）。"""

    essay_id: int
    status: str
    photo_count: int
    task: TaskOut | None = None


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
class TemplateInfo(BaseModel):
    """成册模板清单项。"""

    key: str
    name: str
    description: str


class BookItemOut(BaseModel):
    """成册/投屏共用条目。"""

    student_no: str = ""
    name: str = ""
    title: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    is_selected: bool = False


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
        engine1_text=photo.engine1_text,
        engine2_text=photo.engine2_text,
        diff_json=segments,
    )


def essay_to_out(essay: Essay) -> EssayOut:
    """映射作文列表项。"""
    return EssayOut(
        id=essay.id,
        issue_id=essay.issue_id,
        student_id=essay.student_id,
        student_name=essay.student.name if essay.student is not None else None,
        title=essay.title,
        status=essay.status,
        low_confidence=essay.low_confidence,
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
