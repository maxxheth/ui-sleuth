"""Tests for dom_pruner module."""

from ui_sleuth.dom_pruner import (
    prune_and_map_dom,
)

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>SaaS Landing Page</title>
    <style>body { background: #000; }</style>
    <script>console.log("tracker");</script>
</head>
<body>
    <script src="analytics.js"></script>
    <img src="https://example.com/pixel.gif" width="1" height="1" alt="pixel" />
    <svg><circle cx="50" cy="50" r="40" /></svg>

    <header id="main-header" class="sticky top-0 bg-white">
        <nav class="flex items-center justify-between">
            <a href="/" class="logo">Acme AI</a>
            <div class="nav-links">
                <a href="#features">Features</a>
                <a href="#pricing">Pricing</a>
                <button class="btn btn-primary">Start Free Trial</button>
            </div>
        </nav>
    </header>

    <main id="content">
        <section id="hero" class="hero-section">
            <h1>Build Intelligent Autonomous Systems</h1>
            <p>Deploy AI agents that reason, plan, and execute workflows seamlessly across your cloud infrastructure with enterprise security.</p>
            <form action="/signup" method="post">
                <input type="email" placeholder="Enter your work email" />
                <button type="submit" class="cta-button">Claim Early Access</button>
            </form>
        </section>

        <section id="features" class="feature-grid">
            <h2>Core Platform Features</h2>
            <div class="card-container grid-cols-3">
                <div class="feature-card">
                    <h3>Fast Processing</h3>
                    <p>Sub-millisecond inference and streaming across distributed edge nodes.</p>
                    <a href="/features/speed">Learn More</a>
                </div>
                <div class="feature-card">
                    <h3>Multi-Modal Reasoning</h3>
                    <p>Native comprehension of vision, audio, text, and binary formats.</p>
                    <a href="/features/multimodal">Learn More</a>
                </div>
                <div class="feature-card">
                    <h3>Enterprise Governance</h3>
                    <p>SOC2 compliance, role-based access control, and granular telemetry.</p>
                    <a href="/features/governance">Learn More</a>
                </div>
                <div class="feature-card">
                    <h3>Continuous Learning</h3>
                    <p>Self-improving prompt distillation and adaptive retrieval pipelines.</p>
                    <a href="/features/learning">Learn More</a>
                </div>
            </div>
        </section>
    </main>

    <footer id="colophon">
        <p>&copy; 2026 Acme AI Inc. All rights reserved.</p>
    </footer>
</body>
</html>
"""


def test_prune_and_map_dom():
    landmarks = prune_and_map_dom(SAMPLE_HTML)

    # 1. Check landmarks mapped
    landmark_roles = [lm.landmark for lm in landmarks]
    assert "header" in landmark_roles
    assert "main" in landmark_roles
    assert "footer" in landmark_roles

    # 2. Check header navigation and CTA
    header_lm = next(lm for lm in landmarks if lm.landmark == "header")
    assert header_lm.id == "main-header"
    assert header_lm.interaction.primary_actions_count >= 1
    assert "Start Free Trial" in header_lm.interaction.primary_action_labels

    # 3. Check hero section inside main or subsections
    main_lm = next(lm for lm in landmarks if lm.landmark == "main")
    hero_section = next((s for s in main_lm.subsections if s.id == "hero"), None)
    assert hero_section is not None
    assert hero_section.interaction.forms_count == 1
    assert hero_section.interaction.inputs_count == 1
    assert "Claim Early Access" in hero_section.interaction.primary_action_labels
    assert "Build Intelligent Autonomous Systems" in hero_section.text_preview

    # 4. Check feature cards deduplication and collapsing
    feature_section = next((s for s in main_lm.subsections if s.id == "features"), None)
    assert feature_section is not None
    assert len(feature_section.detected_patterns) == 1

    pattern = feature_section.detected_patterns[0]
    assert pattern.pattern == "FeatureCard"
    assert pattern.instance_count == 4
    assert pattern.sample_content["title"] == "Fast Processing"
    assert "Sub-millisecond" in pattern.sample_content["description"]
    assert pattern.sample_content["action"] == "Learn More"


def test_tracking_pixel_and_script_stripping():
    html_with_junk = """
    <div>
        <script>alert(1);</script>
        <noscript>No JS</noscript>
        <style>.x { color: red; }</style>
        <svg><path d="M0 0" /></svg>
        <iframe src="https://ads.com"></iframe>
        <img src="https://tracker.com/pixel" width="1" height="1" />
        <main>
            <h1>Clean Content</h1>
        </main>
    </div>
    """
    landmarks = prune_and_map_dom(html_with_junk)
    assert len(landmarks) == 1
    assert landmarks[0].landmark == "main"
    assert "Clean Content" in landmarks[0].text_preview


def test_nested_landmarks_in_wrapper_divs():
    html_nested = """
    <header id="hdr"><h1>Header</h1></header>
    <main id="main-area">
        <div class="outer-wrap">
            <div class="inner-container">
                <section id="nested-features">
                    <h2>Features Section</h2>
                    <div class="card-grid">
                        <div class="card"><h3>Feature 1</h3><p>Desc 1</p></div>
                        <div class="card"><h3>Feature 2</h3><p>Desc 2</p></div>
                        <div class="card"><h3>Feature 3</h3><p>Desc 3</p></div>
                    </div>
                </section>
                <section id="nested-pricing">
                    <h2>Pricing Section</h2>
                </section>
            </div>
        </div>
    </main>
    <footer id="ftr"><p>Footer</p></footer>
    """
    landmarks = prune_and_map_dom(html_nested)
    assert [lm.landmark for lm in landmarks] == ["header", "main", "footer"]

    main_lm = landmarks[1]
    assert len(main_lm.subsections) == 2
    sub_ids = [s.id for s in main_lm.subsections]
    assert sub_ids == ["nested-features", "nested-pricing"]

    # Verify pattern inside deeply nested section
    features_sub = main_lm.subsections[0]
    assert len(features_sub.detected_patterns) == 1
    assert features_sub.detected_patterns[0].instance_count == 3
    assert features_sub.detected_patterns[0].sample_content["title"] == "Feature 1"
