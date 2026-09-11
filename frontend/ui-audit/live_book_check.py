# -*- coding: utf-8 -*-
# 补充验证：作文集页（/issues/1/book）真实后端实测（Python playwright）
import json
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 375, "height": 812})
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)

    # 登录
    page.goto(BASE + "/", wait_until="networkidle")
    page.fill('input[type="password"]', "admin123")
    page.keyboard.press("Enter")
    page.wait_for_url(BASE + "/", timeout=10000)

    # 直接进入作文集页
    page.goto(BASE + "/issues/1/book", wait_until="networkidle")
    page.wait_for_timeout(1500)
    page.screenshot(path="ui-audit/live-04-book.png")

    text = page.evaluate("document.body.innerText")
    has_back = ("首页" in text) or ("←" in text)
    has_nav = ("期数列表" in text) or ("看板" in text)
    print(json.dumps({
        "页面文本长度": len(text),
        "返回入口存在": "是" if has_back else "否",
        "看板或期数列表入口": "是" if has_nav else "否",
        "文本前200字": text[:200],
        "运行时错误数": len(errors),
        "错误样例": errors[:3],
    }, ensure_ascii=False, indent=1))
    browser.close()
