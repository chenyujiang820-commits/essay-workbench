"""手机端上传页探针（不调引擎、不点提交）。

真机拍照验证前先用它确认两件事：v1.2 rev.4 前端在 390x844 下能正常加载
（无 pageerror）；HEIC / 无扩展名这类后端不收的照片能在**选图当场**被挑出来
并给出可执行改法。只选文件不提交，因此不会触发识别引擎、不产生费用。

用法：cd backend && .venv/Scripts/python.exe ../deploy/probe_mobile_upload.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

GUIDE = "[data-testid='capture-guide']"
PHOTO_INPUT = "[data-testid='photo-input']"
WARNING = "[data-testid='format-warning']"
SELECTED = "text=/已选/"


def _fake_photo(target: Path, marker: bytes) -> Path:
    """落盘一个够长的假图，只为满足 set_input_files（内容不会被上传）。"""
    target.write_bytes(marker + bytes([0]) * 4096)
    return target


def probe(base_url: str, password: str, out: Path) -> dict[str, Any]:
    """登录手机分辨率下打开上传页，选三张真假混杂的照片并读回提示。"""
    import httpx
    from playwright.sync_api import sync_playwright

    login = httpx.post(
        f"{base_url}/api/auth/login", json={"password": password}, timeout=15.0
    )
    login.raise_for_status()
    token = str(login.json()["data"]["token"])

    out.mkdir(parents=True, exist_ok=True)
    heic = _fake_photo(out / "IMG_9001.HEIC", bytes([0, 0, 0, 24]) + b"ftypheic")
    noext = _fake_photo(out / "IMG_9002", bytes([0, 0, 0, 24]) + b"ftypjpeg")
    jpeg = _fake_photo(out / "IMG_9003.jpg", b"\xff\xd8\xff\xe0" + b"JFIF")

    errors: list[str] = []
    summary: dict[str, Any] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        context.add_init_script(
            "window.localStorage.setItem('ewb_token', '" + token + "')"
        )
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"{base_url}/issues/1/upload", wait_until="networkidle")

        summary["entryScript"] = page.evaluate(
            "() => document.querySelector('script[src*=assets]')?.src"
        )
        summary["guideVisible"] = page.is_visible(GUIDE)
        summary["cameraTips"] = page.locator(GUIDE).inner_text().replace(chr(10), " ")
        summary["accept"] = page.get_attribute(PHOTO_INPUT, "accept")
        summary["multiple"] = page.get_attribute(PHOTO_INPUT, "multiple") is not None
        summary["capture"] = page.get_attribute(PHOTO_INPUT, "capture")

        # 只选文件、不点「上传并识别」：这一步不调引擎
        page.set_input_files(PHOTO_INPUT, [str(heic), str(noext), str(jpeg)])
        page.wait_for_timeout(600)
        locator = page.locator(WARNING)
        summary["warningShown"] = locator.count() > 0
        summary["warningText"] = locator.first.inner_text() if locator.count() else ""
        picked = page.locator(SELECTED)
        summary["selectedText"] = picked.first.inner_text() if picked.count() else ""
        page.screenshot(path=str(out / "手机上传页-选图与格式提示.png"), full_page=True)
        context.close()
        browser.close()

    summary["pageerrors"] = errors
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="手机端上传页探针（不调引擎）")
    parser.add_argument("--base-url", default="http://192.168.31.167:8000")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--out", default="../artifacts/v1.2r4复测")
    args = parser.parse_args(argv)
    summary = probe(args.base_url, args.password, Path(args.out))
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0 if not summary["pageerrors"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
