# -*- coding: utf-8 -*-
"""三项新功能真机验证：删除期数 / 学生管理 / 看板紧凑网格。

可重复运行：每次运行用时间戳后缀生成全新的学号 / 期号，
不依赖数据库残留状态，也不会污染真实数据。
"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
API = "http://127.0.0.1:8000/api"
AUTH = ("admin", "admin123")
results = {}

# 本轮专属测试数据（时间戳后缀，保证幂等隔离）
SUFFIX = str(int(time.time()))[-5:]
NO_ADD = f"2027{SUFFIX}1"      # 单独添加用
NO_IMP_A = f"2027{SUFFIX}2"    # 批量导入用
NO_IMP_B = f"2027{SUFFIX}3"
NAME_ADD = f"测试学生{SUFFIX}"
NAME_IMP_A = f"导入学生A{SUFFIX}"
NAME_IMP_B = f"导入学生B{SUFFIX}"
ISSUE_NO = int(SUFFIX)  # 5 位数期号，避免与真实期数冲突


def api_token() -> str:
    """Login and return bearer token (envelope: {code, data:{token,...}, message})."""
    req = urllib.request.Request(
        API + "/auth/login",
        data=json.dumps({"username": AUTH[0], "password": AUTH[1]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)["data"]["token"]


def api_call(token: str, method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "body": e.read().decode("utf-8", "replace")}


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1366, "height": 850})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 登录
    page.goto(BASE + "/", wait_until="networkidle")
    page.fill('input[type="password"]', "admin123")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)

    # ---- ② 学生管理 ----
    page.get_by_role("button", name="学生名单").click()
    page.wait_for_timeout(1000)
    page.screenshot(path="ui-audit/feat-students.png")
    page.fill('input[placeholder="如 20230101"]', NO_ADD)
    page.fill('input[placeholder="如 张三"]', NAME_ADD)
    page.get_by_role("button", name="添加学生").click()
    page.wait_for_timeout(800)
    results["学生_添加成功提示"] = f"已添加 {NAME_ADD}" in page.evaluate("document.body.innerText")
    results["学生_出现在名单"] = page.get_by_text(NAME_ADD).count() >= 1

    # 停用（确认弹框）。注意：停用后提示文字「XX 已停用」也含姓名，
    # 所以断言对象是名单列表项 <li>，而不是整页文本。
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("listitem").filter(has_text=NAME_ADD).get_by_role(
        "button", name="停用"
    ).click()
    page.wait_for_timeout(800)
    results["学生_停用后消失"] = (
        page.get_by_role("listitem").filter(has_text=NAME_ADD).count() == 0
    )

    # 批量导入
    page.get_by_role("button", name="批量导入").click()
    page.fill(
        '[data-testid="import-text"]',
        f"{NO_IMP_A},{NAME_IMP_A}\n{NO_IMP_B},{NAME_IMP_B}",
    )
    page.get_by_role("button", name="确认导入").click()
    page.wait_for_timeout(900)
    body_text = page.evaluate("document.body.innerText")
    results["导入_成功提示"] = "新增 2 人" in body_text
    results["导入_名单出现"] = page.get_by_text(NAME_IMP_A).count() >= 1
    page.screenshot(path="ui-audit/feat-students-imported.png")

    # ---- ① 删除期数（新建一期空期 → 删除）----
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(800)
    page.fill('input[type="number"]', str(ISSUE_NO))
    page.get_by_role("button", name="创建").click()
    page.wait_for_timeout(900)
    row_issue = page.get_by_role("listitem").filter(has_text=f"第 {ISSUE_NO} 期")
    dialogs = []
    page.once("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    row_issue.get_by_role("button", name="删除").click()
    page.wait_for_timeout(500)
    results["删除_确认弹框出现"] = len(dialogs) == 1
    results["删除_取消后期仍在"] = row_issue.count() >= 1
    # 再点删除并确认
    page.once("dialog", lambda d: d.accept())
    row_issue.get_by_role("button", name="删除").click()
    page.wait_for_timeout(900)
    results["删除_确认后期消失"] = (
        page.get_by_role("listitem").filter(has_text=f"第 {ISSUE_NO} 期").count() == 0
    )
    page.screenshot(path="ui-audit/feat-issue-deleted.png")

    # ---- ③ 看板紧凑网格 ----
    # 找一个含作文的期数进入看板，断言状态分组使用网格布局（非长列表）。
    token = api_token()
    issues = api_call(token, "GET", "/issues")
    issue_list = issues.get("data", []) if isinstance(issues.get("data"), list) else []
    target = None
    for it in issue_list:
        detail = api_call(token, "GET", f"/issues/{it['id']}/essays")
        if detail.get("data"):
            target = it["id"]
            break
    if target is None:
        results["看板_分组网格存在"] = "SKIPPED（没有含作文的期数可测）"
    else:
        page.goto(BASE + f"/issues/{target}/essays", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.screenshot(path="ui-audit/feat-board-grid.png")
        results["看板_分组网格存在"] = page.locator("ul.grid").count() >= 1
    results["看板_运行时错误"] = len(errors)

    browser.close()

print(json.dumps(results, ensure_ascii=False, indent=1))
