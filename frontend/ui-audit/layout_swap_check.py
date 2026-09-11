# -*- coding: utf-8 -*-
"""校对页布局互换实测：定稿编辑区在上、识别对照缩到下方。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"

with sync_playwright() as p:
    browser = p.chromium.launch()
    # 桌面视口
    page = browser.new_page(viewport={"width": 1366, "height": 850})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE + "/", wait_until="networkidle")
    page.fill('input[type="password"]', "admin123")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)

    page.goto(BASE + "/essays/1/proofread", wait_until="networkidle")
    page.wait_for_timeout(1500)
    page.screenshot(path="ui-audit/layout-swap-desktop.png")

    editor = page.locator('[data-testid="final-text"]')
    diff = page.locator('[data-testid="diff-annotated"]')
    ebox = editor.bounding_box()
    dbox = diff.bounding_box()
    result = {
        "编辑区y": round(ebox["y"]),
        "编辑区高": round(ebox["height"]),
        "对照区y": round(dbox["y"]),
        "对照区高": round(dbox["height"]),
        "编辑区在上": ebox["y"] < dbox["y"],
        "编辑区更高": ebox["height"] > dbox["height"],
        "对照区可折叠": page.locator('[data-testid="diff-details"] summary').count() == 1,
        "运行时错误": len(errors),
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))

    # 手机视口
    page2 = browser.new_page(viewport={"width": 375, "height": 812})
    page2.on("pageerror", lambda e: errors.append(str(e)))
    page2.goto(BASE + "/", wait_until="networkidle")
    page2.fill('input[type="password"]', "admin123")
    page2.keyboard.press("Enter")
    page2.wait_for_timeout(1200)
    page2.goto(BASE + "/essays/1/proofread", wait_until="networkidle")
    page2.wait_for_timeout(1000)
    # 切到文字面板
    page2.get_by_role("button", name="文字", exact=True).click()
    page2.wait_for_timeout(600)
    page2.screenshot(path="ui-audit/layout-swap-mobile.png")
    ebox2 = page2.locator('[data-testid="final-text"]').bounding_box()
    dbox2 = page2.locator('[data-testid="diff-annotated"]').bounding_box()
    result["手机_编辑区在上"] = ebox2["y"] < dbox2["y"]
    result["手机_无横向滚动"] = page2.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    print(json.dumps({k: v for k, v in result.items() if k.startswith("手机")}, ensure_ascii=False))
    browser.close()
