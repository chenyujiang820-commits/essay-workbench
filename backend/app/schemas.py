"""API 契约（Pydantic 模型）与统一响应信封、领域异常。

统一信封：成功 ``{code:0, data, message}``；失败 ``{code:非0, data:null, message}``。
HTTP 状态码语义：401 未授权、404 不存在、409 未校对不可成册、422 参数校验失败。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Essay, Issue, Photo, RecognitionTask
from app.pipeline.diff import DiffService, DiffType
from app.ranking import (
    DEFAULT_SELECTED_SUGGEST,
    DEFAULT_STAR_THRESHOLDS,
    MAX_COMMENT_LENGTH,
    MAX_SELECTED_PER_ISSUE,
    MAX_STARS,
    SCORE_MAX,
    SCORE_MIN,
    stars_from_score,
)

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
    student_no: str | None = None
    title: str = ""
    status: str = "uploaded"
    low_confidence: int = 0
    #: 原片张数。
    photo_count: int = 0
    #: 其中画质偏低（``low_resolution=1``）的张数。
    low_resolution_count: int = 0
    created_at: str
    proofread_at: str | None = None
    # -- v1.3（二期）：评语与表彰，列表就带上，看板才能不点开就标出"已评语/已评分" --
    #: 教师评语；``None``/空串都表示没有。
    teacher_comment: str | None = None
    #: 评分（0~100）；``None`` = 未评分，不进任何榜。
    score: float | None = None
    #: 星级（0~5，0=未评分）。**由后端算**，前端与模板只渲染：同一判据写两份迟早漂移。
    stars: int = 0
    #: 是否本期精选（1/0）。
    selected: int = 0


class EssayDetail(EssayOut):
    """作文详情：含定稿文字、照片识别结果与任务状态。"""

    final_text: str = ""
    photos: list[PhotoOut] = Field(default_factory=list)
    task: TaskOut | None = None


class EssayUpdate(BaseModel):
    """校对保存请求。

    v1.3 起可一并提交评语/评分/精选，都是**三态**（"不传"与"传空"是两回事）：

    * ``title`` / ``final_text``：不传即不改；传字符串即覆盖（空白正文仍 400）。
    * ``teacher_comment``：不传即不改；传空串即清空。
    * ``score``：不传即不改；**显式 null 即清空**（判据是字段是否出现，见路由注释）。
    * ``selected``：不传即不改；``0`` 即取消精选。

    **都不参与状态机**：只改评语绝不会把 ``review`` 推成 ``proofread``（PRD v1.3 §10）。
    """

    #: v1.3 起三态：**不传即不改**（只改评语/评分时不必回写整篇正文）；传字符串即覆盖，
    #: 传空串/纯空白仍按一期口径 400 —— 定稿位永远不允许被清空。
    final_text: str | None = Field(default=None, description="老师定稿文字；不传即不改")
    proofread: bool = Field(default=False, description="是否完成校对（铁律）")
    title: str | None = Field(default=None, max_length=200, description="作文标题；不传即不改")
    teacher_comment: str | None = Field(
        default=None, max_length=MAX_COMMENT_LENGTH, description="教师评语；不传即不改，传空串即清空"
    )
    score: float | None = Field(
        default=None,
        description=f"评分 {SCORE_MIN}~{SCORE_MAX}；不传即不改，显式 null 表示清空（星级一并归零）",
    )
    selected: Literal[0, 1] | None = Field(default=None, description="是否精选；不传即不改")

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        """标题去首尾空白；``null`` 表示"本次不改标题"，与"传空串清空标题"区分。"""
        return value.strip() if isinstance(value, str) else value

    @field_validator("teacher_comment", mode="before")
    @classmethod
    def normalize_comment(cls, value: object) -> object:
        """评语只去首尾空白，正文里的换行要保留（老师会分点写）。"""
        return value.strip() if isinstance(value, str) else value

    @field_validator("score", mode="before")
    @classmethod
    def check_score_range(cls, value: object) -> object:
        """评分必须在 0~100；越界是写错，不做钳制（静默改分比报错更糟）。"""
        if value is None:
            return None
        try:
            number = float(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"评分必须是 {SCORE_MIN}~{SCORE_MAX} 的数字") from exc
        if not SCORE_MIN <= number <= SCORE_MAX:
            raise ValueError(f"评分必须在 {SCORE_MIN}~{SCORE_MAX} 之间")
        return number


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

    ``is_draft`` 为真表示该篇尚未定稿、正文取的是识别初稿（仅投屏路径会置真）。

    ``comment`` 为二/三期评语位（FR-12 预留）：**必须在这里显式声明**，否则
    ``BookItemOut(**item)`` 会按 Pydantic 默认策略静默丢弃 ``build_items`` 透出的评语，
    投屏 JSON 与 PDF 就再也看不到它。
    """

    student_no: str = ""
    name: str = ""
    title: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    # ``is_draft`` 同样必须显式声明（见本类 docstring 里 comment 的教训）：不写就会被
    # Pydantic 静默丢弃，投屏再也分不清哪篇是识别初稿。
    is_draft: bool = False
    is_selected: bool = False
    comment: str = ""
    #: v1.3：评分与星级同上一条教训 —— 不在这里显式声明就会被 Pydantic 静默丢弃，
    #: PDF 模板里的星级徽标就永远出不来。``stars`` 由 ``build_items`` 按当前阈值算好。
    score: float | None = None
    stars: int = 0
    #: v1.3：个人文集要在每篇标出来自哪一期（同一份模板要服务整册/档案/家长三种 mode）。
    issue_no: int | None = None


class PresentOut(BaseModel):
    """投屏数据：把该期**有文字的**作文渲染成逐篇结构（未定稿的用识别初稿并标 ``is_draft``）。"""

    class_name: str
    issue_no: int
    week_start_date: str
    generated_at: str
    items: list[BookItemOut] = Field(default_factory=list)
    # 既没定稿也没识别文字的篇数（例如识别失败）：不进轮播，但要在界面上说清楚，
    # 否则老师又会以为「篇目丢了」（GAP-14 的同一类误解）。
    excluded_no_text: int = 0
    # 其中未定稿（正文取的是识别初稿）的篇数：投屏页据此提示「黑板上讲的是初稿」。
    draft_count: int = 0


class ExportRequest(BaseModel):
    """整册导出请求。"""

    template: str = Field(default="elegant", description="模板 key")
    order: str = Field(default="student_no", description="student_no | name | score | selected_score")


# ---------------------------------------------------------------------------
# v1.3（二期）：精选 / 三榜 / 成长档案 / 家长分享
# ---------------------------------------------------------------------------
class SelectionUpdate(BaseModel):
    """整期精选设置（**覆盖式**，PRD v1.3 Q28）。

    一次提交整个集合，而不是逐篇开关：逐篇会产生"勾到第 6 篇才发现超限"的中间态，
    覆盖式则要么整单成功、要么整单 400 且数据库一行都不动。
    """

    essay_ids: list[int] = Field(default_factory=list, description="本期精选的作文 id")


class SelectionOut(BaseModel):
    """精选设置结果（回读最终态，前端据此重绘，不做乐观更新）。"""

    issue_id: int
    selected_ids: list[int] = Field(default_factory=list)
    limit: int = MAX_SELECTED_PER_ISSUE
    suggested: int = DEFAULT_SELECTED_SUGGEST
    #: v1.3：勾完精选顺手回读"本期还差几篇没评分"，省一次 ranking 往返，
    #: 也避免两次请求之间数据被别人改动后界面自相矛盾。
    scored_count: int = 0
    unscored_count: int = 0


class WorkRow(BaseModel):
    """佳作榜一行（绝对水平）。"""

    rank: int
    essay_id: int
    student_id: int
    student_no: str = ""
    name: str = ""
    title: str = ""
    score: float
    stars: int = 0
    selected: bool = False


class ProgressRow(BaseModel):
    """进步榜一行（与上一有分期比较的增量）。"""

    rank: int
    essay_id: int
    student_id: int
    student_no: str = ""
    name: str = ""
    title: str = ""
    score: float
    stars: int = 0
    previous_score: float
    previous_issue_no: int
    delta: float


class StarRow(BaseModel):
    """星级榜一行：**刻意不含 rank 与 score**（弱化名次，PRD v1.3 Q27）。"""

    student_id: int
    student_no: str = ""
    name: str = ""
    title: str = ""
    stars: int = 0


class BoardConfigOut(BaseModel):
    """单榜配置（来自 ``app.yaml`` 的 ranking 段）。"""

    enabled: bool = True
    visibility: Literal["teacher", "public"] = "teacher"


class RankingOut(BaseModel):
    """``GET /api/issues/{id}/ranking``：三榜 + 其配置。

    ``enabled=false`` 的榜返回空数组并把 key 记进 ``disabled`` —— 界面据此显示"已关闭"
    而不是"这周没人上榜"（两者对老师的含义完全不同）。
    """

    issue_id: int
    issue_no: int
    class_name: str = ""
    generated_at: str = ""
    thresholds: list[int] = Field(default_factory=list)
    max_stars: int = MAX_STARS
    config: dict[str, BoardConfigOut] = Field(default_factory=dict)
    work: list[WorkRow] = Field(default_factory=list)
    progress: list[ProgressRow] = Field(default_factory=list)
    star: list[StarRow] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    scored_count: int = 0
    unscored_count: int = 0


class PortfolioEntry(BaseModel):
    """成长档案的一条（一篇已定稿作文）。"""

    essay_id: int
    issue_id: int
    issue_no: int
    week_start_date: str = ""
    title: str = ""
    score: float | None = None
    stars: int = 0
    selected: int = 0
    teacher_comment: str | None = None
    proofread_at: str | None = None
    photo_count: int = 0


class PortfolioStats(BaseModel):
    """档案统计（四项，全部可由 entries/榜单现算，不落列）。"""

    essay_count: int = 0
    selected_count: int = 0
    honoured_count: int = 0
    top_stars: int = 0
    avg_score: float | None = None
    issue_count: int = 0
    last_issue_no: int | None = None


class PortfolioOut(BaseModel):
    """``GET /api/students/{id}/portfolio``：单个学生的作文成长档案。"""

    student: StudentOut
    stats: PortfolioStats = Field(default_factory=PortfolioStats)
    entries: list[PortfolioEntry] = Field(default_factory=list)
    class_name: str = ""
    generated_at: str = ""


class ShareCreate(BaseModel):
    """建家长分享链接。"""

    days: int = Field(default=14, ge=1, le=90, description="有效期天数")
    student_id: int | None = Field(default=None, description="留空 = 整期；非空 = 单生专属")
    label: str = Field(default="", max_length=100, description="老师备注，如「四年级2班家长群」")

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ShareOut(BaseModel):
    """分享链接列表项 / 创建结果。"""

    token: str
    url: str = Field(default="", description="站内相对路径 /share/{token}（域名由前端按 window.location 拼）")
    issue_id: int
    issue_no: int
    student_id: int | None = None
    student_name: str | None = None
    scope: Literal["issue", "student"] = "issue"
    created_at: str = ""
    expires_at: str = ""
    revoked: int = 0
    label: str = ""
    essay_count: int = 0


class ShareViewOut(BaseModel):
    """家长只读视图（免鉴权）。

    与成册**同源**：``items`` 走同一个 ``build_items``，家长看到的正文与 PDF 一致；
    版式却各走各的（家长页移动端单列，PDF 是 A4）。见 PRD v1.3 Q30。
    """

    class_name: str = ""
    issue_no: int
    week_start_date: str = ""
    generated_at: str = ""
    expires_at: str = ""
    scope: Literal["issue", "student"] = "issue"
    student_name: str | None = None
    items: list[BookItemOut] = Field(default_factory=list)


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


def essay_to_out(essay: Essay, thresholds: Sequence[int] = DEFAULT_STAR_THRESHOLDS) -> EssayOut:
    """映射作文列表项。

    ``thresholds`` 由调用方从 ``app.yaml`` 解析后传入（本模块不读配置：schemas 是契约层，
    读配置会把它和启动顺序绑死），缺省默认值保证既有调用点与单测不受影响。
    """
    photo_count, low_resolution_count = _photo_counts(essay)
    return EssayOut(
        id=essay.id,
        issue_id=essay.issue_id,
        student_id=essay.student_id,
        student_name=essay.student.name if essay.student is not None else None,
        student_no=essay.student.student_no if essay.student is not None else None,
        title=essay.title,
        status=essay.status,
        low_confidence=essay.low_confidence,
        photo_count=photo_count,
        low_resolution_count=low_resolution_count,
        created_at=essay.created_at,
        proofread_at=essay.proofread_at,
        teacher_comment=essay.teacher_comment,
        score=essay.score,
        stars=stars_from_score(essay.score, thresholds),
        selected=int(essay.selected or 0),
    )


def essay_to_detail(
    essay: Essay, thresholds: Sequence[int] = DEFAULT_STAR_THRESHOLDS
) -> EssayDetail:
    """映射作文详情（照片按 seq 升序）。"""
    photos = [photo_to_out(photo) for photo in sorted(essay.photos, key=lambda p: p.seq)]
    task = essay.tasks[0] if essay.tasks else None
    return EssayDetail(
        id=essay.id,
        issue_id=essay.issue_id,
        student_id=essay.student_id,
        student_name=essay.student.name if essay.student is not None else None,
        student_no=essay.student.student_no if essay.student is not None else None,
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
        stars=stars_from_score(essay.score, thresholds),
        selected=int(essay.selected or 0),
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
