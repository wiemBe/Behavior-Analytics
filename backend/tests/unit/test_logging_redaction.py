"""Redaction of URLs in log records, and the worker's logging configuration.

The worker and beat do not run the API lifespan. These tests apply the signal
the worker runs and then put the process logging state back (AGENTS §4).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
from celery.signals import after_setup_logger, after_setup_task_logger

from app.alerts.notifiers.base import NotificationMessage
from app.alerts.notifiers.channels import GenericWebhookNotifier
from app.core.logging import RedactionFilter, configure_logging
from app.models.enums import AlertTransition, Severity
from app.workers import celery_app as celery_app_module

_TOKEN = "SECRETTOKEN"
_WEBHOOK = f"https://hooks.slack.com/services/T00/B00/{_TOKEN}"


def _message(text: str, args: tuple[object, ...] = ()) -> str:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=text,
        args=args,
        exc_info=None,
    )
    assert RedactionFilter().filter(record) is True
    return record.getMessage()


@pytest.mark.parametrize(
    ("text", "args", "kept", "gone"),
    [
        (
            'HTTP Request: %s %s "%s %d %s"',
            (
                "POST",
                "https://hooks.slack.com/services/T00/B00/SECRETTOKEN",
                "HTTP/1.1",
                200,
                "OK",
            ),
            'HTTP Request: POST https://hooks.slack.com "HTTP/1.1 200 OK"',
            ("SECRETTOKEN", "/services/"),
        ),
        (
            'HTTP Request: %s %s "%s %d %s"',
            ("POST", httpx.URL("https://hooks.example/gzip-bomb"), "HTTP/1.1", 200, "OK"),
            'HTTP Request: POST https://hooks.example "HTTP/1.1 200 OK"',
            ("gzip-bomb",),
        ),
        (
            "posted %s",
            ("https://user:pass@hooks.example/path?token=secret",),
            "posted https://hooks.example",
            ("pass", "token", "/path"),
        ),
        (
            "target https://[2001:db8::1]:8443/secret?t=1",
            (),
            "target https://[2001:db8::1]:8443",
            ("secret", "t=1"),
        ),
        (
            'HTTP Request: POST http://169.254.169.254/latest/meta-data/ "HTTP/1.1 200 OK"',
            (),
            'HTTP Request: POST http://169.254.169.254 "HTTP/1.1 200 OK"',
            ("meta-data",),
        ),
        (
            "see https://hooks.example:8443 now",
            (),
            "see https://hooks.example:8443 now",
            (),
        ),
    ],
)
def test_redaction_filter_keeps_scheme_host_and_port(
    text: str, args: tuple[object, ...], kept: str, gone: tuple[str, ...]
) -> None:
    rendered = _message(text, args)
    assert rendered == kept
    for secret in gone:
        assert secret not in rendered


def test_a_line_without_a_url_is_unchanged() -> None:
    assert _message("queue depth is 4") == "queue depth is 4"


def test_secret_field_tokens_are_still_redacted() -> None:
    token = "12345678-1234-1234-1234-123456789abc"
    rendered = _message(f"Authorization bearer {token}")
    assert token not in rendered
    assert "<redacted>" in rendered


def test_redaction_is_idempotent() -> None:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=(
            "HTTP Request: POST https://hooks.slack.com/services/T00/B00/"
            'SECRETTOKEN "HTTP/1.1 200 OK"'
        ),
        args=(),
        exc_info=None,
    )
    redactor = RedactionFilter()
    assert redactor.filter(record) is True
    once = record.getMessage()
    assert redactor.filter(record) is True
    assert record.getMessage() == once
    assert _TOKEN not in once


class _LoggerState:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.level = logger.level
        self.filters = list(logger.filters)
        self.handlers = list(logger.handlers)
        self.handler_filters = [list(handler.filters) for handler in logger.handlers]

    def restore(self) -> None:
        self.logger.setLevel(self.level)
        self.logger.filters[:] = self.filters
        self.logger.handlers[:] = self.handlers
        for handler, filters in zip(self.handlers, self.handler_filters, strict=True):
            handler.filters[:] = filters


@contextmanager
def _saved_logging(*names: str) -> Iterator[None]:
    states = [_LoggerState(logging.getLogger(name)) for name in names]
    try:
        yield
    finally:
        for state in states:
            state.restore()


def test_configure_logging_quiets_http_client_loggers_and_redacts() -> None:
    root = logging.getLogger()
    with _saved_logging("", "httpx", "httpcore", "uvicorn.access"):
        configure_logging("INFO")
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert any(isinstance(item, RedactionFilter) for item in root.filters)
        assert len(root.handlers) == 1
        configure_logging("INFO")
        assert sum(isinstance(item, RedactionFilter) for item in root.filters) == 1


def _alert() -> NotificationMessage:
    return NotificationMessage(
        title="VOLUME_DROP on fw-01",
        body="average EPS below baseline",
        severity=Severity.HIGH,
        transition=AlertTransition.OPENED,
        alert_id="a1",
        fingerprint="anomaly:abc",
        fields={"observed_value": 3.0},
    )


async def test_worker_logging_hides_the_webhook_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The signal Celery sends after it installs handlers, with caplog attached."""
    assert celery_app_module._configure_worker_and_beat_logging.__module__ == (
        "app.workers.celery_app"
    )
    root = logging.getLogger()
    sentinel = logging.StreamHandler()
    root.addHandler(sentinel)
    task_logger = logging.getLogger("tests.fix59.task")
    task_filters = list(task_logger.filters)
    try:
        with _saved_logging("", "httpx", "httpcore"):
            after_setup_logger.send(
                sender=None,
                logger=root,
                loglevel=logging.INFO,
                logfile=None,
                format=None,
                colorize=None,
            )
            assert sentinel in root.handlers
            assert logging.getLogger("httpx").level == logging.WARNING
            assert logging.getLogger("httpcore").level == logging.WARNING
            after_setup_task_logger.send(
                sender=None,
                logger=task_logger,
                loglevel=logging.INFO,
                logfile=None,
                format=None,
                colorize=None,
            )
            assert any(isinstance(item, RedactionFilter) for item in task_logger.filters)

            def ok(request: httpx.Request) -> httpx.Response:
                return httpx.Response(204, request=request)

            with caplog.at_level(logging.DEBUG):
                async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as client:
                    await GenericWebhookNotifier(client).send(_WEBHOOK, _alert())
                logging.getLogger("httpx").warning(
                    'HTTP Request: %s %s "%s %d %s"',
                    "POST",
                    httpx.URL(_WEBHOOK),
                    "HTTP/1.1",
                    200,
                    "OK",
                )
            blob = caplog.text + "".join(repr(record.__dict__) for record in caplog.records)
            assert _TOKEN not in blob
            assert "/services/" not in blob
            assert "https://hooks.slack.com" in caplog.text
            assert not any(
                record.name == "httpx" and record.levelno < logging.WARNING
                for record in caplog.records
            )
    finally:
        if sentinel in root.handlers:
            root.removeHandler(sentinel)
        task_logger.filters[:] = task_filters
