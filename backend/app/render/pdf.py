"""Playwright 无头 Chromium：把整册/单篇 HTML 打印为 A4 PDF。

* 与前端预览**同一套** HTML 模板，保证「所见即所得」。
* ``display_header_footer`` + footer 模板实现「第 x 页 / 共 y 页」页码
  （Chromium 不支持 ``@page`` 的 margin-box 计数器，故采用其原生页脚机制）。
* 每次导出都用 ``async with`` 确保浏览器关闭，不泄漏进程。
"""

from __future__ import annotations

from playwright.async_api import PdfMargins, async_playwright

_MARGIN: PdfMargins = {"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"}
_EMPTY = "<div></div>"
_FOOTER = (
    '<div style="width:100%;font-size:9px;color:#64748b;'
    'padding:0 14mm;text-align:center;font-family:sans-serif;">'
    '第 <span class="pageNumber"></span> 页 / 共 <span class="totalPages"></span> 页'
    "</div>"
)


async def render_pdf(html: str, *, with_page_number: bool = True) -> bytes:
    """用无头 Chromium 把 HTML 打印为 A4 PDF 字节。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            return await page.pdf(
                format="A4",
                print_background=True,
                margin=_MARGIN,
                display_header_footer=with_page_number,
                header_template=_EMPTY,
                footer_template=_FOOTER if with_page_number else _EMPTY,
            )
        finally:
            await browser.close()


async def export_book(html: str) -> bytes:
    """整册导出（带页码页脚）。"""
    return await render_pdf(html, with_page_number=True)


async def export_single(html: str) -> bytes:
    """单篇版式导出（打印张贴用，不带页码页脚）。"""
    return await render_pdf(html, with_page_number=False)


async def export_poster(html: str) -> bytes:
    """精选海报导出（A4 单页，发家长群）。

    与单篇版式一样不带页码页脚，但语义不同：海报的"单页"是**产品硬要求**，
    由模板把正文摘要截断来保证，而不是靠分页；有页脚反而说明它超页了。
    """
    return await render_pdf(html, with_page_number=False)
