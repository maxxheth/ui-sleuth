"""Semantic DOM pruning and repetitive pattern recognition engine."""

from __future__ import annotations

import logging
import re
from typing import Any

from lxml import html
from lxml.html import HtmlElement

from ui_sleuth.models import (
    InteractionMetadata,
    PatternSchema,
    SectionBlueprint,
)

logger = logging.getLogger(__name__)

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

# Tracking keywords in class, id, or src
TRACKING_PATTERN = re.compile(
    r"(pixel|beacon|analytics|telemetry|track|doubleclick|clarity|hotjar|facebook\.com/tr)",
    re.IGNORECASE,
)


def is_tracking_pixel(el: HtmlElement) -> bool:
    """Check if an image or element is a 1x1 tracking pixel or analytics beacon."""
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    if tag != "img":
        return False

    width = el.get("width", "").strip()
    height = el.get("height", "").strip()
    if (width in {"0", "1"}) and (height in {"0", "1"}):
        return True

    style = el.get("style", "").lower()
    if "display: none" in style or "display:none" in style or "visibility: hidden" in style:
        return True

    src = el.get("src", "")
    if src and TRACKING_PATTERN.search(src):
        return True

    cls_or_id = f"{el.get('class', '')} {el.get('id', '')}"
    return bool(TRACKING_PATTERN.search(cls_or_id))


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
    cls_text = (el.get("class", "") + " " + el.get("id", "")).lower()

    if any(k in cls_text for k in ["testimonial", "review", "quote"]):
        return "TestimonialCard"
    if any(k in cls_text for k in ["pricing", "plan", "tier", "subscription"]):
        return "PricingPlanCard"
    if any(k in cls_text for k in ["product", "item", "catalog", "shop"]):
        return "ProductCard"
    if any(k in cls_text for k in ["feature", "benefit", "service", "capability"]):
        return "FeatureCard"
    if any(k in cls_text for k in ["team", "member", "author", "person"]):
        return "TeamMemberCard"
    if any(k in cls_text for k in ["post", "blog", "article", "news"]):
        return "ArticleCard"
    if any(k in cls_text for k in ["faq", "accordion", "question"]):
        return "FaqItem"
    if any(k in cls_text for k in ["metric", "stat", "counter"]):
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
    headings = el.xpath(".//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6 | .//strong")
    if headings and isinstance(headings, list):
        title_el = headings[0]
        if isinstance(title_el, HtmlElement):
            title_text = re.sub(r"\s+", " ", title_el.text_content()).strip()
            if title_text:
                sample["title"] = title_text[:60]

    # Description / snippet
    paragraphs = el.xpath(".//p | .//span")
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

    # Image
    images = el.xpath(".//img")
    if images and isinstance(images, list):
        img_el = images[0]
        if isinstance(img_el, HtmlElement):
            sample["image"] = img_el.get("alt", "") or img_el.get("src", "")

    return sample


def get_element_signature(el: HtmlElement) -> str:
    """Generate a structural signature string representing an element's direct children."""
    child_tags = [c.tag.lower() for c in el if isinstance(c.tag, str)]
    return f"{el.tag.lower()}:[{','.join(child_tags)}]"


def detect_and_collapse_patterns(
    container: HtmlElement,
) -> tuple[list[PatternSchema], list[HtmlElement]]:
    """Scan container children for repetitive sibling patterns (>= 3 instances).

    Returns:
        (detected_patterns, remaining_uncollapsed_children)
    """
    children = [c for c in container if isinstance(c.tag, str)]
    if len(children) < 3:
        return [], children

    # Group consecutive or overall siblings by structural signature
    signature_groups: dict[str, list[HtmlElement]] = {}
    for c in children:
        sig = get_element_signature(c)
        signature_groups.setdefault(sig, []).append(c)

    detected: list[PatternSchema] = []
    collapsed_elements: set[HtmlElement] = set()

    for sig, group in signature_groups.items():
        if len(group) >= 3:
            first = group[0]
            pattern_name = infer_pattern_name(first, sig)
            sample_content = extract_sample_content(first)
            detected.append(
                PatternSchema(
                    pattern=pattern_name,
                    instance_count=len(group),
                    sample_content=sample_content,
                )
            )
            # Remove redundant sibling instances from DOM tree after capturing sample
            for node in group[1:]:
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            collapsed_elements.update(group)

    remaining = [c for c in container if isinstance(c.tag, str) and c not in collapsed_elements]
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
        if re.match(
            r"^(p|m|px|py|pt|pb|pl|pr|w|h|max-w|min-w|text|bg|border|rounded|gap|space)-", t
        ):
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

    # 1. Direct children pattern check
    direct_patterns, _ = detect_and_collapse_patterns(el)
    all_patterns.extend(direct_patterns)

    # 2. Descendant containers (div, ul, ol) that are not inside nested landmarks
    for descendant in list(el.iter()):
        if descendant is el:
            continue
        tag = descendant.tag.lower() if isinstance(descendant.tag, str) else ""
        if tag in LANDMARK_TAGS:
            continue
        if is_inside_nested_landmark(descendant, el):
            continue
        if tag in ["div", "ul", "ol"]:
            if inferred_layout is None:
                inferred_layout = infer_layout_hint(descendant)
            desc_patterns, _ = detect_and_collapse_patterns(descendant)
            all_patterns.extend(desc_patterns)

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
