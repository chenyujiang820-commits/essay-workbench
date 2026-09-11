# -*- coding: utf-8 -*-
"""Debug probe: inspect /issues/1/essays page state after login."""
import sys

sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1366, "height": 850})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE + "/", wait_until="networkidle")
    page.fill('input[type="password"]', "admin123")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)

    page.goto(BASE + "/issues/1/essays", wait_until="networkidle")
    page.wait_for_timeout(1500)

    print("URL:", page.url)
    body = page.evaluate("document.body.innerText")
    print("--- BODY (first 800 chars) ---")
    print(body[:800])
    print("--- UL elements ---")
    uls = page.evaluate(
        "Array.from(document.querySelectorAll('ul')).map(u => ({cls: u.className, items: u.children.length}))"
    )
    for u in uls:
        print(u)
    print("ul.grid count:", page.locator("ul.grid").count())
    print("pageerrors:", errors)
    browser.close()
