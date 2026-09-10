"""OCR 引擎适配器：配置驱动、可替换。

* ``OcrEngineAdapter``（Protocol）：统一调用契约。
* ``OpenAICompatAdapter``：OpenAI Chat Completions 兼容实现（含超时/重试）。
* ``AdapterFactory``：从 engines.yaml 的 ``[primary]`` / ``[review]`` 段构造实例。

API Key 只从数据目录的 engines.yaml 读取，**绝不硬编码**。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from app.config import AppSettings

# 固定识别提示词（逐字转写，禁止编造）。
RECOGNITION_PROMPT = (
    "请逐字转写这张手写图片中的全部文字内容。要求：1) 保持原有段落结构；"
    "2) 只输出转写文本，不要加任何解释；"
    "3) 无法确认的字请用【?】标注，不要凭语义猜测编造。"
)

# 触发重试的 HTTP 状态码（服务端问题/限流）。
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# 默认可重试的传输层异常。
_RETRYABLE_HTTPX_ERRORS = (httpx.TimeoutException, httpx.TransportError)


@dataclass
class EngineResult:
    """单次识别结果。"""

    text: str
    self_confidence: float = 1.0
    raw_response: dict[str, Any] = field(default_factory=dict)


class OcrEngineError(RuntimeError):
    """引擎调用失败。

    Attributes:
        status_code: HTTP 状态码（若为响应错误）。
        retryable: 是否可重试。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@runtime_checkable
class OcrEngineAdapter(Protocol):
    """识别引擎统一契约。"""

    name: str

    async def recognize(self, image_bytes: bytes, mime: str = "image/jpeg") -> EngineResult:
        """识别单张图片，返回转写文本与自评置信度。"""
        ...  # pragma: no cover - Protocol 声明


class OpenAICompatAdapter:
    """OpenAI Chat Completions 兼容的识别引擎适配器。"""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = float(timeout_s)
        self.max_retries = int(max_retries)
        self._transport = transport

    # -- 内部 ---------------------------------------------------------------
    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_body(self, image_bytes: bytes, mime: str) -> dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{encoded}"
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": RECOGNITION_PROMPT},
                    ],
                }
            ],
            "max_tokens": 6000,
            "temperature": 0.1,
        }

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OcrEngineError("引擎响应缺少 choices", retryable=True)

        first = choices[0]
        if not isinstance(first, dict):
            raise OcrEngineError("引擎响应 choices[0] 结构非法", retryable=True)

        message = first.get("message")
        if not isinstance(message, dict):
            return ""

        content = message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            content = "".join(parts)

        if content is None:
            return ""
        return str(content).strip()

    @staticmethod
    def _self_confidence(payload: dict[str, Any], text: str) -> float:
        """解析引擎自评置信度；无官方字段时给 1.0 基准，空文本给 0.0。"""
        if not text or not text.strip():
            return 0.0

        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            first = choices[0]
            for key in ("self_confidence", "confidence"):
                value = first.get(key)
                if isinstance(value, (int, float)):
                    return max(0.0, min(1.0, float(value)))
        return 1.0

    async def _request_once(self, body: dict[str, Any]) -> EngineResult:
        async with httpx.AsyncClient(
            timeout=self.timeout_s, transport=self._transport
        ) as client:
            response = await client.post(self._endpoint(), json=body, headers=self._headers())

        if response.status_code in _RETRYABLE_STATUS:
            raise OcrEngineError(
                f"引擎返回可重试状态码 {response.status_code}",
                status_code=response.status_code,
                retryable=True,
            )
        if response.status_code >= 400:
            raise OcrEngineError(
                f"引擎返回错误状态码 {response.status_code}",
                status_code=response.status_code,
                retryable=False,
            )

        try:
            payload = response.json()
        except ValueError as exc:  # pragma: no cover - 取决于上游返回
            raise OcrEngineError(f"引擎响应不是合法 JSON: {exc}", retryable=False) from exc

        if not isinstance(payload, dict):
            raise OcrEngineError("引擎响应结构非法", retryable=False)

        text = self._extract_text(payload)
        confidence = self._self_confidence(payload, text)
        return EngineResult(text=text, self_confidence=confidence, raw_response=payload)

    # -- 公共契约 -----------------------------------------------------------
    async def recognize(self, image_bytes: bytes, mime: str = "image/jpeg") -> EngineResult:
        """调用引擎识别单张图片（超时 + 重试）。

        Args:
            image_bytes: 图片二进制内容。
            mime: 图片 MIME 类型。

        Returns:
            ``EngineResult``。

        Raises:
            OcrEngineError: 所有尝试均失败。
        """
        body = self._build_body(image_bytes, mime)
        attempts = self.max_retries + 1
        last_error: OcrEngineError | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await self._request_once(body)
            except OcrEngineError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
            except _RETRYABLE_HTTPX_ERRORS as exc:
                last_error = OcrEngineError(
                    f"引擎传输错误（第 {attempt}/{attempts} 次）：{exc}",
                    retryable=True,
                )
                if attempt >= attempts:
                    raise last_error from exc
            except httpx.HTTPError as exc:  # pragma: no cover - 非可重试传输错误
                raise OcrEngineError(f"引擎 HTTP 错误：{exc}", retryable=False) from exc

        # 理论上不可达，兜底。
        raise last_error or OcrEngineError("引擎调用失败")  # pragma: no cover


class AdapterFactory:
    """从配置字典构造引擎适配器。"""

    DEFAULT_TIMEOUTS: dict[str, float] = {"primary": 30.0, "review": 120.0}
    DEFAULT_RETRIES = 2

    @staticmethod
    def from_config(
        config: dict[str, Any],
        section: str,
        *,
        name: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> OpenAICompatAdapter:
        """按 section（primary/review）构造适配器；配置缺失时使用安全默认值。

        Args:
            config: engines.yaml 解析出的字典。
            section: ``"primary"`` 或 ``"review"``。
            name: 覆盖适配器名称（默认取 section）。
            transport: 自定义 httpx transport（测试注入 MockTransport）。
        """
        raw_block = config.get(section)
        block: dict[str, Any] = raw_block if isinstance(raw_block, dict) else {}

        timeout_default = AdapterFactory.DEFAULT_TIMEOUTS.get(section, 60.0)
        timeout_raw = block.get("timeout", timeout_default)
        try:
            timeout_s = float(timeout_raw)
        except (TypeError, ValueError):
            timeout_s = timeout_default

        retries_raw = block.get("retries", AdapterFactory.DEFAULT_RETRIES)
        try:
            max_retries = int(retries_raw)
        except (TypeError, ValueError):
            max_retries = AdapterFactory.DEFAULT_RETRIES

        return OpenAICompatAdapter(
            name=name or section,
            base_url=str(block.get("base_url", "")),
            api_key=str(block.get("api_key", "")),
            model=str(block.get("model", "")),
            timeout_s=timeout_s,
            max_retries=max_retries,
            transport=transport,
        )

    @staticmethod
    def from_settings(
        settings: AppSettings,
        section: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> OpenAICompatAdapter:
        """从应用配置（数据目录 engines.yaml）构造适配器。"""
        return AdapterFactory.from_config(
            settings.engines_config(), section, transport=transport
        )
