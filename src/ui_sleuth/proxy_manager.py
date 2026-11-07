"""Proxy and Decodo infrastructure management for UI Sleuth."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

logger = logging.getLogger(__name__)

DEFAULT_DECODO_HOST = "isp.decodo.com"
DEFAULT_DECODO_PORT = 10001


REDACTED_PLACEHOLDER = "<proxy redacted>"
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://(.*)$", re.DOTALL)


class ProxyConfigError(ValueError):
    """Raised when a requested proxy configuration is invalid or unusable.

    Messages never contain proxy credentials or raw proxy strings.
    """


def _valid_port(value: str) -> bool:
    return value.isdigit() and 0 < int(value) < 65536


def _normalize_proxy(raw: str) -> str | None:
    """Normalize a proxy string to ``scheme://[user:pass@]host:port``.

    Accepts ``scheme://...``, ``user:pass@host:port``, ``host:port:user:pass`` and
    ``host:port``. A scheme-less input gets ``http://``; an explicit scheme is kept.
    Returns ``None`` when the string cannot be parsed (callers must not echo it).
    """
    value = raw.strip()
    if not value or any(c.isspace() for c in value):
        return None

    scheme = "http"
    match = _SCHEME_RE.match(value)
    if match:
        scheme, value = match.group(1).lower(), match.group(2)
    value = value.rstrip("/")
    if not value:
        return None

    creds = ""
    if "@" in value:
        creds, _, hostport = value.rpartition("@")
        if not creds:
            return None
    else:
        hostport = value
        parts = value.split(":")
        if len(parts) >= 4 and not match and _valid_port(parts[1]):
            # host:port:user:pass (password may itself contain ':')
            hostport = f"{parts[0]}:{parts[1]}"
            creds = f"{parts[2]}:{':'.join(parts[3:])}"
            if not parts[2]:
                return None
        elif len(parts) > 2:
            return None

    host, sep, port = hostport.rpartition(":")
    if not sep:
        return None
    if not host or not _valid_port(port) or any(c in host for c in "/?#@"):
        return None
    if host.count(":") and not (host.startswith("[") and host.endswith("]")):
        return None
    return f"{scheme}://{creds + '@' if creds else ''}{host}:{port}"


def normalize_proxy(raw: str) -> str:
    """Public wrapper around ``_normalize_proxy`` that raises ``ProxyConfigError``."""
    result = _normalize_proxy(raw)
    if result is None:
        raise ProxyConfigError(
            "Unparseable proxy value; expected scheme://[user:pass@]host:port, "
            "user:pass@host:port, host:port:user:pass or host:port"
        )
    return result


def redact_proxy_url(proxy_url: str) -> str:
    """Redact credentials from a proxy URL for display/logging. Fails closed."""
    if not proxy_url:
        return ""
    normalized = _normalize_proxy(proxy_url)
    if normalized is None:
        return REDACTED_PLACEHOLDER
    try:
        parsed = urlparse(normalized)
        if not parsed.scheme or not parsed.hostname:
            return REDACTED_PLACEHOLDER
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        masked = f"{parsed.scheme}://"
        if parsed.username is not None:
            masked += parsed.username
            if parsed.password is not None:
                masked += ":***"
            masked += "@"
        masked += host
        if parsed.port:
            masked += f":{parsed.port}"
        return masked
    except ValueError:
        return REDACTED_PLACEHOLDER


def load_proxies_from_file(file_path: str | Path) -> list[str]:
    """Load and normalize proxies from a text file (one per line, comments/blanks ignored).

    Raises ``ProxyConfigError`` for a missing file or an unparseable line (the message
    gives the file and line number, never the line content).
    """
    path = Path(file_path)
    if not path.is_file():
        raise ProxyConfigError(f"Proxy file not found: {path}")

    proxies: list[str] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            normalized = _normalize_proxy(cleaned)
            if normalized is None:
                raise ProxyConfigError(
                    f"{path}:{lineno}: unparseable proxy line (content omitted); expected "
                    "scheme://user:pass@host:port, user:pass@host:port, host:port:user:pass "
                    "or host:port"
                )
            proxies.append(normalized)
    logger.info("Loaded %d proxies from %s", len(proxies), path)
    return proxies


def build_decodo_url(
    username: str,
    password: str,
    host: str = DEFAULT_DECODO_HOST,
    port: int = DEFAULT_DECODO_PORT,
) -> str:
    """Construct a standard authenticated Decodo proxy URL."""
    return f"http://{username}:{password}@{host}:{port}"


def generate_decodo_pool(
    username: str,
    password: str,
    host: str = DEFAULT_DECODO_HOST,
    start_port: int = 10001,
    end_port: int = 10010,
) -> list[str]:
    """Generate a list of Decodo proxy URLs across a port range (e.g. 10001-10010)."""
    return [
        build_decodo_url(username=username, password=password, host=host, port=p)
        for p in range(start_port, end_port + 1)
    ]


class ProxyConfig:
    """Encapsulates resolved proxy configuration and rotation support."""

    def __init__(
        self,
        single_proxy: str | None = None,
        proxy_list: list[str] | None = None,
        use_rotator: bool = True,
    ) -> None:
        self.single_proxy = single_proxy
        self.proxy_list: list[str] = proxy_list or []
        self.use_rotator = use_rotator
        self._rotator: ProxyRotator | None = None

        if len(self.proxy_list) > 1 and self.use_rotator:
            self._rotator = ProxyRotator(list(self.proxy_list))
        elif len(self.proxy_list) == 1 and not self.single_proxy:
            self.single_proxy = self.proxy_list[0]

    @property
    def has_proxy(self) -> bool:
        return bool(self.single_proxy or self.proxy_list)

    @property
    def rotator(self) -> ProxyRotator | None:
        return self._rotator

    def get_proxy_for_request(self) -> str | None:
        """Get the current or next proxy string."""
        if self._rotator is not None:
            val = self._rotator.get_proxy()
            if isinstance(val, str):
                return val
            srv = str(val.get("server", ""))
            usr = val.get("username")
            pwd = val.get("password")
            if usr and pwd:
                parts = srv.split("://", 1)
                scheme = parts[0] if len(parts) > 1 else "http"
                host = parts[1] if len(parts) > 1 else parts[0]
                return f"{scheme}://{usr}:{pwd}@{host}"
            return srv
        return self.single_proxy

    def to_scrapling_kwargs(self) -> dict[str, Any]:
        """Convert to kwargs suitable for Scrapling Session / Fetcher."""
        if not self.has_proxy:
            return {}
        if self._rotator is not None:
            return {"proxy_rotator": self._rotator}
        if self.single_proxy:
            return {"proxy": self.single_proxy}
        return {}

    def get_display_summary(self) -> str | None:
        """Return a human-readable, redacted summary of proxy settings."""
        if not self.has_proxy:
            return None
        if self._rotator is not None:
            first_redacted = redact_proxy_url(self.proxy_list[0]) if self.proxy_list else ""
            return f"Pool of {len(self.proxy_list)} proxies (e.g. {first_redacted})"
        if self.single_proxy:
            return redact_proxy_url(self.single_proxy)
        return None


def _resolve_decodo() -> ProxyConfig:
    """Resolve Decodo settings (DECODO_*). Only called when --decodo is requested."""
    env_url = os.getenv("DECODO_PROXY_URL")
    if env_url:
        logger.info("Proxy source: DECODO_PROXY_URL")
        return ProxyConfig(single_proxy=normalize_proxy(env_url))

    user = os.getenv("DECODO_USERNAME")
    password = os.getenv("DECODO_PASSWORD")
    if user and password:
        host = os.getenv("DECODO_HOST", DEFAULT_DECODO_HOST)
        try:
            start = int(os.getenv("DECODO_PORT_START", str(DEFAULT_DECODO_PORT)))
            end = int(os.getenv("DECODO_PORT_END", "10010"))
        except ValueError:
            raise ProxyConfigError(
                "--decodo: DECODO_PORT_START/DECODO_PORT_END must be integers"
            ) from None
        pool = generate_decodo_pool(user, password, host, start, end)
        if not pool:
            raise ProxyConfigError(
                f"--decodo: empty proxy pool (DECODO_PORT_START={start} > DECODO_PORT_END={end})"
            )
        pool = [normalize_proxy(p) for p in pool]
        logger.info("Proxy source: DECODO_USERNAME/DECODO_PASSWORD pool (%d ports)", len(pool))
        return ProxyConfig(proxy_list=pool)

    decodo_file = os.getenv("DECODO_PROXY_FILE")
    if decodo_file:
        proxies = load_proxies_from_file(decodo_file)
        if not proxies:
            raise ProxyConfigError(f"--decodo: DECODO_PROXY_FILE {decodo_file} has no proxies")
        logger.info("Proxy source: DECODO_PROXY_FILE %s", decodo_file)
        return ProxyConfig(proxy_list=proxies)

    raise ProxyConfigError(
        "--decodo requires DECODO_PROXY_URL, DECODO_USERNAME and DECODO_PASSWORD, "
        "or DECODO_PROXY_FILE to be set"
    )


def _resolve_env() -> ProxyConfig:
    """Resolve PROXY_FILE / HTTPS_PROXY / HTTP_PROXY. Only called for --proxy-env."""
    env_file = os.getenv("PROXY_FILE")
    if env_file:
        proxies = load_proxies_from_file(env_file)
        if not proxies:
            raise ProxyConfigError(f"--proxy-env: PROXY_FILE {env_file} has no proxies")
        logger.info("Proxy source: PROXY_FILE %s", env_file)
        return ProxyConfig(proxy_list=proxies)
    standard = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if standard:
        logger.info("Proxy source: HTTP(S)_PROXY")
        return ProxyConfig(single_proxy=normalize_proxy(standard))
    raise ProxyConfigError("--proxy-env: none of PROXY_FILE, HTTPS_PROXY, HTTP_PROXY is set")


def resolve_proxy_config(
    proxy: str | None = None,
    proxy_file: str | None = None,
    enable_decodo: bool = False,
    use_env: bool = False,
    no_proxy: bool = False,
) -> ProxyConfig:
    """Resolve proxy configuration from explicit opt-ins only.

    Precedence (first match wins; nothing is read from the environment or the
    working directory unless explicitly requested):

    1. ``no_proxy``: direct connection (conflicts with any other source -> error)
    2. ``proxy``: single proxy
    3. ``proxy_file``: pool from file
    4. ``enable_decodo``: DECODO_PROXY_URL / DECODO_USERNAME+PASSWORD / DECODO_PROXY_FILE
    5. ``use_env``: PROXY_FILE / HTTPS_PROXY / HTTP_PROXY
    6. otherwise: direct connection

    Raises ``ProxyConfigError`` if a requested source yields no usable proxy.
    """
    if no_proxy:
        if proxy or proxy_file or enable_decodo or use_env:
            raise ProxyConfigError("--no-proxy cannot be combined with other proxy options")
        return ProxyConfig()

    if proxy:
        return ProxyConfig(single_proxy=normalize_proxy(proxy))

    if proxy_file:
        proxies = load_proxies_from_file(proxy_file)
        if not proxies:
            raise ProxyConfigError(f"Proxy file {proxy_file} contains no proxies")
        return ProxyConfig(proxy_list=proxies)

    if enable_decodo:
        return _resolve_decodo()

    if use_env:
        return _resolve_env()

    return ProxyConfig()
