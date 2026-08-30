"""Environment-backed service configuration with optional syslog output."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from logging.handlers import SysLogHandler

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _read_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _read_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    mode: str
    base_url: str
    model: str
    temperature: float
    timeout_seconds: float
    max_retries: int
    retry_base_delay: float
    cache_ttl_seconds: int
    log_file: Path
    syslog_host: str | None
    syslog_port: int
    syslog_protocol: str
    syslog_facility: str

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("LLM_MODE", "auto").strip().lower()
        if mode not in {"auto", "proxy", "stub"}:
            mode = "auto"
        protocol = os.getenv("SYSLOG_PROTOCOL", "udp").strip().lower()
        if protocol not in {"udp", "tcp"}:
            protocol = "udp"
        host = os.getenv("SYSLOG_HOST", "").strip() or None
        port = max(1, min(_read_int("SYSLOG_PORT", 514), 65535))
        facility = os.getenv("SYSLOG_FACILITY", "local0").strip() or "local0"
        # SysLogHandler raises KeyError for unknown facility names. Normalize
        # invalid optional logging configuration before constructing the app.
        if facility not in SysLogHandler.facility_names:
            facility = "local0"

        return cls(
            mode=mode,
            base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:8317/v1").rstrip("/"),
            model=os.getenv("LLM_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini",
            temperature=max(0.0, min(_read_float("LLM_TEMPERATURE", 0.2), 2.0)),
            timeout_seconds=max(0.1, _read_float("LLM_TIMEOUT_SECONDS", 15.0)),
            max_retries=max(0, min(_read_int("LLM_MAX_RETRIES", 2), 2)),
            retry_base_delay=max(0.0, _read_float("LLM_RETRY_BASE_DELAY", 0.25)),
            cache_ttl_seconds=max(1, _read_int("CACHE_TTL_SECONDS", 600)),
            log_file=Path(os.getenv("LOG_FILE", str(PROJECT_ROOT / "logs" / "service.jsonl"))),
            syslog_host=host,
            syslog_port=port,
            syslog_protocol=protocol,
            syslog_facility=facility,
        )

    @property
    def token(self) -> str | None:
        return os.getenv("CODEX_CLI_TOKEN")


settings = Settings.from_env()
