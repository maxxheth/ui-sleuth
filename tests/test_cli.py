"""Tests for CLI interface."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ui_sleuth.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Reverse-engineer live web pages" in result.stdout


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    from ui_sleuth import __version__

    assert __version__ in result.stdout


def test_cli_missing_url():
    result = runner.invoke(app, ["extract"])
    assert result.exit_code != 0
    assert "Missing target URL" in result.stdout


_PROXY_ENV = (
    "DECODO_PROXY_URL",
    "DECODO_USERNAME",
    "DECODO_PASSWORD",
    "DECODO_PORT_START",
    "DECODO_PORT_END",
    "DECODO_PROXY_FILE",
    "PROXY_FILE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "UI_SLEUTH_PROXY",
    "UI_SLEUTH_PROXY_FILE",
    "UI_SLEUTH_DECODO",
    "UI_SLEUTH_PROXY_ENV",
    "UI_SLEUTH_NO_PROXY",
    "UI_SLEUTH_DECODO_STRATEGY",
    "DECODO_STRATEGY",
    "DECODO_SECONDARY_HOST",
    "DECODO_SECONDARY_USERNAME",
    "DECODO_SECONDARY_PASSWORD",
    "DECODO_SECONDARY_PORT_START",
    "DECODO_SECONDARY_PORT_END",
)


def _clean(monkeypatch):
    for var in _PROXY_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("command", ["extract", "crawl"])
def test_cli_decodo_empty_pool_exits_nonzero(monkeypatch, command):
    _clean(monkeypatch)
    monkeypatch.setenv("DECODO_USERNAME", "user")
    monkeypatch.setenv("DECODO_PASSWORD", "secretpw")
    monkeypatch.setenv("DECODO_PORT_START", "10010")
    monkeypatch.setenv("DECODO_PORT_END", "10001")
    result = runner.invoke(app, [command, "https://example.com", "--decodo"])
    assert result.exit_code == 1
    assert "empty proxy pool" in result.stdout
    assert "secretpw" not in result.stdout


@pytest.mark.parametrize("command", ["extract", "crawl"])
def test_cli_decodo_missing_credentials_exits_nonzero(monkeypatch, command):
    _clean(monkeypatch)
    result = runner.invoke(app, [command, "https://example.com", "--decodo"])
    assert result.exit_code == 1
    assert "--decodo requires" in result.stdout


def test_cli_bad_proxy_file_line_no_leak(monkeypatch, tmp_path):
    _clean(monkeypatch)
    f = tmp_path / "p.txt"
    f.write_text("secretpw@@@\n")
    result = runner.invoke(app, ["extract", "https://example.com", "--proxy-file", str(f)])
    assert result.exit_code == 1
    assert ":1:" in result.stdout
    assert "secretpw" not in result.stdout


def test_cli_no_proxy_conflicts(monkeypatch):
    _clean(monkeypatch)
    result = runner.invoke(
        app, ["extract", "https://example.com", "--no-proxy", "--proxy", "h.example.com:80"]
    )
    assert result.exit_code == 1
    assert "--no-proxy" in result.stdout


@pytest.mark.parametrize("command", ["extract", "crawl"])
def test_cli_invalid_format_rejected_writes_nothing(monkeypatch, tmp_path, command):
    _clean(monkeypatch)
    out = tmp_path / "out"
    result = runner.invoke(app, [command, "https://example.com", "-f", "bogus", "-o", str(out)])
    assert result.exit_code != 0
    assert not out.exists()


@pytest.mark.parametrize("alias", ["md", "yml"])
def test_cli_format_alias_accepted(monkeypatch, tmp_path, alias):
    from ui_sleuth import cli
    from ui_sleuth.models import SiteBlueprint

    _clean(monkeypatch)
    calls = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def execute(self):
            return SiteBlueprint.model_construct()

    def fake_export(**kwargs):
        calls.update(kwargs)
        return {}

    monkeypatch.setattr(cli, "ScraplingEngine", FakeEngine)
    monkeypatch.setattr(cli, "export_blueprint", fake_export)
    result = runner.invoke(
        app, ["extract", "https://example.com", "-f", alias, "-o", str(tmp_path)]
    )
    assert "Invalid value" not in result.output
    assert calls["export_markdown"] is (alias == "md")
    assert calls["export_yaml"] is (alias == "yml")
    assert calls["export_json"] is False


def test_default_output_dir_uses_host():
    from ui_sleuth.cli import _default_output_dir

    assert _default_output_dir("https://www.Example.com/a?b=1") == Path("output/example.com")
    assert _default_output_dir("http://localhost:8000/") == Path("output/localhost")


def test_extract_without_output_uses_host_dir(monkeypatch):
    from ui_sleuth import cli
    from ui_sleuth.models import SiteBlueprint

    _clean(monkeypatch)
    calls = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            pass

        def execute(self):
            return SiteBlueprint.model_construct()

    monkeypatch.setattr(cli, "ScraplingEngine", FakeEngine)
    monkeypatch.setattr(cli, "export_blueprint", lambda **kw: calls.update(kw) or {})
    runner.invoke(app, ["extract", "https://www.example.com/x"])
    assert calls["output_dir"] == Path("output/example.com")


def test_stale_artifact_warning_lists_other_formats(tmp_path, capsys):
    from ui_sleuth.cli import _warn_stale_artifacts

    (tmp_path / "blueprint.md").write_text("old")
    _warn_stale_artifacts(tmp_path, True, False, False)
    out = capsys.readouterr().out
    assert "blueprint.md" in out
    assert (tmp_path / "blueprint.md").exists()
    _warn_stale_artifacts(tmp_path, True, True, True)
    assert capsys.readouterr().out == ""


def test_import_does_not_touch_environ(tmp_path):
    import subprocess
    import sys

    (tmp_path / ".env").write_text("UI_SLEUTH_PROBE=1\n")
    code = "import os, ui_sleuth, sys; assert 'ui_sleuth.cli' not in sys.modules; print(os.environ.get('UI_SLEUTH_PROBE'))"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "None"


def _fake_engine(monkeypatch):
    """Stub engine/export so no browser runs; returns the kwargs the engine received."""
    from ui_sleuth import cli
    from ui_sleuth.models import DesignTokens, SiteBlueprint, SiteMetadata

    seen: dict = {}

    class FakeEngine:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def execute(self):
            return SiteBlueprint(
                metadata=SiteMetadata(url="https://example.com", timestamp="now"),
                tokens=DesignTokens(),
            )

    monkeypatch.setattr(cli, "ScraplingEngine", FakeEngine)
    monkeypatch.setattr(cli, "export_blueprint", lambda **kw: {})
    return seen


_DECODO_CREDS = {
    "DECODO_USERNAME": "user",
    "DECODO_PASSWORD": "secretpw",
    "DECODO_PORT_START": "10001",
    "DECODO_PORT_END": "10001",
}


def test_env_decodo_enables_proxy(monkeypatch):
    _clean(monkeypatch)
    for k, v in _DECODO_CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com"])
    assert result.exit_code == 0, result.stdout
    assert seen["proxy_config"].has_proxy
    assert "secretpw" not in result.stdout


def test_env_proxy_value_used_and_redacted(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("UI_SLEUTH_PROXY", "user:secretpw@h.example.com:10001")
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com"])
    assert result.exit_code == 0, result.stdout
    assert seen["proxy_config"].has_proxy
    assert "h.example.com:10001" in result.stdout
    assert "secretpw" not in result.stdout


def test_cli_no_proxy_overrides_env(monkeypatch):
    _clean(monkeypatch)
    for k, v in _DECODO_CREDS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com", "--no-proxy"])
    assert result.exit_code == 0, result.stdout
    assert not seen["proxy_config"].has_proxy


def test_cli_proxy_overrides_env_proxy_file(monkeypatch, tmp_path):
    _clean(monkeypatch)
    f = tmp_path / "p.txt"
    f.write_text("file.example.com:81\n")
    monkeypatch.setenv("UI_SLEUTH_PROXY_FILE", str(f))
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")  # would error (no creds) if not dropped
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com", "--proxy", "cli.example.com:80"])
    assert result.exit_code == 0, result.stdout
    assert "cli.example.com" in result.stdout
    assert "file.example.com" not in result.stdout
    assert seen["proxy_config"].has_proxy


@pytest.mark.parametrize("command", ["extract", "crawl"])
def test_env_conflict_still_errors(monkeypatch, command):
    _clean(monkeypatch)
    monkeypatch.setenv("UI_SLEUTH_NO_PROXY", "1")
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")
    result = runner.invoke(app, [command, "https://example.com"])
    assert result.exit_code == 1
    assert "--no-proxy" in result.stdout


def test_env_no_proxy_false_value_is_not_set(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("UI_SLEUTH_NO_PROXY", "0")
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com"])
    assert result.exit_code == 0, result.stdout
    assert not seen["proxy_config"].has_proxy


def test_dotenv_file_enables_decodo(monkeypatch, tmp_path):
    """A real .env in the cwd is loaded before the subcommand's options are parsed."""
    from dotenv import load_dotenv as real_load_dotenv

    _clean(monkeypatch)
    # load_dotenv mutates os.environ: register every key with monkeypatch so it is restored.
    monkeypatch.setenv("UI_SLEUTH_DECODO", "placeholder")
    monkeypatch.delenv("UI_SLEUTH_DECODO")
    monkeypatch.setattr("ui_sleuth.cli.load_dotenv", real_load_dotenv)
    (tmp_path / ".env").write_text("UI_SLEUTH_DECODO=1\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["extract", "https://example.com"])
    assert result.exit_code == 1
    assert "--decodo requires" in result.stdout
    # CLI still wins over the .env value
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com", "--no-proxy"])
    assert result.exit_code == 0, result.stdout
    assert not seen["proxy_config"].has_proxy


def test_help_shows_proxy_env_vars():
    for command in ("extract", "crawl"):
        result = runner.invoke(app, [command, "--help"], env={"COLUMNS": "300"})
        for name in (
            "UI_SLEUTH_PROXY",
            "UI_SLEUTH_PROXY_FILE",
            "UI_SLEUTH_DECODO",
            "UI_SLEUTH_PROXY_ENV",
            "UI_SLEUTH_NO_PROXY",
        ):
            assert name in result.stdout


_SECONDARY = {
    "DECODO_SECONDARY_HOST": "203.0.113.7",
    "DECODO_SECONDARY_USERNAME": "user2",
    "DECODO_SECONDARY_PASSWORD": "secretpw2",
    "DECODO_SECONDARY_PORT_START": "10001",
    "DECODO_SECONDARY_PORT_END": "10002",
}


def _two_endpoints(monkeypatch):
    _clean(monkeypatch)
    for k, v in {**_DECODO_CREDS, **_SECONDARY}.items():
        monkeypatch.setenv(k, v)


def test_cli_decodo_strategy_overrides_env_strategy(monkeypatch):
    _two_endpoints(monkeypatch)
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")
    monkeypatch.setenv("UI_SLEUTH_DECODO_STRATEGY", "pool")
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com", "--decodo-strategy", "failover"])
    assert result.exit_code == 0, result.stdout
    assert seen["proxy_config"].strategy == "failover"
    assert seen["proxy_config"].can_fail_over
    assert "secretpw" not in result.stdout


def test_crawl_decodo_strategy_overrides_env_strategy(monkeypatch):
    from ui_sleuth import cli

    _two_endpoints(monkeypatch)
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")
    monkeypatch.setenv("UI_SLEUTH_DECODO_STRATEGY", "pool")
    seen = {}
    monkeypatch.setattr(cli, "_execute_multi_page_crawl", lambda **kw: seen.update(kw))
    result = runner.invoke(app, ["crawl", "https://example.com", "--decodo-strategy", "FAILOVER"])
    assert result.exit_code == 0, result.stdout
    assert seen["proxy_cfg"].strategy == "failover"


def test_cli_env_strategy_and_dotenv_style_fallback(monkeypatch):
    _two_endpoints(monkeypatch)
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")
    monkeypatch.setenv("UI_SLEUTH_DECODO_STRATEGY", "failover")
    seen = _fake_engine(monkeypatch)
    assert runner.invoke(app, ["extract", "https://example.com"]).exit_code == 0
    assert seen["proxy_config"].strategy == "failover"

    monkeypatch.delenv("UI_SLEUTH_DECODO_STRATEGY")
    monkeypatch.setenv("DECODO_STRATEGY", "failover")  # what .env would provide
    assert runner.invoke(app, ["extract", "https://example.com"]).exit_code == 0
    assert seen["proxy_config"].strategy == "failover"

    monkeypatch.delenv("DECODO_STRATEGY")
    assert runner.invoke(app, ["extract", "https://example.com"]).exit_code == 0
    assert seen["proxy_config"].strategy == "pool"


def test_cli_strategy_alone_does_not_drop_env_decodo(monkeypatch):
    _two_endpoints(monkeypatch)
    monkeypatch.setenv("UI_SLEUTH_DECODO", "1")
    seen = _fake_engine(monkeypatch)
    result = runner.invoke(app, ["extract", "https://example.com", "--decodo-strategy", "pool"])
    assert result.exit_code == 0, result.stdout
    assert seen["proxy_config"].has_proxy
    assert len(seen["proxy_config"].groups) == 2


@pytest.mark.parametrize("command", ["extract", "crawl"])
def test_cli_invalid_decodo_strategy_exits_nonzero(monkeypatch, command):
    _two_endpoints(monkeypatch)
    result = runner.invoke(
        app, [command, "https://example.com", "--decodo", "--decodo-strategy", "sideways"]
    )
    assert result.exit_code != 0
    assert "secretpw" not in result.output


def test_help_shows_decodo_strategy():
    for command in ("extract", "crawl"):
        out = runner.invoke(app, [command, "--help"], env={"COLUMNS": "200"}).output
        assert "--decodo-strategy" in out
        assert "UI_SLEUTH_DECODO_STRATEGY" in out
