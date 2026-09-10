#!/usr/bin/env python
"""用 Playwright 无头 Chromium 截取投屏视图与校对页（一期收尾交付物）。

两种用法：

1) 连接已在运行的服务（需自行提供 token）::

     cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/capture_screenshots.py \
         --base-url http://127.0.0.1:8077 --token <token> --issue-id 1 --essay-id 1

2) ``--serve``：脚本自行用指定数据目录拉起 uvicorn、登录、截图、再关停（推荐）::

     cd backend && PYTHONPATH= .venv/Scripts/python.exe ../deploy/capture_screenshots.py \
         --serve --data-dir "<绝对数据目录>" --password admin123 --issue-id 1 --essay-id 1

前置：前端 ``frontend/dist`` 已构建（后端静态托管 SPA）。

产出（写入 ``artifacts/``）：
* ``投屏视图-1920x1080.png``  讲评投屏（PresentPage）横版全屏
* ``校对页-1920x1080.png``    校对环（ProofreadPage）桌面左右分栏
* ``校对页-375x812.png``      校对环手机竖版
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
ARTIFACTS = REPO / "artifacts"
TOKEN_KEY = "ewb_token"


# ---------------------------------------------------------------------------
# 服务/登录辅助（仅 --serve 模式使用）
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """向系统借一个空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(base_url: str, timeout_s: float = 30.0) -> None:
    """轮询 /api/health 直到就绪。"""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        try:
            if httpx.get(f"{base_url}/api/health", timeout=3.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"服务未在 {timeout_s}s 内就绪：{base_url}")


def _login(base_url: str, password: str) -> str:
    """登录取 token。"""
    response = httpx.post(f"{base_url}/api/auth/login", json={"password": password}, timeout=15.0)
    response.raise_for_status()
    return str(response.json()["data"]["token"])


def _start_server(data_dir: Path, port: int) -> tuple[subprocess.Popen[bytes], object]:
    """以指定数据目录启动 uvicorn。"""
    env = dict(os.environ)
    env["EWB_DATA_DIR"] = str(data_dir)
    env["EWB_WORKER_ENABLED"] = "true"
    env["PYTHONPATH"] = ""
    log_handle = (ARTIFACTS / "screenshots-server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(BACKEND), env=env, stdout=log_handle, stderr=subprocess.STDOUT,
    )
    return proc, log_handle


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------
def _token_init_script(token: str) -> str:
    """在页面脚本执行前把登录 token 注入 localStorage。"""
    return f"try {{ window.localStorage.setItem({TOKEN_KEY!r}, {token!r}); }} catch (e) {{}}"


def capture(
    base_url: str, token: str, issue_id: int, essay_id: int, out_dir: Path
) -> list[dict[str, object]]:
    """依次截取三张图，返回产物清单。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        # 1) 投屏视图：1920x1080 全屏
        desktop = browser.new_context(viewport={"width": 1920, "height": 1080})
        desktop.add_init_script(_token_init_script(token))
        page = desktop.new_page()
        page.goto(f"{base_url}/present/{issue_id}", wait_until="networkidle")
        page.wait_for_selector('[data-testid="present-slide"]', timeout=15000)
        page.wait_for_timeout(800)
        target = out_dir / "投屏视图-1920x1080.png"
        page.screenshot(path=str(target))
        produced.append({"filename": target.name, "bytes": target.stat().st_size})
        print(f"[shot] {target.name} ({target.stat().st_size} bytes)", flush=True)

        # 2) 校对页：桌面左右分栏
        page.goto(f"{base_url}/essays/{essay_id}/proofread", wait_until="networkidle")
        page.get_by_role("button", name="保存并定稿").wait_for(timeout=15000)
        page.wait_for_timeout(1800)  # 等原片 objectURL 加载完成
        target = out_dir / "校对页-1920x1080.png"
        page.screenshot(path=str(target))
        produced.append({"filename": target.name, "bytes": target.stat().st_size})
        print(f"[shot] {target.name} ({target.stat().st_size} bytes)", flush=True)
        desktop.close()

        # 3) 校对页：手机竖版（响应式堆叠）
        mobile = browser.new_context(viewport={"width": 375, "height": 812})
        mobile.add_init_script(_token_init_script(token))
        phone = mobile.new_page()
        phone.goto(f"{base_url}/essays/{essay_id}/proofread", wait_until="networkidle")
        phone.get_by_role("button", name="保存并定稿").wait_for(timeout=15000)
        phone.wait_for_timeout(1800)
        target = out_dir / "校对页-375x812.png"
        phone.screenshot(path=str(target))
        produced.append({"filename": target.name, "bytes": target.stat().st_size})
        print(f"[shot] {target.name} ({target.stat().st_size} bytes)", flush=True)
        mobile.close()

        browser.close()

    return produced


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="截取投屏视图与校对页")
    parser.add_argument("--base-url", help="服务基址，如 http://127.0.0.1:8077")
    parser.add_argument("--token", help="登录 token（非 --serve 模式必填）")
    parser.add_argument("--issue-id", type=int, required=True, help="期数 id")
    parser.add_argument("--essay-id", type=int, required=True, help="用于校对页截图的作文 id")
    parser.add_argument("--out-dir", default=str(ARTIFACTS), help="产物输出目录")
    parser.add_argument("--serve", action="store_true", help="自行拉起后端服务后再截图")
    parser.add_argument("--data-dir", help="数据目录（--serve 模式必填）")
    parser.add_argument("--password", default="admin123", help="登录口令（--serve 模式）")
    parser.add_argument("--port", type=int, default=0, help="服务端口（0=自动）")
    args = parser.parse_args(argv)

    proc: subprocess.Popen[bytes] | None = None
    log_handle: object | None = None
    base_url = args.base_url or ""
    token = args.token or ""

    try:
        if args.serve:
            if not args.data_dir:
                raise SystemExit("--serve 模式需要 --data-dir")
            port = args.port or _free_port()
            base_url = f"http://127.0.0.1:{port}"
            proc, log_handle = _start_server(Path(args.data_dir).resolve(), port)
            _wait_health(base_url)
            token = _login(base_url, args.password)

        if not base_url or not token:
            raise SystemExit("需要 --base-url 与 --token（或使用 --serve）")

        capture(base_url, token, args.issue_id, args.essay_id, Path(args.out_dir))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover - 兜底强杀
                proc.kill()
        if log_handle is not None:
            log_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
