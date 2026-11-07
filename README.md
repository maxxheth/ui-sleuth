# UI Sleuth 🕵️‍♂️✨

> **Reverse-engineer live web pages into ultra-dense, token-optimized JSON/YAML context blueprints specifically designed for downstream LLMs and "vibe-coding" automated UI reconstruction.**

`UI Sleuth` (`ui-sleuth`) is a specialized automation tool and Python package built on modern standards (`uv`, `scrapling`, `pydantic`, `typer`). It bridges the gap between raw web scraping and LLM context windows by extracting design tokens, pruning DOM boilerplate, deduplicating repetitive UI patterns (e.g. card grids), and diagnosing external production disciplines (Three.js 3D models, Rive/Lottie vector animations, WebGL canvases, and background video loops).

---

## 🌟 Key Features

1. **Scrapling Engine Pipeline (`scrapling_engine.py`)**
   - Headless Chromium powered by `scrapling.DynamicFetcher` and `StealthyFetcher` (for Turnstile/bot bypass).
   - Network interception listeners attached to the browser session prior to navigation.
   - Configurable wait conditions (`networkidle`, `load`, or CSS selector) and timeouts.
   - Configurable screen viewport dimensions (e.g. `1440x900`).

2. **Decodo Proxy Integration (`proxy_manager.py`)**
   - Native support for Decodo ISP and residential proxy pools.
   - Automatic proxy parsing, credentials handling, and password redaction.
   - Round-robin proxy rotation pool support via Scrapling's `ProxyRotator`.
   - Proxies are opt-in only (`--proxy`, `--proxy-file`, `--decodo`, `--proxy-env`); nothing is picked up implicitly. `--no-proxy` forces a direct connection.

3. **Design Token Harvesting (`token_extractor.py`)**
   - Client-side JavaScript evaluation that samples computed styles across landmarks (`header`, `main`, `footer`, `section`, `nav`).
   - Normalizes and deduplicates color palettes into semantic roles: `backgrounds`, `surfaces`, `text`, `accents`.
   - Extracts typography stacks, base font sizes, line heights, and header scales (`<h1>` through `<h4>`).
   - Extracts dominant border radii, box-shadow elevation signatures, and CSS custom properties (`--color-*`, `--theme-*`).

4. **Semantic DOM Pruning & Pattern Recognition (`dom_pruner.py`)**
   - Strips non-semantic elements: `<script>`, `<style>`, `<noscript>`, `<svg>`, `<iframe>`, and 1x1 tracking pixels.
   - Maps page by major structural landmarks (`header`, `nav`, `main`, `section`, `article`, `aside`, `footer`).
   - **Pattern Deduplication**: Detects containers with 3+ repetitive sibling cards and collapses them into a concise schema:
     ```json
     {
       "pattern": "FeatureCard",
       "instance_count": 6,
       "sample_content": { "title": "Fast Inference", "description": "..." }
     }
     ```
   - Actionable interaction metadata: counts of forms, inputs, selects, and textareas, plus primary CTA button labels.
   - Concise copy previews (15–25 words) capturing copy intent without wasting LLM tokens.

5. **Specialized Asset & Runtime Inspector (`asset_inspector.py`)**
   - **Network Interception**: Logs binary 3D models (`.glb`, `.gltf`, `.splinecode`, `.usdz`, `.fbx`, `.bin`), vector motion (`.riv`, `.lottie`), and video streams (`.mp4`, `.webm`, `.m3u8`).
   - **Canvas & WebGL Evaluation**: Probes `<canvas>` elements for active 2D/WebGL/WebGL2 contexts and inspects runtime window engines (`Three.js`, `Babylon.js`, `Spline`, `Rive`, `Lottie`, `PixiJS`).
   - **Video Diagnostics**: Inspects playback flags (`autoplay`, `muted`, `loop`, `playsinline`) to distinguish decorative background loops from interactive players.
   - **Runtime Ecosystem Mapping**: Recommends modern React/Next.js wrappers (`@react-three/fiber`, `@rive-app/react-canvas`, `@splinetool/react-spline`, `@lottiefiles/react-lottie-player`).

6. **Sophisticated Feature Detection & Exploration (`feature_detector.py`)**
   - **Motion Runtimes**: Detects GSAP (ScrollTrigger, SplitText), Lenis smooth scrolling, Three.js, Rive, and Lottie.
   - **Sticky Tracks**: Identifies CSS and JS sticky scroll containers, pinning mechanics, and horizontal scroll tracks.
   - **Off-Screen Drawers & Modals**: Detects hidden drawers/modals, triggers them open to evaluate active DOM state and extract copy/structure, and restores original layout.
   - **Interactive Elements**: Inspects accordions, expandable FAQ modules, tabs, and marquees.

7. **Multimodal & Export Layer (`exporter.py`, `cli.py`)**
   - Full-page screenshots saved alongside blueprints (`--screenshot`), with progressive scrolling, texture clamp safeguards, and GSAP stabilization.
   - `blueprint.json`: Validated against strict Pydantic schemas.
   - `blueprint.md`: LLM-ready context block with embedded vibe-coding instructions, ready to copy-paste directly into Claude, GPT-4, or Gemini.
   - `blueprint.yaml`: Ultra-dense YAML representation for minimal token consumption.

8. **Multi-Page Site Crawl & Component System Synthesis (`crawler.py`)**
   - **Internal Route Discovery**: Explores internal links with intelligent architectural prioritization (nav and header links to `/pricing`, `/features`, `/about`, `/docs` over deep pagination).
   - **Cross-Page Design Tokens**: Synthesizes global color palettes with frequency analysis, typography hierarchies, and root CSS custom variables across all crawled pages.
   - **Component System Catalog**: Inters and synthesizes global layout shells (`GlobalNavbar`, `GlobalFooter`) and reusable modular components (`FeatureCard`, `PricingCard`, `AccordionFAQ`) with inferred TypeScript props interfaces and concrete sample content.
   - **Site-Wide Motion & Asset Manifest**: Deduplicates motion runtimes (GSAP, Lenis, Three.js, Lottie), sticky scroll tracks, offscreen drawers, and external 3D/video assets.
   - **Multi-Page Artifacts**: Generates `site_design_system.json`, `site_design_system.yaml`, `site_design_system.md`, `manifest.json`, and per-page detailed blueprints in `pages/<slug>/`.

---

## 🚀 Quickstart

### Prerequisites
- Python 3.11+
- `uv` (fast Python package manager)

### Installation
```bash
git clone git@github.com:maxxheth/ui-sleuth.git
cd ui-sleuth
uv sync
```

### Basic Extraction (Single Page)
```bash
uv run ui-sleuth extract https://example.com --output ./output --screenshot
```

### Multi-Page Crawl & Design System Synthesis
Crawl multiple pages to synthesize a unified component system and global design tokens:
```bash
# Dedicated crawl command
uv run ui-sleuth crawl https://example.com --max-pages 5 --max-depth 2 --output ./output --screenshot

# Or via extract with --crawl
uv run ui-sleuth extract https://example.com --crawl --max-pages 5 --screenshot
```

### Using Wait Conditions & Viewport
```bash
uv run ui-sleuth extract https://example.com \
  --output ./output \
  --wait-until networkidle \
  --timeout 30 \
  --viewport 1920x1080 \
  --screenshot
```

### Proxy Support
Proxies are **opt-in**. Without a proxy flag the connection is always direct; `proxies.txt`, `PROXY_FILE`, `HTTP(S)_PROXY` and `DECODO_*` are never read implicitly.

```bash
# Single proxy (any of: scheme://user:pass@host:port, user:pass@host:port, host:port:user:pass, host:port)
uv run ui-sleuth extract https://example.com --proxy "user:password@isp.decodo.com:10001"

# Pool from file, one proxy per line, same formats (rotates round-robin)
uv run ui-sleuth extract https://example.com --proxy-file ./proxies.txt

# Decodo settings from DECODO_* environment / .env
uv run ui-sleuth extract https://example.com --decodo

# PROXY_FILE / HTTPS_PROXY / HTTP_PROXY from the environment
uv run ui-sleuth extract https://example.com --proxy-env

# Force a direct connection
uv run ui-sleuth extract https://example.com --no-proxy
```

Precedence when several are given: `--no-proxy` (cannot be combined with others) > `--proxy` > `--proxy-file` > `--decodo` > `--proxy-env`. Proxies without a scheme are normalized to `http://user:pass@host:port`; an explicit scheme (`socks5://`, `https://`) is preserved. Unparseable proxy-file lines abort with the file and line number (never the content). If `--decodo` (or `--proxy-env`) cannot produce a usable proxy (missing credentials, `DECODO_PORT_START` > `DECODO_PORT_END`, ...), the command exits with an error instead of silently connecting directly. Passwords are always redacted in output and logs. Loopback targets (`localhost`, `*.localhost`, `127.0.0.0/8`, `::1`) bypass the proxy, and this is logged at INFO.

Environment variables read only with `--decodo`, in `.env`:
```env
DECODO_PROXY_URL="http://your_username:your_password@isp.decodo.com:10001"
# or
DECODO_USERNAME="your_username"
DECODO_PASSWORD="your_password"
DECODO_HOST="isp.decodo.com"
DECODO_PORT_START=10001
DECODO_PORT_END=10010
# or
DECODO_PROXY_FILE="./decodo-proxies.txt"
```

### Stealth Mode (Cloudflare Turnstile Bypass)
```bash
uv run ui-sleuth extract https://protected-site.com --stealth
```

---

## 📋 CLI Reference

### `ui-sleuth extract`
```
Usage: ui-sleuth extract [OPTIONS] [url]

Options:
  -u, --url <str>               Target URL (alternative to positional argument)
  -o, --output <path>           Output directory for generated blueprints [default: output]
  -s, --screenshot              Capture a full-page screenshot alongside the blueprint
      --wait-until <str>        Wait condition: 'networkidle', 'load', or CSS selector [default: networkidle]
  -t, --timeout <int>           Timeout in seconds [default: 30]
      --viewport <str>          Emulated viewport dimensions (WIDTHxHEIGHT) [default: 1440x900]
      --proxy <str>             Single proxy (scheme://user:pass@host:port, user:pass@host:port, host:port:user:pass, host:port)
      --proxy-file <str>        Path to text file with one proxy per line
      --decodo                  Use DECODO_* settings (errors if unusable)
      --proxy-env               Use PROXY_FILE / HTTPS_PROXY / HTTP_PROXY from the environment
      --no-proxy                Force a direct connection
      --stealth                 Use StealthyFetcher to bypass Cloudflare
  -f, --format <str>            Output format: 'all', 'json', 'markdown', or 'yaml' [default: all]
      --headless / --no-headless Run browser in headless or visible mode [default: headless]
      --real-chrome / --no-real-chrome Use installed Google Chrome executable
  -d, --delay <float>           Post-load stabilization delay in seconds [default: 2.0]
      --auto-scroll / --no-auto-scroll Progressively scroll page to trigger lazy loading [default: auto-scroll]
      --explore / --no-explore  Detect and independently explore sophisticated features [default: explore]
      --crawl                   Enable multi-page site crawl to gather component and layout evidence
  -m, --max-pages <int>         Maximum number of pages to crawl [default: 1 for single, 5 for crawl]
      --max-depth <int>         Maximum crawl link depth for internal link discovery [default: 2]
  -v, --verbose                 Enable verbose debug logging
      --help                    Show this message and exit.
```

### `ui-sleuth crawl`
```
Usage: ui-sleuth crawl [OPTIONS] [url]

Options:
  -u, --url <str>               Target URL (alternative to positional argument)
  -o, --output <path>           Output directory for generated blueprints [default: output]
  -m, --max-pages <int>         Maximum number of pages to crawl across the site [default: 5]
      --max-depth <int>         Maximum crawl link depth for internal link discovery [default: 2]
  -s, --screenshot              Capture full-page screenshots alongside blueprints
      --wait-until <str>        Wait condition: 'networkidle', 'load', or CSS selector [default: networkidle]
  -t, --timeout <int>           Timeout in seconds [default: 30]
      --viewport <str>          Emulated viewport dimensions (WIDTHxHEIGHT) [default: 1440x900]
      --proxy <str>             Single proxy (scheme://user:pass@host:port, user:pass@host:port, host:port:user:pass, host:port)
      --proxy-file <str>        Path to text file with one proxy per line
      --decodo                  Use DECODO_* settings (errors if unusable)
      --proxy-env               Use PROXY_FILE / HTTPS_PROXY / HTTP_PROXY from the environment
      --no-proxy                Force a direct connection
      --stealth                 Use StealthyFetcher to bypass Cloudflare
  -f, --format <str>            Output format: 'all', 'json', 'markdown', or 'yaml' [default: all]
      --headless / --no-headless Run browser in headless or visible mode [default: headless]
      --real-chrome / --no-real-chrome Use installed Google Chrome executable
  -d, --delay <float>           Post-load stabilization delay in seconds [default: 2.0]
      --auto-scroll / --no-auto-scroll Progressively scroll page to trigger lazy loading [default: auto-scroll]
      --explore / --no-explore  Detect and independently explore sophisticated features [default: explore]
  -v, --verbose                 Enable verbose debug logging
      --help                    Show this message and exit.
```

---

## 🧪 Testing & Code Quality

Run tests with `pytest`:
```bash
uv run pytest
```

Check formatting and lint rules with strict `ruff` settings:
```bash
uv run ruff check
uv run ruff format --check
```

Run strict static type analysis with `pyright`:
```bash
uv run pyright
```
