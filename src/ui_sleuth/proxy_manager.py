"""Proxy and Decodo infrastructure management for UI Sleuth."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_DECODO_HOST = "isp.decodo.com"
DEFAULT_DECODO_PORT = 10001


def redact_proxy_url(proxy_url: str) -> str:
    """Redact sensitive username/password from a proxy URL for safe display and logging."""
    if not proxy_url:
        return ""
    try:
        parsed = urlparse(proxy_url)
        if parsed.password:
            masked = f"{parsed.scheme}://{parsed.username}:***@{parsed.hostname}"
            if parsed.port:
                masked += f":{parsed.port}"
            return masked
        if parsed.username:
            masked = f"{parsed.scheme}://{parsed.username}@{parsed.hostname}"
            if parsed.port:
                masked += f":{parsed.port}"
            return masked
        return proxy_url
    except Exception:
        # Fallback regex masking
        return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", proxy_url)


def load_proxies_from_file(file_path: str | Path) -> list[str]:
    """Load proxy URLs from a text file (one URL per line, comments/blanks ignored)."""
    path = Path(file_path)
    if not path.is_file():
        logger.debug("Proxy file %s not found", path)
        return []

    proxies: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                proxies.append(cleaned)
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


def resolve_proxy_config(
    proxy: str | None = None,
    proxy_file: str | None = None,
    enable_decodo: bool = False,
) -> ProxyConfig:
    """Resolve proxy configuration from CLI args, environment variables, or default files."""
    # 1. Explicit single proxy provided via CLI
    if proxy:
        return ProxyConfig(single_proxy=proxy)

    # 2. Explicit proxy file provided via CLI
    if proxy_file:
        proxies = load_proxies_from_file(proxy_file)
        if proxies:
            return ProxyConfig(proxy_list=proxies)

    # 3. Environment variable DECODO_PROXY_URL
    env_decodo_url = os.getenv("DECODO_PROXY_URL")
    if env_decodo_url:
        return ProxyConfig(single_proxy=env_decodo_url)

    # 4. Decodo credentials in environment (activated via --decodo)
    if enable_decodo:
        decodo_user = os.getenv("DECODO_USERNAME")
        decodo_pass = os.getenv("DECODO_PASSWORD")
        if decodo_user and decodo_pass:
            decodo_host = os.getenv("DECODO_HOST", DEFAULT_DECODO_HOST)
            start_port_str = os.getenv("DECODO_PORT_START", "10001")
            end_port_str = os.getenv("DECODO_PORT_END", "10010")
            try:
                start_port = int(start_port_str)
                end_port = int(end_port_str)
                pool = generate_decodo_pool(
                    username=decodo_user,
                    password=decodo_pass,
                    host=decodo_host,
                    start_port=start_port,
                    end_port=end_port,
                )
                return ProxyConfig(proxy_list=pool)
            except ValueError:
                single = build_decodo_url(decodo_user, decodo_pass, decodo_host)
                return ProxyConfig(single_proxy=single)

        decodo_file = os.getenv("DECODO_PROXY_FILE")
        if decodo_file and Path(decodo_file).is_file():
            proxies = load_proxies_from_file(decodo_file)
            if proxies:
                logger.info("Using Decodo proxies from %s", decodo_file)
                return ProxyConfig(proxy_list=proxies)
    env_proxy_file = os.getenv("PROXY_FILE")
    if env_proxy_file:
        proxies = load_proxies_from_file(env_proxy_file)
        if proxies:
            return ProxyConfig(proxy_list=proxies)

    # 6. Auto-detect proxies.txt in current directory if present
    cwd_proxy = Path.cwd() / "proxies.txt"
    if cwd_proxy.is_file():
        proxies = load_proxies_from_file(cwd_proxy)
        if proxies:
            return ProxyConfig(proxy_list=proxies)

    # 7. Standard HTTP_PROXY / HTTPS_PROXY
    standard_proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if standard_proxy:
        return ProxyConfig(single_proxy=standard_proxy)

    return ProxyConfig()
