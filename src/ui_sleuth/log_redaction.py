"""Redact proxy credentials from log records (notably Scrapling's own logger).

Scrapling logs ``Proxy '{proxy}' failed (attempt N)`` with the raw proxy, which is either a
``scheme://user:pass@host:port`` string or a Playwright dict repr containing ``'password'``.
The patterns here are self-contained and deliberately conservative.
"""

from __future__ import annotations

import logging
import re

MASK = "***"

# scheme://user:pass@host  (password may contain '@' or '/'; greedy match backtracks to the
# last '@'). Fails closed: ``http://host:8080/a@b`` is over-masked rather than risk a leak.
_URL_USERINFO = re.compile(r"(?P<pre>[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@'\"]*:)[^\s'\"]*@")
# user:pass@host:port without a scheme (requires a trailing :port to avoid matching e-mails)
_BARE_USERINFO = re.compile(r"(?P<pre>(?<![\w/:.%@-])[\w.%-]+:)[^\s@/'\"]+@(?=[\w.-]+:\d+)")
# 'password': '...' / "password": "..." (dict / JSON repr, tolerates escaped quotes)
_DICT_PASSWORD = re.compile(
    r"(?P<pre>(['\"])password\2\s*:\s*)(?P<q>['\"])(?:\\.|(?!(?P=q)).)*(?P=q)"
)
# host.example:port:user:pass (host must be dotted, port numeric)
_HOST_PORT_USER_PASS = re.compile(
    r"\b(?P<pre>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+:\d{2,5}:[^\s:@/'\"]+:)[^\s:'\",)}\]]+"
)


def redact_text(text: str) -> str:
    """Return ``text`` with proxy credentials masked."""
    if not text:
        return text
    text = _URL_USERINFO.sub(lambda m: f"{m['pre']}{MASK}@", text)
    text = _BARE_USERINFO.sub(lambda m: f"{m['pre']}{MASK}@", text)
    text = _DICT_PASSWORD.sub(lambda m: f"{m['pre']}{m['q']}{MASK}{m['q']}", text)
    return _HOST_PORT_USER_PASS.sub(lambda m: f"{m['pre']}{MASK}", text)


class CredentialRedactingFilter(logging.Filter):
    """Mask proxy credentials in the message, exception text and stack of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed format/args: leave it for logging to report
            return True
        redacted = redact_text(message)
        if redacted != message or record.args:
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


def _attach(target: logging.Logger | logging.Handler) -> None:
    if not any(isinstance(f, CredentialRedactingFilter) for f in target.filters):
        target.addFilter(CredentialRedactingFilter())


def install_scrapling_log_redaction() -> None:
    """Idempotently attach the redaction filter where Scrapling's records pass.

    Logger filters only see records logged directly on that logger, so the filter is also
    attached to the handlers of ``scrapling`` and of the root logger (which receive records
    propagated from child loggers such as ``scrapling.spiders.*``). Call again after
    handlers are (re)configured, e.g. after ``logging.basicConfig``.
    """
    scrapling_logger = logging.getLogger("scrapling")
    root = logging.getLogger()
    _attach(scrapling_logger)
    for logger in (scrapling_logger, root):
        for handler in logger.handlers:
            _attach(handler)
