# UI Sleuth: Code Review & Remediation Plan

- **Date:** 2026-11-07
- **Scope:** `src/ui_sleuth/*` (~4.7k LOC), `tests/*`, `pyproject.toml`, README
- **Revision reviewed:** `5e38587` (master, clean tree)
- **Note:** `/var/www/edfx-labs/ui-sleuth` is a symlink to this repo, so there is one codebase.

## Baseline

| Check | Result |
|---|---|
| `ruff check src tests` | Clean |
| `pyright` (strict) | 0 errors when run with the venv interpreter (see L9) |
| `pytest` | 34/34 pass, including e2e (which needs Google Chrome installed) |

Everything below gets past the current suite. Each item names the regression test that should be added with its fix.

**Evidence legend**
- **Reproduced:** I ran a script against the current code and saw the failure.
- **Source-verified:** confirmed by reading this repo plus installed `scrapling==0.4.15`.
- **Reasoned:** found by reading the code, not executed.

---

## Summary

| ID | Sev | Area | Issue | Evidence |
|---|---|---|---|---|
| C1 | Critical | dom_pruner | Distinct page `<section>`s are collapsed into a "ContentCard" pattern and deleted from the blueprint | Reproduced |
| C2 | Critical | scrapling_engine | Auto-scroll hydration never ends on infinite-scroll pages; the CLI hangs forever | Reproduced |
| H1 | High | dom_pruner | Detached (already-collapsed) nodes are re-scanned, giving duplicate patterns | Reproduced |
| H2 | High | asset_inspector, feature_detector | Canvas probe permanently locks untouched canvases to 2D, breaking lazy WebGL | Reproduced |
| H3 | High | scrapling_engine, feature_detector | Page is left scrolled to the bottom: false "drawer" detections, and the screenshot is taken in scrolled state | Reproduced |
| H4 | High | proxy_manager | Scheme-less proxy strings (Decodo's export format) are printed **unredacted** | Reproduced |
| H5 | High | scrapling_engine | Proxy bypass decided by a substring match on the URL, which leaks the real IP | Reasoned |
| H6 | High | scrapling_engine | `--wait-until <css>` is awaited *after* tokens and screenshot are captured | Source-verified |
| H7 | High | crawler | `www.` and `http://` variants are crawled as separate pages (homepage up to 3×) | Reproduced |
| H8 | High | crawler, exporter | Slug collisions overwrite per-page blueprints and screenshots | Reproduced |
| H9 | High | feature_detector | "Exploration" is not implemented, but output marks features `explored` | Source-verified |
| H10 | High | feature_detector | No dedupe or caps: 124 marquee entries and a 204 KB "token-optimized" blueprint | Reproduced (`konpo-output/`) |
| M1 | Med | scrapling_engine | One `try` around all page-action steps: any failure skips title and screenshot | Reasoned |
| M2 | Med | scrapling_engine | Scrapling retries (3×) reuse the asset collector and action data | Source-verified |
| M3 | Med | proxy_manager | Proxies activated implicitly without `--decodo`; an empty Decodo pool silently goes direct | Reproduced (empty pool) |
| M4 | Med | scrapling_engine, tests | `real_chrome` auto-enabled when only Chromium exists; e2e test hardcodes Chrome | Source-verified |
| M5 | Med | token_extractor | CSS custom properties missed inside `@layer`/`@media`/selector lists (Tailwind v4) | Reasoned |
| M6 | Med | exporter | Untrusted page text is embedded verbatim in a "SYSTEM PROMPT" file (prompt injection) | Reasoned |
| M7 | Med | cli | An invalid `--format` writes nothing and exits 0 | Reasoned |
| M8 | Med | crawler, cli | A crawl where every page fails still exits 0 with empty artifacts | Reasoned |
| M9 | Med | feature_detector, exporter | Selectors hardcoded for one reference site leak into every run | Source-verified |
| M10 | Med | dom_pruner, feature_detector, asset_inspector | Over-broad substring heuristics misclassify or drop content | Reasoned |
| M11 | Med | asset_inspector | Rive detection misses URLs with query strings | Reasoned |
| M12 | Med | crawler | A new browser is launched for every crawled page | Source-verified |
| L1–L12 | Low | various | Dead code, nondeterminism, import side effects, config | See below |

---

## Critical

### C1: Distinct page sections are collapsed and deleted

- **Where:** `dom_pruner.py:242-289`. The signature is computed at :242, siblings are removed at :281-285, and it is called on every landmark's direct children at :346.
- **Problem:** The pattern signature is `tag:[direct child tags]`. A `<main>` holding several `<section><div>…</div></section>` blocks gives `section:[div]` for every section. They are grouped as one "ContentCard" pattern, and sections 2..N are **removed from the lxml tree**, so they never become subsections. Most page builders wrap section content in a single div, so this layout is extremely common. On such pages most of the page disappears from the blueprint.
- **Evidence:**
  ```python
  sec = '<section id="s{i}"><div><h2>Section {i}</h2><p>Distinct copy {i}</p></div></section>'
  prune_and_map_dom("<html><body><main>" + "".join(sec.format(i=i) for i in range(5)) + "</main></body></html>")
  # main.subsections ids -> ['s0']            (4 of 5 sections lost)
  # main.detected_patterns -> [('ContentCard', 5, {...'Section 0'...})]
  ```
- **Fix:**
  1. Never treat landmark tags (`LANDMARK_TAGS`) as pattern instances. They are handled as subsections.
  2. Strengthen similarity. Include normalized non-utility class tokens and a depth-2 child shape in the signature, and require a shared class token, not just the same tag list.
  3. Separate analysis from mutation. Compute patterns on an unmodified tree, collect the nodes to drop, and apply the removals after traversal. This also fixes H1.
- **Test:** `test_distinct_sections_not_collapsed`: 5 sections give 5 subsections and no pattern. Keep `test_prune_and_map_dom`'s card-grid case passing.

### C2: Hydration loop hangs forever on infinite-scroll pages

- **Where:** `scrapling_engine.py:74-83` (scroll loop), `:97-100` (`img.decode()`), `:103-105` (`document.fonts.ready`). Invoked at `:197`.
- **Problem:** The loop re-reads `scrollHeight` on every step. On any page that appends content while scrolled (feeds, blogs, shops), `currentY < maxScroll` never becomes false. `page.evaluate` has **no timeout** (Playwright's default timeout does not apply to it), so the whole CLI hangs. `img.decode()` on a stalled image request and `fonts.ready` are also unbounded.
- **Evidence:** A local page that appends 5×400px on scroll. With `page.set_default_timeout(5000)`, `page.evaluate(HYDRATE_PAGE_JS)` had still not returned when the process was killed at 25 s.
- **Fix:** Use an in-page budget passed from Python, cap the steps, and race every await against a timeout:
  ```js
  async ({ maxMs = 15000, maxSteps = 80, maxHeight = 40000 } = {}) => {
    const deadline = performance.now() + maxMs;
    const left = () => Math.max(0, deadline - performance.now());
    const withTimeout = (p, ms) => Promise.race([p, new Promise(r => setTimeout(r, ms))]);
    let steps = 0;
    while (currentY < Math.min(maxScroll, maxHeight) && steps++ < maxSteps && left() > 0) { /* ... */ }
    await withTimeout(Promise.all(imgs.map(i => i.decode?.().catch(() => {}))), Math.min(left(), 5000));
    await withTimeout(document.fonts?.ready ?? Promise.resolve(), 3000);
    window.scrollTo(0, 0);                                   // see H3
    try { window.lenis?.scrollTo?.(0, { immediate: true }); } catch (e) {}
    window.ScrollTrigger?.update?.();
  }
  ```
  Call it as `page.evaluate(HYDRATE_PAGE_JS, {"maxMs": min(self.timeout_ms // 2, 15000)})`.
- **Test:** A browser-marked test with the infinite-feed page (`page.set_content`, no network) asserts the call returns within budget + 2 s.

---

## High

### H1: Collapsed (detached) nodes are re-scanned, giving duplicate patterns

- **Where:** `dom_pruner.py:350` (`for descendant in list(el.iter())`) and `:361`.
- **Problem:** The descendant list is a snapshot taken before collapsing. Nodes removed by an earlier collapse are still visited. Their ancestor walk never reaches `el`, so `is_inside_nested_landmark` returns False, and their inner lists are scanned as patterns.
- **Evidence:** 6 cards, each containing a 3-item `<ul>`, give `FeatureCard ×6` plus **six** `ListItem ×3` patterns. It should be one.
- **Fix:** Fixed by C1 step 3 (no mutation during analysis). The minimal fix is to skip any descendant that is no longer attached under `el`.
- **Test:** Assert exactly one `ListItem` pattern in the scenario above.

### H2: Canvas probe locks untouched canvases to 2D

- **Where:** `asset_inspector.py:52-62`, `feature_detector.py:268-276`.
- **Problem:** Calling `getContext('2d')` on a canvas *creates* a 2D context, and a canvas can only ever hold one context type. The init script (`scrapling_engine.py:162-174`) already records every context the page creates, so `unknown` really means "not initialized yet". Instead of reporting that, the probe:
  1. mislabels the canvas as `2d`;
  2. stops the site from ever starting WebGL on it. The probe runs **before the screenshot**, so lazy 3D scenes render blank.
  3. Its own call also goes through the patched `getContext`, so the later feature probe sees `__sleuth_context_type__ = '2d'`.
- **Evidence:** On a fresh `<canvas>`, the probe reports `2d`. Afterwards, `getContext('webgl2') || getContext('webgl')` returns `null`.
- **Fix:** Never call `getContext` from a probe. Report `"uninitialized"` when the tag is missing. Also patch `transferControlToOffscreen` in the init script so it tags `"offscreen"`. Merge the two canvas probes into one shared helper.
- **Test:** A browser test checks that after the probe, a WebGL context can still be obtained and the probe reports `uninitialized`.

### H3: Page left scrolled to the bottom (false drawers, scrolled screenshot)

- **Where:** The end of `HYDRATE_PAGE_JS` (`scrapling_engine.py:107-110`) never scrolls back up. `feature_detector.py:158-161` uses viewport-relative `rect`.
- **Problem:** After hydration, `scrollY` is the document bottom. `rect.bottom < -100` is then true for **every** element above the viewport. Any absolute or fixed element over 500 px tall with a transform becomes a "Modal/Drawer"; hero backgrounds using `translate(-50%,-50%)` are typical. The full-page screenshot (`:229`) and token sampling also happen in the scrolled state: headers that hide on scroll, "back to top" buttons, scroll-progress UI.
- **Evidence:** Probing a page with a centered hero background image gives `[]` drawers before hydration and `['Modal/Drawer (img.hero-bg)']` after (`scrollY = 5000`).
- **Fix:** Scroll back to the top at the end of hydration (see the C2 snippet) and wait about 2 animation frames. In the detector, use document coordinates (`rect.top + scrollY`) and compare against document bounds for vertical offscreen.
- **Test:** A browser test with the hero page asserts no drawers after hydration and `scrollY == 0`.

### H4: Proxy passwords printed unredacted for scheme-less proxy strings

- **Where:** `proxy_manager.py:23-42`. It is consumed by `cli.py:74,345` (console panel) and `scrapling_engine.py:244` (INFO log).
- **Problem:** Without a scheme, `urlparse` finds no password, so the function returns the input unchanged. Decodo's dashboard exports `host:port:user:pass`, and `user:pass@host:port` is also common, so these are the formats people will put in `proxies.txt`. Scrapling then rejects both (`ValueError: Invalid proxy string!`). The user sees the password in the terminal and logs, and then gets an unhelpful error.
- **Evidence:**
  | Input | `redact_proxy_url` output |
  |---|---|
  | `user:secretpw@isp.decodo.com:10001` | unchanged (leaked) |
  | `isp.decodo.com:10001:user:secretpw` | unchanged (leaked) |
  | `http://u:secretpw@h:10001` | `http://u:***@h:10001` (OK) |
- **Fix:**
  1. Normalize on load. Add `_normalize_proxy()` that accepts `scheme://…`, `user:pass@host:port`, `host:port:user:pass` and `host:port`, and emits `http://user:pass@host:port`. Reject unparseable lines with a clear error that gives the line number, never the content.
  2. Make redaction fail closed. If no scheme or host can be parsed, return `"<proxy redacted>"`. Never echo input that contains `@` or 3 or more `:`.
- **Test:** Parametrized redaction and normalization tests over the formats above. Assert `"secretpw"` never appears in the output.

### H5: Proxy bypass is decided by a URL substring match

- **Where:** `scrapling_engine.py:277`, which is `"127.0.0.1" in self.url or "localhost" in self.url`.
- **Problem:** URLs like `https://shop.example.com/?return=localhost`, `https://localhost.example.com` and `https://127.0.0.1.nip.io/` all **skip the proxy**. The tool then connects directly and exposes the operator's IP even though `--proxy`/`--decodo` was given. Meanwhile real loopback hosts like `[::1]` are not detected.
- **Fix:**
  ```python
  host = (urlparse(self.url).hostname or "").lower()
  try:
      is_loopback = ipaddress.ip_address(host).is_loopback
  except ValueError:
      is_loopback = host == "localhost" or host.endswith(".localhost")
  ```
  Log at INFO when a configured proxy is skipped.
- **Test:** Parametrize over the URLs above and check that `fetch_kwargs` contains the proxy (mock `fetcher_cls.fetch`).

### H6: `--wait-until <css selector>` is awaited after extraction

- **Where:** `scrapling_engine.py:270-273` passes `wait_selector` to scrapling. In scrapling 0.4.15, `_controllers.py:168-180` runs `page_action` **first** and waits for the selector afterwards.
- **Problem:** Token harvest, DOM probes, title and screenshot all happen before the selector appears, so the option does not do what `--help` says.
- **Fix:** Handle the selector at the start of `_page_action`: `page.wait_for_selector(sel, state="visible", timeout=self.timeout_ms)`, catch the timeout and warn. Do not pass `wait_selector` to scrapling.
- **Test:** A local page injects `.late` after 1.5 s and changes the body background at the same time. Assert the harvested background is the post-injection color.

### H7: Same-site URL variants are crawled as separate pages

- **Where:** `crawler.py:130-134` strips `www.` for the same-site check, but `:161` rebuilds the URL with the original scheme and netloc.
- **Problem:** `https://example.com/`, `https://www.example.com/` and `http://example.com/` are distinct `visited` keys. The homepage can be crawled 3 times. Each crawl uses a `max_pages` slot, and all of them write `pages/index/`.
- **Evidence:** `normalize_url("https://www.example.com/", "https://example.com")` gives `https://www.example.com/`. The entry URL itself normalizes to `https://example.com/`.
- **Fix:** After the same-site check, rewrite scheme and netloc to the entry URL's (or key `visited` by `(host_without_www, path, sorted_query)`). Sort query parameters, and treat `:443`/`:80` as the default port (currently they are rejected as external). Add the post-redirect URL (`response.url`) to `visited` as well.
- **Test:** Extend `test_normalize_url` with the www, http, port and query-order variants.

### H8: Slug collisions overwrite per-page output

- **Where:** `crawler.py:97-107` (`slugify_url`), used at `:301,306` (screenshot path) and `exporter.py:778-792` (page directory).
- **Problem:** The query string is ignored, and both `/` and `-` become `-`. So `/shop?cat=a` and `/shop?cat=b` both become `shop`, and `/about-us` and `/about/us` both become `about-us`. The later page overwrites the earlier blueprint and screenshot, and `manifest.json` lists two pages pointing at the same files.
- **Fix:** The crawler assigns a **unique** slug once (a registry that appends `-2`, or a short hash of path + query when they collide). Store it on the page summary and use only that slug for screenshots and export.
- **Test:** Crawl synthesis with colliding URLs produces distinct `blueprint_path` values.

### H9: "Exploration" is advertised and labeled but never performed

- **Where:** `feature_detector.py:495-771`; `README.md:52` ("triggers them open … restores original layout"); the `--explore` help text.
- **Problem:** Nothing in `src/` clicks, hovers or expands anything. `explore` only switches the label between `explored` and `detected` for 3 categories, and the other 7 hardcode `explored`. `konpo-output` shows 161 features marked `explored`. The downstream LLM is told that content was inspected when it wasn't, and `--no-explore` changes nothing but labels.
- **Fix:**
  - **Now:** use `detected` everywhere, and make README and `--help` describe the actual behavior.
  - **Later, as a separate feature:** implement bounded exploration. Expand `<details>`, `[aria-expanded="false"]` and drawer triggers, capture the revealed headings and text, then restore (Escape or re-click). Use a time budget per feature, and set `explored` only on success.
- **Test:** Assert no feature reports `explored` unless an exploration routine ran.

### H10: Feature lists are not deduplicated or capped, which bloats output

- **Where:** `feature_detector.py:217-260` (disclosures and marquees: no outermost-only filter, and marquees have no dedupe at all) and `:152-214` (drawers).
- **Problem:** `[class*="marquee"]` matches the container, its track and every item. `[class*="accordion"]` likewise matches every part of each accordion item. `konpo-output/blueprint.json` is **204 KB** and contains **124** `marquee_stream` entries and 19 drawers. That defeats the "ultra-dense, token-optimized" goal and floods the Markdown prompt.
- **Fix:** Keep only the outermost match (`if (el.parentElement?.closest(sel)) continue`). Group by selector with a `count`. Cap each category (for example 5, plus a "+N more" summary). Apply the same caps in site-level synthesis (`crawler.py:758-776`).
- **Test:** A mocked probe with 50 nested marquee nodes gives at most the cap. Add a size budget to the e2e test (for example `blueprint.json` under 30 KB).

---

## Medium

### M1: One failing page-action step skips everything after it

- **Where:** `scrapling_engine.py:192-234`.
- **Problem:** All steps share one `try`. If `detect_and_explore_features` raises, the title, meta and screenshot are skipped, yet the run "succeeds" with a hollow blueprint. It can raise from direct indexing (`track["name"]`, `feature_detector.py:538-541`), or when pydantic rejects NaN in `dimensions: dict[str, int]` (`models.py:230`); a ScrollTrigger with an uncomputed `end` would produce one.
- **Fix:** Give each step its own try. Parse probe entries defensively (`.get`, skip malformed entries, coerce non-finite numbers). Add `warnings: list[str]` to `SiteMetadata` so step failures show up in the output.

### M2: Scrapling retries reuse per-attempt state

- **Where:** `scrapling_engine.py:151-152, 158`; scrapling `retries` defaults to 3 (`_validators.py:90`).
- **Problem:** Each retry calls `_page_setup` on a new page, but `self.collector` and `self._action_data` persist. Assets from the failed attempt are mixed in, and a partial later attempt can keep stale title or tokens.
- **Fix:** Reset `self.collector = NetworkAssetCollector()` and clear `self._action_data` at the top of `_page_setup`.

### M3: Proxy resolution is implicit and fails open

- **Where:** `proxy_manager.py:168-218`. Also `scrapling_engine.py:137` resolves env proxies when none is passed.
- **Problem:**
  - `DECODO_PROXY_URL` is used **without** `--decodo`, which makes the flag a no-op.
  - `PROXY_FILE`, `./proxies.txt` and `HTTP(S)_PROXY` are used silently, which can consume a paid pool unexpectedly.
  - If `DECODO_PORT_START` is greater than `DECODO_PORT_END`, `generate_decodo_pool` returns `[]` (reproduced). The result is `ProxyConfig()` and a **direct** connection, even though `--decodo` was requested.
  - `--decodo` without credentials also goes direct silently.
- **Fix:** Gate all `DECODO_*` settings behind the flag. Exit with an error when `--decodo` cannot produce a usable config or the pool is empty. Add `--no-proxy`. Log the chosen source (redacted).

### M4: `real_chrome` auto-detection is wrong, and the e2e test depends on Chrome

- **Where:** `scrapling_engine.py:46-48`; scrapling `_base.py:506` (`channel="chrome"`); `tests/test_e2e.py:134`.
- **Problem:** A `chromium` binary on PATH turns on `real_chrome`, but Playwright's `channel="chrome"` requires **Google Chrome**, so Chromium-only systems fail to launch. On macOS, Chrome is not on PATH, so it is never detected. The e2e test hardcodes `real_chrome=True` and will fail in CI without Chrome.
- **Fix:** Detect Google Chrome specifically (`google-chrome`, `google-chrome-stable`, and the macOS and Windows install paths), or default to `False`. In the e2e test, use the bundled Chromium and a `browser` marker.

### M5: CSS custom properties are missed

- **Where:** `token_extractor.py:213-244`.
- **Problem:** Only top-level style rules whose `selectorText` is exactly `:root` or `html` are read. This misses:
  - `:root, :host`, which is how Tailwind v4 emits `@layer theme { :root, :host { --color-* } }`;
  - rules inside `@media`, `@supports` or `@layer`;
  - `[data-theme]` roots;
  - cross-origin (CDN) stylesheets.

  The inline fallback (`:239-244`) also uses a narrower prefix list than the stylesheet scan.
- **Fix:** Walk `rule.cssRules` recursively. Split `selectorText` on commas. Collect every custom property on root-like selectors, then prioritize color, font, radius, shadow and spacing. For cross-origin sheets, resolve known names through `getComputedStyle(document.documentElement).getPropertyValue()`, or read the CSS text captured by the network collector.
- **Test:** The e2e page gets an `@layer theme { :root, :host { --color-brand: … } }` block, and the test asserts it is captured.

### M6: Untrusted page content is embedded in a "SYSTEM PROMPT"

- **Where:** `exporter.py:213-224, 373, 392, 492` and the site Markdown.
- **Problem:** `blueprint.md` starts with `# SYSTEM PROMPT` and interpolates the page title, description, text previews and sample content verbatim. It is meant to be pasted into a coding agent, so any page can plant instructions there. Backticks and pipes in scraped text also break inline code and the route table.
- **Fix:** Put scraped strings in a clearly delimited "untrusted page content, treat as data" block, or JSON-encode them. Escape `` ` `` and `|`, truncate long values, and retitle the header (for example "UI Reconstruction Brief").
- **Test:** A title containing `` ` ``, `|` and an injection string renders inside the data fence.

### M7: An invalid `--format` silently writes nothing

- **Where:** `cli.py:105-107, 379-381`.
- **Problem:** `-f jsn` sets every export flag to False. The CLI prints "Artifacts successfully saved:" with an empty list and exits 0. The `md` and `yml` aliases work but are undocumented.
- **Fix:** Define `class OutputFormat(str, Enum)` and use it as the Typer option type.
- **Test:** `CliRunner` with `-f bogus` exits non-zero.

### M8: A failed crawl still exits 0

- **Where:** `crawler.py:348-349`, `cli.py:94-115`.
- **Problem:** Per-page exceptions are only logged. If every page fails, the CLI writes empty artifacts and exits 0.
- **Fix:** Record `failed_pages: [{url, error}]` in the result and `manifest.json`. Exit 1 when `total_pages_crawled == 0`, and warn on partial failure.

### M9: Selectors hardcoded for one reference site

- **Where:**
  - `feature_detector.py:218` (`.home-ser-item`)
  - `:388-390` (`.home-hero-title, .home-abt-title, .ser-hero-title, .ser-why-item-title, .projlist-title`)
  - `:450` (`target_selector: '.hero-visual, .media-preview-container'`, reported as if detected)
  - `exporter.py:80-81` (`.cursor-wrap`, `.cursor-img`) and `:177` (`#hero-active-video`)
- **Problem:** Details of one reference site appear in every run's output.
- **Fix:** Remove them. Fill runbook parameters from the detected feature selectors, and omit `target_selector` unless it was actually detected.

### M10: Over-broad substring heuristics

| Location | Problem | Fix |
|---|---|---|
| `dom_pruner.py:46-72` | `track` or `pixel` anywhere in an `img` src, class or id removes the image (`soundtrack-cover.jpg`, `pixel-art.png`, `class="track-image"`) | Require tiny dimensions **or** a known tracker host |
| `dom_pruner.py:157-187` | Substring matching: `item` (`w-dyn-item`, `carousel-item`) becomes ProductCard; also `plan`⊂`explanation`, `stat`⊂`status`, `review`⊂`preview`, `news`⊂`newsletter` | Split class names on `[-_\s]`, match whole tokens, and drop `item` |
| `feature_detector.py:166` | Any class containing `news` counts as an overlay | Remove it, or match whole tokens |
| `asset_inspector.py:19` | Any `.bin` response counts as a 3D model | Count `.bin` only when a `.gltf` was also loaded |

### M11: Rive files with query strings are missed

- **Where:** `asset_inspector.py:249`.
- **Problem:** `u.endswith(".riv")` misses URLs like `scene.riv?v=3`, even though `PATTERN_MOTION` (`:20`) accepts query strings. Those files then get the Lottie wrapper suggestion.
- **Fix:** Use `urlparse(u).path.lower().endswith(".riv")`.

### M12: A browser is launched for every crawled page

- **Where:** `crawler.py:317-331` leads to `scrapling_engine.py:284` (`fetcher_cls.fetch`, which opens and closes a session each time).
- **Fix:** Open one `DynamicSession`/`StealthySession` for the whole crawl and call `session.fetch(url, page_setup=…, page_action=…)` per page; 0.4.15 supports these as per-request params. Also replace the sort-then-`pop(0)` queue (`:294-295`) with `heapq`.

---

## Low

| ID | Where | Issue | Fix |
|---|---|---|---|
| L1 | `asset_inspector.py:126,139` | `all_responses` stores every network response and is never read, so memory grows without bound | Remove it, or cap it |
| L2 | `asset_inspector.py:207-217` | `has_three = … or not has_spline` makes `if not engines` unreachable and labels every unknown 3D asset as Three.js | Make `has_three` depend only on evidence |
| L3 | `crawler.py:786-798` | The dedupe key includes all URLs, so the "union URLs" branch never adds anything, and URL-less canvases from different pages collapse into one | Key by category + primary URL, or by category + page + index for canvases |
| L4 | `crawler.py:629, 668` | `list(set)[:5]` gives output order that changes between runs (string hash randomization), causing noisy diffs | Use `sorted(...)` or `dict.fromkeys` |
| L5 | `pyproject.toml`, `__init__.py:45`, `cli.py:336, 586` | The version string is duplicated in 4 places | Use `importlib.metadata.version("ui-sleuth")` |
| L6 | `cli.py:20`, `proxy_manager.py:15`, `__init__.py:7` | `load_dotenv()` runs at import time (twice), and `import ui_sleuth` imports the CLI, so importing the library changes `os.environ`. `find_dotenv()` also searches up from the package file, not the working directory, so an installed tool ignores the user's `./.env` | Call `load_dotenv(find_dotenv(usecwd=True))` once in a Typer callback; import the CLI lazily |
| L7 | `token_extractor.py:45, 57-60` | Alpha of 0.1 or more is flattened to opaque hex (`rgba(0,0,0,.5)` becomes `#000000`), and black written in modern syntax (`oklch(0 0 0)`) is dropped because it equals the `#000000` sentinel | Keep alpha (`#rrggbbaa`); use two different sentinels |
| L8 | `feature_detector.py:82-84, 315, 433, 470` | Framer site-builder attributes are labeled "Framer Motion", and `[data-magnetic]`/`[data-video]`/`[data-move]` is appended to selectors even when the element matched some other way, producing selectors that match nothing | Fix the label; build selectors from the attribute that actually matched |
| L9 | `pyproject.toml [tool.pyright]` | Running `pyright` outside `uv run` reports 34 bogus import errors | Add `venvPath = "."` and `venv = ".venv"` |
| L10 | `crawler.py:131-161` | Query parameter order is not normalized | Covered by the H7 fix |
| L11 | git history | The Decodo **username** appears in `4f3b648` (removed in `b09867e`). The password was never committed, and `.env` is gitignored | Optional: rotate the username, or accept the risk |
| L12 | `exporter.py:735-738`, `cli.py:179` | In crawl mode, `blueprint.json` holds a *different schema* (SiteDesignSystem) than in single-page mode. The default `./output` is shared across sites and runs, and `-f json` leaves stale `.md`/`.yaml` files from earlier runs | Default to `output/<host>/`; drop or rename the alias; warn about or clean stale artifacts |

---

## Implementation plan

Order: data loss and hangs first, then security, then correctness, then quality. Each phase is a separate PR with its regression tests.

1. **Phase 1: extraction correctness (C1, H1, C2, H3, H2)**
   - Rework `dom_pruner` to analyze without mutating, exclude landmarks from patterns, and use a stronger signature.
   - Bound the hydration script and restore scroll at the end.
   - Make the canvas probe non-destructive and merge the two probes into one helper.
2. **Phase 2: proxy and prompt safety (H4, H5, M3, M6)**
   - Normalize proxy strings and make redaction fail closed.
   - Detect loopback by hostname.
   - Require an explicit opt-in for proxies and fail loudly when misconfigured.
   - Fence untrusted content in the Markdown output.
3. **Phase 3: engine robustness and CLI (H6, M1, M2, M4, M7, M8)**
   - Wait for the selector inside `_page_action`.
   - Give each step its own error handling and surface warnings in the output.
   - Reset state per attempt; fix Chrome detection.
   - Make `--format` an Enum; exit non-zero on failed crawls.
4. **Phase 4: crawler (H7, H8, M12, L3, L4, L10)**
   - Canonicalize URLs and assign unique slugs.
   - Reuse one browser session for the crawl; use a heap for the queue; make dedupe deterministic.
5. **Phase 5: output honesty and density (H9, H10, M5, M9, M10, M11, L7, L8)**
   - Report honest exploration status; dedupe and cap features.
   - Fix custom-property harvesting; remove site-specific selectors; tighten the heuristics.
6. **Phase 6: cleanup (L1, L2, L5, L6, L9, L11, L12)**

## Test and CI plan

- **Pure-Python regression tests**, built from the reproductions above: C1, H1, H4, H5, H7, H8, M3, M7, M11.
- **A `browser` pytest marker** for tests that use `page.set_content` with bundled Chromium (no network): C2 (infinite feed), H2 (canvas lock), H3 (hero background plus scroll position), H6 (late selector), M5 (`@layer` variables). Skip them when Playwright browsers are not installed.
- **Output budget checks** in e2e: a maximum `blueprint.json` size and a maximum number of features per category.
- **CI (GitHub Actions):**
  - Main job: `uv sync`, `ruff check`, `ruff format --check`, `pyright`, `pytest -m "not browser"`.
  - Second job: `playwright install chromium`, then `pytest -m browser`.
