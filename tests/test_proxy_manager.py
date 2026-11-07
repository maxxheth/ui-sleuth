"""Tests for Decodo and proxy management."""

from pathlib import Path

import pytest

from ui_sleuth.proxy_manager import (
    DecodoEndpoint,
    DecodoStrategy,
    GroupedRotation,
    ProxyConfig,
    ProxyConfigError,
    ProxyGroup,
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
    "DECODO_STRATEGY",
    "DECODO_SECONDARY_HOST",
    "DECODO_SECONDARY_USERNAME",
    "DECODO_SECONDARY_PASSWORD",
    "DECODO_SECONDARY_PORT_START",
    "DECODO_SECONDARY_PORT_END",
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


# ---------------------------------------------------------------- secondary Decodo


def _set_primary(env, start="10001", end="10003"):
    env.setenv("DECODO_USERNAME", "puser")
    env.setenv("DECODO_PASSWORD", "ppass-secret")
    env.setenv("DECODO_PORT_START", start)
    env.setenv("DECODO_PORT_END", end)


def _set_secondary(env, host="203.0.113.7", start="10001", end="10002"):
    env.setenv("DECODO_SECONDARY_HOST", host)
    env.setenv("DECODO_SECONDARY_USERNAME", "suser")
    env.setenv("DECODO_SECONDARY_PASSWORD", "spass-secret")
    env.setenv("DECODO_SECONDARY_PORT_START", start)
    env.setenv("DECODO_SECONDARY_PORT_END", end)


def test_secondary_unset_is_unchanged(clean_env):
    _set_primary(clean_env)
    cfg = resolve_proxy_config(enable_decodo=True)
    assert len(cfg.proxy_list) == 3
    assert not cfg.can_fail_over
    assert cfg.fail_over() is False
    assert cfg.get_display_summary().startswith("Pool of 3 proxies")
    clean_env.setenv("DECODO_PROXY_URL", "http://u:p@h.example.com:1")
    cfg = resolve_proxy_config(enable_decodo=True)
    assert cfg.single_proxy == "http://u:p@h.example.com:1"
    assert cfg.proxy_list == []


def test_pool_interleaves_and_sums(clean_env):
    _set_primary(clean_env)
    _set_secondary(clean_env)
    cfg = resolve_proxy_config(enable_decodo=True)
    assert cfg.strategy is DecodoStrategy.POOL
    assert len(cfg.proxy_list) == 5
    hosts = [u.split("@")[1].split(":")[0] for u in cfg.proxy_list]
    assert hosts == [
        "isp.decodo.com",
        "203.0.113.7",
        "isp.decodo.com",
        "203.0.113.7",
        "isp.decodo.com",
    ]
    assert cfg.rotator is not None and len(cfg.rotator) == 5
    assert cfg.fail_over() is False and not cfg.can_fail_over
    assert cfg.get_display_summary() == (
        "Decodo pool (round-robin): primary isp.decodo.com x3, secondary 203.0.113.7 x2"
    )


def test_secondary_defaults_ports(clean_env):
    _set_primary(clean_env)
    for v, val in (
        ("HOST", "sec.example.com"),
        ("USERNAME", "suser"),
        ("PASSWORD", "spass-secret"),
    ):
        clean_env.setenv(f"DECODO_SECONDARY_{v}", val)
    cfg = resolve_proxy_config(enable_decodo=True)
    sec = next(g for g in cfg.groups if g.label == "secondary")
    assert len(sec.urls) == 10
    assert sec.urls[0].endswith("sec.example.com:10001")
    assert sec.urls[-1].endswith("sec.example.com:10010")


def test_single_url_primary_with_secondary(clean_env):
    clean_env.setenv("DECODO_PROXY_URL", "http://u:p@h.example.com:1")
    _set_secondary(clean_env, end="10001")
    cfg = resolve_proxy_config(enable_decodo=True, decodo_strategy="pool")
    assert len(cfg.proxy_list) == 2
    assert cfg.rotator is not None


def test_secondary_partial_names_missing_not_values(clean_env):
    _set_primary(clean_env)
    clean_env.setenv("DECODO_SECONDARY_HOST", "203.0.113.7")
    clean_env.setenv("DECODO_SECONDARY_USERNAME", "suser-name")
    with pytest.raises(ProxyConfigError) as exc:
        resolve_proxy_config(enable_decodo=True)
    msg = str(exc.value)
    assert "DECODO_SECONDARY_PASSWORD" in msg
    assert "DECODO_SECONDARY_HOST" not in msg
    assert "suser-name" not in msg and "203.0.113.7" not in msg
    assert "ppass-secret" not in msg


def test_secondary_only_port_var_counts_as_configured(clean_env):
    _set_primary(clean_env)
    clean_env.setenv("DECODO_SECONDARY_PORT_START", "10001")
    with pytest.raises(ProxyConfigError, match="DECODO_SECONDARY_HOST"):
        resolve_proxy_config(enable_decodo=True)


def test_secondary_without_primary_errors(clean_env):
    _set_secondary(clean_env)
    with pytest.raises(ProxyConfigError, match="secondary requires a primary") as exc:
        resolve_proxy_config(enable_decodo=True)
    assert "spass-secret" not in str(exc.value)


@pytest.mark.parametrize(
    ("start", "end"), [("abc", "10002"), ("10001", "x"), ("10005", "10001"), ("0", "10")]
)
def test_secondary_bad_ports_error(clean_env, start, end):
    _set_primary(clean_env)
    _set_secondary(clean_env, start=start, end=end)
    with pytest.raises(ProxyConfigError) as exc:
        resolve_proxy_config(enable_decodo=True)
    assert "spass-secret" not in str(exc.value)


def test_secondary_bad_host_errors_without_secrets(clean_env):
    _set_primary(clean_env)
    _set_secondary(clean_env, host="http://u:spass-secret@bad host")
    with pytest.raises(ProxyConfigError) as exc:
        resolve_proxy_config(enable_decodo=True)
    assert "spass-secret" not in str(exc.value)


def test_secondary_ignored_without_flag(clean_env):
    _set_primary(clean_env)
    _set_secondary(clean_env)
    assert not resolve_proxy_config().has_proxy


def test_overlap_warns(clean_env, caplog):
    _set_primary(clean_env)
    _set_secondary(clean_env, host="isp.decodo.com", start="10003", end="10004")
    with caplog.at_level("WARNING", logger="ui_sleuth.proxy_manager"):
        resolve_proxy_config(enable_decodo=True)
    assert any("overlapping" in r.getMessage() for r in caplog.records)
    assert "ppass-secret" not in caplog.text and "spass-secret" not in caplog.text
    caplog.clear()
    _set_secondary(clean_env, host="isp.decodo.com", start="10004", end="10005")
    with caplog.at_level("WARNING", logger="ui_sleuth.proxy_manager"):
        resolve_proxy_config(enable_decodo=True)
    assert not caplog.records


def test_strategy_precedence_and_validation(clean_env):
    _set_primary(clean_env)
    _set_secondary(clean_env)
    assert resolve_proxy_config(enable_decodo=True).strategy is DecodoStrategy.POOL
    clean_env.setenv("DECODO_STRATEGY", "failover")
    assert resolve_proxy_config(enable_decodo=True).strategy is DecodoStrategy.FAILOVER
    cfg = resolve_proxy_config(enable_decodo=True, decodo_strategy=DecodoStrategy.POOL)
    assert cfg.strategy is DecodoStrategy.POOL
    cfg = resolve_proxy_config(enable_decodo=True, decodo_strategy="FAILOVER")
    assert cfg.strategy is DecodoStrategy.FAILOVER
    with pytest.raises(ProxyConfigError, match="strategy"):
        resolve_proxy_config(enable_decodo=True, decodo_strategy="bogus")
    clean_env.setenv("DECODO_STRATEGY", "bogus")
    with pytest.raises(ProxyConfigError, match="strategy"):
        resolve_proxy_config(enable_decodo=True)


def test_strategy_ignored_with_single_group(clean_env):
    _set_primary(clean_env)
    cfg = resolve_proxy_config(enable_decodo=True, decodo_strategy="failover")
    assert not cfg.can_fail_over and cfg.fail_over() is False
    assert cfg.get_display_summary().startswith("Pool of 3 proxies")


def test_failover_summary_and_flow(clean_env):
    _set_primary(clean_env, end="10002")
    _set_secondary(clean_env)
    cfg = resolve_proxy_config(enable_decodo=True, decodo_strategy="failover")
    assert cfg.get_display_summary() == (
        "Decodo failover: primary isp.decodo.com x2 \u2192 secondary 203.0.113.7 x2"
    )
    assert cfg.can_fail_over and cfg.active_label == "primary"
    first = {cfg.get_proxy_for_request() for _ in range(6)}
    assert all("isp.decodo.com" in u for u in first) and len(first) == 2
    assert cfg.fail_over() is True
    assert cfg.active_label == "secondary" and not cfg.can_fail_over
    second = {cfg.get_proxy_for_request() for _ in range(6)}
    assert all("203.0.113.7" in u for u in second) and len(second) == 2
    assert cfg.fail_over() is False


def test_failover_logs_labels_only(clean_env, caplog):
    _set_primary(clean_env)
    _set_secondary(clean_env)
    cfg = resolve_proxy_config(enable_decodo=True, decodo_strategy="failover")
    with caplog.at_level("WARNING", logger="ui_sleuth.proxy_manager"):
        cfg.fail_over()
    assert "primary" in caplog.text and "secondary" in caplog.text
    for secret in ("ppass-secret", "spass-secret", "puser", "suser"):
        assert secret not in caplog.text


def test_grouped_rotation_direct():
    groups = [ProxyGroup("a", ["http://h1:1", "http://h1:2"]), ProxyGroup("b", ["http://h2:1"])]
    from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

    rot = GroupedRotation(groups)
    pr = ProxyRotator([u for g in groups for u in g.urls], strategy=rot)
    assert [pr.get_proxy() for _ in range(4)] == ["http://h1:1", "http://h1:2"] * 2
    assert rot.advance() is True and rot.advance() is False
    assert [pr.get_proxy() for _ in range(3)] == ["http://h2:1"] * 3


def test_endpoint_for(clean_env):
    _set_primary(clean_env)
    _set_secondary(clean_env)
    cfg = resolve_proxy_config(enable_decodo=True)
    sec_url = next(g for g in cfg.groups if g.label == "secondary").urls[1]
    assert cfg.endpoint_for(sec_url) == "secondary (203.0.113.7:10002)"
    as_dict = {"server": "http://203.0.113.7:10002", "username": "suser", "password": "x"}
    assert cfg.endpoint_for(as_dict) == "secondary (203.0.113.7:10002)"
    assert cfg.endpoint_for(cfg.proxy_list[0]) == "primary (isp.decodo.com:10001)"
    assert cfg.endpoint_for("http://other.example.com:9") is None
    assert cfg.endpoint_for(None) is None
    assert ProxyConfig(proxy_list=["http://a:1", "http://b:2"]).endpoint_for("http://a:1") is None


def test_no_secrets_in_summary_repr_or_errors(clean_env):
    _set_primary(clean_env)
    _set_secondary(clean_env)
    for strat in ("pool", "failover"):
        cfg = resolve_proxy_config(enable_decodo=True, decodo_strategy=strat)
        text = f"{cfg.get_display_summary()} {cfg.groups!r} {cfg.rotator!r}"
        for secret in ("ppass-secret", "spass-secret", "puser", "suser"):
            assert secret not in text
    ep = DecodoEndpoint("secondary", "h.example.com", "suser", "spass-secret")
    assert "spass-secret" not in repr(ep)
    assert len(ep.urls()) == 10


# ---------------------------------------------------------------- hosts


def test_ipv4_secondary_host_url(clean_env):
    assert build_decodo_url("u", "p", host="203.0.113.7", port=10003) == (
        "http://u:p@203.0.113.7:10003"
    )


@pytest.mark.parametrize("host", ["2001:db8::1", "[2001:db8::1]"])
def test_ipv6_host_bracketed_roundtrip(host):
    url = build_decodo_url("u", "secretpw", host=host, port=10001)
    assert url == "http://u:secretpw@[2001:db8::1]:10001"
    assert normalize_proxy(url) == url
    masked = redact_proxy_url(url)
    assert masked == "http://u:***@[2001:db8::1]:10001"
    assert "secretpw" not in masked
    assert DecodoEndpoint("secondary", host, "u", "secretpw").urls()[0] == url


@pytest.mark.parametrize(
    "host",
    ["", "bad host", "a/b", "u:secretpw@h.com", "999.1.1.1", "[1.2.3.4]", "fe80::1%eth0", "-x.com"],
)
def test_invalid_hosts_rejected_without_leak(host):
    with pytest.raises(ProxyConfigError) as exc:
        build_decodo_url("u", "p", host=host)
    assert "secretpw" not in str(exc.value)
