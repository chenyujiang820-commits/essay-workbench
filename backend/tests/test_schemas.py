"""Pydantic 请求模型的输入规范测试。"""

from __future__ import annotations

import pytest
from app.schemas import IssueCreate, IssueUpdate, StudentCreate, StudentImportItem, StudentUpdate
from pydantic import ValidationError


def test_student_text_fields_are_trimmed_at_parse_time() -> None:
    assert StudentCreate(student_no="  S001  ", name="  张 三 ").model_dump() == {
        "student_no": "S001",
        "name": "张 三",
    }
    assert StudentUpdate(student_no=" S002 ", name=" 李 四 ").model_dump() == {
        "student_no": "S002",
        "name": "李 四",
    }
    assert StudentImportItem(student_no=" S003 ", name=" 王 五 ").model_dump() == {
        "student_no": "S003",
        "name": "王 五",
    }


@pytest.mark.parametrize("model", [StudentCreate, StudentImportItem])
def test_student_text_fields_reject_blank_after_trim(model: type[StudentCreate]) -> None:
    with pytest.raises(ValidationError):
        model(student_no="   ", name="有效姓名")


@pytest.mark.parametrize("value", ["2026-02-30", "2026-9-7", "2026/09/07", " 2026-09-07"])
def test_issue_dates_require_valid_iso_calendar_date(value: str) -> None:
    with pytest.raises(ValidationError):
        IssueCreate(issue_no=1, week_start_date=value)
    with pytest.raises(ValidationError):
        IssueUpdate(week_start_date=value)


def test_issue_date_accepts_any_valid_calendar_date() -> None:
    assert IssueCreate(issue_no=1, week_start_date="2026-09-11").week_start_date == "2026-09-11"
