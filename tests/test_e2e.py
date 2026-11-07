"""End-to-end integration test with local HTTP server and Scrapling engine."""

import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from ui_sleuth.exporter import export_blueprint
from ui_sleuth.scrapling_engine import ScraplingEngine

E2E_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>VibeCraft Cloud</title>
    <style>
        :root {
            --color-primary: #6366f1;
            --color-surface: #1e1e24;
            --theme-accent: #06b6d4;
        }
        body {
            background-color: #0b0c10;
            color: #c5c6c7;
            font-family: 'Inter', sans-serif;
            font-size: 16px;
            line-height: 1.6;
            margin: 0;
        }
        h1 {
            font-size: 48px;
            font-weight: 800;
            line-height: 56px;
            color: #66fcf1;
        }
        .btn {
            background-color: #45a29e;
            color: #ffffff;
            border-radius: 8px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
            padding: 12px 24px;
            border: none;
            cursor: pointer;
        }
        .card {
            background-color: #1f2833;
            border-radius: 12px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
            padding: 20px;
            margin: 10px;
        }
    </style>
</head>
<body>
    <header id="top-nav">
        <nav>
            <span class="brand">VibeCraft</span>
            <button class="btn">Launch Console</button>
        </nav>
    </header>

    <main id="app">
        <section id="hero">
            <h1>Autonomous UI Reconstruction</h1>
            <p>Reverse-engineering real web interfaces into production Next.js blueprints.</p>
            <form action="/subscribe">
                <input type="email" placeholder="you@company.com" />
                <button type="submit" class="btn">Get Started</button>
            </form>
            <video autoplay muted loop playsinline src="/assets/hero-bg.mp4"></video>
            <canvas id="three-viewport" width="600" height="400"></canvas>
        </section>

        <section id="features">
            <h2>System Capabilities</h2>
            <div class="grid">
                <div class="card">
                    <h3>Token Harvesting</h3>
                    <p>Extracts colors, typography, and spacing system directly from computed styles.</p>
                </div>
                <div class="card">
                    <h3>DOM Pruning</h3>
                    <p>Strips noise and maps landmarks with semantic accuracy and compact context.</p>
                </div>
                <div class="card">
                    <h3>Pattern Deduplication</h3>
                    <p>Collapses repetitive card structures into unified schema instances.</p>
                </div>
            </div>
        </section>
    </main>

    <footer>
        <p>&copy; 2026 VibeCraft</p>
    </footer>
</body>
</html>
"""


class MockServerHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("hero-bg.mp4"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            self.wfile.write(b"fake_mp4_bytes")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(E2E_HTML.encode("utf-8"))

    def log_message(self, format, *args):
        pass


BLUEPRINT_BUDGET_BYTES = 30 * 1024


@pytest.mark.browser
def test_e2e_scrapling_engine(tmp_path: Path):
    # Start local server on available port
    server = socketserver.TCPServer(("127.0.0.1", 0), MockServerHandler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}"

    try:
        screenshot_file = tmp_path / "screenshot.png"
        engine = ScraplingEngine(
            url=url,
            wait_until="load",
            timeout=10,
            viewport="1440x900",
            real_chrome=False,  # bundled Chromium; no Google Chrome needed
            headless=True,
            screenshot_path=screenshot_file,
        )

        blueprint = engine.execute()

        # Verify Metadata
        assert blueprint.metadata.title == "VibeCraft Cloud"
        assert blueprint.metadata.status_code == 200

        # Verify Design Tokens
        colors = blueprint.tokens.colors
        assert len(colors.all_colors) > 0
        assert "#0b0c10" in colors.backgrounds or any(c.hex == "#0b0c10" for c in colors.all_colors)
        assert blueprint.tokens.typography.primary_font_family != ""
        assert "h1" in blueprint.tokens.typography.header_scales

        # Verify Custom Properties
        assert "--color-primary" in blueprint.tokens.custom_properties
        assert blueprint.tokens.custom_properties["--color-primary"] == "#6366f1"

        # Verify DOM Landmarks
        landmarks = [lm.landmark for lm in blueprint.landmarks]
        assert "header" in landmarks
        assert "main" in landmarks
        assert "footer" in landmarks

        # Verify Pattern Deduplication in features
        main_lm = next(lm for lm in blueprint.landmarks if lm.landmark == "main")
        feat_section = next((s for s in main_lm.subsections if s.id == "features"), None)
        assert feat_section is not None
        assert len(feat_section.detected_patterns) == 1
        assert feat_section.detected_patterns[0].pattern == "FeatureCard"
        assert feat_section.detected_patterns[0].instance_count == 3

        # Verify External Production Assets (video and canvas)
        assert len(blueprint.external_production_assets) > 0
        categories = [a.category for a in blueprint.external_production_assets]
        assert "rich_video" in categories or "interactive_canvas" in categories

        # Verify Screenshot was saved
        assert screenshot_file.exists()

        # Verify Export
        out_dir = tmp_path / "output"
        saved = export_blueprint(blueprint, out_dir)
        assert saved["json"].exists()
        assert saved["markdown"].exists()
        assert saved["yaml"].exists()

        # Output budget: the "token-optimized" blueprint must stay small.
        assert saved["json"].stat().st_size < BLUEPRINT_BUDGET_BYTES
        assert all(
            f.exploration_status != "explored"
            for f in (
                blueprint.sophisticated_features.features
                if blueprint.sophisticated_features
                else []
            )
        )

    finally:
        server.shutdown()
        server.server_close()
