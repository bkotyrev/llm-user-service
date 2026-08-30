"""Structured JSON logging to console, file and optional network syslog."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from logging.handlers import SysLogHandler
from pathlib import Path
from typing import Any


_LOGGER_NAME = "llm_user_service"
_LOGGER_LOCK = threading.RLock()
_SECRET_KEY_PARTS = (
    "token",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "password",
)


def _redact(value: Any, key: str | None = None) -> Any:
    """Return JSON-safe fields with credentials removed."""

    if key and any(part in key.lower() for part in _SECRET_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact(item) for item in value]
    token = os.getenv("CODEX_CLI_TOKEN")
    if token and isinstance(value, str):
        return value.replace(token, "[REDACTED]")
    return value


class JsonLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def emit(self, event: str, **fields: Any) -> None:
        record = {"event": event, **fields}
        try:
            self._logger.info(
                json.dumps(_redact(record), ensure_ascii=False, default=str)
            )
        except Exception:
            # Logging is best effort; a broken sink must not fail requests.
            return


class _QuietSysLogHandler(SysLogHandler):
    """Keep a logging outage from affecting request processing."""

    def __init__(self, *args: Any, connect_timeout: float = 1.0, **kwargs: Any) -> None:
        try:
            self.connect_timeout = max(0.1, float(connect_timeout))
        except (TypeError, ValueError):
            self.connect_timeout = 1.0
        super().__init__(*args, **kwargs)

    def createSocket(self) -> None:  # noqa: N802
        """Create sockets with a timeout already applied before TCP connect."""

        address = self.address
        socktype = self.socktype
        if isinstance(address, str):
            self.unixsocket = True
            try:
                self._connect_unixsocket(address)
                if self.socket is not None:
                    self.socket.settimeout(self.connect_timeout)
            except OSError:
                self.socket = None
            return

        self.unixsocket = False
        if socktype is None:
            socktype = socket.SOCK_DGRAM
        host, port = address
        resources = socket.getaddrinfo(host, port, 0, socktype)
        if not resources:
            raise OSError("getaddrinfo returns an empty list")

        selected_socket = None
        selected_socktype = socktype
        last_error: OSError | None = None
        for family, candidate_type, protocol, _, sockaddr in resources:
            candidate = None
            try:
                candidate = socket.socket(family, candidate_type, protocol)
                # Set this before connect: SysLogHandler's stock implementation
                # connects first, which can otherwise block application start.
                candidate.settimeout(self.connect_timeout)
                if candidate_type == socket.SOCK_STREAM:
                    candidate.connect(sockaddr)
                selected_socket = candidate
                selected_socktype = candidate_type
                break
            except OSError as exc:
                last_error = exc
                if candidate is not None:
                    try:
                        candidate.close()
                    except OSError:
                        pass

        if selected_socket is None:
            raise last_error or OSError("unable to create syslog socket")
        self.socket = selected_socket
        self.socktype = selected_socktype

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        del record

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except Exception:
            # Cover formatter/platform errors that can escape handleError.
            return


def _handler_kind(handler: logging.Handler) -> str | None:
    return getattr(handler, "_llm_user_service_kind", None)


def _tag_handler(handler: logging.Handler, kind: str) -> logging.Handler:
    setattr(handler, "_llm_user_service_kind", kind)
    return handler


def _find_handler(logger: logging.Logger, kind: str) -> logging.Handler | None:
    return next((item for item in logger.handlers if _handler_kind(item) == kind), None)


def _remove_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    logger.removeHandler(handler)
    try:
        handler.close()
    except Exception:
        return


def configure_logger(
    log_file: Path,
    *,
    syslog_host: str | None = None,
    syslog_port: int = 514,
    syslog_protocol: str = "udp",
    syslog_facility: str = "local0",
) -> JsonLogger:
    # Configuration can be called by tests and by application reloaders, so
    # serialize it and reconcile only handlers owned by this module.
    with _LOGGER_LOCK:
        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        formatter = logging.Formatter("%(message)s")

        protocol = str(syslog_protocol or "udp").strip().lower()
        if protocol not in {"udp", "tcp"}:
            protocol = "udp"
        try:
            port = int(syslog_port)
        except (TypeError, ValueError):
            port = 514
        port = max(1, min(port, 65535))
        facility = str(syslog_facility or "local0").strip().lower()
        if facility not in SysLogHandler.facility_names:
            facility = "local0"
        host = str(syslog_host or "").strip() or None

        console = _find_handler(logger, "console")
        if console is None:
            console = _tag_handler(logging.StreamHandler(), "console")
            console.setFormatter(formatter)
            logger.addHandler(console)

        desired_file = Path(log_file).resolve(strict=False)
        file_handler = _find_handler(logger, "file")
        if file_handler is not None and Path(
            getattr(file_handler, "baseFilename", "")
        ).resolve(strict=False) != desired_file:
            _remove_handler(logger, file_handler)
            file_handler = None
        if file_handler is None:
            try:
                desired_file.parent.mkdir(parents=True, exist_ok=True)
                file_handler = _tag_handler(
                    logging.FileHandler(desired_file, encoding="utf-8"), "file"
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            except (OSError, ValueError) as exc:
                logger.warning(
                    json.dumps(
                        {
                            "event": "file_log_unavailable",
                            "error_type": type(exc).__name__,
                        },
                        ensure_ascii=False,
                    )
                )

        network_handler = _find_handler(logger, "syslog")
        desired_syslog = (host, port, protocol, facility)
        if network_handler is not None and getattr(
            network_handler, "_llm_user_service_config", None
        ) != desired_syslog:
            _remove_handler(logger, network_handler)
            network_handler = None
        if host and network_handler is None:
            socktype = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
            try:
                network_handler = _QuietSysLogHandler(
                    address=(host, port), facility=facility, socktype=socktype
                )
                if getattr(network_handler, "socket", None) is not None:
                    network_handler.socket.settimeout(1.0)
                _tag_handler(network_handler, "syslog")
                setattr(network_handler, "_llm_user_service_config", desired_syslog)
                network_handler.setFormatter(formatter)
                logger.addHandler(network_handler)
                logger.info(
                    json.dumps(
                        {
                            "event": "syslog_configured",
                            "host": host,
                            "port": port,
                            "protocol": protocol,
                            "facility": facility,
                        },
                        ensure_ascii=False,
                    )
                )
            except Exception as exc:
                logger.warning(
                    json.dumps(
                        {
                            "event": "syslog_unavailable",
                            "error_type": type(exc).__name__,
                        },
                        ensure_ascii=False,
                    )
                )
        elif not host and network_handler is not None:
            _remove_handler(logger, network_handler)

        return JsonLogger(logger)
