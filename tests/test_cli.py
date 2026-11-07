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
