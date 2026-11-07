"""Tests for Decodo and proxy management."""

from pathlib import Path

import pytest

from ui_sleuth.proxy_manager import (
    ProxyConfig,
    ProxyConfigError,
    _normalize_proxy,
    build_decodo_url,
    generate_decodo_pool,
    load_proxies_from_file,
    normalize_proxy,
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


ENV_VARS = (
    "DECODO_PROXY_URL",
    "DECODO_USERNAME",
    "DECODO_PASSWORD",
    "DECODO_HOST",
    "DECODO_PORT_START",
    "DECODO_PORT_END",
    "DECODO_PROXY_FILE",
    "PROXY_FILE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_decodo_env_ignored_without_flag(clean_env):
    clean_env.setenv("DECODO_PROXY_URL", "http://envuser:envpass@isp.decodo.com:10001")
    clean_env.setenv("DECODO_USERNAME", "u")
    clean_env.setenv("DECODO_PASSWORD", "p")
    assert not resolve_proxy_config().has_proxy


def test_decodo_proxy_url_with_flag(clean_env):
    clean_env.setenv("DECODO_PROXY_URL", "envuser:envpass@isp.decodo.com:10001")
    cfg = resolve_proxy_config(enable_decodo=True)
    assert cfg.single_proxy == "http://envuser:envpass@isp.decodo.com:10001"


def test_decodo_pool_with_flag(clean_env):
    clean_env.setenv("DECODO_USERNAME", "myuser")
    clean_env.setenv("DECODO_PASSWORD", "mypass")
    clean_env.setenv("DECODO_PORT_START", "10001")
    clean_env.setenv("DECODO_PORT_END", "10003")
    assert not resolve_proxy_config(enable_decodo=False).has_proxy
    cfg = resolve_proxy_config(enable_decodo=True)
    assert len(cfg.proxy_list) == 3


def test_decodo_empty_pool_errors(clean_env):
    clean_env.setenv("DECODO_USERNAME", "myuser")
    clean_env.setenv("DECODO_PASSWORD", "secretpw")
    clean_env.setenv("DECODO_PORT_START", "10010")
    clean_env.setenv("DECODO_PORT_END", "10001")
    with pytest.raises(ProxyConfigError, match="empty proxy pool") as exc:
        resolve_proxy_config(enable_decodo=True)
    assert "secretpw" not in str(exc.value)


def test_decodo_bad_ports_error(clean_env):
    clean_env.setenv("DECODO_USERNAME", "u")
    clean_env.setenv("DECODO_PASSWORD", "p")
    clean_env.setenv("DECODO_PORT_START", "abc")
    with pytest.raises(ProxyConfigError):
        resolve_proxy_config(enable_decodo=True)


def test_decodo_missing_credentials_errors(clean_env):
    with pytest.raises(ProxyConfigError, match="--decodo requires"):
        resolve_proxy_config(enable_decodo=True)


def test_implicit_sources_not_used(clean_env, tmp_path):
    (tmp_path / "proxies.txt").write_text("http://u:p@h.example.com:1\n")
    pf = tmp_path / "env.txt"
    pf.write_text("http://u:p@h.example.com:2\n")
    clean_env.setenv("PROXY_FILE", str(pf))
    clean_env.setenv("HTTPS_PROXY", "http://u:p@h.example.com:3")
    clean_env.setenv("HTTP_PROXY", "http://u:p@h.example.com:4")
    assert not resolve_proxy_config().has_proxy


def test_proxy_env_opt_in(clean_env, tmp_path):
    clean_env.setenv("HTTPS_PROXY", "user:pw@h.example.com:3")
    cfg = resolve_proxy_config(use_env=True)
    assert cfg.single_proxy == "http://user:pw@h.example.com:3"

    pf = tmp_path / "env.txt"
    pf.write_text("h.example.com:2:user:pw\n")
    clean_env.setenv("PROXY_FILE", str(pf))
    assert resolve_proxy_config(use_env=True).proxy_list == ["http://user:pw@h.example.com:2"]


def test_proxy_env_nothing_set_errors(clean_env):
    with pytest.raises(ProxyConfigError):
        resolve_proxy_config(use_env=True)


def test_no_proxy_is_direct_and_exclusive(clean_env):
    clean_env.setenv("DECODO_PROXY_URL", "http://u:p@h.example.com:1")
    assert not resolve_proxy_config(no_proxy=True).has_proxy
    with pytest.raises(ProxyConfigError):
        resolve_proxy_config(no_proxy=True, proxy="h.example.com:1")


def test_explicit_proxy_and_file(clean_env, tmp_path):
    assert resolve_proxy_config(proxy="h.example.com:80").single_proxy == "http://h.example.com:80"
    missing = tmp_path / "nope.txt"
    with pytest.raises(ProxyConfigError, match="not found"):
        resolve_proxy_config(proxy_file=str(missing))
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    with pytest.raises(ProxyConfigError, match="no proxies"):
        resolve_proxy_config(proxy_file=str(empty))


FORMATS = [
    ("http://user:secretpw@isp.decodo.com:10001", "http://user:secretpw@isp.decodo.com:10001"),
    ("socks5://user:secretpw@isp.decodo.com:10001", "socks5://user:secretpw@isp.decodo.com:10001"),
    ("https://user:secretpw@isp.decodo.com:10001", "https://user:secretpw@isp.decodo.com:10001"),
    ("user:secretpw@isp.decodo.com:10001", "http://user:secretpw@isp.decodo.com:10001"),
    ("isp.decodo.com:10001:user:secretpw", "http://user:secretpw@isp.decodo.com:10001"),
    ("isp.decodo.com:10001", "http://isp.decodo.com:10001"),
]


@pytest.mark.parametrize(("raw", "expected"), FORMATS)
def test_normalize_proxy(raw, expected):
    assert _normalize_proxy(raw) == expected


@pytest.mark.parametrize(("raw", "_expected"), FORMATS)
def test_redact_formats_never_leak(raw, _expected):
    out = redact_proxy_url(raw)
    assert "secretpw" not in out
    if "user" in raw:
        assert "user" in out
        assert "***" in out


@pytest.mark.parametrize(
    "raw",
    [
        "secretpw",
        "user:secretpw@",
        "a:b:c:secretpw:extra:stuff",
        "user:secretpw@host",
        "host:notaport:user:secretpw",
        "user secretpw@host:80",
        "://user:secretpw@host:80",
        "@secretpw",
    ],
)
def test_redact_fails_closed(raw):
    out = redact_proxy_url(raw)
    assert "secretpw" not in out
    assert out == "<proxy redacted>"


def test_load_file_normalizes(tmp_path: Path):
    f = tmp_path / "p.txt"
    f.write_text("isp.decodo.com:10001:user:secretpw\nuser:secretpw@h.example.com:2\n")
    assert load_proxies_from_file(f) == [
        "http://user:secretpw@isp.decodo.com:10001",
        "http://user:secretpw@h.example.com:2",
    ]


def test_load_file_bad_line_reports_line_number_not_content(tmp_path: Path):
    f = tmp_path / "p.txt"
    f.write_text("# c\nh.example.com:1\n\nsecretpw:garbage@@\n")
    with pytest.raises(ProxyConfigError) as exc:
        load_proxies_from_file(f)
    msg = str(exc.value)
    assert ":4:" in msg
    assert str(f) in msg
    assert "secretpw" not in msg


def test_normalize_proxy_error_has_no_content():
    with pytest.raises(ProxyConfigError) as exc:
        normalize_proxy("secretpw@@@")
    assert "secretpw" not in str(exc.value)
