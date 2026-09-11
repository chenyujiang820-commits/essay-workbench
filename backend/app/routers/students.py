"""学生名单与管理：``/api/students``。

* GET    ``/api/students``            列出启用学生（按学号升序）。
* POST   ``/api/students``            新增学生（学号唯一，重复 409）。
* PATCH  ``/api/students/{id}``       改名 / 改学号。
* DELETE ``/api/students/{id}``       停用（active=0，保留历史作文关联）。
* POST   ``/api/students/import``     批量导入：学号已存在视为改名更新。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_session
from app.models import Student, utcnow_iso
from app.schemas import (
    ApiError,
    Envelope,
    StudentCreate,
    StudentImportRequest,
    StudentOut,
    StudentUpdate,
    envelope,
)

router = APIRouter(prefix="/api/students", tags=["students"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


@router.get("", response_model=Envelope[list[StudentOut]])
async def list_students(session: SessionDep, _auth: AuthDep) -> dict[str, Any]:
    """列出启用中的学生（按学号升序）。"""
    stmt = select(Student).where(Student.active == 1).order_by(Student.student_no.asc())
    students = (await session.execute(stmt)).scalars().all()
    return envelope([StudentOut.model_validate(student) for student in students])


async def _ensure_no_taken(session: AsyncSession, student_no: str, exclude_id: int | None) -> None:
    """学号唯一性校验（exclude_id 供更新时排除自身）。"""
    stmt = select(Student).where(Student.student_no == student_no)
    if exclude_id is not None:
        stmt = stmt.where(Student.id != exclude_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise ApiError(f"学号 {student_no} 已存在", code=409, status_code=409)


@router.post("", status_code=201, response_model=Envelope[StudentOut])
async def create_student(
    payload: StudentCreate, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """新增一名学生。"""
    await _ensure_no_taken(session, payload.student_no, exclude_id=None)
    student = Student(
        student_no=payload.student_no,
        name=payload.name,
        active=1,
        created_at=utcnow_iso(),
    )
    session.add(student)
    await session.commit()
    await session.refresh(student)
    return envelope(StudentOut.model_validate(student))


@router.patch("/{student_id}", response_model=Envelope[StudentOut])
async def update_student(
    student_id: int, payload: StudentUpdate, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """更新学生姓名 / 学号。"""
    student = await session.get(Student, student_id)
    if student is None or student.active != 1:
        raise ApiError("学生不存在", code=404, status_code=404)

    if payload.student_no is not None and payload.student_no != student.student_no:
        await _ensure_no_taken(session, payload.student_no, exclude_id=student.id)
        student.student_no = payload.student_no
    if payload.name is not None:
        student.name = payload.name

    await session.commit()
    await session.refresh(student)
    return envelope(StudentOut.model_validate(student))


@router.delete("/{student_id}", response_model=Envelope[dict[str, Any]])
async def deactivate_student(
    student_id: int, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """停用学生（active=0，保留历史作文关联，不从名单删除）。"""
    student = await session.get(Student, student_id)
    if student is None:
        raise ApiError("学生不存在", code=404, status_code=404)

    student.active = 0
    await session.commit()
    return envelope({"id": student_id}, message="学生已停用")


@router.post("/import", response_model=Envelope[dict[str, Any]])
async def import_students(
    payload: StudentImportRequest, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """批量导入学生：学号已存在则更新姓名，否则新增。

    Raises:
        ApiError: 400 学号在本次导入清单内重复。
    """
    seen: set[str] = set()
    for item in payload.students:
        if item.student_no in seen:
            raise ApiError(f"导入清单内学号 {item.student_no} 重复", code=400, status_code=400)
        seen.add(item.student_no)

    existing = {
        s.student_no: s
        for s in (
            await session.execute(
                select(Student).where(Student.student_no.in_(seen))  # noqa: S608
            )
        ).scalars().all()
    }

    created = updated = 0
    for item in payload.students:
        current = existing.get(item.student_no)
        if current is not None:
            if current.active != 1 or current.name != item.name:
                current.name = item.name
                current.active = 1  # 重新导入即恢复启用
                updated += 1
        else:
            session.add(
                Student(
                    student_no=item.student_no,
                    name=item.name,
                    active=1,
                    created_at=utcnow_iso(),
                )
            )
            created += 1

    await session.commit()
    return envelope({"created": created, "updated": updated})
