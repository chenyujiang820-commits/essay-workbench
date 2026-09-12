"""二期页面版式探针：看板精选 / 表彰榜 / 分享管理 / 成长档案 x 三档视口。

为什么要单独一条探针：二期四个新页面在 jsdom（vitest）与真链路探针里都只验过「内容对不对」，
没验过「在教室那块大屏和老师的手机上长什么样」。一期正是栽在这里（GAP-09 / GAP-12：桌面好看、
手机看不清），所以二期收口前把版式也变成可判定的东西，而不是「看着还行」。

用法（后端需已在 8000 端口运行）：
    cd backend && .venv/Scripts/python.exe ../deploy/probe_phase2_layout.py
    # 想在榜上有行的状态下量行字号，加 --seed：临时给已定稿作文打分，量完按快照还原
    # 出取证截图：--out ../artifacts/v1.3二期复测

默认**只读**：绝不调用任何写接口 —— 不保存精选、不生成/撤销链接、不导出 PDF。
只有加了 --seed 才写库（只写 score 与 teacher_comment，结束前一定按快照回写，含失败路径）。

退出码：0 = 全部判据通过；1 = 任一判据不成立（打印失败项）。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx
from playwright.sync_api import sync_playwright

DEFAULT_BASE = "http://127.0.0.1:8000"
# 三档：老师手机竖屏 + 教室常见的两种投影分辨率。手机横屏二期没有专属布局，不重复量。
VIEWPORTS: list[tuple[int, int]] = [(390, 844), (1366, 768), (1920, 1080)]
# 与 PresentPage 书式两栏同一个断点：榜面 <1024 竖排、>=1024 双列（RankingPage 的 lg:grid-cols-2）。
TWO_COLUMN_MIN_WIDTH = 1024
# 投影可读性下限，与看板卡片同档；低于此值坐最后一排读不到行内容。
MIN_ROW_FONT_PX = 14.0

MEASURE_JS = r'''
(args) => {
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    return {
      x: Math.round(r.x + window.scrollX),
      y: Math.round(r.y + window.scrollX),
      top: Math.round(r.top),
      w: Math.round(r.width),
      h: Math.round(r.height),
      fontPx: parseFloat(cs.fontSize) || 0,
    };
  };
  const out = {
    path: window.location.pathname,
    innerHeight: window.innerHeight,
    scrollHeight: document.documentElement.scrollHeight,
    boxes: {},
    rows: 0,
    rowFontPx: null,
    essayLinks: document.querySelectorAll('a[href^="/essays/"]').length,
    atBottom: true,
  };
  for (const key of Object.keys(args.selectors)) {
    const el = document.querySelector(args.selectors[key]);
    out.boxes[key] = el ? boxOf(el) : null;
  }
  if (args.rowSelector) {
    const nodes = Array.from(document.querySelectorAll(args.rowSelector));
    out.rows = nodes.length;
    const fonts = nodes.map((node) => parseFloat(window.getComputedStyle(node).fontSize) || 0);
    out.rowFontPx = fonts.length ? Math.min.apply(null, fonts) : null;
  }
  if (args.tailSelector) {
    window.scrollTo(0, document.documentElement.scrollHeight);
    const tail = document.querySelector(args.tailSelector);
    out.atBottom = Boolean(tail && tail.getBoundingClientRect().top < window.innerHeight);
    window.scrollTo(0, 0);
  }
  return out;
}
'''
# 每页要量什么：选择器 + 判定用的补充信息。路径里的 {issue}/{student} 由 main 填。
PAGES: dict[str, dict[str, Any]] = {
    "board": {
        "path": "/issues/{issue}/essays",
        "selectors": {
            "panel": '[data-testid="selection-panel"]',
            "limit": '[data-testid="selection-limit"]',
            "firstPick": '[data-testid^="select-essay-"]',
            "save": '[data-testid="submit-selection"]',
            "toRanking": '[data-testid="board-ranking"]',
            "toShares": '[data-testid="board-shares"]',
        },
        "rowSelector": "",
        "tail": '[data-testid="board-shares"]',
    },
    "ranking": {
        "path": "/issues/{issue}/ranking",
        "selectors": {
            "title": '[data-testid="ranking-title"]',
            "thresholds": '[data-testid="ranking-thresholds"]',
            "work": '[data-testid="board-work"]',
            "progress": '[data-testid="board-progress"]',
            "star": '[data-testid="board-star"]',
            "poster": '[data-testid="ranking-poster"]',
        },
        "rowSelector": '[data-testid^="ranking-row-"]',
        "tail": '[data-testid="ranking-back"]',
    },
    "shares": {
        "path": "/issues/{issue}/shares",
        "selectors": {
            "form": '[data-testid="share-form"]',
            "create": '[data-testid="create-share"]',
            "list": '[data-testid="shares-empty"], [data-testid="share-row"]',
            "back": '[data-testid="back-home"]',
        },
        "rowSelector": '[data-testid="share-row"]',
        "tail": '[data-testid="back-home"]',
    },
    "portfolio": {
        "path": "/students/{student}/portfolio",
        "selectors": {
            "avg": '[data-testid="avg-score"]',
            "export": '[data-testid="export-portfolio"]',
            "entry": '[data-testid^="portfolio-entry-"]',
            "back": '[data-testid="back-students"]',
        },
        "rowSelector": '[data-testid^="portfolio-entry-"]',
        "tail": '[data-testid="back-students"]',
    },
}


def measure(page: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """一次 evaluate 量完：可见性、绝对坐标、行字号，最后滚到底补量页脚能不能读到。"""
    data = page.evaluate(
        MEASURE_JS,
        {
            "selectors": spec["selectors"],
            "rowSelector": spec["rowSelector"],
            "tailSelector": spec["tail"],
        },
    )
    page.wait_for_timeout(120)
    return data


def check(page_key: str, width: int, height: int, data: dict[str, Any]) -> list[str]:
    """把一次快照换成「这一屏能不能用」的判定。"""
    problems: list[str] = []
    boxes: dict[str, Any] = data.get("boxes") or {}
    path = str(data.get("path"))
    if path in ("/login", "/"):
        # 二期四页都在 RequireAuth 之内：被踢回登录页 = 探针注入的 token 没生效，算探针自己的错。
        return ["被踢回登录页（path=" + path + "）"]

    missing = [
        key for key in PAGES[page_key]["selectors"] if boxes.get(key) is None
    ]
    if missing:
        problems.append("缺元素: " + ", ".join(missing))

    if not data.get("atBottom", True):
        problems.append(
            "滚到底仍读不到页脚（页面高 " + str(data.get("scrollHeight")) + "px）"
        )

    if page_key == "board":
        panel = boxes.get("panel")
        first = boxes.get("firstPick")
        if panel and first:
            # 看见就能勾才算顺：面板与第一个勾选框相距超过 1.5 屏，45 人的班就得来回滚。
            gap = first["y"] - (panel["y"] + panel["h"])
            if gap > 1.5 * height:
                problems.append(
                    "精选面板到第一个勾选框相距 " + str(int(gap)) + "px（>1.5 屏）"
                )
            if first["y"] > 3 * height:
                problems.append("第一个勾选框在 " + str(first["y"]) + "px 处，超过 3 屏")

    if page_key == "ranking":
        keys = [key for key in ("work", "progress", "star") if boxes.get(key)]
        xs = sorted({boxes[key]["x"] for key in keys})
        if width >= TWO_COLUMN_MIN_WIDTH and len(xs) < 2 and len(keys) >= 2:
            problems.append("宽屏 " + str(width) + "px 下三榜仍全部竖排（没有双列）")
        if width < TWO_COLUMN_MIN_WIDTH and len(xs) > 1:
            problems.append("窄屏 " + str(width) + "px 下三榜并排了（会挤成一列几个字）")
        rows = int(data.get("rows") or 0)
        font = data.get("rowFontPx")
        if rows and font is not None and font < MIN_ROW_FONT_PX:
            problems.append(
                "榜单行字号仅 " + str(font) + "px（低于 " + str(MIN_ROW_FONT_PX) + "px）"
            )

    if page_key == "portfolio" and int(data.get("essayLinks") or 0) == 0:
        # 不当 FAIL 处理：这是二期现状（档案里点不回原文），量成数字交给 UI 评估。
        print("    note: 成长档案内没有指向 /essays/ 的链接（回看正文要跳回看板）")

    return problems
def seed_scores(client: httpx.Client, headers: dict[str, str], issue_id: int) -> list[dict[str, Any]]:
    """临时给已定稿作文打分，让三榜有行可量；返回改前快照（含评语，一起还原）。"""
    essays = client.get("/api/issues/" + str(issue_id) + "/essays", headers=headers).json()["data"]
    snapshot: list[dict[str, Any]] = []
    index = 0
    for item in essays:
        if item.get("status") != "proofread":
            continue
        essay_id = int(item["id"])
        snapshot.append(
            {"id": essay_id, "score": item.get("score"), "comment": item.get("teacher_comment")}
        )
        client.patch(
            "/api/essays/" + str(essay_id),
            json={"score": 95.0 - 7.0 * index, "teacher_comment": "探针临时评语（结束还原）"},
            headers=headers,
        )
        index += 1
    return snapshot


def unseed(client: httpx.Client, headers: dict[str, str], snapshot: list[dict[str, Any]]) -> None:
    for row in snapshot:
        body: dict[str, Any] = {
            # 三态：显式 null 才是清空，缺字段等于不改。
            "score": row.get("score"),
            "teacher_comment": row.get("comment") or "",
        }
        client.patch("/api/essays/" + str(row["id"]), json=body, headers=headers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="二期四页版式探针")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--seed", action="store_true", help="临时写评分让榜上有行（结束还原）")
    parser.add_argument("--out", default="", help="传目录则同时输出整页取证截图")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    client = httpx.Client(base_url=base, timeout=60.0)
    login = client.post("/api/auth/login", json={"password": args.password})
    login.raise_for_status()
    token = str(login.json()["data"]["token"])
    headers = {"Authorization": "Bearer " + token}

    issues = client.get("/api/issues", headers=headers).json()["data"]
    if not issues:
        print("库里没有期数，探针无从量起")
        return 1
    issue_id = int(issues[0]["id"])
    essays = client.get("/api/issues/" + str(issue_id) + "/essays", headers=headers).json()["data"]
    if not essays:
        print("本期没有作文，探针无从量起")
        return 1
    student_id = int(essays[0]["student_id"])

    snapshot: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    failures: list[str] = []
    try:
        if args.seed:
            snapshot = seed_scores(client, headers, issue_id)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            context.add_init_script(
                "window.localStorage.setItem('ewb_token', '" + token + "')"
            )
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            for width, height in VIEWPORTS:
                page.set_viewport_size({"width": width, "height": height})
                for page_key, spec in PAGES.items():
                    url = base + spec["path"].format(issue=issue_id, student=student_id)
                    page.goto(url, wait_until="networkidle")
                    page.wait_for_timeout(600)
                    data = measure(page, spec)
                    problems = check(page_key, width, height, data)
                    label = page_key + " " + str(width) + "x" + str(height)
                    boxes: dict[str, Any] = data.get("boxes") or {}
                    if errors:
                        problems.append("页面 JS 错误: " + "; ".join(errors[:2]))
                        errors.clear()
                    summary = {
                        "page": page_key,
                        "viewport": str(width) + "x" + str(height),
                        "rows": data.get("rows"),
                        "rowFontPx": data.get("rowFontPx"),
                        "essayLinks": data.get("essayLinks"),
                        "scrollHeight": data.get("scrollHeight"),
                        "starY": (boxes.get("star") or {}).get("y"),
                        "firstPickY": (boxes.get("firstPick") or {}).get("y"),
                        "problems": problems,
                    }
                    report.append(summary)
                    print(
                        "[" + ("FAIL" if problems else "PASS") + "] " + label
                        + " :: " + json.dumps(summary, ensure_ascii=False)[:300]
                    )
                    failures.extend(label + ": " + text for text in problems)
                    if args.out:
                        page.screenshot(
                            path=args.out + "/二期-" + page_key
                            + "-" + str(width) + "x" + str(height) + ".png",
                            full_page=True,
                        )
            context.close()
            browser.close()
    finally:
        if snapshot:
            unseed(client, headers, snapshot)
            print("[seed] 已按快照还原评分 :: " + json.dumps(snapshot, ensure_ascii=False)[:240])
        client.close()

    print(json.dumps({"runs": len(report), "failed": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())