"""Semantic DOM pruning and repetitive pattern recognition engine."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

from lxml import html
from lxml.html import HtmlElement

from ui_sleuth.models import (
    InteractionMetadata,
    PatternSchema,
    SectionBlueprint,
)

logger = logging.getLogger(__name__)

# Atomic utility classes (Tailwind-style spacing/colour/dimension) that carry no semantics
_UTILITY_CLASS_RE = re.compile(
    r"^(p|m|px|py|pt|pb|pl|pr|w|h|max-w|min-w|text|bg|border|rounded|gap|space)-"
)

# Elements to completely strip from DOM tree
STRIP_TAGS: set[str] = {
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",  # Inspected separately by asset_inspector
    "embed",
    "object",
    "param",
    "track",
}

# Major landmark tags
LANDMARK_TAGS: list[str] = [
    "header",
    "nav",
    "main",
    "section",
    "article",
    "aside",
    "footer",
]

# Landmarks that are never pattern instances (always subsections); <article> may repeat as cards
_NON_REPEATABLE_LANDMARKS: frozenset[str] = frozenset(LANDMARK_TAGS) - {"article"}

# Known tracker hosts (matched as host suffix) and host+path prefixes
_TRACKER_HOSTS: tuple[str, ...] = (
    "doubleclick.net",
    "google-analytics.com",
    "googletagmanager.com",
    "googleadservices.com",
    "googlesyndication.com",
    "bat.bing.com",
    "clarity.ms",
    "hotjar.com",
    "hotjar.io",
    "analytics.twitter.com",
    "px.ads.linkedin.com",
    "ads.linkedin.com",
    "snap.licdn.com",
    "segment.io",
    "mixpanel.com",
    "scorecardresearch.com",
    "quantserve.com",
    "adsrvr.org",
)
_TRACKER_HOST_PATHS: tuple[tuple[str, str], ...] = (("facebook.com", "/tr"),)

_TINY_PX_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$")
_STYLE_DIM_RE = re.compile(r"(?:^|;)\s*(width|height)\s*:\s*([^;]+)", re.IGNORECASE)
_TINY_MAX_PX = 2.0


def _tiny_px(value: str) -> bool:
    match = _TINY_PX_RE.match(value)
    return bool(match) and float(match.group(1)) <= _TINY_MAX_PX


def _has_tiny_dimensions(el: HtmlElement) -> bool:
    """True when every declared width/height (attrs, else inline style) is <= 2px."""
    style_dims = {k.lower(): v for k, v in _STYLE_DIM_RE.findall(el.get("style", ""))}
    dims = [el.get(name, "").strip() or style_dims.get(name, "") for name in ("width", "height")]
    declared = [d for d in dims if d.strip()]
    return bool(declared) and all(_tiny_px(d) for d in declared)


def _is_tracker_url(src: str) -> bool:
    try:
        parsed = urlsplit(src.strip() if src.startswith(("http", "//")) else "")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if any(host == h or host.endswith("." + h) for h in _TRACKER_HOSTS):
        return True
    path = parsed.path.lower()
    return any(
        (host == h or host.endswith("." + h)) and (path == p or path.startswith(p + "/"))
        for h, p in _TRACKER_HOST_PATHS
    )


def is_tracking_pixel(el: HtmlElement) -> bool:
    """Check if an image is a tiny (<=2px) tracking pixel or served from a known tracker host.

    Names like ``soundtrack-cover.jpg`` or ``class="track-image"`` are deliberately ignored.
    """
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    if tag != "img":
        return False
    return _has_tiny_dimensions(el) or _is_tracker_url(el.get("src", ""))


def clean_dom_tree(root: HtmlElement) -> None:
    """Strip unwanted tags and tracking elements in-place from lxml tree."""
    # Strip forbidden tags
    for tag_name in STRIP_TAGS:
        for node in list(root.iter(tag_name)):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    # Strip comments
    for comment in list(root.xpath("//comment()")):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)

    # Strip tracking pixels
    for img in list(root.iter("img")):
        if is_tracking_pixel(img):
            parent = img.getparent()
            if parent is not None:
                parent.remove(img)


def extract_concise_preview(el: HtmlElement, max_words: int = 25) -> str:
    """Extract a concise text preview (15-25 words) capturing copy intent."""
    raw_text = el.text_content()
    words = [w for w in re.split(r"\s+", raw_text.strip()) if w]
    if not words:
        return ""
    preview_words = words[:max_words]
    preview = " ".join(preview_words)
    if len(words) > max_words:
        preview += "..."
    return preview


def extract_interaction_metadata(el: HtmlElement) -> InteractionMetadata:
    """Scan an element for forms, inputs, and primary CTA buttons."""
    forms = list(el.iter("form"))
    inputs = [
        inp
        for inp in el.iter("input")
        if inp.get("type", "text").lower() not in {"hidden", "submit", "button"}
    ]
    textareas = list(el.iter("textarea"))
    selects = list(el.iter("select"))

    action_buttons: list[HtmlElement] = []
    # Real buttons and submit inputs
    for btn in el.iter("button"):
        action_buttons.append(btn)
    for inp in el.iter("input"):
        if inp.get("type", "").lower() in {"submit", "button"}:
            action_buttons.append(inp)

    # Primary action anchors
    for a in el.iter("a"):
        cls = a.get("class", "").lower()
        role = a.get("role", "").lower()
        if role == "button" or any(k in cls for k in ["btn", "button", "cta", "action"]):
            action_buttons.append(a)

    labels: list[str] = []
    for btn in action_buttons:
        txt = ""
        if btn.tag.lower() == "input":
            txt = btn.get("value", "").strip()
        else:
            txt = re.sub(r"\s+", " ", btn.text_content()).strip()
        if txt and len(txt) <= 40 and txt not in labels:
            labels.append(txt)

    return InteractionMetadata(
        inputs_count=len(inputs),
        forms_count=len(forms),
        textareas_count=len(textareas),
        selects_count=len(selects),
        primary_actions_count=len(action_buttons),
        primary_action_labels=labels[:6],
    )


def infer_pattern_name(el: HtmlElement, tag_signature: str) -> str:
    """Infer a high-level component pattern name from classes, attributes, or tag signature."""
    tokens = {
        t for t in re.split(r"[-_\s]+", (el.get("class", "") + " " + el.get("id", "")).lower()) if t
    }

    def has(*words: str) -> bool:
        return any(w in tokens for w in words)

    def has_pair(a: str, b: str) -> bool:
        return a in tokens and b in tokens

    if has("testimonial", "review", "quote"):
        return "TestimonialCard"
    if has("pricing", "plan", "tier", "subscription"):
        return "PricingPlanCard"
    if has_pair("menu", "card") or has_pair("nav", "card") or has_pair("sm", "menu"):
        return "NavMenuCard"
    if has_pair("menu", "item") or has_pair("nav", "item") or has_pair("toc", "item"):
        return "NavMenuItem"
    if has("product", "catalog", "shop"):
        return "ProductCard"
    if has("feature", "benefit", "service", "capability"):
        return "FeatureCard"
    if has("team", "member", "author", "person"):
        return "TeamMemberCard"
    if has("post", "blog", "article", "news"):
        return "ArticleCard"
    if has("faq", "accordion", "question"):
        return "FaqItem"
    if has("metric", "stat", "counter"):
        return "MetricCard"
    if el.tag.lower() == "li":
        return "ListItem"

    # Fallback to tag signature hint
    if "h3" in tag_signature or "h4" in tag_signature:
        return "FeatureCard"
    return "ContentCard"


def extract_sample_content(el: HtmlElement) -> dict[str, Any]:
    """Extract sample content fields from a representative card instance."""
    sample: dict[str, Any] = {}

    # Heading / title
    headings = el.xpath(
        ".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6 | .//strong"
        " | .//*[contains(@class, 'heading') or contains(@class, 'title') or contains(@class, 'menu-link-item')]"
    )
    if headings and isinstance(headings, list):
        title_el = headings[0]
        if isinstance(title_el, HtmlElement):
            title_text = re.sub(r"\s+", " ", title_el.text_content()).strip()
            if title_text:
                sample["title"] = title_text[:60]

    # Description / snippet
    paragraphs = el.xpath(".//p | .//span | .//*[contains(@class, 'txt')]")
    if paragraphs and isinstance(paragraphs, list):
        for p in paragraphs:
            if isinstance(p, HtmlElement):
                p_text = re.sub(r"\s+", " ", p.text_content()).strip()
                if p_text and len(p_text) > 10 and p_text != sample.get("title"):
                    words = p_text.split()
                    sample["description"] = " ".join(words[:15]) + (
                        "..." if len(words) > 15 else ""
                    )
                    break

    # Action / CTA
    actions = el.xpath(".//button | .//a")
    if actions and isinstance(actions, list):
        action_el = actions[0]
        if isinstance(action_el, HtmlElement):
            action_text = re.sub(r"\s+", " ", action_el.text_content()).strip()
            if action_text and len(action_text) <= 30:
                sample["action"] = action_text
    elif el.tag.lower() in {"a", "button"}:
        self_text = re.sub(r"\s+", " ", el.text_content()).strip()
        if self_text and len(self_text) <= 40:
            sample["action"] = self_text

    # Image
    images = el.xpath(".//img")
    if images and isinstance(images, list):
        img_el = images[0]
        if isinstance(img_el, HtmlElement):
            sample["image"] = img_el.get("alt", "") or img_el.get("src", "")

    return sample


def get_element_signature(el: HtmlElement) -> str:
    """Generate a structural signature: the tag plus a depth-2 shape of its children."""
    parts: list[str] = []
    for c in el:
        if not isinstance(c.tag, str):
            continue
        grandchildren = [g.tag.lower() for g in c if isinstance(g.tag, str)]
        parts.append(f"{c.tag.lower()}({','.join(grandchildren)})")
    return f"{el.tag.lower()}:[{','.join(parts)}]"


def _class_tokens(el: HtmlElement) -> list[str]:
    """Normalized non-utility class tokens of an element, in document order."""
    seen: dict[str, None] = {}
    for t in (el.get("class") or "").lower().split():
        if not _UTILITY_CLASS_RE.match(t):
            seen.setdefault(t)
    return list(seen)


def _contains_landmark(el: HtmlElement) -> bool:
    return any(
        isinstance(d.tag, str) and d.tag.lower() in _NON_REPEATABLE_LANDMARKS
        for d in el.iterdescendants()
    )


def _split_by_shared_class(group: list[HtmlElement]) -> list[list[HtmlElement]]:
    """Split same-shape siblings into clusters that share at least one class token.

    Classless ``<li>`` elements are inherently repeated items and cluster together.
    """
    tokens = {id(n): _class_tokens(n) for n in group}
    counts: dict[str, int] = {}
    for n in group:
        for t in tokens[id(n)]:
            counts[t] = counts.get(t, 0) + 1

    clusters: list[list[HtmlElement]] = []
    assigned: set[int] = set()
    for token in sorted(counts, key=lambda t: -counts[t]):
        members = [n for n in group if id(n) not in assigned and token in tokens[id(n)]]
        if len(members) >= 3:
            clusters.append(members)
            assigned.update(id(n) for n in members)

    classless_li = [n for n in group if not tokens[id(n)] and n.tag.lower() == "li"]
    if len(classless_li) >= 3:
        clusters.append(classless_li)
    return clusters


def analyze_patterns(
    container: HtmlElement,
) -> tuple[list[PatternSchema], list[HtmlElement]]:
    """Detect repetitive sibling patterns (>= 3 instances) without mutating the tree.

    Returns:
        (detected_patterns, redundant_nodes_to_drop)
    """
    children = [
        c
        for c in container
        if isinstance(c.tag, str) and c.tag.lower() not in _NON_REPEATABLE_LANDMARKS
    ]
    if len(children) < 3:
        return [], []

    signature_groups: dict[str, list[HtmlElement]] = {}
    for c in children:
        signature_groups.setdefault(get_element_signature(c), []).append(c)

    detected: list[PatternSchema] = []
    to_drop: list[HtmlElement] = []

    for sig, group in signature_groups.items():
        if len(group) < 3:
            continue
        for cluster in _split_by_shared_class(group):
            if any(_contains_landmark(n) for n in cluster):
                continue
            first = cluster[0]
            detected.append(
                PatternSchema(
                    pattern=infer_pattern_name(first, sig),
                    instance_count=len(cluster),
                    sample_content=extract_sample_content(first),
                )
            )
            to_drop.extend(cluster[1:])

    return detected, to_drop


def drop_nodes(nodes: list[HtmlElement]) -> None:
    """Remove nodes from their parents (no-op for already detached nodes)."""
    for node in nodes:
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)


def detect_and_collapse_patterns(
    container: HtmlElement,
) -> tuple[list[PatternSchema], list[HtmlElement]]:
    """Scan container children for repetitive sibling patterns (>= 3 instances).

    Redundant instances are removed from the tree after analysis completes.

    Returns:
        (detected_patterns, remaining_uncollapsed_children)
    """
    detected, to_drop = analyze_patterns(container)
    dropped = set(to_drop)
    remaining = [c for c in container if isinstance(c.tag, str) and c not in dropped]
    drop_nodes(to_drop)
    return detected, remaining


def infer_layout_hint(el: HtmlElement) -> str | None:
    """Infer layout characteristics from CSS classes or styles."""
    cls_str = el.get("class", "").lower()
    style_str = el.get("style", "").lower()

    if "grid-cols-" in cls_str:
        match = re.search(r"grid-cols-(\d+)", cls_str)
        if match:
            return f"grid({match.group(1)} cols)"
        return "grid"
    if "grid" in cls_str or "display: grid" in style_str:
        return "grid"
    if "flex-col" in cls_str or "flex-direction: column" in style_str:
        return "flex-col"
    if "flex" in cls_str or "display: flex" in style_str:
        return "flex-row"
    return None


def clean_classes(cls_str: str | None) -> list[str]:
    """Filter out noisy atomic utility classes and preserve semantic identifiers."""
    if not cls_str:
        return []
    tokens = cls_str.strip().split()
    semantic: list[str] = []
    for t in tokens:
        # Skip pure Tailwind color/spacing/dimension utilities if long
        if _UTILITY_CLASS_RE.match(t):
            continue
        semantic.append(t)
    return semantic[:5]


def is_inside_nested_landmark(descendant: HtmlElement, root: HtmlElement) -> bool:
    """Check if descendant is inside another landmark between root and descendant."""
    curr = descendant.getparent()
    while curr is not None and curr is not root:
        curr_tag = curr.tag.lower() if isinstance(curr.tag, str) else ""
        if curr_tag in LANDMARK_TAGS:
            return True
        curr = curr.getparent()
    return False


def find_all_patterns_in_element(
    el: HtmlElement,
) -> tuple[list[PatternSchema], str | None]:
    """Scan element and its descendant containers for repetitive patterns and layout hints."""
    all_patterns: list[PatternSchema] = []
    inferred_layout: str | None = infer_layout_hint(el)

    # Analysis runs on the unmodified tree; removals are applied after traversal.
    to_drop: list[HtmlElement] = []
    dropped: set[HtmlElement] = set()

    def is_under_dropped(node: HtmlElement) -> bool:
        curr = node
        while curr is not None and curr is not el:
            if curr in dropped:
                return True
            curr = curr.getparent()
        return False

    # 1. Direct children pattern check
    direct_patterns, direct_drop = analyze_patterns(el)
    all_patterns.extend(direct_patterns)
    to_drop.extend(direct_drop)
    dropped.update(direct_drop)

    # 2. Descendant containers (div, ul, ol) that are not inside nested landmarks
    for descendant in el.iterdescendants():
        tag = descendant.tag.lower() if isinstance(descendant.tag, str) else ""
        if tag in LANDMARK_TAGS:
            continue
        if is_inside_nested_landmark(descendant, el):
            continue
        if is_under_dropped(descendant):
            continue
        if tag in ["div", "ul", "ol"]:
            if inferred_layout is None:
                inferred_layout = infer_layout_hint(descendant)
            desc_patterns, desc_drop = analyze_patterns(descendant)
            all_patterns.extend(desc_patterns)
            to_drop.extend(desc_drop)
            dropped.update(desc_drop)

    drop_nodes(to_drop)

    return all_patterns, inferred_layout


def parse_landmark_node(el: HtmlElement, landmark_role: str) -> SectionBlueprint:
    """Parse a single landmark DOM element into a SectionBlueprint."""
    interaction = extract_interaction_metadata(el)
    text_preview = extract_concise_preview(el)
    patterns, layout = find_all_patterns_in_element(el)

    subsections: list[SectionBlueprint] = []
    # Discover direct sub-landmarks within this element in document order
    for candidate in list(el.iter()):
        if candidate is el or not isinstance(candidate.tag, str):
            continue
        c_tag = candidate.tag.lower()
        if c_tag in LANDMARK_TAGS:
            # Check if there is an intermediate landmark ancestor between candidate and el
            p = candidate.getparent()
            has_intermediate = False
            while p is not None and p is not el:
                p_tag = p.tag.lower() if isinstance(p.tag, str) else ""
                if p_tag in LANDMARK_TAGS:
                    has_intermediate = True
                    break
                p = p.getparent()
            if not has_intermediate:
                subsections.append(parse_landmark_node(candidate, c_tag))

    return SectionBlueprint(
        landmark=landmark_role,
        tag_name=el.tag.lower() if isinstance(el.tag, str) else landmark_role,
        id=el.get("id"),
        classes=clean_classes(el.get("class")),
        layout_hint=layout,
        text_preview=text_preview,
        interaction=interaction,
        detected_patterns=patterns,
        subsections=subsections,
    )


def prune_and_map_dom(html_content: str) -> list[SectionBlueprint]:
    """Parse rendered HTML, clean non-semantic clutter, and extract structured landmarks."""
    if not html_content or not html_content.strip():
        return []

    try:
        root = html.fromstring(html_content)
    except Exception as e:
        logger.warning("Failed to parse HTML string with lxml: %s", e)
        return []

    clean_dom_tree(root)

    landmarks: list[SectionBlueprint] = []
    visited_nodes: set[HtmlElement] = set()

    # Search for standard landmark tags in exact document order
    xpath_selector = ".//*[" + " or ".join(f"self::{t}" for t in LANDMARK_TAGS) + "]"
    candidate_nodes = root.xpath(xpath_selector)
    if isinstance(candidate_nodes, list):
        for node in candidate_nodes:
            if not isinstance(node, HtmlElement) or node in visited_nodes:
                continue

            # Skip nodes collapsed (detached) by an earlier landmark's pattern detection
            if not any(a is root for a in node.iterancestors()):
                continue

            # Don't add nested landmarks at top level if already inside a parent landmark
            parent = node.getparent()
            inside_landmark = False
            while parent is not None:
                p_tag = parent.tag.lower() if isinstance(parent.tag, str) else ""
                if p_tag in LANDMARK_TAGS:
                    inside_landmark = True
                    break
                parent = parent.getparent()

            if not inside_landmark:
                visited_nodes.add(node)
                tag_name = node.tag.lower() if isinstance(node.tag, str) else "section"
                landmarks.append(parse_landmark_node(node, tag_name))

    # Supplement missing header / nav landmarks if site uses div.header or div.nav (e.g. Webflow)
    existing_roles = {lm.landmark for lm in landmarks}
    supplemental_header_nav: list[SectionBlueprint] = []
    if "header" not in existing_roles:
        header_divs = root.xpath(
            ".//div[@id='header' or @role='banner' or contains(concat(' ', normalize-space(@class), ' '), ' header ') or contains(concat(' ', normalize-space(@class), ' '), ' header-top ')]"
        )
        if isinstance(header_divs, list):
            for h_div in header_divs:
                if isinstance(h_div, HtmlElement) and h_div not in visited_nodes:
                    # Skip if nested inside another header candidate already visited
                    if any(p in visited_nodes for p in h_div.iterancestors()):
                        continue
                    visited_nodes.add(h_div)
                    supplemental_header_nav.append(parse_landmark_node(h_div, "header"))
                    break

    if "nav" not in existing_roles:
        nav_divs = root.xpath(
            ".//div[@id='nav' or @role='navigation' or contains(concat(' ', normalize-space(@class), ' '), ' nav ')]"
        )
        if isinstance(nav_divs, list):
            for n_div in nav_divs:
                if isinstance(n_div, HtmlElement) and n_div not in visited_nodes:
                    if any(p in visited_nodes for p in n_div.iterancestors()):
                        continue
                    visited_nodes.add(n_div)
                    supplemental_header_nav.append(parse_landmark_node(n_div, "nav"))
                    break

    if supplemental_header_nav:
        landmarks = supplemental_header_nav + landmarks

    # If no standard HTML5 landmarks were found (e.g. older legacy site), look for divs
    if not landmarks:
        candidates = root.xpath(
            ".//div[@id='header' or @id='main' or @id='footer' or @id='nav' or @role='banner' or @role='main' or @role='contentinfo']"
        )
        if isinstance(candidates, list):
            for cand in candidates:
                if isinstance(cand, HtmlElement) and cand not in visited_nodes:
                    visited_nodes.add(cand)
                    cand_id = cand.get("id", "") or cand.get("role", "section")
                    landmarks.append(parse_landmark_node(cand, cand_id))

    # If still empty, fall back to parsing body
    if not landmarks:
        body = root.find(".//body")
        if body is not None:
            landmarks.append(parse_landmark_node(body, "main"))

    return landmarks
