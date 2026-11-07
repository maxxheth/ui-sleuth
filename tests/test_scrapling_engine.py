"""Tests for proxy bypass logic in ScraplingEngine."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ui_sleuth import scrapling_engine
from ui_sleuth.models import DesignTokens
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


# --- is_chrome_installed (M4) ---


def _no_chrome_env(monkeypatch, tmp_path, which: dict[str, str] | None = None) -> None:
    which = which or {}
    monkeypatch.setattr(scrapling_engine.shutil, "which", lambda name: which.get(name))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "is_file", lambda self: False)


def test_chrome_not_detected_for_chromium_only(monkeypatch, tmp_path):
    _no_chrome_env(monkeypatch, tmp_path, {"chromium": "/usr/bin/chromium", "chrome": "/x/chrome"})
    assert scrapling_engine.is_chrome_installed() is False


@pytest.mark.parametrize("name", ["google-chrome", "google-chrome-stable"])
def test_chrome_detected_on_path(monkeypatch, tmp_path, name):
    _no_chrome_env(monkeypatch, tmp_path, {name: f"/usr/bin/{name}"})
    assert scrapling_engine.is_chrome_installed() is True


def test_chrome_detected_macos_path(monkeypatch, tmp_path):
    _no_chrome_env(monkeypatch, tmp_path)
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == mac)
    assert scrapling_engine.is_chrome_installed() is True


def test_chrome_detected_windows_path(monkeypatch, tmp_path):
    _no_chrome_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    monkeypatch.setattr(Path, "is_file", lambda self: self == exe)
    assert scrapling_engine.is_chrome_installed() is True


# --- page action isolation, retries, selector wait (H6, M1, M2) ---


def _action_engine(monkeypatch, tmp_path, **kwargs) -> tuple[ScraplingEngine, MagicMock]:
    page = MagicMock()
    page.title.return_value = "Hello"
    page.query_selector.return_value = None

    def fake_screenshot(path, **_kw):
        Path(path).write_bytes(b"png")

    page.screenshot.side_effect = fake_screenshot
    monkeypatch.setattr(scrapling_engine, "harvest_design_tokens", lambda p: DesignTokens())
    monkeypatch.setattr(scrapling_engine, "inspect_canvas_and_video", lambda p: {})
    monkeypatch.setattr(
        scrapling_engine, "detect_and_explore_features", lambda p, explore=True: None
    )
    engine = ScraplingEngine(
        url="https://example.com",
        delay=0,
        screenshot_path=tmp_path / "s.png",
        **kwargs,
    )
    return engine, page


def test_failing_feature_detection_still_captures_title_and_screenshot(monkeypatch, tmp_path):
    engine, page = _action_engine(monkeypatch, tmp_path)

    def boom(p, explore=True):
        raise KeyError("name")

    monkeypatch.setattr(scrapling_engine, "detect_and_explore_features", boom)
    engine._page_action(page)
    assert engine._action_data["title"] == "Hello"
    assert engine._action_data["screenshot_saved"] == str(tmp_path / "s.png")
    assert any(w.startswith("feature_detection: KeyError") for w in engine._action_data["warnings"])


def test_each_step_failure_is_isolated(monkeypatch, tmp_path):
    engine, page = _action_engine(monkeypatch, tmp_path)
    page.evaluate.side_effect = RuntimeError("hydrate")
    page.title.side_effect = RuntimeError("title")
    page.query_selector.side_effect = RuntimeError("meta")
    monkeypatch.setattr(
        scrapling_engine,
        "harvest_design_tokens",
        lambda p: (_ for _ in ()).throw(ValueError("tok")),
    )
    monkeypatch.setattr(
        scrapling_engine,
        "inspect_canvas_and_video",
        lambda p: (_ for _ in ()).throw(ValueError("probe")),
    )
    engine._page_action(page)
    steps = {w.split(":")[0] for w in engine._action_data["warnings"]}
    assert steps == {
        "hydration",
        "design_tokens",
        "asset_probe",
        "title",
        "meta_description",
    }
    assert "screenshot_saved" in engine._action_data


def test_selector_wait_happens_first_and_timeout_warns(monkeypatch, tmp_path):
    engine, page = _action_engine(monkeypatch, tmp_path, wait_until=".Late-Item", timeout=7)
    calls: list[str] = []
    page.wait_for_selector.side_effect = lambda *a, **k: (
        calls.append("wait") or (_ for _ in ()).throw(TimeoutError("nope"))
    )
    page.evaluate.side_effect = lambda *a, **k: calls.append("evaluate")
    engine._page_action(page)
    assert calls[:2] == ["wait", "evaluate"]
    page.wait_for_selector.assert_called_once_with(".Late-Item", state="visible", timeout=7000)
    assert engine._action_data["warnings"][0].startswith("wait_selector: TimeoutError")
    assert engine._action_data["title"] == "Hello"


def test_wait_selector_not_passed_to_scrapling(monkeypatch):
    kwargs = _run(monkeypatch, "https://example.com", None)
    assert "wait_selector" not in kwargs
    fetch = MagicMock(side_effect=RuntimeError("stop"))
    fake_cls = MagicMock()
    fake_cls.fetch = fetch
    monkeypatch.setattr(scrapling_engine, "DynamicFetcher", fake_cls)
    with pytest.raises(RuntimeError):
        ScraplingEngine(url="https://example.com", wait_until=".hero").execute()
    assert "wait_selector" not in fetch.call_args.kwargs


def test_page_setup_resets_collector_and_action_data():
    engine = ScraplingEngine(url="https://example.com")
    old_collector = engine.collector
    engine._action_data = {"title": "stale", "warnings": ["x"]}
    page = MagicMock()
    engine._page_setup(page)
    assert engine.collector is not old_collector
    assert engine._action_data == {}
    page.on.assert_called_once_with("response", engine.collector.handle_response)


def test_engine_with_session_uses_session_fetch_without_browser_level_kwargs(monkeypatch):
    fake_cls = MagicMock()
    monkeypatch.setattr(scrapling_engine, "DynamicFetcher", fake_cls)
    session = MagicMock()
    session.fetch.side_effect = RuntimeError("stop")
    engine = ScraplingEngine(
        url="https://example.com/",
        proxy_config=ProxyConfig(single_proxy=PROXY),
        session=session,
    )
    with pytest.raises(RuntimeError):
        engine.execute()
    fake_cls.fetch.assert_not_called()
    kwargs = session.fetch.call_args.kwargs
    for session_level in ("headless", "real_chrome", "additional_args", "proxy", "proxy_rotator"):
        assert session_level not in kwargs
    assert kwargs["page_setup"] == engine._page_setup
    assert kwargs["page_action"] == engine._page_action
    # tabs are discarded so listeners and init scripts do not accumulate
    session.close_pages.assert_called_once()


def test_browser_session_configuration_and_close(monkeypatch):
    created = {}

    class FakeSession:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            created["closed"] = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            created["closed"] = True

    monkeypatch.setattr(scrapling_engine, "DynamicSession", FakeSession)
    with (
        pytest.raises(ValueError),
        scrapling_engine.browser_session(
            "https://example.com/",
            proxy_config=ProxyConfig(single_proxy=PROXY),
            real_chrome=False,
            viewport="800x600",
            timeout=10,
        ),
    ):
        raise ValueError("boom")
    assert created["closed"] is True
    kw = created["kwargs"]
    assert kw["proxy"] == PROXY
    assert kw["real_chrome"] is False
    assert kw["timeout"] == 10000
    assert kw["additional_args"]["viewport"] == {"width": 800, "height": 600}


def test_browser_session_skips_proxy_for_loopback_and_picks_stealth(monkeypatch):
    created = {}

    class FakeStealth:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(scrapling_engine, "StealthySession", FakeStealth)
    with scrapling_engine.browser_session(
        "http://localhost:3000/",
        proxy_config=ProxyConfig(single_proxy=PROXY),
        use_stealth=True,
        real_chrome=False,
    ):
        pass
    assert "proxy" not in created["kwargs"]
