"""Tests for CLI interface."""

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
    assert "0.1.0" in result.stdout


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
