#!/usr/bin/env python
"""学生名单初始化脚本：读 CSV（学号,姓名）写入 ``students`` 表（幂等 upsert）。

一期不做界面管理，名单由本脚本导入。重复执行只更新变化的姓名/启用状态，不产生重复行。

用法（注意：请用 shim 兼容方式运行，详见 README 常见问题）::

    # Windows (Git Bash)
    cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/seed_students.py ../deploy/students.sample.csv
    # Linux / macOS
    cd backend && .venv/bin/python ../deploy/seed_students.py ../deploy/students.sample.csv

数据目录由环境变量 ``EWB_DATA_DIR`` 指定（默认相对 backend 的 ``./data``）。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

# 让脚本无论从何处运行都能 import app.*（backend 为 deploy 的姊妹目录）。
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.config import AppSettings  # noqa: E402
from app.db import create_engine, create_session_factory, init_db  # noqa: E402
from app.models import Student, utcnow_iso  # noqa: E402
from sqlalchemy import select  # noqa: E402

_HEADER_KEYS = {"student_no", "学号", "no", "id"}


def parse_rows(csv_path: Path) -> list[tuple[str, str]]:
    """解析 CSV，返回去空的 ``(学号, 姓名)`` 列表（自动跳过表头与空行）。"""
    rows: list[tuple[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.reader(handle):
            cells = [cell.strip() for cell in raw]
            if len(cells) < 2:
                continue
            student_no, name = cells[0], cells[1]
            if not student_no or not name:
                continue
            if student_no.lower() in _HEADER_KEYS:
                continue
            rows.append((student_no, name))
    return rows


async def seed(csv_path: Path) -> tuple[int, int]:
    """把 CSV 中的学生 upsert 进数据库，返回 ``(新增数, 更新数)``。"""
    rows = parse_rows(csv_path)

    settings = AppSettings()
    settings.ensure_directories()
    engine = create_engine(settings)
    await init_db(engine)
    factory = create_session_factory(engine)

    created = 0
    updated = 0
    async with factory() as session:
        for student_no, name in rows:
            existing = (
                (await session.execute(select(Student).where(Student.student_no == student_no)))
                .scalars()
                .first()
            )
            if existing is None:
                session.add(
                    Student(student_no=student_no, name=name, active=1, created_at=utcnow_iso())
                )
                created += 1
            elif existing.name != name or existing.active != 1:
                existing.name = name
                existing.active = 1
                updated += 1
        await session.commit()

    await engine.dispose()
    return created, updated


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="从 CSV 导入学生名单（学号,姓名）")
    parser.add_argument(
        "csv",
        nargs="?",
        default=str(_ROOT / "deploy" / "students.sample.csv"),
        help="CSV 路径（默认 deploy/students.sample.csv）",
    )
    args = parser.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV 不存在：{csv_path}", file=sys.stderr)
        return 1

    created, updated = asyncio.run(seed(csv_path))
    print(
        f"完成：新增 {created} 人，更新 {updated} 人；"
        f"数据目录 = {AppSettings().data_dir}（DB = {AppSettings().db_path}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
