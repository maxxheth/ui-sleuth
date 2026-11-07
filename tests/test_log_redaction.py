"""Scrapling must never print proxy credentials."""

from __future__ import annotations

import io
import logging

import pytest

from ui_sleuth.log_redaction import (
    CredentialRedactingFilter,
    install_scrapling_log_redaction,
    redact_text,
)

FMT = "Proxy '{proxy}' failed (attempt {n}) | Retrying in 1s..."
URL_PROXY = "http://user-abc:secretpw@gate.decodo.com:10001"
DICT_PROXY = {
    "server": "http://gate.decodo.com:10001",
    "username": "user-abc",
    "password": "secretpw",
}


@pytest.fixture
def scrapling_logger():
    logger = logging.getLogger("scrapling")
    install_scrapling_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    install_scrapling_log_redaction()  # attaches to the new handler too
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    yield logger, stream
    logger.removeHandler(handler)
    logger.setLevel(old_level)


@pytest.mark.parametrize("proxy", [URL_PROXY, DICT_PROXY], ids=["url", "dict"])
def test_scrapling_proxy_log_redacted(scrapling_logger, caplog, proxy):
    logger, stream = scrapling_logger
    with caplog.at_level(logging.DEBUG, logger="scrapling"):
        logger.warning(FMT.format(proxy=proxy, n=1))
    assert "secretpw" not in stream.getvalue()
    assert "gate.decodo.com" in stream.getvalue()
    assert "secretpw" not in caplog.text
    assert all("secretpw" not in r.getMessage() for r in caplog.records)
    assert "gate.decodo.com" in caplog.text


def test_scrapling_logproxy_redacted(scrapling_logger):
    from scrapling.core.utils import log

    _, stream = scrapling_logger
    log.warning(FMT.format(proxy=DICT_PROXY, n=2))
    log.warning(FMT.format(proxy=URL_PROXY, n=3))
    assert "secretpw" not in stream.getvalue()
    assert "gate.decodo.com:10001" in stream.getvalue()


def test_child_logger_redacted_via_handler(scrapling_logger):
    _, stream = scrapling_logger
    logging.getLogger("scrapling.spiders.x").error("bad %s", URL_PROXY)
    assert "secretpw" not in stream.getvalue()


def test_exception_text_redacted(scrapling_logger):
    logger, stream = scrapling_logger
    try:
        raise RuntimeError(f"connect failed via {URL_PROXY}")
    except RuntimeError:
        logger.exception("Failed")
    assert "secretpw" not in stream.getvalue()
    assert "gate.decodo.com" in stream.getvalue()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://u:p@h.io:1", "http://u:***@h.io:1"),
        ("socks5://u:p%40ss@h.io:1/x", "socks5://u:***@h.io:1/x"),
        ("http://u:se/cr@et@h.io:10001", "http://u:***@h.io:10001"),
        ("use u:pw@h.io:80 now", "use u:***@h.io:80 now"),
        ("{'password': 'a\\'b', 'x': 1}", "{'password': '***', 'x': 1}"),
        ('{"password": "pw"}', '{"password": "***"}'),
        ("gate.decodo.com:10001:user:pw", "gate.decodo.com:10001:user:***"),
    ],
)
def test_redact_text_patterns(raw, expected):
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Fetched https://example.com/a?b=1 in 12:30:45",
        "mail me@example.com or user: note",
        "no creds",
    ],
)
def test_non_credential_text_unchanged(text):
    assert redact_text(text) == text


def test_non_credential_record_unchanged():
    rec = logging.LogRecord("scrapling", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    assert CredentialRedactingFilter().filter(rec)
    assert rec.getMessage() == "hello world"


def test_install_idempotent():
    for _ in range(3):
        install_scrapling_log_redaction()
    logger = logging.getLogger("scrapling")
    count = lambda t: sum(isinstance(f, CredentialRedactingFilter) for f in t.filters)  # noqa: E731
    assert count(logger) == 1
    assert all(count(h) == 1 for h in logger.handlers)
