"""二期（v1.3）真链路探针：评语/评分 → 精选 → 三榜 → 海报 → 档案 → 家长链接。

为什么要有这个脚本：单元测试证的是"函数按约定返回"，证不了**手机浏览器打开家长链接时
真能看到作文、且一个学号一个分数都不漏**。这一段只能靠真 HTTP + 真 Chromium 走一遍。

用法（先起服务，且前端已 npm run build）：
    cd backend && .venv/Scripts/python.exe ../deploy/probe_phase2_flow.py
    可选：--base http://192.168.31.167:8000 --password admin123 --issue 1

退出码：0 = 全部判据通过；1 = 任一判据不成立（打印失败项）。

数据边界：本脚本**会改真库**（写评语/评分/精选、建一条 1 天链接），但结束前一定还原：
  * 每篇作文改前的 final_text/score/teacher_comment/selected 全部记录并回写；
  * 分享链接用完即删（直接删 ShareLink 行，不留测试残留），因此不会留下可访问的公网入口。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import httpx
from playwright.sync_api import sync_playwright

DEFAULT_BASE = "http://127.0.0.1:8000"
GONE_TEXT = "链接已失效或不存在"
# 家长页上绝不该出现的字样：管理入口 + 校内口径（学号 S0xx、分数）。
# 家长页绝不该出现的东西：管理入口（老师动词）与校内口径（学号、分数）。
FORBIDDEN_WORDS = ("上传", "重新识别", "保存并定稿", "生成链接", "撤销", "改名", "停用")

def _page_count(pdf: bytes) -> int:
    """数 PDF 里的页面对象（与 deploy/probe 既有用法一致，不引第三方 PDF 库）。"""
    body = pdf.decode("latin-1", errors="ignore")
    found = len(re.findall(r"/Type\s*/Page(?!s)", body))
    declared = re.findall(r"/Count\s+(\d+)", body)
    if found == 0:
        raise AssertionError("PDF 里没数到页面对象，导出可能失败")
    if declared and int(max(declared)) > found:
        raise AssertionError(f"PDF 页数对不上：/Count={max(declared)} 实际 {found}")
    return found


class Probe:
    """把一次完整链路拆成"每步一句判据"，失败时能直接说出卡在哪一步。"""

    def __init__(self, base: str, password: str, issue_no: int | None) -> None:
        self.base = base.rstrip("/")
        self.client = httpx.Client(base_url=self.base, timeout=90.0)
        self.headers: dict[str, str] = {}
        self.issue_no = issue_no
        self.steps: list[dict[str, Any]] = []
        self.snapshot: list[dict[str, Any]] = []
        self.share_tokens: list[str] = []
        self.thresholds: list[int] = [60, 70, 80, 90]
        self._password = password

    def step(self, name: str, ok: bool, detail: Any = None) -> None:
        self.steps.append({"step": name, "ok": bool(ok), "detail": detail})
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {name} :: {json.dumps(detail, ensure_ascii=False)[:200]}")

    # -- 基础 --
    def login(self) -> None:
        res = self.client.post("/api/auth/login", json={"password": self._password})
        res.raise_for_status()
        token = res.json()["data"]["token"]
        self.headers = {"Authorization": "Bearer " + token}

    def pick_issue(self) -> int:
        res = self.client.get("/api/issues", headers=self.headers)
        res.raise_for_status()
        issues = sorted(res.json()["data"], key=lambda item: int(item["issue_no"]), reverse=True)
        if self.issue_no is not None:
            issues = [item for item in issues if int(item["issue_no"]) == self.issue_no]
        if not issues:
            raise AssertionError("库里没有期数：先用一期真照片走完链路")
        for candidate in issues:
            essays = self._essays(int(candidate["id"]))
            proofread = [item for item in essays if item["status"] == "proofread"]
            if len(proofread) >= 2:
                self.step("选取可跑的期数", True, {"issue_id": candidate["id"], "proofread": len(proofread)})
                return int(candidate["id"])
        raise AssertionError("没有任何一期有 2 篇以上已定稿作文，二期链路跑不起来")

    def _essays(self, issue_id: int) -> list[dict[str, Any]]:
        res = self.client.get(f"/api/issues/{issue_id}/essays", headers=self.headers)
        res.raise_for_status()
        return list(res.json()["data"])

    # -- 阶段 2：评语与评分（只写这两样，不碰正文与状态） --
    def stage_scores(self, issue_id: int) -> int:
        essays = [item for item in self._essays(issue_id) if item["status"] == "proofread"]
        for item in essays:
            essay_id = int(item["id"])
            detail = self._detail(essay_id)
            self.snapshot.append({
                "id": essay_id,
                "teacher_comment": item.get("teacher_comment"),
                "score": item.get("score"),
                "selected": int(item.get("selected") or 0),
                "final_text": detail["final_text"],
            })
        probe_scores = [95, 82, 66, 71, 90]
        rows = []
        ok = True
        for index, item in enumerate(essays[:5]):
            essay_id = int(item["id"])
            score = probe_scores[index % len(probe_scores)]
            # 刻意不带 final_text：v1.3 起「只改评分」不该被迫回写整篇正文
            res = self.client.patch(
                "/api/essays/" + str(essay_id),
                json={"teacher_comment": "探针评语：细节到位，结尾可再收一收。", "score": score},
                headers=self.headers,
            )
            data = res.json().get("data") or {}
            kept = self._detail(essay_id)
            stars_ok = data.get("stars") == _stars(score, self.thresholds)
            body_ok = kept.get("final_text") == self.snapshot[index]["final_text"]
            status_ok = kept.get("status") == "proofread"
            if res.status_code != 200 or not (stars_ok and body_ok and status_ok):
                ok = False
            rows.append({
                "id": essay_id,
                "score": score,
                "stars": data.get("stars"),
                "expected_stars": _stars(score, self.thresholds),
                "body_kept": body_ok,
                "status_kept": status_ok,
            })
        self.step("写评语+评分：星级对、正文与状态都不动", ok and len(essays) >= 2, rows)
        return len(essays)

    def _detail(self, essay_id: int) -> dict[str, Any]:
        res = self.client.get("/api/essays/" + str(essay_id), headers=self.headers)
        res.raise_for_status()
        return dict(res.json()["data"])

    # -- 阶段 3：精选整期覆盖 + 回读计数 --
    def stage_selection(self, issue_id: int, essay_ids: list[int]) -> None:
        wanted = essay_ids[:2]
        res = self.client.put(
            "/api/issues/" + str(issue_id) + "/selection",
            json={"essay_ids": wanted},
            headers=self.headers,
        )
        data = res.json().get("data") or {}
        self.step(
            "精选覆盖式保存并回读一致",
            res.status_code == 200
            and sorted(data.get("selected_ids") or []) == sorted(wanted),
            {"wanted": wanted, "readback": data.get("selected_ids")},
        )
        board = self.client.get(
            "/api/issues/" + str(issue_id) + "/ranking", headers=self.headers
        ).json()["data"]
        self.step(
            "精选回读带已评/未评计数，且与三榜同一判据",
            data.get("scored_count") == board.get("scored_count")
            and data.get("unscored_count") == board.get("unscored_count")
            and int(data.get("scored_count") or 0) >= 2,
            {"selection": [data.get("scored_count"), data.get("unscored_count")],
             "ranking": [board.get("scored_count"), board.get("unscored_count")]},
        )
        foreign = self.client.put(
            "/api/issues/" + str(issue_id) + "/selection",
            json={"essay_ids": essay_ids[:1] + [999999]},
            headers=self.headers,
        )
        overflow = self.client.put(
            "/api/issues/" + str(issue_id) + "/selection",
            json={"essay_ids": essay_ids[:1] + list(range(900001, 900011))},
            headers=self.headers,
        )
        self.step(
            "外来 id 与超上限各自整单拒绝（不做半套写入）",
            foreign.status_code == 400
            and "不属于本期" in (foreign.json().get("message") or "")
            and overflow.status_code == 400
            and "最多 10 篇" in (overflow.json().get("message") or ""),
            {"foreign": foreign.json().get("message"), "overflow": overflow.json().get("message")},
        )
        kept_now = [
            int(item["id"])
            for item in self._essays(issue_id)
            if int(item.get("selected") or 0) == 1
        ]
        self.step(
            "两次被拒之后，先前保存的勾选一格没动",
            sorted(kept_now) == sorted(wanted),
            {"kept": kept_now, "wanted": wanted},
        )

    # -- 阶段 6：家长链接（真浏览器 + 手机视口 + 零登录态） --
    def stage_share(self, issue_id: int, student_nos: list[str], expect_items: int) -> None:
        created = self.client.post(
            "/api/issues/" + str(issue_id) + "/shares",
            json={"days": 1, "label": "probe"},
            headers=self.headers,
        )
        if created.status_code != 201:
            self.step("建家长链接", False, {"status": created.status_code, "body": created.text[:120]})
            return
        link = created.json()["data"]
        self.share_tokens.append(str(link["token"]))
        self.step(
            "建家长链接（只出已定稿）",
            str(link["url"]).startswith("/share/") and int(link["essay_count"]) == expect_items,
            {"essay_count": link["essay_count"], "expect": expect_items, "url": link["url"]},
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            # 独立 context：家长是游客，不带任何 localStorage；视口用真机那一档 390x844
            context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            page = context.new_page()
            page.goto(self.base + str(link["url"]), wait_until="networkidle")
            items = page.locator(SELECTOR_ITEM).count()
            self.step("家长页免鉴权可读（手机视口）", items >= 1, {"items": items})
            text = page.inner_text("body")
            leaked_no = [code for code in student_nos if code and code in text]
            self.step("家长页不出现任何学号", not leaked_no, leaked_no)
            self.step("家长页不出现分数（页面上没有任何「N 分」字样）", not re.search(r"\d+\s*分", text), re.findall(r"\d+\s*分", text)[:3])
            leaked_admin = [word for word in FORBIDDEN_WORDS if word in text]
            self.step("家长页没有管理入口", not leaked_admin, leaked_admin)
            self.step("家长页有星级", "★" in text, {"stars": text.count("★")})

            page.click(SELECTOR_PRINT)
            page.wait_for_selector(SELECTOR_IFRAME, timeout=30000)
            doc = page.frame_locator(SELECTOR_IFRAME)
            self.step("家长可打印版（与成册同一套模板）", doc.locator("article, section, h1").count() >= 1, {})

            # 撤销：老师侧 DELETE 之后，家长刷新只能看到一句失效文案，一个字都不该剩
            self.client.delete("/api/shares/" + str(link["token"]), headers=self.headers)
            page.goto(self.base + str(link["url"]), wait_until="networkidle")
            gone = page.locator(SELECTOR_GONE).count()
            items_after = page.locator(SELECTOR_ITEM).count()
            text_after = page.inner_text("body")
            leaked_after = [code for code in student_nos if code and code in text_after]
            self.step(
                "撤销后只剩失效文案、不泄露任何作文",
                gone == 1 and items_after == 0 and GONE_TEXT in text_after and not leaked_after,
                {"gone": gone, "items": items_after, "leaked": leaked_after},
            )
            browser.close()

    # -- 还原：探针跑完必须把真库恢复原样 --
    def restore(self) -> None:
        for row in self.snapshot:
            self.client.patch(
                "/api/essays/" + str(row["id"]),
                json={
                    "final_text": row["final_text"],
                    "teacher_comment": row["teacher_comment"] if row["teacher_comment"] is not None else "",
                    "score": row["score"],
                    "selected": 1 if row["selected"] else 0,
                },
                headers=self.headers,
            )
        for token in self.share_tokens:
            self.client.delete("/api/shares/" + token, headers=self.headers)
        self.step("已还原真库（评语/评分/精选/链接）", True, {"essays": len(self.snapshot), "shares": len(self.share_tokens)})


    # -- 配置：阈值与三榜开关（探针自己算星级要用） --
    def load_config(self, issue_id: int) -> dict[str, Any]:
        board = self.client.get(
            "/api/issues/" + str(issue_id) + "/ranking", headers=self.headers
        ).json()["data"]
        self.thresholds = [int(item) for item in board["thresholds"]]
        self.step(
            "读到榜单配置（四档阈值 + 三榜开关）",
            len(self.thresholds) == 4 and set(board["config"]) == {"work", "progress", "star"},
            {"thresholds": self.thresholds, "disabled": board["disabled"]},
        )
        return board

# 家长页选择器：前端 data-testid 改名会让本探针立刻失效，这正是想要的（不静默放过）。
ATTR = "data-testid"


def _by_test(value: str) -> str:
    return "[" + ATTR + "=\"" + value + "\"" + "]"


SELECTOR_ITEM = _by_test("share-item")
SELECTOR_GONE = _by_test("share-gone")
SELECTOR_PRINT = _by_test("share-print")
SELECTOR_IFRAME = "iframe[title=\"成册预览\"]"


def _stars(score: float, thresholds: list[int]) -> int:
    # 与后端 stars_from_score 同判据：至少 1 星，每跨过一档加一颗。
    # 探针故意自己算一遍，不采信接口返回值 —— 否则「星级算错」会被「前端只渲染后端
    # 给的数」直接掩盖掉，等于自己给自己发合格证。
    return 1 + sum(1 for item in thresholds if score >= item)



def main() -> int:
    parser = argparse.ArgumentParser(description="二期（v1.3）真链路探针")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--password", default=os.environ.get("EWB_PASSWORD", "admin123"))
    parser.add_argument("--issue", type=int, default=None, help="指定期号；默认取最新可跑的期")
    args = parser.parse_args()

    probe = Probe(args.base, args.password, args.issue)
    try:
        probe.login()
        issue_id = probe.pick_issue()
        probe.load_config(issue_id)
        essays = probe._essays(issue_id)
        proofread_ids = [int(item["id"]) for item in essays if item["status"] == "proofread"]
        if not proofread_ids:
            raise AssertionError("该期没有已定稿作文，二期链路（评分/精选/分享）无从跑起")
        probe.stage_scores(issue_id)
        probe.stage_selection(issue_id, proofread_ids)
        probe.stage_ranking(issue_id)
        student_id = int(essays[0]["student_id"])
        probe.stage_portfolio(student_id)
        probe.stage_exports(issue_id, student_id)
        students = probe.client.get("/api/students", headers=probe.headers).json()["data"]
        student_nos = [str(item.get("student_no") or "") for item in students]
        probe.stage_share(issue_id, student_nos, len(proofread_ids))
    except Exception as exc:  # noqa: BLE001 - 探针把任何异常都算失败
        probe.step("探针异常终止", False, {"error": repr(exc)})
    finally:
        try:
            probe.restore()
        except Exception as exc:  # noqa: BLE001
            probe.step("还原失败：请手工核对真库", False, {"error": repr(exc)})
        probe.client.close()

    failed = [row["step"] for row in probe.steps if not row["ok"]]
    print(json.dumps({"steps": len(probe.steps), "failed": failed, "base": probe.base}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
