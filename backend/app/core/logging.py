"""Structured JSON logging.

All logs are single-line JSON so a collector can parse them without regex. A
redaction filter strips anything that looks like a QRadar token or Authorization
header, and reduces any URL to scheme, host and port, before it can reach a log
sink — a webhook URL's path is the credential, and secrets must never appear in
logs.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

from pythonjsonlogger import jsonlogger

# Patterns for values we refuse to log even if a caller passes them by mistake.
_SEC_HEADER = re.compile(r"(SEC|Authorization|sec_token|api_key|password)\b", re.IGNORECASE)
_TOKEN_LIKE = re.compile(
    r"\b[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}\b"
)
# httpx logs `HTTP Request: POST <url>` with the URL as an argument, not as
# already-rendered text. The path and query are the credential; userinfo is too.
_URL = re.compile(
    r"\b(?P<scheme>https?://)"
    r"(?P<userinfo>[^\s/?#@]+@)?"
    r"(?P<host>\[[0-9A-Fa-f:.]+\]|[^:/\s?#]+)"
    r"(?P<port>:\d+)?"
    r"(?P<tail>\S*)",
    re.IGNORECASE,
)


def _mask_url(match: re.Match[str]) -> str:
    userinfo = match.group("userinfo") or ""
    tail = match.group("tail") or ""
    if not userinfo and not tail:
        return match.group(0)
    return f"{match.group('scheme')}{match.group('host')}{match.group('port') or ''}"


def _redact_arg(value: object) -> object:
    if isinstance(value, str):
        return RedactionFilter._redact(value)
    try:
        text = str(value)
    except Exception:
        return value
    if "://" not in text:
        return value
    return RedactionFilter._redact(text)


def _redact_args(
    args: tuple[object, ...] | Mapping[str, object],
) -> tuple[object, ...] | dict[str, object]:
    """Used only when ``%`` formatting itself fails. The success path redacts the
    rendered line, which is where an ``httpx.URL`` argument becomes text."""
    if isinstance(args, tuple):
        return tuple(_redact_arg(item) for item in args)
    return {key: _redact_arg(value) for key, value in args.items()}


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.msg
        if not isinstance(message, str):
            return True
        args = record.args
        if args:
            try:
                rendered_value = message % args
            except (TypeError, ValueError, KeyError):
                record.msg = self._redact(message)
                record.args = _redact_args(args)
                record.__dict__.pop("message", None)
                return True
            rendered = rendered_value if isinstance(rendered_value, str) else str(rendered_value)
        else:
            rendered = message
        redacted = self._redact(rendered)
        if redacted != rendered:
            # The rendered line is what sinks format. Leaving the original
            # arguments in place would interpolate the URL again.
            record.msg = redacted
            record.args = ()
            record.__dict__.pop("message", None)
        return True

    @staticmethod
    def _redact(text: str) -> str:
        if _SEC_HEADER.search(text):
            # Line mentions a secret field name; blunt-redact any long token.
            text = _TOKEN_LIKE.sub("<redacted>", text)
        return _URL.sub(_mask_url, text)


class _JsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(
        self, log_record: dict[str, Any], record: logging.LogRecord, message_dict: dict[str, Any]
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record.setdefault("level", record.levelname)
        log_record.setdefault("logger", record.name)


def install_redaction_filter(logger: logging.Logger) -> None:
    """Attach the filter to ``logger`` and to the handlers it already has.

    The logger filter runs before every handler, including one added by Celery
    or by a test's caplog. A second copy on the handler covers a logger that
    does not propagate.
    """
    if not any(isinstance(existing, RedactionFilter) for existing in logger.filters):
        logger.addFilter(RedactionFilter())
    for handler in logger.handlers:
        if not any(isinstance(existing, RedactionFilter) for existing in handler.filters):
            handler.addFilter(RedactionFilter())


def configure_logging(level: str = "INFO", *, keep_handlers: bool = False) -> None:
    """Install JSON logging, or, when Celery already did, only the shared parts.

    ``httpx`` and ``httpcore`` stay at WARNING. Their INFO line is
    ``HTTP Request: POST <url>`` and a webhook URL's path is the credential.
    ``keep_handlers`` is for the worker and beat: Celery has already installed
    its own handlers, and clearing them would drop task and beat logs.
    """
    root = logging.getLogger()
    if not keep_handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            _JsonFormatter("%(asctime)s %(level)s %(logger)s %(message)s")
        )
        root.handlers.clear()
        root.addHandler(handler)
        # uvicorn access logs are noisy and duplicate our request logging.
        logging.getLogger("uvicorn.access").handlers.clear()
    install_redaction_filter(root)
    root.setLevel(level.upper())
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
