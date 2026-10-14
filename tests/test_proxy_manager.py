"""Tests for Decodo and proxy management."""

from pathlib import Path

from ui_sleuth.proxy_manager import (
    ProxyConfig,
    build_decodo_url,
    generate_decodo_pool,
    load_proxies_from_file,
    redact_proxy_url,
    resolve_proxy_config,
)


def test_redact_proxy_url():
    url = "http://myuser:secret123@isp.decodo.com:10001"
    masked = redact_proxy_url(url)
    assert "secret123" not in masked
    assert "myuser" in masked
    assert "isp.decodo.com:10001" in masked


def test_build_decodo_url():
    url = build_decodo_url(username="sp98test", password="mypassword", port=10005)
    assert url == "http://sp98test:mypassword@isp.decodo.com:10005"


def test_generate_decodo_pool():
    pool = generate_decodo_pool(username="usr", password="pwd", start_port=10001, end_port=10003)
    assert len(pool) == 3
    assert pool[0] == "http://usr:pwd@isp.decodo.com:10001"
    assert pool[2] == "http://usr:pwd@isp.decodo.com:10003"


def test_load_proxies_from_file(tmp_path: Path):
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text(
        "# Comment line\n"
        "http://usr1:pwd1@isp.decodo.com:10001\n"
        "\n"
        "http://usr2:pwd2@isp.decodo.com:10002\n"
    )

    proxies = load_proxies_from_file(proxy_file)
    assert len(proxies) == 2
    assert proxies[0] == "http://usr1:pwd1@isp.decodo.com:10001"
    assert proxies[1] == "http://usr2:pwd2@isp.decodo.com:10002"


def test_proxy_config_rotation():
    pool = [
        "http://usr1:pwd1@isp.decodo.com:10001",
        "http://usr2:pwd2@isp.decodo.com:10002",
    ]
    cfg = ProxyConfig(proxy_list=pool, use_rotator=True)
    assert cfg.has_proxy
    assert cfg.rotator is not None

    p1 = cfg.get_proxy_for_request()
    p2 = cfg.get_proxy_for_request()
    assert p1 != p2

    kwargs = cfg.to_scrapling_kwargs()
    assert "proxy_rotator" in kwargs


def test_resolve_proxy_config(monkeypatch):
    monkeypatch.setenv("DECODO_PROXY_URL", "http://envuser:envpass@isp.decodo.com:10001")
    cfg = resolve_proxy_config()
    assert cfg.has_proxy
    assert "envuser" in (cfg.single_proxy or "")


def test_resolve_proxy_config_decodo_flag(monkeypatch):
    monkeypatch.delenv("DECODO_PROXY_URL", raising=False)
    monkeypatch.setenv("DECODO_USERNAME", "myuser")
    monkeypatch.setenv("DECODO_PASSWORD", "mypass")
    monkeypatch.setenv("DECODO_PORT_START", "10001")
    monkeypatch.setenv("DECODO_PORT_END", "10003")

    # Disabled by default
    cfg_off = resolve_proxy_config(enable_decodo=False)
    assert not cfg_off.has_proxy

    # Enabled via flag
    cfg_on = resolve_proxy_config(enable_decodo=True)
    assert cfg_on.has_proxy
    assert len(cfg_on.proxy_list) == 3
