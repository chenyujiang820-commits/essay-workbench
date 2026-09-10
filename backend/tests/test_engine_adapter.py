"""T02：引擎适配器（httpx.MockTransport 模拟成功/超时/重试/504）。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.pipeline.engine_adapter import (
    RECOGNITION_PROMPT,
    AdapterFactory,
    OcrEngineError,
    OpenAICompatAdapter,
)

ENDPOINT = "https://engine.example/v1/chat/completions"


def make_adapter(*, max_retries: int = 2, transport: httpx.MockTransport) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(
        name="primary",
        base_url="https://engine.example/v1",
        api_key="secret-key",
        model="m1",
        timeout_s=30.0,
        max_retries=max_retries,
        transport=transport,
    )


def ok_response(request: httpx.Request, text: str, extra_choice: dict[str, Any] | None = None) -> httpx.Response:
    choice: dict[str, Any] = {"message": {"role": "assistant", "content": text}}
    if extra_choice:
        choice.update(extra_choice)
    return httpx.Response(200, json={"choices": [choice]}, request=request)


def test_prompt_constant_matches_spec() -> None:
    assert RECOGNITION_PROMPT == (
        "请逐字转写这张手写图片中的全部文字内容。要求：1) 保持原有段落结构；"
        "2) 只输出转写文本，不要加任何解释；"
        "3) 无法确认的字请用【?】标注，不要凭语义猜测编造。"
    )


async def test_recognize_success_parses_text_and_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return ok_response(request, "  你好，世界。  ")

    adapter = make_adapter(transport=httpx.MockTransport(handler))
    result = await adapter.recognize(b"fake-bytes", "image/jpeg")

    assert result.text == "你好，世界。"
    assert result.self_confidence == 1.0
    assert captured["url"] == ENDPOINT
    assert captured["auth"] == "Bearer secret-key"

    body = captured["body"]
    assert body["model"] == "m1"
    assert body["max_tokens"] == 6000
    assert body["temperature"] == 0.1
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert content[1] == {"type": "text", "text": RECOGNITION_PROMPT}


async def test_recognize_uses_official_confidence_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response(request, "正文", extra_choice={"self_confidence": 0.42})

    adapter = make_adapter(transport=httpx.MockTransport(handler))
    result = await adapter.recognize(b"x", "image/jpeg")
    assert result.self_confidence == 0.42


async def test_recognize_empty_text_is_zero_confidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok_response(request, "")

    adapter = make_adapter(transport=httpx.MockTransport(handler))
    result = await adapter.recognize(b"x", "image/jpeg")
    assert result.text == ""
    assert result.self_confidence == 0.0


async def test_recognize_supports_content_parts_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": [{"type": "text", "text": "段落一"}]}}]},
            request=request,
        )

    adapter = make_adapter(transport=httpx.MockTransport(handler))
    result = await adapter.recognize(b"x", "image/jpeg")
    assert result.text == "段落一"


async def test_recognize_retries_on_timeout_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise httpx.TimeoutException("too slow", request=request)
        return ok_response(request, "重试后成功")

    adapter = make_adapter(max_retries=2, transport=httpx.MockTransport(handler))
    result = await adapter.recognize(b"x", "image/jpeg")

    assert attempts["n"] == 3
    assert result.text == "重试后成功"


async def test_recognize_raises_after_retries_on_504() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(504, json={"error": "gateway timeout"}, request=request)

    adapter = make_adapter(max_retries=2, transport=httpx.MockTransport(handler))

    with pytest.raises(OcrEngineError) as excinfo:
        await adapter.recognize(b"x", "image/jpeg")

    assert attempts["n"] == 3  # 1 次 + 2 次重试
    assert excinfo.value.status_code == 504
    assert excinfo.value.retryable is True


async def test_recognize_non_retryable_status_raises_immediately() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad request"}, request=request)

    adapter = make_adapter(max_retries=2, transport=httpx.MockTransport(handler))

    with pytest.raises(OcrEngineError) as excinfo:
        await adapter.recognize(b"x", "image/jpeg")

    assert attempts["n"] == 1
    assert excinfo.value.retryable is False


async def test_recognize_missing_choices_is_retryable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, request=request)

    adapter = make_adapter(max_retries=0, transport=httpx.MockTransport(handler))
    with pytest.raises(OcrEngineError):
        await adapter.recognize(b"x", "image/jpeg")


def test_adapter_factory_from_config() -> None:
    config = {
        "primary": {
            "base_url": "https://p.example/v1/",
            "api_key": "k1",
            "model": "pm",
            "timeout": 30,
            "retries": 2,
        },
        "review": {
            "base_url": "https://r.example/v1",
            "api_key": "k2",
            "model": "rm",
            "timeout": 120,
            "retries": 2,
        },
    }
    primary = AdapterFactory.from_config(config, "primary")
    assert primary.name == "primary"
    assert primary.base_url == "https://p.example/v1"
    assert primary.api_key == "k1"
    assert primary.model == "pm"
    assert primary.timeout_s == 30.0
    assert primary.max_retries == 2

    review = AdapterFactory.from_config(config, "review")
    assert review.timeout_s == 120.0


def test_adapter_factory_missing_section_uses_defaults() -> None:
    adapter = AdapterFactory.from_config({}, "review")
    assert adapter.base_url == ""
    assert adapter.timeout_s == 120.0  # review 默认长超时
    assert adapter.max_retries == 2

    adapter_primary = AdapterFactory.from_config({}, "primary")
    assert adapter_primary.timeout_s == 30.0


def test_adapter_factory_from_settings(settings) -> None:
    adapter = AdapterFactory.from_settings(settings, "review")
    assert adapter.base_url == "https://review.example/v1"
    assert adapter.model == "review-model"
    assert adapter.timeout_s == 120.0
