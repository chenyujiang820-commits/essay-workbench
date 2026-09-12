# -*- coding: utf-8 -*-
"""投屏翻页实况探针（只读）：API 篇目 vs 真浏览器里真能翻到的篇目。

来源：真机 GAP-08（投屏只能看到一篇）与 GAP-14（拍 3 篇只定稿 1 篇时，后端按
status=proofread 过滤，未定稿的那几篇干脆不进轮播）。单元测试只能证明 React 回调接对了，
证不了真浏览器里按钮没被盖住、篇目没被服务端悄悄丢掉，所以这里按真人操作走一遍，
并把 /present 返回的篇目当成期望集合逐一比对。

只读：不上传、不识别、不改数据，因此不产生引擎费用。

用法：
    cd backend && .venv/Scripts/python.exe ../deploy/probe_present_paging.py

退出码：0 = API 里每篇都翻到了；1 = 有篇目翻不到（GAP-14 复现）。

阶段 2 用响应桩把第二篇改成未定稿（真机三篇都已定稿，造不出这个状态），
只为在真实 Chromium + 生产构建包里确认徽标与顶栏提示会渲染；后端契约由
backend/tests/test_render.py::test_present_includes_unproofread_draft 用真库真 HTTP 覆盖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8000'

READ_JS = r'''() => {
  const q = (s) => document.querySelector(s);
  const btns = Array.from(document.querySelectorAll('button')).map((b) => ({
    text: (b.innerText || '').trim(),
    disabled: b.disabled,
    visible: b.offsetParent !== null
  })).filter((b) => b.text.indexOf('页') >= 0);
  return {
    title: q('[data-testid="present-title"]') ? q('[data-testid="present-title"]').innerText.trim() : null,
    name: q('[data-testid="present-name"]') ? q('[data-testid="present-name"]').innerText.trim() : null,
    progress: q('[data-testid="present-screen-progress"]') ? q('[data-testid="present-screen-progress"]').innerText.trim() : null,
    draft: q('[data-testid="present-draft"]') ? q('[data-testid="present-draft"]').innerText.trim() : null,
    pageButtons: btns,
    columns: document.querySelectorAll('[data-testid="present-column"]').length,
    bodyChars: q('[data-testid="present-body"]') ? q('[data-testid="present-body"]').innerText.replace(/\s/g, '').length : -1
  };
}'''

# 顶栏第二行 p：期数 + 进度 + 未定稿/被排除篇数说明。
NOTICE_JS = r'''() => {
  const nodes = document.querySelectorAll('header p');
  const node = nodes.length > 1 ? nodes[1] : null;
  return node ? node.innerText.trim() : null;
}'''


def main() -> int:
    login = httpx.post(BASE + '/api/auth/login', json={'password': 'admin123'}, timeout=15.0)
    token = str(login.json()['data']['token'])
    api = httpx.Client(base_url=BASE, headers={'Authorization': 'Bearer ' + token}, timeout=20.0)
    payload = api.get('/api/exports/1/present').json()['data']
    expected = [
        {
            'name': item.get('name'),
            'title': (item.get('title') or '')[:14],
            'chars': sum(len(paragraph) for paragraph in item.get('paragraphs') or []),
            'is_draft': bool(item.get('is_draft')),
        }
        for item in payload.get('items') or []
    ]
    print('API items =', len(expected), 'draft_count =', payload.get('draft_count'),
          'excluded_no_text =', payload.get('excluded_no_text'))
    for row in expected:
        print('  -', json.dumps(row, ensure_ascii=False))

    steps = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={'width': 1366, 'height': 768})
        ctx.add_init_script("window.localStorage.setItem('ewb_token', '" + token + "')")
        page = ctx.new_page()
        page.goto(BASE + '/present/1', wait_until='networkidle')
        page.wait_for_selector('[data-testid="present-slide"]', timeout=15000)
        page.wait_for_timeout(600)
        for step in range(6):
            state = page.evaluate(READ_JS)
            state['step'] = step
            steps.append(state)
            nxt = page.query_selector('button:has-text("下一页")')
            if nxt is None or nxt.is_disabled():
                state['stoppedAt'] = 'no-next-or-disabled'
                break
            nxt.click()
            page.wait_for_timeout(500)
        print('--- keyboard ArrowRight x3 ---')
        for _ in range(3):
            page.keyboard.press('ArrowRight')
            page.wait_for_timeout(400)
        steps.append(dict(page.evaluate(READ_JS), step='after-arrow-x3'))

        # ---- 阶段 2：未定稿初稿徽标的真实浏览器取证（响应桩）----
        stub_hits = {
            'hits': 0,
            'marked': 0,
        }

        def stub_draft(route):
            response = route.fetch()
            stub_hits['hits'] += 1
            body = response.json()
            items = body['data']['items']
            if len(items) < 2:
                route.fulfill(response=response)
                return
            stub_hits['marked'] += 1
            items[1]['is_draft'] = True
            body['data']['draft_count'] = 1
            body['data']['excluded_no_text'] = 1
            route.fulfill(response=response, body=json.dumps(body, ensure_ascii=False))

        ctx2 = browser.new_context(viewport={'width': 1366, 'height': 768})
        ctx2.add_init_script("window.localStorage.setItem('ewb_token', '" + token + "')")
        ctx2.route('**/api/exports/*/present*', stub_draft)
        # 末尾的 * 不能省：前端带 ?order=student_no 查询串，glob 匹配整条 URL。
        page2 = ctx2.new_page()
        page2.goto(BASE + '/present/1', wait_until='networkidle')
        page2.wait_for_selector('[data-testid="present-slide"]', timeout=15000)
        page2.wait_for_timeout(600)
        first = page2.evaluate(READ_JS)
        page2.click('button:has-text("下一页")')
        page2.wait_for_timeout(600)
        second = page2.evaluate(READ_JS)
        shot = Path(__file__).resolve().parents[1] / 'artifacts' / '投屏未定稿徽标-1366x768.png'
        page2.screenshot(path=str(shot))
        draft_badge_ok = bool(second.get('draft'))
        first_badge_ok = not first.get('draft')
        notice_text = page2.evaluate(NOTICE_JS)
        print('STUB_PHASE hits =', stub_hits)
        print('STUB_PHASE first_draft_badge =', repr(first.get('draft')))
        print('STUB_PHASE second_draft_badge =', repr(second.get('draft')))
        print('STUB_PHASE header_notice =', repr(notice_text))
        print('STUB_PHASE screenshot =', str(shot))
        ctx2.close()

        browser.close()
    for s in steps:
        print(json.dumps(s, ensure_ascii=False))
    seen = sorted({str(s.get('name')) for s in steps if s.get('name')})
    want = sorted({str(row['name']) for row in expected if row['name']})
    missing = [name for name in want if name not in seen]
    badges = [s for s in steps if s.get('draft')]
    print('SEEN_NAMES     =', json.dumps(seen, ensure_ascii=False))
    print('EXPECTED_NAMES =', json.dumps(want, ensure_ascii=False))
    print('MISSING_NAMES  =', json.dumps(missing, ensure_ascii=False))
    print('SCREENS_TOUCHED =', len(steps), 'DRAFT_BADGE_SEEN =', len(badges))
    if not stub_hits['hits']:
        print('PRESENT_PROBE FAIL: 响应桩一次都没命中（route 通配没匹配上），本次未判定')
        return 1

    if not (draft_badge_ok and first_badge_ok):
        print('PRESENT_PROBE FAIL: 未定稿徽标没渲染, first=' + repr(first.get('draft')) + ', second=' + repr(second.get('draft')))
        return 1
    if not notice_text or '未定稿' not in notice_text or '暂无文字' not in notice_text:
        print('PRESENT_PROBE FAIL: 顶栏没说明未定稿/被排除篇数，实测 =', repr(notice_text))
        return 1
    if missing:
        print('PRESENT_PROBE FAIL: 这些篇目在轮播里翻不到 ->', json.dumps(missing, ensure_ascii=False))
        return 1
    print('PRESENT_PROBE OK: API 里每一篇都真能翻到，未定稿徽标与顶栏说明均已渲染')
    return 0


if __name__ == '__main__':
    sys.exit(main())
