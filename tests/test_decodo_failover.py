"""Engine and crawl behaviour for Decodo primary -> secondary failover."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ui_sleuth import crawler as crawler_mod
from ui_sleuth import scrapling_engine
from ui_sleuth.crawler import SiteCrawler
from ui_sleuth.exporter import export_site_design_system
from ui_sleuth.proxy_manager import DecodoEndpoint, ProxyConfig, ProxyGroup
from ui_sleuth.scrapling_engine import ScraplingEngine

PRIMARY_PW = "primarypw"
SECONDARY_PW = "secondarypw"
PROXY_ERR = "Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://example.com/"


def _seed_links(monkeypatch, links):
    monkeypatch.setattr(
        crawler_mod, "extract_links_from_html", lambda html, base: [(u, "x", 50) for u in links]
    )


def _config(strategy: str = "failover") -> ProxyConfig:
    primary = DecodoEndpoint("primary", "isp.decodo.com", "u1", PRIMARY_PW, 10001, 10002)
    secondary = DecodoEndpoint("secondary", "203.0.113.7", "u2", SECONDARY_PW, 10001, 10002)
    return ProxyConfig(
        groups=[ProxyGroup("primary", primary.urls()), ProxyGroup("secondary", secondary.urls())],
        strategy=strategy,
    )


def _response(proxy: str | None, html: str = "<html><body><p>hi</p></body></html>"):
    return SimpleNamespace(
        url="https://example.com/", status=200, body=html.encode(), meta={"proxy": proxy}
    )


def _patch_fetcher(monkeypatch, fetch):
    fake_cls = MagicMock()
    fake_cls.fetch = fetch
    monkeypatch.setattr(scrapling_engine, "DynamicFetcher", fake_cls)


def _fetch_with(config: ProxyConfig, outcomes: list[Exception | None]):
    """Fake fetch: like scrapling, draw one proxy per attempt from the shared rotator."""
    used: list[str] = []

    def fetch(url, **kwargs):
        proxy = kwargs["proxy_rotator"].get_proxy()
        used.append(proxy)
        outcome = outcomes[len(used) - 1]
        if outcome is not None:
            raise outcome
        return _response(proxy)

    return MagicMock(side_effect=fetch), used


def test_engine_refetches_once_on_proxy_error_and_records_warning(monkeypatch, caplog):
    config = _config()
    fetch, used = _fetch_with(config, [RuntimeError(PROXY_ERR), None])
    _patch_fetcher(monkeypatch, fetch)
    with caplog.at_level(logging.DEBUG):
        blueprint = ScraplingEngine(url="https://example.com/", proxy_config=config).execute()
    assert fetch.call_count == 2
    assert "isp.decodo.com" in used[0] and "203.0.113.7" in used[1]
    meta = blueprint.metadata
    assert meta.warnings == ["proxy_failover: primary -> secondary"]
    assert meta.proxy_used is not None and meta.proxy_used.startswith("secondary (203.0.113.7:")
    assert config.active_label == "secondary"
    dumped = blueprint.model_dump_json() + caplog.text
    assert PRIMARY_PW not in dumped and SECONDARY_PW not in dumped


def test_engine_retries_only_once(monkeypatch):
    config = _config()
    fetch, _ = _fetch_with(config, [RuntimeError(PROXY_ERR)] * 3)
    _patch_fetcher(monkeypatch, fetch)
    with pytest.raises(RuntimeError, match="ERR_PROXY"):
        ScraplingEngine(url="https://example.com/", proxy_config=config).execute()
    assert fetch.call_count == 2


def test_engine_does_not_retry_non_proxy_error(monkeypatch):
    config = _config()
    fetch, _ = _fetch_with(config, [RuntimeError("Timeout 30000ms exceeded"), None])
    _patch_fetcher(monkeypatch, fetch)
    with pytest.raises(RuntimeError, match="Timeout"):
        ScraplingEngine(url="https://example.com/", proxy_config=config).execute()
    assert fetch.call_count == 1
    assert config.active_label == "primary"


def test_engine_no_failover_when_pool_strategy(monkeypatch):
    config = _config("pool")
    assert not config.can_fail_over
    fetch, _ = _fetch_with(config, [RuntimeError(PROXY_ERR), None])
    _patch_fetcher(monkeypatch, fetch)
    with pytest.raises(RuntimeError, match="ERR_PROXY"):
        ScraplingEngine(url="https://example.com/", proxy_config=config).execute()
    assert fetch.call_count == 1


def test_engine_no_failover_once_exhausted(monkeypatch):
    config = _config()
    assert config.fail_over()  # already on the last endpoint
    fetch, _ = _fetch_with(config, [RuntimeError(PROXY_ERR), None])
    _patch_fetcher(monkeypatch, fetch)
    with pytest.raises(RuntimeError, match="ERR_PROXY"):
        ScraplingEngine(url="https://example.com/", proxy_config=config).execute()
    assert fetch.call_count == 1


def test_proxy_used_falls_back_to_summary_without_meta(monkeypatch):
    config = _config("pool")
    fetch = MagicMock(
        return_value=SimpleNamespace(url="https://example.com/", status=200, body=b"<p>x</p>")
    )
    _patch_fetcher(monkeypatch, fetch)
    meta = ScraplingEngine(url="https://example.com/", proxy_config=config).execute().metadata
    assert meta.proxy_used == config.get_display_summary()


def test_session_mode_failover_is_sticky_across_crawl(monkeypatch, tmp_path, caplog):
    config = _config()
    attempts: list[tuple[str, str]] = []  # (path, endpoint label)

    class FakeSession:
        def fetch(self, url, **kwargs):
            assert "proxy_rotator" not in kwargs  # lives on the shared session
            assert config.rotator is not None
            proxy = config.rotator.get_proxy()
            label = (config.endpoint_for(proxy) or "").split(" ")[0]
            attempts.append(((url.rsplit("/", 1)[-1] or "root"), label))
            if url.endswith("/a") and label == "primary":
                raise RuntimeError(PROXY_ERR)
            return _response(proxy)

        def close_pages(self):
            pass

    sessions: list[object] = []

    @contextmanager
    def fake_session(url, **kwargs):
        assert kwargs["proxy_config"] is config
        sessions.append(kwargs["proxy_config"])
        yield FakeSession()

    seen_configs: list[ProxyConfig] = []
    real_engine = crawler_mod.ScraplingEngine

    def engine_factory(*args, **kwargs):
        seen_configs.append(kwargs["proxy_config"])
        return real_engine(*args, **kwargs)

    monkeypatch.setattr(crawler_mod, "browser_session", fake_session)
    monkeypatch.setattr(crawler_mod, "ScraplingEngine", engine_factory)
    _seed_links(monkeypatch, ["https://example.com/a", "https://example.com/b"])

    with caplog.at_level(logging.DEBUG):
        res = SiteCrawler(
            "https://example.com",
            max_pages=3,
            max_depth=1,
            output_dir=tmp_path,
            proxy_config=config,
        ).crawl()

    assert all(c is config for c in seen_configs) and len(seen_configs) == 3
    assert [a for a in attempts] == [
        ("root", "primary"),
        ("a", "primary"),
        ("a", "secondary"),
        ("b", "secondary"),
    ]
    assert res.design_system.total_pages_crawled == 3 and not res.failed_pages

    labels = {
        (url.rsplit("/", 1)[-1] or "root"): (bp.metadata.proxy_used or "").split(" (")[0]
        for url, bp in res.pages.items()
    }
    assert labels == {"root": "primary", "a": "secondary", "b": "secondary"}
    warned = {
        (url.rsplit("/", 1)[-1] or "root"): bp.metadata.warnings for url, bp in res.pages.items()
    }
    assert warned["a"] == ["proxy_failover: primary -> secondary"]
    assert warned["b"] == [] and warned["root"] == []

    saved = export_site_design_system(res, tmp_path / "out")
    manifest_text = saved["manifest_json"].read_text()
    assert json.loads(manifest_text)["proxy_endpoints"] == {"primary": 1, "secondary": 2}
    everything = (
        manifest_text + caplog.text + "".join(bp.model_dump_json() for bp in res.pages.values())
    )
    assert PRIMARY_PW not in everything and SECONDARY_PW not in everything


def test_manifest_has_no_endpoint_counts_without_groups(monkeypatch, tmp_path):
    @contextmanager
    def fake_session(url, **kwargs):
        yield MagicMock(fetch=lambda u, **kw: _response(None))

    monkeypatch.setattr(crawler_mod, "browser_session", fake_session)
    res = SiteCrawler("https://example.com", max_pages=1, output_dir=tmp_path).crawl()
    saved = export_site_design_system(res, tmp_path / "out")
    assert "proxy_endpoints" not in json.loads(saved["manifest_json"].read_text())


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Page.goto: net::ERR_PROXY_CONNECTION_FAILED at http://example.com/", True),
        # Google Chrome (real_chrome) reports a dead proxy this way
        ("Page.goto: net::ERR_BLOCKED_BY_CLIENT at http://example.com/", True),
        ("Page.goto: net::ERR_NAME_NOT_RESOLVED at http://nope.invalid/", False),
        ("Timeout 30000ms exceeded waiting for selector", False),
    ],
)
def test_is_proxy_failure_classification(message: str, expected: bool) -> None:
    from ui_sleuth.scrapling_engine import is_proxy_failure

    assert is_proxy_failure(Exception(message)) is expected
