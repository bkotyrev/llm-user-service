"""Application pipeline for the summarization scenario."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from .cache import TTLCache
from .config import Settings
from .llm import LLMClient, LLMError, LLMParseError
from .logging_utils import JsonLogger
from .prompt import build_prompt


FALLBACK_TEXT = "Сервис временно недоступен, попробуйте позже"


@dataclass(frozen=True)
class ServiceResult:
    response: str
    cached: bool
    fallback: bool
    request_id: str
    status_code: int


class ChatService:
    def __init__(
        self,
        settings: Settings,
        client: LLMClient,
        cache: TTLCache,
        logger: JsonLogger,
    ) -> None:
        self.settings = settings
        self.client = client
        self.cache = cache
        self.logger = logger

    async def handle(self, message: str, request_id: str) -> ServiceResult:
        started = time.perf_counter()
        self.logger.emit(
            "request_received",
            request_id=request_id,
            message_length=len(message),
            model=self.settings.model,
        )
        system_prompt, user_prompt = build_prompt(message)
        key = _cache_key(
            message=message,
            model=self.settings.model,
            temperature=self.settings.temperature,
            system_prompt=system_prompt,
        )

        cache_started = time.perf_counter()
        cached_response = self.cache.get(key)
        self.logger.emit(
            "cache_lookup",
            request_id=request_id,
            result="hit" if cached_response is not None else "miss",
            duration_ms=round((time.perf_counter() - cache_started) * 1000, 2),
        )
        if cached_response is not None:
            self.logger.emit(
                "response_sent",
                request_id=request_id,
                cached=True,
                fallback=False,
                status_code=200,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            return ServiceResult(cached_response, True, False, request_id, 200)

        self.logger.emit(
            "prompt_built",
            request_id=request_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        try:
            raw_response = await self.client.generate(
                system_prompt=system_prompt,
                message=user_prompt,
                request_id=request_id,
            )
            response = _postprocess(raw_response)
            self.logger.emit(
                "postprocess_complete",
                request_id=request_id,
                response=response,
            )
        except LLMError as exc:
            self.logger.emit(
                "llm_error",
                request_id=request_id,
                error_type=type(exc).__name__,
                error=str(exc),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            self.logger.emit(
                "response_sent",
                request_id=request_id,
                cached=False,
                fallback=True,
                status_code=503,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            return ServiceResult(FALLBACK_TEXT, False, True, request_id, 503)

        self.cache.set(key, response)
        self.logger.emit(
            "cache_store",
            request_id=request_id,
            ttl_seconds=self.settings.cache_ttl_seconds,
        )
        self.logger.emit(
            "response_sent",
            request_id=request_id,
            cached=False,
            fallback=False,
            status_code=200,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return ServiceResult(response, False, False, request_id, 200)


def _cache_key(
    *, message: str, model: str, temperature: float, system_prompt: str
) -> str:
    canonical = json.dumps(
        {
            "message": message,
            "model": model,
            "temperature": temperature,
            "system_prompt": system_prompt,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _postprocess(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise LLMParseError("Пустой ответ модели")
    return normalized
