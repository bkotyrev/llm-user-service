"""LLM clients for CLIProxy and deterministic local demonstrations."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from .config import Settings
from .logging_utils import JsonLogger


_TRANSIENT_STATUS_CODES = frozenset({408, 425, 429})


class LLMError(Exception):
    """Base error for model calls."""


class LLMUnavailable(LLMError):
    """The configured model cannot be reached or accepted the request."""


class LLMParseError(LLMError):
    """The provider returned an unusable response."""


class LLMClient(Protocol):
    async def generate(
        self,
        system_prompt: str,
        message: str,
        request_id: str,
    ) -> str: ...


class StubLLMClient:
    """Deterministic client used for local manual checks."""

    async def generate(
        self,
        system_prompt: str,
        message: str,
        request_id: str,
    ) -> str:
        del system_prompt, request_id
        words = " ".join(message.split())
        if len(words) > 220:
            words = words[:217].rstrip() + "..."
        return f"Краткое резюме: {words}"


class CLIProxyClient:
    def __init__(self, settings: Settings, logger: JsonLogger) -> None:
        self.settings = settings
        self.logger = logger

    async def generate(
        self,
        system_prompt: str,
        message: str,
        request_id: str,
    ) -> str:
        token = self.settings.token
        if not token:
            raise LLMUnavailable("CODEX_CLI_TOKEN не задан")

        url = f"{self.settings.base_url}/responses"
        payload = {
            "model": self.settings.model,
            "instructions": system_prompt,
            "input": message,
            "temperature": self.settings.temperature,
            "max_output_tokens": 512,
            "store": False,
        }

        for attempt in range(self.settings.max_retries + 1):
            started = asyncio.get_running_loop().time()
            try:
                async with httpx.AsyncClient(
                    timeout=self.settings.timeout_seconds
                ) as client:
                    response = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                elapsed_ms = round(
                    (asyncio.get_running_loop().time() - started) * 1000, 2
                )
                self.logger.emit(
                    "llm_attempt",
                    request_id=request_id,
                    attempt=attempt + 1,
                    status_code=response.status_code,
                    duration_ms=elapsed_ms,
                )
                if (
                    response.status_code in _TRANSIENT_STATUS_CODES
                    or 500 <= response.status_code <= 599
                ):
                    raise _TransientProviderError(
                        f"CLIProxy вернул HTTP {response.status_code}"
                    )
                if response.status_code >= 400:
                    raise LLMUnavailable(
                        f"CLIProxy вернул HTTP {response.status_code}"
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise LLMParseError("Ответ CLIProxy не является JSON") from exc
                try:
                    text = _extract_text(data)
                except LLMParseError:
                    raise
                except Exception as exc:
                    raise LLMParseError(
                        "Ответ CLIProxy имеет неожиданную структуру"
                    ) from exc
                return text.strip()
            except LLMParseError:
                raise
            except LLMUnavailable:
                raise
            except (_TransientProviderError, httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt >= self.settings.max_retries:
                    raise LLMUnavailable(str(exc)) from exc
                delay = self.settings.retry_base_delay * (2**attempt)
                self.logger.emit(
                    "llm_retry",
                    request_id=request_id,
                    attempt=attempt + 1,
                    delay_ms=round(delay * 1000, 2),
                    error=type(exc).__name__,
                )
                await asyncio.sleep(delay)

        raise LLMUnavailable("CLIProxy недоступен")


class _TransientProviderError(Exception):
    pass


def _extract_text(data: Any) -> str:
    """Extract text from common Responses API and proxy response shapes.

    A provider response is treated as malformed when its top-level value or a
    declared output container has the wrong type.  This keeps malformed
    upstream data on the normal 503/fallback path instead of leaking an
    ``AttributeError`` or returning an apparently successful empty response.
    """

    if not isinstance(data, dict):
        raise LLMParseError("Ответ CLIProxy должен быть JSON-объектом")

    # Native Responses API convenience field.
    if "output_text" in data:
        direct = data["output_text"]
        if isinstance(direct, str) and direct.strip():
            return direct
        if direct is not None and not isinstance(direct, str):
            raise LLMParseError("Поле output_text имеет неверный тип")

    chunks: list[str] = []

    if "output" in data and data["output"] is not None:
        output = data["output"]
        if not isinstance(output, list):
            raise LLMParseError("Поле output имеет неверный тип")
        for item in output:
            if not isinstance(item, dict):
                raise LLMParseError("Элемент output имеет неверный тип")
            chunks.extend(_extract_output_item(item))

    # A few Responses-compatible proxies expose Chat Completions-shaped data.
    if not chunks and "choices" in data and data["choices"] is not None:
        choices = data["choices"]
        if not isinstance(choices, list):
            raise LLMParseError("Поле choices имеет неверный тип")
        for choice in choices:
            if not isinstance(choice, dict):
                raise LLMParseError("Элемент choices имеет неверный тип")
            message = choice.get("message")
            if message is not None:
                if not isinstance(message, dict):
                    raise LLMParseError("Поле message имеет неверный тип")
                chunks.extend(_extract_content_value(message.get("content")))
            if isinstance(choice.get("text"), str):
                chunks.append(choice["text"])

    # Simple proxy shims sometimes return {"response": "..."} or {"text":
    # "..."}; accept those explicit fields, but never stringify arbitrary
    # metadata such as IDs or usage counters.
    if not chunks:
        for field in ("response", "text"):
            value = data.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise LLMParseError(f"Поле {field} имеет неверный тип")
                if value.strip():
                    chunks.append(value)

    text = "\n".join(part for part in chunks if isinstance(part, str) and part.strip())
    if not text:
        raise LLMParseError("Ответ CLIProxy не содержит текста")
    return text


def _extract_output_item(item: dict[str, Any]) -> list[str]:
    chunks: list[str] = []
    # Some lightweight shims put text directly on the output item.
    if "text" in item:
        value = item["text"]
        if isinstance(value, str):
            chunks.append(value)
        elif value is not None:
            raise LLMParseError("Поле output.text имеет неверный тип")

    if "content" not in item or item["content"] is None:
        return chunks
    content = item["content"]
    if isinstance(content, str):
        return chunks + ([content] if content.strip() else [])
    if isinstance(content, dict):
        return chunks + _extract_content_value(content)
    if not isinstance(content, list):
        raise LLMParseError("Поле output.content имеет неверный тип")
    for part in content:
        if not isinstance(part, dict):
            raise LLMParseError("Элемент output.content имеет неверный тип")
        chunks.extend(_extract_content_value(part))
    return chunks


def _extract_content_value(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content] if content.strip() else []
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            chunks.extend(_extract_content_value(part))
        return chunks
    if not isinstance(content, dict):
        raise LLMParseError("Содержимое ответа имеет неверный тип")
    value = content.get("text")
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        nested = value.get("value")
        if isinstance(nested, str):
            return [nested] if nested.strip() else []
        if nested is not None:
            raise LLMParseError("Поле text.value имеет неверный тип")
    elif value is not None:
        raise LLMParseError("Поле content.text имеет неверный тип")
    # Chat Completions can return an array of content parts nested under
    # message.content; recurse only through that explicit field.
    nested_parts = content.get("content")
    if nested_parts is not None:
        if not isinstance(nested_parts, list):
            raise LLMParseError("Вложенное content имеет неверный тип")
        chunks: list[str] = []
        for part in nested_parts:
            chunks.extend(_extract_content_value(part))
        return chunks
    return []


def create_llm_client(settings: Settings, logger: JsonLogger) -> LLMClient:
    if settings.mode == "stub":
        return StubLLMClient()
    return CLIProxyClient(settings, logger)
