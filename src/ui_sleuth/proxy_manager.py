"""Proxy and Decodo infrastructure management for UI Sleuth."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import zip_longest
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

logger = logging.getLogger(__name__)

DEFAULT_DECODO_HOST = "isp.decodo.com"
DEFAULT_DECODO_PORT = 10001
DEFAULT_DECODO_PORT_END = 10010
DECODO_STRATEGY_ENV = "DECODO_STRATEGY"
SECONDARY_ENV_PREFIX = "DECODO_SECONDARY_"
_SECONDARY_REQUIRED = ("HOST", "USERNAME", "PASSWORD")
_SECONDARY_OPTIONAL = ("PORT_START", "PORT_END")


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


_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME_RE = re.compile(rf"^{_HOST_LABEL}(?:\.{_HOST_LABEL})*$")


class DecodoStrategy(StrEnum):
    """How multiple Decodo endpoints are combined."""

    POOL = "pool"
    FAILOVER = "failover"


def _coerce_strategy(value: DecodoStrategy | str | None) -> DecodoStrategy:
    """Resolve the strategy: explicit value > ``DECODO_STRATEGY`` env > ``pool``."""
    if value is None or not value.strip():
        value = os.getenv(DECODO_STRATEGY_ENV, "").strip() or DecodoStrategy.POOL
    try:
        return DecodoStrategy(str(value).strip().lower())
    except ValueError:
        allowed = ", ".join(s.value for s in DecodoStrategy)
        raise ProxyConfigError(f"Invalid Decodo strategy (expected one of: {allowed})") from None


def _format_host(host: str) -> str:
    """Validate a proxy host and return it URL-ready (IPv6 bracketed).

    Accepts IPv4/IPv6 literals (IPv6 optionally already bracketed) and DNS
    hostnames. Raises ``ProxyConfigError`` without echoing the value.
    """
    value = host.strip()
    bracketed = value.startswith("[") and value.endswith("]")
    inner = value[1:-1] if bracketed else value
    invalid = ProxyConfigError(
        "Invalid proxy host; expected a hostname, IPv4 address or IPv6 address"
    )
    if not inner or "%" in inner:
        raise invalid
    try:
        ip = ipaddress.ip_address(inner)
    except ValueError:
        if bracketed or not _HOSTNAME_RE.match(inner) or len(inner) > 253:
            raise invalid from None
        if re.fullmatch(r"[\d.]+", inner):  # malformed IPv4 such as 999.1.1.1
            raise invalid from None
        return inner
    if ip.version == 6:
        return f"[{inner}]"
    if bracketed:
        raise invalid
    return inner


def build_decodo_url(
    username: str,
    password: str,
    host: str = DEFAULT_DECODO_HOST,
    port: int = DEFAULT_DECODO_PORT,
) -> str:
    """Construct a standard authenticated Decodo proxy URL (IPv6-safe)."""
    return f"http://{username}:{password}@{_format_host(host)}:{port}"


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


@dataclass
class DecodoEndpoint:
    """One Decodo host with its own credentials and port range."""

    label: str
    host: str
    username: str
    password: str = field(repr=False)
    port_start: int = DEFAULT_DECODO_PORT
    port_end: int = DEFAULT_DECODO_PORT_END

    def urls(self) -> list[str]:
        """Normalized ``http://user:pass@host:port`` URLs, one per port (may be empty)."""
        pool = generate_decodo_pool(
            self.username, self.password, self.host, self.port_start, self.port_end
        )
        return [normalize_proxy(u) for u in pool]


@dataclass
class ProxyGroup:
    """A labelled set of proxy URLs belonging to one endpoint (``primary``/``secondary``)."""

    label: str
    urls: list[str] = field(repr=False)


class GroupedRotation:
    """Rotation strategy that cycles only inside the *active* group.

    Usable as ``ProxyRotator(flattened, strategy=GroupedRotation(groups))`` where
    ``flattened`` is the group URLs concatenated in group order. ``advance()`` moves
    to the next group (sticky: there is no fail-back) and is safe across threads.
    """

    def __init__(self, groups: Sequence[ProxyGroup]) -> None:
        self._labels = [g.label for g in groups]
        self._offsets: list[int] = []
        total = 0
        for g in groups:
            self._offsets.append(total)
            total += len(g.urls)
        self._total = total
        self._active = 0
        self._lock = Lock()

    @property
    def active(self) -> int:
        return self._active

    @property
    def active_label(self) -> str:
        return self._labels[self._active]

    @property
    def labels(self) -> list[str]:
        return list(self._labels)

    @property
    def can_advance(self) -> bool:
        return self._active < len(self._labels) - 1

    def advance(self) -> bool:
        """Switch to the next group. Returns ``False`` when none is left."""
        with self._lock:
            if self._active >= len(self._labels) - 1:
                return False
            self._active += 1
            return True

    def __call__(self, proxies: list[Any], current_index: int) -> tuple[Any, int]:
        with self._lock:
            start = self._offsets[self._active]
            end = (
                self._offsets[self._active + 1]
                if self._active + 1 < len(self._offsets)
                else min(self._total, len(proxies))
            )
        if not start <= current_index < end:
            current_index = start
        nxt = current_index + 1
        return proxies[current_index], (start if nxt >= end else nxt)


def _proxy_key(proxy: str | dict[str, Any] | None) -> tuple[str, int, str | None] | None:
    """(host, port, username) of a proxy given as URL string or scrapling dict."""
    if not proxy:
        return None
    if isinstance(proxy, dict):
        server = proxy.get("server")
        username = proxy.get("username")
        raw = str(server) if server else ""
    else:
        raw, username = proxy, None
    normalized = _normalize_proxy(raw)
    if normalized is None:
        return None
    try:
        parsed = urlparse(normalized)
        if not parsed.hostname or parsed.port is None:
            return None
        return parsed.hostname.lower(), parsed.port, username or parsed.username
    except ValueError:
        return None


def _hostport(key: tuple[str, int, str | None]) -> str:
    host = f"[{key[0]}]" if ":" in key[0] else key[0]
    return f"{host}:{key[1]}"


class ProxyConfig:
    """Encapsulates resolved proxy configuration and rotation support.

    ``groups`` (optional) labels the URLs by endpoint. With more than one group,
    ``strategy`` selects ``pool`` (one interleaved rotator) or ``failover`` (rotate
    inside the active group; ``fail_over()`` moves to the next). When ``groups`` is
    given and ``proxy_list`` is not, the list is derived from the groups.
    """

    def __init__(
        self,
        single_proxy: str | None = None,
        proxy_list: list[str] | None = None,
        use_rotator: bool = True,
        groups: Sequence[ProxyGroup] | None = None,
        strategy: DecodoStrategy | str | None = None,
    ) -> None:
        self.groups: list[ProxyGroup] = [g for g in (groups or []) if g.urls]
        self.strategy = DecodoStrategy(strategy) if strategy else DecodoStrategy.POOL
        multi = len(self.groups) > 1
        failover = multi and self.strategy is DecodoStrategy.FAILOVER

        if self.groups and not proxy_list and not single_proxy:
            if multi and not failover:
                proxy_list = [
                    u for tup in zip_longest(*(g.urls for g in self.groups)) for u in tup if u
                ]
            else:
                proxy_list = [u for g in self.groups for u in g.urls]

        self.single_proxy = single_proxy
        self.proxy_list: list[str] = proxy_list or []
        self.use_rotator = use_rotator
        self._rotator: ProxyRotator | None = None
        self._grouped: GroupedRotation | None = None

        self._by_key: dict[tuple[str, int, str | None], str] = {}
        self._by_hostport: dict[tuple[str, int], set[str]] = {}
        for g in self.groups:
            for url in g.urls:
                key = _proxy_key(url)
                if key is not None:
                    self._by_key.setdefault(key, g.label)
                    self._by_hostport.setdefault(key[:2], set()).add(g.label)

        if len(self.proxy_list) > 1 and self.use_rotator:
            if failover:
                self._grouped = GroupedRotation(self.groups)
                self._rotator = ProxyRotator(list(self.proxy_list), strategy=self._grouped)
            else:
                self._rotator = ProxyRotator(list(self.proxy_list))
        elif len(self.proxy_list) == 1 and not self.single_proxy:
            self.single_proxy = self.proxy_list[0]

    @property
    def has_proxy(self) -> bool:
        return bool(self.single_proxy or self.proxy_list)

    @property
    def rotator(self) -> ProxyRotator | None:
        return self._rotator

    @property
    def can_fail_over(self) -> bool:
        """True when failover is active and a further endpoint is available."""
        return self._grouped is not None and self._grouped.can_advance

    @property
    def active_label(self) -> str | None:
        """Label of the endpoint currently in use under failover, else ``None``."""
        return self._grouped.active_label if self._grouped is not None else None

    def fail_over(self) -> bool:
        """Switch to the next endpoint (sticky). ``False`` if not applicable/none left."""
        grouped = self._grouped
        if grouped is None:
            return False
        before = grouped.active_label
        if not grouped.advance():
            return False
        logger.warning(
            "Proxy failover: switching from %s endpoint to %s endpoint",
            before,
            grouped.active_label,
        )
        return True

    def endpoint_for(self, proxy: str | dict[str, Any] | None) -> str | None:
        """Describe which endpoint a proxy (URL or scrapling dict) belongs to.

        Returns e.g. ``"secondary (203.0.113.7:10003)"``; never credentials.
        ``None`` when unknown or no groups are configured.
        """
        key = _proxy_key(proxy)
        if key is None:
            return None
        label = self._by_key.get(key)
        if label is None:
            labels = self._by_hostport.get(key[:2], set())
            label = next(iter(labels)) if len(labels) == 1 else None
        return f"{label} ({_hostport(key)})" if label else None

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

    def _group_summary(self, group: ProxyGroup) -> str:
        hosts: list[str] = []
        for url in group.urls:
            key = _proxy_key(url)
            if key is not None:
                host = f"[{key[0]}]" if ":" in key[0] else key[0]
                if host not in hosts:
                    hosts.append(host)
        return f"{group.label} {'/'.join(hosts) or '?'} x{len(group.urls)}"

    def get_display_summary(self) -> str | None:
        """Return a human-readable, redacted summary of proxy settings."""
        if not self.has_proxy:
            return None
        if len(self.groups) > 1:
            parts = [self._group_summary(g) for g in self.groups]
            if self.strategy is DecodoStrategy.FAILOVER:
                return "Decodo failover: " + " → ".join(parts)
            return "Decodo pool (round-robin): " + ", ".join(parts)
        if self._rotator is not None:
            first_redacted = redact_proxy_url(self.proxy_list[0]) if self.proxy_list else ""
            return f"Pool of {len(self.proxy_list)} proxies (e.g. {first_redacted})"
        if self.single_proxy:
            return redact_proxy_url(self.single_proxy)
        return None


def _env_int(name: str, default: int, what: str) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ProxyConfigError(f"{what}: {name} must be an integer") from None


def _resolve_decodo_primary() -> ProxyConfig | None:
    """Resolve the primary Decodo source; ``None`` when no source is configured."""
    env_url = os.getenv("DECODO_PROXY_URL")
    if env_url:
        logger.info("Proxy source: DECODO_PROXY_URL")
        url = normalize_proxy(env_url)
        return ProxyConfig(single_proxy=url, groups=[ProxyGroup("primary", [url])])

    user = os.getenv("DECODO_USERNAME")
    password = os.getenv("DECODO_PASSWORD")
    if user and password:
        host = os.getenv("DECODO_HOST", DEFAULT_DECODO_HOST)
        try:
            start = int(os.getenv("DECODO_PORT_START", str(DEFAULT_DECODO_PORT)))
            end = int(os.getenv("DECODO_PORT_END", str(DEFAULT_DECODO_PORT_END)))
        except ValueError:
            raise ProxyConfigError(
                "--decodo: DECODO_PORT_START/DECODO_PORT_END must be integers"
            ) from None
        endpoint = DecodoEndpoint("primary", host, user, password, start, end)
        pool = endpoint.urls()
        if not pool:
            raise ProxyConfigError(
                f"--decodo: empty proxy pool (DECODO_PORT_START={start} > DECODO_PORT_END={end})"
            )
        logger.info("Proxy source: DECODO_USERNAME/DECODO_PASSWORD pool (%d ports)", len(pool))
        return ProxyConfig(proxy_list=pool, groups=[ProxyGroup("primary", pool)])

    decodo_file = os.getenv("DECODO_PROXY_FILE")
    if decodo_file:
        proxies = load_proxies_from_file(decodo_file)
        if not proxies:
            raise ProxyConfigError(f"--decodo: DECODO_PROXY_FILE {decodo_file} has no proxies")
        logger.info("Proxy source: DECODO_PROXY_FILE %s", decodo_file)
        return ProxyConfig(proxy_list=proxies, groups=[ProxyGroup("primary", proxies)])
    return None


def _secondary_configured() -> bool:
    names = _SECONDARY_REQUIRED + _SECONDARY_OPTIONAL
    return any((os.getenv(SECONDARY_ENV_PREFIX + n) or "").strip() for n in names)


def _resolve_decodo_secondary() -> DecodoEndpoint:
    """Build the secondary endpoint from ``DECODO_SECONDARY_*`` (fails closed)."""
    missing = [
        SECONDARY_ENV_PREFIX + n
        for n in _SECONDARY_REQUIRED
        if not (os.getenv(SECONDARY_ENV_PREFIX + n) or "").strip()
    ]
    if missing:
        raise ProxyConfigError(
            "--decodo: secondary Decodo endpoint is partially configured; missing "
            + ", ".join(missing)
        )
    start = _env_int(SECONDARY_ENV_PREFIX + "PORT_START", DEFAULT_DECODO_PORT, "--decodo secondary")
    end = _env_int(SECONDARY_ENV_PREFIX + "PORT_END", DEFAULT_DECODO_PORT_END, "--decodo secondary")
    if start > end:
        raise ProxyConfigError(
            "--decodo: empty secondary proxy pool "
            f"({SECONDARY_ENV_PREFIX}PORT_START={start} > {SECONDARY_ENV_PREFIX}PORT_END={end})"
        )
    return DecodoEndpoint(
        "secondary",
        os.environ[SECONDARY_ENV_PREFIX + "HOST"].strip(),
        os.environ[SECONDARY_ENV_PREFIX + "USERNAME"],
        os.environ[SECONDARY_ENV_PREFIX + "PASSWORD"],
        start,
        end,
    )


def _warn_overlap(groups: Sequence[ProxyGroup]) -> None:
    seen: dict[tuple[str, int], str] = {}
    for g in groups:
        for url in g.urls:
            key = _proxy_key(url)
            if key is None:
                continue
            owner = seen.setdefault(key[:2], g.label)
            if owner != g.label:
                logger.warning(
                    "Decodo %s and %s endpoints share host and overlapping ports "
                    "(e.g. %s); duplicate entries may collapse in the rotator",
                    owner,
                    g.label,
                    _hostport(key),
                )
                return


def _resolve_decodo(strategy: DecodoStrategy | str | None = None) -> ProxyConfig:
    """Resolve Decodo settings (DECODO_*). Only called when --decodo is requested.

    ``strategy`` (``pool``/``failover``) falls back to ``DECODO_STRATEGY`` then
    ``pool``; it only matters when a secondary endpoint is configured.
    """
    chosen = _coerce_strategy(strategy)
    primary = _resolve_decodo_primary()
    if primary is None:
        if _secondary_configured():
            raise ProxyConfigError(
                "--decodo: secondary requires a primary Decodo configuration "
                "(DECODO_PROXY_URL, DECODO_USERNAME and DECODO_PASSWORD, or DECODO_PROXY_FILE)"
            )
        raise ProxyConfigError(
            "--decodo requires DECODO_PROXY_URL, DECODO_USERNAME and DECODO_PASSWORD, "
            "or DECODO_PROXY_FILE to be set"
        )
    if not _secondary_configured():
        return primary

    secondary_urls = _resolve_decodo_secondary().urls()
    # Primary URLs live in its single group (single_proxy configs included).
    groups = [primary.groups[0], ProxyGroup("secondary", secondary_urls)]
    _warn_overlap(groups)
    logger.info(
        "Proxy source: primary + secondary Decodo endpoints (%s, %d + %d proxies)",
        chosen.value,
        len(groups[0].urls),
        len(secondary_urls),
    )
    return ProxyConfig(groups=groups, strategy=chosen)


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
    decodo_strategy: DecodoStrategy | str | None = None,
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

    ``decodo_strategy`` (``pool``/``failover``) beats ``DECODO_STRATEGY`` which beats
    ``pool``; it is only read under ``enable_decodo`` and is ignored (but still
    validated) when only one Decodo endpoint is configured.

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
        return _resolve_decodo(strategy=decodo_strategy)

    if use_env:
        return _resolve_env()

    return ProxyConfig()
