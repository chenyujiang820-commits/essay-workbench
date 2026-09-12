"""前端静态托管（SPA）：入口不缓存、深层路由回退、缺失产物真 404。

手机端真机验证暴露：重新构建后手机浏览器仍用旧 index.html，去请求已被删除的
/assets/index-<hash>.js；该请求命中 SPA 回退拿到 HTML，模块脚本静默失败 → 白屏
且无任何报错。两条约束（入口 no-cache、assets 前缀不回退）都在这里锁住。
"""

from __future__ import annotations

from pathlib import Path

from app.main import _clean_request_path, _is_asset_path, _is_entry_path, _SpaStaticFiles
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

INDEX_HTML = "<!doctype html><html><body>SPA 入口</body></html>"
ASSET_JS = "console.log('ok')"


async def _client(dist: Path) -> AsyncClient:
    """在临时 dist 上挂载 SPA 静态托管，返回指向它的异步客户端。"""
    app = FastAPI()
    app.mount("/", _SpaStaticFiles(directory=str(dist), html=True), name="frontend")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://spa-test")


def _make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text(ASSET_JS, encoding="utf-8")
    return dist


async def test_entry_html_is_not_cached(tmp_path: Path) -> None:
    """根路径与 index.html 直连都必须回源校验：手机缓存旧入口是白屏根因。"""
    async with await _client(_make_dist(tmp_path)) as client:
        for path in ("/", "/index.html"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.headers["cache-control"] == "no-cache", path


async def test_deep_frontend_route_falls_back_without_caching(tmp_path: Path) -> None:
    """深层前端路由回退到入口，且同样带上 no-cache。"""
    async with await _client(_make_dist(tmp_path)) as client:
        response = await client.get("/issues/1/upload")
        assert response.status_code == 200
        assert response.text == INDEX_HTML
        assert response.headers["cache-control"] == "no-cache"


async def test_existing_asset_is_served_and_cacheable(tmp_path: Path) -> None:
    """产物按内容哈希命名，不该被入口策略拖成 no-cache。"""
    async with await _client(_make_dist(tmp_path)) as client:
        response = await client.get("/assets/index-abc123.js")
        assert response.status_code == 200
        assert response.text == ASSET_JS
        assert response.headers.get("cache-control") is None


async def test_missing_asset_returns_real_404(tmp_path: Path) -> None:
    """缺失产物必须真 404：用 HTML 顶替会让手机白屏且零线索。"""
    async with await _client(_make_dist(tmp_path)) as client:
        response = await client.get("/assets/index-OLDHASH.js")
        assert response.status_code == 404
        assert "<!doctype html>" not in response.text.lower()


async def test_api_404_is_not_fallback_to_entry(tmp_path: Path) -> None:
    """api 前缀不参与回退（保持既有契约）。"""
    async with await _client(_make_dist(tmp_path)) as client:
        response = await client.get("/api/nope")
        assert response.status_code == 404


def test_path_predicates_cover_shapes_starlette_actually_sends() -> None:
    """判据前必须先规范化：starlette 在 Windows 上传反斜杠，根目录传 "."。"""
    assert _clean_request_path(".") == ""
    assert _clean_request_path("api\\nope") == "api/nope"
    assert _clean_request_path("assets\\index-x.js") == "assets/index-x.js"
    assert _clean_request_path("/./assets/index-x.js") == "assets/index-x.js"
    assert _is_asset_path(_clean_request_path("assets\\index-x.js")) is True
    # 真实回归点：Windows 下未知 api 路径曾经被判成"可回退"，返回 HTML 200
    assert _clean_request_path("api\\nope").startswith("api/") is True
    assert _is_entry_path(_clean_request_path(".")) is True
    assert _is_entry_path(_clean_request_path("index.html")) is True
    assert _is_entry_path(_clean_request_path("present\\1")) is False
