"""HTTP request and response models."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: Annotated[
        str,
        Field(
            min_length=1,
            max_length=1000,
            description="Пользовательский текст длиной от 1 до 1000 символов.",
            examples=["Составь краткое резюме текста о планировании проекта."],
        ),
    ]

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message не должен быть пустым")
        return normalized


class ChatResponse(BaseModel):
    response: str = Field(description="Результат обработки пользовательского запроса.")
    cached: bool = Field(description="Признак ответа из TTL-кэша.")
    fallback: bool = Field(description="Признак ответа при недоступной модели.")
    request_id: str = Field(description="Идентификатор запроса для диагностики.")


class HealthResponse(BaseModel):
    status: str
    mode: str
    model: str
    cache_ttl_seconds: int


class ErrorResponse(BaseModel):
    detail: str
    errors: list[dict] | None = None
