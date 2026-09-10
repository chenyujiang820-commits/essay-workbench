"""学生名单（只读）：``GET /api/students``。

一期不做学生管理界面，名单由 ``deploy/seed_students.py`` 从 CSV 导入；
此处仅提供只读列表，供上传页选择学生。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_session
from app.models import Student
from app.schemas import Envelope, StudentOut, envelope

router = APIRouter(prefix="/api/students", tags=["students"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


@router.get("", response_model=Envelope[list[StudentOut]])
async def list_students(session: SessionDep, _auth: AuthDep) -> dict[str, Any]:
    """列出启用中的学生（按学号升序）。"""
    stmt = select(Student).where(Student.active == 1).order_by(Student.student_no.asc())
    students = (await session.execute(stmt)).scalars().all()
    return envelope([StudentOut.model_validate(student) for student in students])
