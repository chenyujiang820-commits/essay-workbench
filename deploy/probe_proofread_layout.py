
"""校对页手机端版式探针（GAP-12 取证：只读、不调识别引擎）。

自我登录取 token，在 390x844 / 844x390 / 1366x768 / 1920x1080 四档视口下检查：
「文字」页是否默认可见、定稿框高度、识别对照是否可见且能滚进视野、原片缩放层是否有高度。
任一项不合格即以退出码 1 结束，可直接当回归门禁。

用法：cd backend && .venv/Scripts/python.exe ../deploy/probe_proofread_layout.py
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

SNAP_JS = """() => {
  const q = (sel) => document.querySelector(sel);
  const vis = (el) => !!el && el.offsetParent !== null;
  const compare =
    q('[data-testid="ocr-compare"]') || q('[data-testid="ocr-compare-clean"]');
  const ta = q('[data-testid="final-text"]');
  const counter = q('[data-testid="suspect-counter"]');
  const tabs = Array.from(document.querySelectorAll('[data-testid^="pane-"]')).map(
    (b) => b.textContent.trim()
  );
  const firstTab = document.querySelector('[data-testid^="pane-"]');
  const se = document.scrollingElement || document.documentElement;
  const box = compare ? compare.getBoundingClientRect() : null;
  return {
    path: location.pathname,
    tabs: tabs,
    tabsVisible: !!firstTab && firstTab.offsetParent !== null,
    textareaHeight: ta ? Math.round(ta.getBoundingClientRect().height) : -1,
    compareVisible: vis(compare),
    compareChars: compare ? (compare.innerText || "").trim().length : -1,
    suspectCounter: counter ? counter.innerText : null,
    docScrollHeight: se.scrollHeight,
    innerHeight: window.innerHeight
  };
}"""

SCROLL_JS = """() => {
  const e =
    document.querySelector('[data-testid="diff-annotated"]') ||
    document.querySelector('[data-testid="ocr-compare"]');
  if (e) e.scrollIntoView(true);
}"""

PANEL_END_JS = """() => {
  const e = document.querySelector('[data-testid="diff-annotated"]');
  if (e) {
    e.scrollTop = e.scrollHeight;
    e.scrollIntoView(false);
  }
  return true;
}"""

AFTER_JS = """() => {
  const e =
    document.querySelector('[data-testid="ocr-compare"]') ||
    document.querySelector('[data-testid="ocr-compare-clean"]');
  if (!e) return null;
  const b = e.getBoundingClientRect();
  return {
    top: Math.round(b.top),
    height: Math.round(b.height),
    inView: b.top >= -1 && b.top < window.innerHeight
  };
}"""


TAIL_JS = """() => {
  const panel = document.querySelector('[data-testid="diff-annotated"]');
  const content =
    document.querySelector('[data-testid="ocr-compare"]') ||
    document.querySelector('[data-testid="ocr-compare-clean"]');
  if (!panel) return null;
  const panelRect = panel.getBoundingClientRect();
  const contentRect = content ? content.getBoundingClientRect() : null;
  const style = getComputedStyle(panel);
  const se = document.scrollingElement || document.documentElement;
  return {
    bottom: Math.round(panelRect.bottom),
    height: Math.round(panelRect.height),
    panelClientHeight: panel.clientHeight,
    panelScrollHeight: panel.scrollHeight,
    panelScrollTop: Math.round(panel.scrollTop),
    panelOverflowY: style.overflowY,
    panelCanScroll: panel.scrollHeight > panel.clientHeight + 1,
    panelAtEnd: panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 1,
    tailVisible: panelRect.bottom <= window.innerHeight + 2,
    contentBottom: contentRect ? Math.round(contentRect.bottom) : null,
    documentCanScroll: se.scrollHeight > se.clientHeight + 1
  };
}"""

VIEWER_JS = """() => {
  // 只能在原片真的可见时量：手机端默认停在文字页，此刻整个原片区是 display:none,
  // 量到 0 不代表照片有问题，所以 snap() 是先切页签、再量这两个高度。
  const box = document.querySelector('[data-testid="photo-viewer"]');
  const img = document.querySelector('[data-testid="photo-viewer"] img');
  return {
    viewerHeight: box ? Math.round(box.getBoundingClientRect().height) : -1,
    photoImgHeight: img ? Math.round(img.getBoundingClientRect().height) : -1
  };
}"""

MARKS_JS = """() => {
  // GAP-13 判据：识别对照里的每个标注框只许盖住一行原文。
  // 旧实现只按句末标点切单元，OCR 页眉那几行后面没有句号，于是「页眉三行 + 第一个长句」
  // 并成一个单元，老师删掉页眉后整句被连坐标红 —— 框高达到 6 行。
  const panel = document.querySelector('[data-testid="ocr-compare"]');
  if (!panel) return null;
  const marks = Array.from(panel.querySelectorAll('[data-suspect-key]')).map((node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    const lineHeight = parseFloat(style.lineHeight) || 32;
    const text = node.innerText || '';
    return {
      key: node.getAttribute('data-suspect-key'),
      kind: node.getAttribute('data-suspect'),
      chars: text.replace(/\\s/g, '').length,
      hasNewline: /\\n/.test(text),
      lines: Math.max(1, Math.round(rect.height / lineHeight)),
    };
  });
  return {
    count: marks.length,
    maxLines: marks.reduce((acc, mark) => Math.max(acc, mark.lines), 0),
    maxChars: marks.reduce((acc, mark) => Math.max(acc, mark.chars), 0),
    multiLine: marks.filter((mark) => mark.hasNewline || mark.lines > 4).map((mark) => mark.key),
    marks: marks,
  };
  }"""


def snap(page: Any) -> dict[str, Any]:
    """量三件事：默认可见分栏、识别对照能否滚进视野/滚到底，最后量原片区与照片高度。

    页签只在手机端渲染（桌面档被 `lg:hidden` 隐藏），所以必须按 tabsVisible 决定要不要点：
    在桌面档点一个隐藏按钮会让 Playwright 等到超时、整个探针崩掉（上一轮就是这么挂的）。
    
    """
    data = page.evaluate(SNAP_JS)
    page.evaluate(SCROLL_JS)
    page.wait_for_timeout(250)
    data["afterScroll"] = page.evaluate(AFTER_JS)
    page.evaluate(PANEL_END_JS)
    page.wait_for_timeout(250)
    data["tail"] = page.evaluate(TAIL_JS)
    data["marks"] = page.evaluate(MARKS_JS)
    if data["tabsVisible"]:
        # 手机端原片在另一个页签：先切过去，照片可见了才量得到高度，量完切回文字页。
        page.click("[data-testid='pane-photo']")
        page.wait_for_timeout(600)
    measured = page.evaluate(VIEWER_JS)
    data["viewerHeight"] = measured["viewerHeight"]
    data["photoImgHeight"] = measured["photoImgHeight"]
    if data["tabsVisible"]:
        page.click("[data-testid='pane-text']")
        page.wait_for_timeout(150)
    return data


def check(width: int, height: int, data: dict[str, Any], essay: int) -> list[str]:
    """把一次快照换成"这一屏能不能完成校对"的判定。"""
    problems: list[str] = []
    if data.get("path") != "/essays/" + str(essay) + "/proofread":
        problems.append("被踢回登录页")
    if not data["compareVisible"]:
        problems.append("识别对照不可见")
    if data["compareChars"] <= 0:
        problems.append("识别对照无内容")
    if data["textareaHeight"] < 120:
        problems.append("定稿框仅 " + str(data["textareaHeight"]) + "px")
    after = data.get("afterScroll") or {}
    tail = data.get("tail") or {}
    if not after.get("inView"):
        problems.append("识别对照滚不进视野")
    # 「整页可滚」不是目的，能读到底才是：桌面一屏放得下时本来就不需要滚动。
    if tail.get("panelOverflowY") != "auto":
        problems.append("识别对照未启用内部滚动（overflow-y=" + str(tail.get("panelOverflowY")) + "）")
    if tail.get("panelCanScroll") and not tail.get("panelAtEnd"):
        problems.append("识别对照内部滚动不到底（" + str(tail.get("panelScrollTop")) + "/" + str(tail.get("panelScrollHeight")) + "px）")
    if not tail.get("tailVisible") and not tail.get("documentCanScroll"):
        problems.append("识别对照面板滚不进视野（面板高 " + str(tail.get("height")) + "px）")
    if data["tabsVisible"] and width < height:
        # 手机端原片页：盒子有高度不等于照片看得见，照片本身也得撑开。
        if data["viewerHeight"] < 200:
            problems.append("原片缩放层仅 " + str(data["viewerHeight"]) + "px")
        if data.get("photoImgHeight", -1) < 150:
            problems.append("原片照片仅 " + str(data.get("photoImgHeight")) + "px")
        # 一屏能看完整张照片是底线：超过屏高就要来回滚，手机上等于看不清。
        if data["viewerHeight"] > height + 40:
            problems.append("原片区高 " + str(data["viewerHeight"]) + "px 超过屏高 " + str(height) + "px")
    marks = data.get("marks")
    if marks:
        # GAP-13：对照框一旦跨行（含换行）或高过 4 视觉行，就说明比对单元又变粗了。
        if marks.get("multiLine"):
            problems.append(
                "识别对照框跨行/过高: " + ", ".join(marks.get("multiLine") or [])
            )
        if marks.get("count", 0) > 0 and marks.get("maxChars", 0) > 120:
            problems.append(
                "识别对照单框最长 " + str(marks.get("maxChars")) + " 字，疑似整段连坐标红"
            )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校对页手机端版式探针（只读）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--password", default="admin123")
    parser.add_argument(
        "--essay",
        type=int,
        nargs="+",
        default=[1],
        help="可一次给多个篇 id，逐篇 × 各视口取证",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    import httpx
    from playwright.sync_api import sync_playwright

    login = httpx.post(
        args.base_url + "/api/auth/login", json={"password": args.password}, timeout=15.0
    )
    login.raise_for_status()
    token = str(login.json()["data"]["token"])

    # 390x844 = 竖屏手机；844x390 = 横屏手机（最矮一档，专门卡「滚到底」判据）；
    # 1366x768 = 教室智慧黑板常见分辨率；1920x1080 = 老师笔记本。
    sizes = [(390, 844), (844, 390), (1366, 768), (1920, 1080)]
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for essay in args.essay:
            for width, height in sizes:
                ctx = browser.new_context(viewport={"width": width, "height": height})
                ctx.add_init_script(
                    "window.localStorage.setItem('ewb_token', '" + token + "')"
                )
                page = ctx.new_page()
                page.goto(
                    args.base_url + "/essays/" + str(essay) + "/proofread",
                    wait_until="networkidle",
                )
                page.wait_for_timeout(700)
                data = snap(page)
                label = "essay" + str(essay) + " " + str(width) + "x" + str(height)
                print(label + " " + json.dumps(data, ensure_ascii=False))
                for line in check(width, height, data, essay):
                    failures.append(label + ": " + line)
                if args.out:
                    page.evaluate("() => window.scrollTo(0, 0)")
                    page.wait_for_timeout(150)
                    page.screenshot(
                        path=args.out + "/校对页-e" + str(essay) + "-" + str(width) + "x" + str(height) + ".png",
                        full_page=True,
                    )
                ctx.close()
        browser.close()

    for line in failures:
        print("FAIL " + line)
    print("PROBE " + ("OK" if not failures else "FAILED"))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
