#!/usr/bin/env python
"""整册 PDF 导出在真实班级规模下的计时（补齐「45 篇规模需复测」这条开放项）。

PRD 的验收写的是「全部定稿 → 导出 PDF ≤ 1 分钟」，一期只在 5 篇上量过（28.0s 出三套模板）。
45 篇是老师一个班的真实规模，此前**没有数据**。本脚本补齐它。

**不联网、不调引擎**：成册导出只读库里的定稿文本 + Playwright 渲染，正文用合成句池即可
复现老师点「导出」走过的同一条链路（HTTP -> 校对铁律 -> 模板渲染 -> 无头 Chromium -> PDF 字节）。

用法（隔离数据目录，跑完自动清理）::

    cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/bench_export.py

退出码：任一模板超过 ``--limit-seconds`` 即 1（可直接当门禁跑）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from app.auth import generate_secret, hash_password  # noqa: E402
from app.config import AppSettings  # noqa: E402
from app.db import create_engine, create_session_factory, init_db  # noqa: E402
from app.models import Essay, Issue, Student, utcnow_iso  # noqa: E402

#: 合成正文用的句池：长度与真实高中生作文同量级，句式重复不影响渲染计时。
SENTENCES: tuple[str, ...] = (
    "那天傍晚我独自走在回家的路上，风把梧桐叶翻得哗哗响。",
    "母亲在厨房里忙到很晚，灯影把她的背压得有些弯。",
    "我把卷子折了两折塞进书包，还是没能把它藏进自己的沉默里。",
    "老师没有点名，只是在我的作文本上画了一个很大的问号。",
    "后来我才明白，所谓长大，是把一句说不出口的话写成三段话。",
    "操场边的梧桐又黄了一回，我们在树影里排成不太整齐的一列。",
    "那天下雨，伞很小，两个人的肩膀都湿了半截。",
    "我把这句话抄在扉页上，每次翻开都被它绊一下。",
    "毕业那天我们没有说再见，只说了句有空一起吃饭。",
    "许多事情过去很久才看得懂，比如那一次没有回头的眼神。",
)

TITLES: tuple[str, ...] = (
    "那盏没关的灯",
    "梧桐又黄了一回",
    "伞很小",
    "说不出口的话",
    "被绊了一下的扉页",
)


def _free_port() -> int:
    """向系统借一个空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _essay_text(index: int) -> str:
    """生成一篇 400~1200 字的正文（段落数与真实稿件同量级）。"""
    paragraphs = 3 + index % 4
    out: list[str] = []
    for para in range(paragraphs):
        sentences = 2 + (index + para) % 4
        out.append(
            "　　"
            + "".join(
                SENTENCES[(index * 7 + para * 3 + k) % len(SENTENCES)] for k in range(sentences)
            )
        )
    return "\n\n".join(out)


async def _seed(data_dir: Path, students: int, password: str) -> int:
    """建隔离库：N 名学生、1 期、N 篇全部定稿的作文，返回期数 id。"""
    os.environ["EWB_DATA_DIR"] = str(data_dir)
    settings = AppSettings(data_dir=data_dir)
    settings.ensure_directories()
    (settings.config_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "class_name": "高一(2)班（导出压测）",
                "auth": {
                    "password_hash": hash_password(password),
                    "token_secret": generate_secret(),
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    engine = create_engine(settings)
    await init_db(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        issue = Issue(issue_no=1, week_start_date="2026-09-07", created_at=utcnow_iso())
        session.add(issue)
        await session.flush()
        for i in range(students):
            student = Student(
                student_no=f"2026{i:04d}",
                name=f"学生{i + 1:02d}",
                active=1,
                created_at=utcnow_iso(),
            )
            session.add(student)
            await session.flush()
            session.add(
                Essay(
                    issue_id=issue.id,
                    student_id=student.id,
                    title=TITLES[i % len(TITLES)],
                    final_text=_essay_text(i),
                    status="proofread",
                    low_confidence=0,
                    created_at=utcnow_iso(),
                    proofread_at=utcnow_iso(),
                )
            )
        await session.commit()
        issue_id = int(issue.id)
    await engine.dispose()
    return issue_id


def _wait_health(base_url: str, timeout_s: float = 60.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        try:
            if httpx.get(f"{base_url}/api/health", timeout=3.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"服务未在 {timeout_s}s 内就绪：{base_url}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="整册 PDF 导出计时（不联网、不调引擎）")
    parser.add_argument("--students", type=int, default=45, help="班级人数=篇数，默认 45")
    parser.add_argument("--password", default="bench-local-pass", help="仅本机压测用口令")
    parser.add_argument("--limit-seconds", type=float, default=60.0, help="单套模板的验收上限")
    parser.add_argument("--templates", default="elegant,formal,playful", help="逗号分隔的模板键")
    parser.add_argument("--data-dir", default=None, help="隔离数据目录（默认临时目录，跑完删除）")
    parser.add_argument("--keep", action="store_true", help="保留数据目录与 PDF 以便目视检查")
    args = parser.parse_args(argv)

    data_dir = (
        Path(args.data_dir)
        if args.data_dir
        else Path(os.environ.get("TEMP", ".")) / "ewb-bench-export"
    )
    if data_dir.exists():
        shutil.rmtree(data_dir)
    issue_id = asyncio.run(_seed(data_dir, args.students, args.password))

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env["EWB_DATA_DIR"] = str(data_dir)
    env["EWB_WORKER_ENABLED"] = "false"
    env["PYTHONPATH"] = ""
    log = (data_dir / "bench-server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    failures: list[str] = []
    try:
        _wait_health(base_url)
        login = httpx.post(
            f"{base_url}/api/auth/login", json={"password": args.password}, timeout=20.0
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + str(login.json()["data"]["token"])}
        print(f"[bench] {args.students} 篇全部定稿，issue={issue_id}，开始逐套模板计时", flush=True)
        for key in [item.strip() for item in args.templates.split(",") if item.strip()]:
            started = time.perf_counter()
            response = httpx.post(
                f"{base_url}/api/exports/{issue_id}",
                json={"template": key},
                headers=headers,
                timeout=600.0,
            )
            elapsed = time.perf_counter() - started
            ok = response.status_code == 200 and response.content[:5] == b"%PDF-"
            size_mb = len(response.content) / 1024 / 1024
            out = data_dir / "exports" / f"bench-{key}.pdf"
            out.write_bytes(response.content)
            flag = "OK " if ok else "FAIL"
            print(
                f"[bench] {flag} {key:8s} HTTP {response.status_code}  {elapsed:6.2f}s  "
                f"{size_mb:5.2f} MB -> {out.name}",
                flush=True,
            )
            if not ok:
                failures.append(f"{key} 未返回 PDF（HTTP {response.status_code}）")
            elif elapsed > args.limit_seconds:
                failures.append(f"{key} {elapsed:.1f}s 超过上限 {args.limit_seconds:.0f}s")
        listed = httpx.get(
            f"{base_url}/api/issues/{issue_id}/essays", headers=headers, timeout=30.0
        )
        listed.raise_for_status()
        first_id = listed.json()["data"][0]["id"]
        started = time.perf_counter()
        single = httpx.post(
            f"{base_url}/api/exports/{issue_id}/single/{first_id}",
            params={"template": "elegant"},
            headers=headers,
            timeout=300.0,
        )
        elapsed = time.perf_counter() - started
        ok = single.status_code == 200 and single.content[:5] == b"%PDF-"
        flag = "OK " if ok else "FAIL"
        print(
            f"[bench] {flag} single     HTTP {single.status_code}  {elapsed:6.2f}s  "
            f"{len(single.content) / 1024:6.0f} KB",
            flush=True,
        )
        if not ok:
            failures.append(f"单篇版式未返回 PDF（HTTP {single.status_code}）")
        elif elapsed > args.limit_seconds:
            failures.append(f"单篇版式 {elapsed:.1f}s 超过上限 {args.limit_seconds:.0f}s")
        return 1 if failures else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if not args.keep and not args.data_dir:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
