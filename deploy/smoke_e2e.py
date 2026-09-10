#!/usr/bin/env python
"""真实引擎端到端冒烟（一期收尾）：种子名单 → 建期 → 上传原片 → 真实 OCR → 校对定稿 → 成册导出。

本脚本是**手动**冒烟：只有在这里才会真实调用 ``engines.yaml`` 中配置的识别引擎；
自动化测试（pytest）始终使用 mock，绝不触网。

执行链路
    1. 准备隔离数据目录（``backend/data/smoke``，gitignore 覆盖）并复制真实 engines.yaml；
    2. ``deploy/seed_students.py`` 导入 5 名学生；
    3. 以真实配置启动 uvicorn（绑定 127.0.0.1，随机高位端口）；
    4. 登录 → 建期 → 上传 5 张真实手写原片（1 篇 1 张）→ 轮询状态机至 review/failed；
    5. 校对 PATCH 定稿（以引擎转写文本作为老师定稿文本）；
    6. 导出三套模板 PDF（elegant / playful / formal）→ ``artifacts/``；
    7. 落盘 ``artifacts/smoke_report.json``（含每步耗时、识别质量观察、降级情况）。

安全约束：脚本只从数据目录的 engines.yaml 读配置，**任何日志/报告都不输出 api_key**。

用法（务必用后端 venv 的 python 运行）::

    cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/smoke_e2e.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
DEPLOY = REPO / "deploy"
ARTIFACTS = REPO / "artifacts"

# 真实 engines.yaml 所在（含 API Key，已被 .gitignore 覆盖，绝不入库）。
SOURCE_ENGINES = BACKEND / "data" / "config" / "engines.yaml"

# 冒烟隔离数据目录（位于已被 gitignore 的 backend/data/ 之下）。
SMOKE_DATA_DIR = BACKEND / "data" / "smoke"

# 默认真实手写样本图目录与文件名（可用 --images-dir / --images 覆盖）。
DEFAULT_IMAGES_DIR = Path(
    os.environ.get("EWB_SMOKE_IMAGES_DIR", r"C:\Users\15050\.workbuddy\clipboard-images")
)
DEFAULT_IMAGE_NAMES = [
    "clipboard-2026-09-10T08-58-01-067Z-0818f656.png",
    "clipboard-2026-09-10T08-58-01-077Z-e3231fed.jpg",
    "clipboard-2026-09-10T08-58-01-082Z-0e3ea3f7.png",
    "clipboard-2026-09-10T08-58-01-090Z-da1fc96f.jpg",
    "clipboard-2026-09-10T08-58-01-094Z-edde4d46.jpg",
]

TEMPLATES = ("elegant", "playful", "formal")
DEFAULT_PASSWORD = "admin123"
POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 360.0


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """当前 UTC 时间（秒精度 ISO 8601）。"""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _free_port() -> int:
    """向系统借一个空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _mime_for(path: Path) -> str:
    """按扩展名推断图片 MIME（上传 multipart 用）。"""
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(path.suffix.lower(), "application/octet-stream")


class Recorder:
    """记录每步耗时与观察，最终产出报告字典。"""

    def __init__(self) -> None:
        self.started = _now_iso()
        self._t0 = time.perf_counter()
        self.steps: list[dict[str, Any]] = []
        self.notes: list[str] = []

    def step(self, name: str, detail: str, seconds: float) -> None:
        """记录一步。"""
        self.steps.append({"step": name, "detail": detail, "seconds": round(seconds, 2)})
        print(f"[step] {name} :: {detail} :: {seconds:.2f}s", flush=True)

    def note(self, text: str) -> None:
        """记录一条观察/结论。"""
        self.notes.append(text)
        print(f"[note] {text}", flush=True)

    def elapsed(self) -> float:
        """脚本累计执行秒数。"""
        return round(time.perf_counter() - self._t0, 2)


# ---------------------------------------------------------------------------
# 准备数据目录与种子
# ---------------------------------------------------------------------------
def prepare_data_dir(images: list[Path]) -> None:
    """重建隔离数据目录，复制真实 engines.yaml，并写入班级名。"""
    if not SOURCE_ENGINES.exists():
        raise SystemExit(
            f"未找到真实引擎配置：{SOURCE_ENGINES}\n"
            "请先按 README 将真实 base_url/api_key/model 写入该文件。"
        )
    if SMOKE_DATA_DIR.exists():
        shutil.rmtree(SMOKE_DATA_DIR, ignore_errors=True)
    (SMOKE_DATA_DIR / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_ENGINES, SMOKE_DATA_DIR / "config" / "engines.yaml")

    # 写入班级名（成册封面/投屏抬头用），app.yaml 交由应用首次启动自动生成。
    app_yaml = SMOKE_DATA_DIR / "config" / "app.yaml"
    with app_yaml.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {"class_name": "高一（2）班", "auth": {"password_hash": "", "token_secret": ""}},
            handle,
            allow_unicode=True,
            sort_keys=False,
        )

    missing = [str(p) for p in images if not p.exists()]
    if missing:
        raise SystemExit("以下样本图不存在：\n  " + "\n  ".join(missing))


def read_engine_summary() -> dict[str, Any]:
    """从 engines.yaml 提取**不含密钥**的引擎摘要（model/timeout/retries/阈值）。"""
    data = yaml.safe_load(SOURCE_ENGINES.read_text(encoding="utf-8")) or {}
    summary: dict[str, Any] = {"low_confidence_threshold": data.get("low_confidence_threshold")}
    for section in ("primary", "review"):
        block = data.get(section) if isinstance(data.get(section), dict) else {}
        summary[section] = {
            "model": block.get("model"),
            "timeout": block.get("timeout"),
            "retries": block.get("retries"),
        }
    return summary


def run_seed(settings_env: dict[str, str]) -> str:
    """运行种子脚本导入学生名单，返回其 stdout。"""
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "seed_students.py"), str(DEPLOY / "students.sample.csv")],
        cwd=str(BACKEND),
        env=settings_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"种子脚本失败：\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 服务器生命周期
# ---------------------------------------------------------------------------
def start_server(settings_env: dict[str, str], port: int) -> tuple[subprocess.Popen[bytes], Any]:
    """启动 uvicorn 子进程，返回 (进程, 日志文件句柄)。"""
    log_path = ARTIFACTS / "smoke-server.log"
    log_handle = log_path.open("w", encoding="utf-8")
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
            "info",
        ],
        cwd=str(BACKEND),
        env=settings_env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return proc, log_handle


def wait_health(base_url: str, timeout_s: float = 30.0) -> None:
    """轮询 /api/health 直到就绪。"""
    deadline = time.perf_counter() + timeout_s
    last_error = ""
    while time.perf_counter() < deadline:
        try:
            response = httpx.get(f"{base_url}/api/health", timeout=3.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise SystemExit(f"服务未在 {timeout_s}s 内就绪：{last_error}")


# ---------------------------------------------------------------------------
# API 步骤
# ---------------------------------------------------------------------------
def login(client: httpx.Client, password: str) -> dict[str, str]:
    """登录并返回鉴权头。"""
    response = client.post("/api/auth/login", json={"password": password})
    response.raise_for_status()
    token = response.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


def upload_essay(
    client: httpx.Client, issue_id: int, student_id: int, image: Path
) -> dict[str, Any]:
    """上传一篇作文（单图）。"""
    content = image.read_bytes()
    files = [("files", (image.name, content, _mime_for(image)))]
    response = client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=files,
    )
    response.raise_for_status()
    return response.json()["data"]


def wait_until_finished(client: httpx.Client, essay_id: int) -> dict[str, Any]:
    """轮询作文详情，直到状态离开 recognizing/uploaded（或超时）。"""
    deadline = time.perf_counter() + POLL_TIMEOUT_S
    seen_steps: list[str] = []
    last: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        response = client.get(f"/api/essays/{essay_id}")
        response.raise_for_status()
        detail = response.json()["data"]
        last = detail
        step = (detail.get("task") or {}).get("step")
        if step and (not seen_steps or seen_steps[-1] != step):
            seen_steps.append(str(step))
        if detail["status"] not in {"uploaded", "recognizing"}:
            detail["_seen_steps"] = seen_steps
            return detail
        time.sleep(POLL_INTERVAL_S)
    last["_seen_steps"] = seen_steps
    last["_timeout"] = True
    return last


def proofread(client: httpx.Client, essay_id: int, final_text: str) -> dict[str, Any]:
    """提交校对定稿。"""
    response = client.patch(
        f"/api/essays/{essay_id}", json={"final_text": final_text, "proofread": True}
    )
    response.raise_for_status()
    return response.json()["data"]


def export_book(client: httpx.Client, issue_id: int, template: str) -> bytes:
    """导出整册 PDF，返回字节。"""
    response = client.post(
        f"/api/exports/{issue_id}", json={"template": template, "order": "student_no"}
    )
    response.raise_for_status()
    return response.content


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="真实引擎端到端冒烟")
    parser.add_argument("--images-dir", default=str(DEFAULT_IMAGES_DIR), help="样本图目录")
    parser.add_argument("--images", nargs="*", default=None, help="样本图文件名（覆盖默认 5 张）")
    parser.add_argument("--port", type=int, default=0, help="服务端口（0=自动）")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="登录口令")
    args = parser.parse_args(argv)

    images_dir = Path(args.images_dir)
    names = args.images if args.images else DEFAULT_IMAGE_NAMES
    images = [images_dir / name for name in names]

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    recorder = Recorder()

    t0 = time.perf_counter()
    prepare_data_dir(images)
    recorder.step("prepare", f"隔离数据目录 {SMOKE_DATA_DIR}", time.perf_counter() - t0)

    env = dict(os.environ)
    env["EWB_DATA_DIR"] = str(SMOKE_DATA_DIR)
    env["EWB_WORKER_ENABLED"] = "true"
    env["PYTHONPATH"] = ""

    t0 = time.perf_counter()
    seed_out = run_seed(env)
    recorder.step("seed", seed_out, time.perf_counter() - t0)

    port = args.port or _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc, log_handle = start_server(env, port)
    try:
        t0 = time.perf_counter()
        wait_health(base_url)
        recorder.step("serve", f"uvicorn 就绪 @ {base_url}", time.perf_counter() - t0)

        with httpx.Client(base_url=base_url, timeout=180.0) as client:
            t0 = time.perf_counter()
            headers = login(client, args.password)
            client.headers.update(headers)
            recorder.step("login", "登录取 token", time.perf_counter() - t0)

            t0 = time.perf_counter()
            students = client.get("/api/students").json()["data"]
            recorder.step("students", f"启用学生 {len(students)} 人", time.perf_counter() - t0)

            t0 = time.perf_counter()
            issue = client.post(
                "/api/issues", json={"issue_no": 1, "week_start_date": "2026-09-07"}
            ).json()["data"]
            recorder.step("issue", f"建期 #{issue['issue_no']} (id={issue['id']})", time.perf_counter() - t0)

            # 逐篇上传（1 篇 1 张），并等待真实识别完成。
            essay_reports: list[dict[str, Any]] = []
            for index, image in enumerate(images):
                student = students[index % len(students)]
                t0 = time.perf_counter()
                up = upload_essay(client, issue["id"], student["id"], image)
                upload_s = time.perf_counter() - t0

                t1 = time.perf_counter()
                detail = wait_until_finished(client, up["essay_id"])
                ocr_s = time.perf_counter() - t1

                photos = detail.get("photos", [])
                ocr_chars = [len(p.get("engine1_text") or "") for p in photos]
                photo_sizes = [[p.get("width"), p.get("height")] for p in photos]
                task = detail.get("task") or {}
                seen = detail.get("_seen_steps", [])
                degraded = "engine2_failed" in seen or task.get("step") == "engine2_failed"

                essay_reports.append(
                    {
                        "essay_id": detail.get("id", up["essay_id"]),
                        "student_no": student["student_no"],
                        "name": student["name"],
                        "image": image.name,
                        "status": detail.get("status"),
                        "low_confidence": detail.get("low_confidence"),
                        "photo_count": len(photos),
                        "photo_sizes": photo_sizes,
                        "ocr_chars": ocr_chars,
                        "task_step": task.get("step"),
                        "task_error": task.get("error"),
                        "degraded": degraded,
                        "upload_s": round(upload_s, 2),
                        "ocr_s": round(ocr_s, 2),
                        "timed_out": bool(detail.get("_timeout")),
                    }
                )
                print(
                    f"[ocr] #{detail.get('id')} {student['name']} status={detail.get('status')} "
                    f"low={detail.get('low_confidence')} chars={ocr_chars} "
                    f"size={photo_sizes} ocr={ocr_s:.1f}s",
                    flush=True,
                )

            recorder.step(
                "recognize",
                f"{len(essay_reports)} 篇完成真实识别",
                sum(e["ocr_s"] for e in essay_reports),
            )

            # 校对定稿：以引擎转写文本作为老师定稿文本。
            t0 = time.perf_counter()
            for essay in essay_reports:
                detail = client.get(f"/api/essays/{essay['essay_id']}").json()["data"]
                text = "\n".join(
                    (p.get("engine1_text") or "").strip()
                    for p in detail.get("photos", [])
                    if (p.get("engine1_text") or "").strip()
                )
                if not text.strip():
                    text = "（识别为空，需人工补录）"
                    recorder.note(f"作文 {essay['essay_id']} 识别为空，已用占位文本定稿")
                proofread(client, essay["essay_id"], text)
                essay["final_chars"] = len(text)
            recorder.step("proofread", f"{len(essay_reports)} 篇定稿", time.perf_counter() - t0)

            # 导出三套模板 PDF。
            exports: list[dict[str, Any]] = []
            for template in TEMPLATES:
                t0 = time.perf_counter()
                data = export_book(client, issue["id"], template)
                out_name = f"样例作文集-{template}.pdf"
                (ARTIFACTS / out_name).write_bytes(data)
                exports.append(
                    {
                        "template": template,
                        "filename": out_name,
                        "bytes": len(data),
                        "seconds": round(time.perf_counter() - t0, 2),
                    }
                )
                print(f"[export] {template} -> {out_name} ({len(data)} bytes)", flush=True)
            recorder.step("export", "导出三套模板 PDF", sum(e["seconds"] for e in exports))

            # 投屏数据（供截图脚本与验收记录参考）。
            present = client.get(f"/api/exports/{issue['id']}/present").json()["data"]
            (ARTIFACTS / "present-data.json").write_text(
                json.dumps(present, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            first_essay_id = essay_reports[0]["essay_id"] if essay_reports else None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - 兜底强杀
            proc.kill()
        log_handle.close()

    report = {
        "started_at": recorder.started,
        "finished_at": _now_iso(),
        "total_seconds": recorder.elapsed(),
        "engine": read_engine_summary(),
        "data_dir": str(SMOKE_DATA_DIR),
        "issue": issue,
        "student_count": len(students),
        "essays": essay_reports,
        "exports": exports,
        "degraded": any(e["degraded"] for e in essay_reports),
        "steps": recorder.steps,
        "notes": recorder.notes,
        "for_screenshots": {
            "base_url": base_url,
            "issue_id": issue["id"],
            "first_essay_id": first_essay_id,
        },
    }
    (ARTIFACTS / "smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"\n总耗时 {report['total_seconds']}s；报告已写入 {ARTIFACTS / 'smoke_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
