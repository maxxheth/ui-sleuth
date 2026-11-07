"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(scope="module")
def chromium_browser() -> Iterator[object]:
    """Launch bundled Chromium per module (sync Playwright cannot nest with other sync users)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:
        pw.stop()
        pytest.skip(f"Playwright Chromium not available: {exc}")
    try:
        yield browser
    finally:
        browser.close()
        pw.stop()


@pytest.fixture
def browser_page(chromium_browser: object) -> Iterator[object]:
    """Fresh page (1440x900) in an isolated context; no network is used by tests."""
    context = chromium_browser.new_context(viewport={"width": 1440, "height": 900})  # type: ignore[attr-defined]
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()
