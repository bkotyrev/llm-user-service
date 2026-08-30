"""FastAPI entry point with optional syslog logging."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cache import TTLCache
from .config import settings
from .llm import create_llm_client
from .logging_utils import configure_logger
from .schemas import ChatRequest, ChatResponse, ErrorResponse, HealthResponse
from .service import ChatService


logger = configure_logger(
    settings.log_file,
    syslog_host=settings.syslog_host,
    syslog_port=settings.syslog_port,
    syslog_protocol=settings.syslog_protocol,
    syslog_facility=settings.syslog_facility,
)
cache = TTLCache(settings.cache_ttl_seconds)
llm_client = create_llm_client(settings, logger)
chat_service = ChatService(settings, llm_client, cache, logger)

app = FastAPI(
    title="LLM User Service",
    summary="Минимальный сервис суммаризации текста через LLM.",
    version="1.0.0",
)

static_dir = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/health", response_model=HealthResponse, tags=["service"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        mode=settings.mode,
        model=settings.model,
        cache_ttl_seconds=settings.cache_ttl_seconds,
    )


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["chat"],
    responses={
        400: {"model": ErrorResponse, "description": "Некорректный JSON или message."},
        503: {"model": ChatResponse, "description": "Модель временно недоступна."},
    },
)
async def chat(payload: ChatRequest) -> JSONResponse:
    request_id = str(uuid.uuid4())
    result = await chat_service.handle(payload.message, request_id)
    body = ChatResponse(
        response=result.response,
        cached=result.cached,
        fallback=result.fallback,
        request_id=result.request_id,
    ).model_dump()
    return JSONResponse(status_code=result.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = str(uuid.uuid4())
    # Pydantic may place a ``ValueError`` instance in ``ctx`` for custom
    # validators (for example, a whitespace-only message). Convert it before
    # handing the payload to JSONResponse so every validation failure remains
    # a clean HTTP 400 rather than surfacing as a serialization 500.
    errors = jsonable_encoder(
        exc.errors(), custom_encoder={Exception: str, BaseException: str}
    )
    logger.emit(
        "validation_error",
        request_id=request_id,
        path=request.url.path,
        errors=errors,
    )
    return JSONResponse(
        status_code=400,
        content={
            "detail": (
                "Проверьте JSON и поле message: оно обязательно и содержит "
                "от 1 до 1000 символов."
            ),
            "errors": errors,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if exc.status_code == 404 and request.url.path == "/chat":
        return JSONResponse(status_code=404, content={"detail": "Маршрут не найден"})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
