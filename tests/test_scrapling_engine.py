"""Tests for proxy bypass logic in ScraplingEngine."""

from unittest.mock import MagicMock

import pytest

from ui_sleuth import scrapling_engine
from ui_sleuth.proxy_manager import ProxyConfig
from ui_sleuth.scrapling_engine import ScraplingEngine, is_loopback_url

PROXY = "http://user:secretpw@proxy.example.com:8080"


def _run(monkeypatch, url: str, proxy_config: ProxyConfig | None) -> dict:
    fetch = MagicMock(side_effect=RuntimeError("stop"))
    fake_cls = MagicMock()
    fake_cls.fetch = fetch
    monkeypatch.setattr(scrapling_engine, "DynamicFetcher", fake_cls)
    engine = ScraplingEngine(url=url, proxy_config=proxy_config)
    with pytest.raises(RuntimeError):
        engine.execute()
    return fetch.call_args.kwargs


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.com/?return=localhost",
        "https://localhost.example.com",
        "https://127.0.0.1.nip.io/",
    ],
)
def test_proxy_used_for_non_loopback_lookalikes(monkeypatch, url):
    kwargs = _run(monkeypatch, url, ProxyConfig(single_proxy=PROXY))
    assert kwargs["proxy"] == PROXY


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1/",
        "http://[::1]:3000/",
        "http://app.localhost/",
    ],
)
def test_proxy_bypassed_for_loopback(monkeypatch, caplog, url):
    with caplog.at_level("INFO", logger="ui_sleuth.scrapling_engine"):
        kwargs = _run(monkeypatch, url, ProxyConfig(single_proxy=PROXY))
    assert "proxy" not in kwargs
    assert "proxy_rotator" not in kwargs
    assert any("Skipping configured proxy" in r.message for r in caplog.records)
    assert "secretpw" not in caplog.text


def test_engine_does_not_resolve_env_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", PROXY)
    monkeypatch.setenv("DECODO_PROXY_URL", PROXY)
    kwargs = _run(monkeypatch, "https://example.com", None)
    assert "proxy" not in kwargs
    assert "proxy_rotator" not in kwargs


def test_is_loopback_url_garbage():
    assert not is_loopback_url("http://[bad")
    assert not is_loopback_url("")
