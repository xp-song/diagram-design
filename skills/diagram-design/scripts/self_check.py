#!/usr/bin/env python3
"""Self-check a generated diagram HTML file, with no third-party deps.

Ships inside the skill so an installed agent can verify its own output:

    python3 <skill-dir>/scripts/self_check.py my-diagram.html

Checks the accessible-SVG contract, the single-file safety rules (no remote
assets beyond the approved Google Fonts stylesheet, no executable attributes,
no scripts other than the one canonical motion controller), and — when motion
markup is present — the structural motion contract. This is a distilled
subset of the repository gates (`lint-skin.py`, `verify-motion.py`), which
remain the authority for contributions to the repository itself.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

SKILL_DIR = Path(__file__).resolve().parent.parent
MOTION_TEMPLATE = SKILL_DIR / "assets" / "template-motion.html"
MODES = {"none", "reveal", "step", "loop"}
ACTIONS = {"play", "pause", "replay", "prev", "next"}
ASCII_DECIMAL_RE = re.compile(r"^[0-9]+$")
REFERENCE_ATTRS = {
    "src",
    "href",
    "xlink:href",
    "poster",
    "srcset",
    "action",
    "formaction",
    "background",
    "manifest",
}
CSS_VALUE_ATTRS = {
    "style",
    "fill",
    "stroke",
    "filter",
    "clip-path",
    "mask",
    "marker",
    "marker-start",
    "marker-mid",
    "marker-end",
    "cursor",
}
CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\(\s*([^)]+?)\s*\)", re.IGNORECASE)
CSS_IMAGE_SET_RE = re.compile(r"(?:-webkit-)?image-set\s*\(", re.IGNORECASE)
CSS_SRC_RE = re.compile(r"\bsrc\s*\(", re.IGNORECASE)
CSS_IMAGE_RE = re.compile(r"\bimage\s*\(", re.IGNORECASE)
CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.))", re.DOTALL)
SVG_DECLARATIVE_ANIMATION_TAGS = {
    "animate",
    "animatemotion",
    "animatetransform",
    "set",
}
INERT_OR_RAW_ANCESTORS = {
    "template",
    "noscript",
    "noembed",
    "noframes",
    "textarea",
    "title",
    "xmp",
    "plaintext",
}
DISALLOWED_PARSER_MODE_TAGS = {
    "template",
    "noembed",
    "noframes",
    "textarea",
    "xmp",
    "plaintext",
    "select",
    "frameset",
}
MOTION_TREE_CONSTRUCTION_TAGS = {
    "form",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "tr",
    "td",
    "th",
    "caption",
    "colgroup",
}
HTML_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class DiagramParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.roots: list[dict[str, str]] = []
        self.items: list[dict[str, str]] = []
        self.actions: set[str] = set()
        self.controls = 0
        self.statuses: list[dict[str, str]] = []
        self.statuses_in_controls = 0
        self.scripts: list[dict[str, object]] = []
        self.stylesheets: list[dict[str, object]] = []
        self.css_values: list[str] = []
        self.svgs: list[dict[str, object]] = []
        self.unsafe: list[str] = []
        self.references: list[tuple[str, str, str]] = []
        self._svg_depth = 0
        self._current_svg: dict[str, object] | None = None
        self._capture: str | None = None
        self._current_script: dict[str, object] | None = None
        self._current_style: dict[str, object] | None = None
        self._element_stack: list[str] = []
        self._motion_root_depth: int | None = None
        self._controls_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        normalized_attrs = [(key.casefold(), value or "") for key, value in attrs]
        data: dict[str, str] = {}
        for key, value in normalized_attrs:
            if key in data:
                self.unsafe.append(f"duplicate attribute {key} on <{tag}> is not allowed")
            else:
                data[key] = value
        if tag in {"base", "embed", "object", "iframe"}:
            self.unsafe.append(f"<{tag}> is not allowed in a diagram file")
        if tag in DISALLOWED_PARSER_MODE_TAGS:
            self.unsafe.append(f"HTML parser-mode element <{tag}> is not allowed")
        if self._motion_root_depth is not None and tag in MOTION_TREE_CONSTRUCTION_TAGS:
            self.unsafe.append(
                f"HTML tree-construction element <{tag}> is not allowed inside a motion root"
            )
        if tag in SVG_DECLARATIVE_ANIMATION_TAGS:
            self.unsafe.append(f"SVG declarative animation <{tag}> is not allowed")
        for key, value in normalized_attrs:
            if key.startswith("on"):
                self.unsafe.append(f"executable attribute {key} on <{tag}>")
            if key == "srcdoc":
                self.unsafe.append(f"srcdoc attribute on <{tag}>")
            if key == "srcset":
                self.unsafe.append(f"srcset attribute on <{tag}> is not allowed")
            if key == "imagesrcset":
                self.unsafe.append(f"imagesrcset attribute on <{tag}> is not allowed")
            if key == "ping":
                self.unsafe.append(f"ping attribute on <{tag}> is not allowed")
            if key == "attributionsrc":
                self.unsafe.append(f"attributionsrc attribute on <{tag}> is not allowed")
            if key == "xml:base":
                self.unsafe.append(f"xml:base attribute on <{tag}> is not allowed")
            if key in REFERENCE_ATTRS:
                self.references.append((tag, data.get("rel", ""), value))
            if key in CSS_VALUE_ATTRS:
                self.css_values.append(value)
        inactive_context = any(
            ancestor in INERT_OR_RAW_ANCESTORS for ancestor in self._element_stack
        )
        required_motion_attrs = {
            "data-motion-root",
            "data-motion-item",
            "data-motion-action",
            "data-motion-controls",
            "data-motion-status",
        }
        if inactive_context and (
            tag == "svg" or required_motion_attrs.intersection(data)
        ):
            self.unsafe.append(
                f"required diagram element <{tag}> is inside browser-inert/raw-text markup"
            )
        if tag == "meta" and "http-equiv" in data:
            self.unsafe.append("http-equiv <meta> is not allowed in a diagram file")
        if "data-motion-root" in data:
            root = dict(data)
            root["_tag"] = tag
            root["_ancestors"] = "/".join(self._element_stack)
            self.roots.append(root)
            if self._motion_root_depth is None:
                self._motion_root_depth = len(self._element_stack)
        if self._motion_root_depth is not None:
            if "data-motion-item" in data:
                self.items.append(data)
            if "data-motion-action" in data:
                self.actions.add(data["data-motion-action"])
            if "data-motion-controls" in data:
                self.controls += 1
                if self._controls_depth is None:
                    self._controls_depth = len(self._element_stack)
            if "data-motion-status" in data:
                self.statuses.append(data)
                if self._controls_depth is not None:
                    self.statuses_in_controls += 1
        if tag == "script":
            self._current_script = {
                "attrs": data,
                "attr_names": [name for name, _value in normalized_attrs],
                "ancestors": tuple(self._element_stack),
                "body": [],
                "closed": False,
            }
            self.scripts.append(self._current_script)
        if tag == "style":
            if tuple(self._element_stack) not in {(), ("html", "head")}:
                self.unsafe.append(
                    "<style> must be top-level fragment content or a direct child of HTML <head>"
                )
            self._current_style = {
                "attrs": data,
                "ancestors": tuple(self._element_stack),
                "body": [],
                "closed": False,
            }
            self.stylesheets.append(self._current_style)
        if tag not in HTML_VOID_TAGS:
            self._element_stack.append(tag)
        if tag == "svg" and self._svg_depth == 0:
            self._svg_depth = 1
            self._current_svg = {"attrs": data, "first": None, "title": {}, "desc": {}}
            self.svgs.append(self._current_svg)
            return
        if self._svg_depth:
            self._svg_depth += 1
            assert self._current_svg is not None
            if self._svg_depth == 2 and self._current_svg["first"] is None:
                self._current_svg["first"] = tag
            if self._svg_depth == 2 and tag in {"title", "desc"}:
                self._current_svg[tag] = {"attrs": data, "text": ""}
                self._capture = tag

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if (
            self._svg_depth == 0
            and normalized_tag not in HTML_VOID_TAGS
            and normalized_tag != "svg"
        ):
            self.unsafe.append(
                f"non-void HTML element <{normalized_tag}/> may not use self-closing syntax"
            )
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script" and self._current_script is not None:
            self._current_script["closed"] = True
            self._current_script = None
        if tag == "style" and self._current_style is not None:
            self._current_style["closed"] = True
            self._current_style = None
        if self._svg_depth:
            if tag in {"title", "desc"}:
                self._capture = None
            self._svg_depth -= 1
            if self._svg_depth == 0:
                self._current_svg = None
        found = False
        for index in range(len(self._element_stack) - 1, -1, -1):
            if self._element_stack[index] == tag:
                del self._element_stack[index:]
                found = True
                break
        if not found and tag not in HTML_VOID_TAGS:
            self.unsafe.append(f"closing tag </{tag}> has no matching open element")
        if (
            self._motion_root_depth is not None
            and len(self._element_stack) <= self._motion_root_depth
        ):
            self._motion_root_depth = None
        if (
            self._controls_depth is not None
            and len(self._element_stack) <= self._controls_depth
        ):
            self._controls_depth = None

    def handle_data(self, data: str) -> None:
        if self._current_script is not None:
            body = self._current_script["body"]
            assert isinstance(body, list)
            body.append(data)
        if self._current_style is not None:
            body = self._current_style["body"]
            assert isinstance(body, list)
            body.append(data)
        if self._capture and self._current_svg:
            node = self._current_svg[self._capture]
            assert isinstance(node, dict)
            node["text"] = str(node.get("text", "")) + data


def normalized_controller(body: str) -> str:
    return body.replace("\r\n", "\n").replace("\r", "\n").strip()


def parsed_document(source: str) -> DiagramParser:
    parser = DiagramParser()
    parser.feed(source)
    parser.close()
    if parser._element_stack:
        parser.unsafe.append(
            "unclosed HTML elements: " + ", ".join(parser._element_stack)
        )
    return parser


def ambiguous_markup_errors(source: str) -> list[str]:
    """Reject constructs whose HTML5 parsing differs from Python HTMLParser."""
    errors: list[str] = []
    if re.search(r"<!\[", source, re.IGNORECASE):
        errors.append("HTML marked-section syntax is not allowed")
    title_openings = len(re.findall(r"<title\b", source, re.IGNORECASE))
    title_blocks = len(
        re.findall(
            r"<title\b[^<>]*>[^<]*</title[ \t\n\f\r]*>",
            source,
            re.IGNORECASE,
        )
    )
    if title_openings != title_blocks:
        errors.append("<title> must contain plain text and a canonical closing tag")
    noscript_openings = len(re.findall(r"<noscript\b", source, re.IGNORECASE))
    noscript_blocks = len(
        re.findall(
            r"<noscript[ \t\n\f\r]*>[ \t\n\f\r]*<p>[^<]*</p>"
            r"[ \t\n\f\r]*</noscript[ \t\n\f\r]*>",
            source,
            re.IGNORECASE,
        )
    )
    if noscript_openings != noscript_blocks:
        errors.append(
            "<noscript> must contain exactly one plain-text paragraph and a canonical closing tag"
        )
    if re.search(
        r"</\s+(?:style|script|title|textarea|xmp|iframe|noscript|noembed|noframes|plaintext)\b",
        source,
        re.IGNORECASE,
    ):
        errors.append("whitespace before a raw-text closing tag name is not allowed")
    for closing in re.finditer(
        r"</(?P<tag>style|script|title|textarea|xmp|iframe|noscript|noembed|noframes|plaintext)\b[^>]*>",
        source,
        re.IGNORECASE,
    ):
        tag = closing.group("tag")
        if re.fullmatch(
            rf"</{re.escape(tag)}[ \t\n\f\r]*>", closing.group(), re.IGNORECASE
        ) is None:
            errors.append(f"noncanonical raw-text closing tag </{tag}> is not allowed")
    cursor = 0
    while True:
        opening = source.find("<!--", cursor)
        if opening < 0:
            break
        if source.startswith("<!-->", opening) or source.startswith("<!--->", opening):
            errors.append("abrupt HTML comment syntax is not allowed")
        closing = source.find("-->", opening + 4)
        if closing < 0:
            errors.append("unclosed HTML comment is not allowed")
            break
        content = source[opening + 4 : closing]
        if "<!--" in content or "--!>" in content or content.endswith("<!-"):
            errors.append("ambiguous nested HTML comment syntax is not allowed")
        cursor = closing + 3
    return errors


def is_approved_google_fonts_stylesheet(value: str) -> bool:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    query = parse_qsl(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "fonts.googleapis.com"
        and parsed.username is None
        and parsed.password is None
        and port is None
        and parsed.path == "/css2"
        and not parsed.fragment
        and any(key == "family" and item for key, item in query)
        and all(key in {"family", "display"} for key, _item in query)
    )


def reference_error(tag: str, rel: str, value: str) -> str | None:
    stripped = value.strip()
    lowered = stripped.casefold()
    if not stripped or stripped.startswith("#"):
        return None
    if lowered.startswith("javascript:") or lowered.startswith("data:text/html"):
        return f"executable URL on <{tag}> is not allowed"
    if tag == "link" and "stylesheet" in rel.casefold().split():
        if is_approved_google_fonts_stylesheet(stripped):
            return None
        return "remote stylesheet is not the approved credential-free Google Fonts /css2 URL"
    if lowered.startswith("data:"):
        if re.match(r"data:image/(?:png|gif|jpeg|webp|avif);base64,", lowered):
            return None
        return f"unsupported data URL on <{tag}>"
    return f"external reference on <{tag}> is not self-contained"


def check_css_references(parser: DiagramParser, errors: list[str]) -> None:
    stylesheet_bodies = []
    for stylesheet in parser.stylesheets:
        body = stylesheet["body"]
        assert isinstance(body, list)
        stylesheet_bodies.append("".join(body))
    for source in (*stylesheet_bodies, *parser.css_values):
        source = source.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
        source = source.replace("\0", "\N{REPLACEMENT CHARACTER}")
        source = without_css_comments(source)
        def decode_escape(match: re.Match[str]) -> str:
            if match.group(1) is None:
                return match.group(2)
            codepoint = int(match.group(1), 16)
            return chr(codepoint) if 0 < codepoint <= 0x10FFFF else "\N{REPLACEMENT CHARACTER}"

        source = CSS_ESCAPE_RE.sub(decode_escape, source)
        if CSS_IMPORT_RE.search(source):
            errors.append("CSS @import is not allowed")
        if CSS_IMAGE_SET_RE.search(source):
            errors.append("CSS image-set() is not allowed")
        if CSS_SRC_RE.search(source):
            errors.append("CSS src() is not allowed")
        if CSS_IMAGE_RE.search(source):
            errors.append("CSS image() is not allowed")
        for match in CSS_URL_RE.finditer(source):
            value = match.group(1).strip().strip("'\"").strip()
            if not value.startswith("#"):
                errors.append("non-fragment CSS url() is not allowed")


def without_css_comments(source: str, *, preserve_token_boundary: bool = False) -> str:
    """Remove CSS comments while preserving quoted strings."""
    output: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote is not None:
            output.append(character)
            if character == "\\" and index + 1 < len(source):
                index += 1
                output.append(source[index])
            elif character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
            output.append(character)
        elif character == "/" and following == "*":
            if preserve_token_boundary:
                output.append(" ")
            index += 2
            while index < len(source) - 1 and source[index : index + 2] != "*/":
                if preserve_token_boundary and source[index] in "\r\n":
                    output.append(source[index])
                index += 1
            index += 1
        else:
            output.append(character)
        index += 1
    return "".join(output)


def css_at_rule_blocks(source: str, header_pattern: str) -> list[str]:
    """Return balanced bodies for matching CSS at-rules."""
    blocks: list[str] = []
    source = without_css_comments(source, preserve_token_boundary=True)
    header = re.compile(header_pattern + r"\s*\{", re.IGNORECASE | re.ASCII)
    depth = 0
    paren_depth = 0
    bracket_depth = 0
    quote: str | None = None
    top_level_content = False
    index = 0
    while index < len(source):
        character = source[index]
        if quote is not None:
            if character == "\\" and index + 1 < len(source):
                index += 1
            elif character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
            if depth == 0:
                top_level_content = True
        elif character == "\\":
            top_level_content = True
            if index + 1 < len(source):
                index += 1
        elif character == "(":
            paren_depth += 1
            if depth == 0:
                top_level_content = True
        elif character == ")":
            if paren_depth == 0:
                return []
            paren_depth -= 1
        elif character == "[":
            bracket_depth += 1
            if depth == 0:
                top_level_content = True
        elif character == "]":
            if bracket_depth == 0:
                return []
            bracket_depth -= 1
        elif character == "{" and paren_depth == 0 and bracket_depth == 0:
            depth += 1
            if depth == 1:
                top_level_content = False
        elif character == "}" and paren_depth == 0 and bracket_depth == 0:
            depth = max(0, depth - 1)
            if depth == 0:
                top_level_content = False
        elif character == ";" and depth == 0 and paren_depth == 0 and bracket_depth == 0:
            top_level_content = False
        elif (
            character == "@"
            and depth == 0
            and paren_depth == 0
            and bracket_depth == 0
            and not top_level_content
        ):
            match = header.match(source, index)
            if match is not None:
                block_depth = 1
                cursor = match.end()
                start = cursor
                block_quote: str | None = None
                while cursor < len(source) and block_depth:
                    current = source[cursor]
                    if block_quote is not None:
                        if current == "\\" and cursor + 1 < len(source):
                            cursor += 1
                        elif current == block_quote:
                            block_quote = None
                    elif current in {"'", '"'}:
                        block_quote = current
                    elif current == "{":
                        block_depth += 1
                    elif current == "}":
                        block_depth -= 1
                    cursor += 1
                if block_depth == 0:
                    blocks.append(source[start : cursor - 1])
                    index = cursor - 1
                    top_level_content = False
                else:
                    top_level_content = True
            else:
                top_level_content = True
        elif depth == 0 and character not in " \t\n\f\r":
            top_level_content = True
        index += 1
    if quote is not None or depth != 0 or paren_depth != 0 or bracket_depth != 0:
        return []
    return blocks


def canonical_controller() -> str:
    if not MOTION_TEMPLATE.is_file():
        raise RuntimeError(
            f"cannot find the canonical controller at {MOTION_TEMPLATE}; "
            "run self_check.py from its shipped location inside the skill"
        )
    parser = parsed_document(MOTION_TEMPLATE.read_text(encoding="utf-8"))
    if len(parser.scripts) != 1 or not parser.scripts[0]["closed"]:
        raise RuntimeError("template-motion.html must contain one closed controller")
    body = parser.scripts[0]["body"]
    assert isinstance(body, list)
    return normalized_controller("".join(body))


def check_svgs(parser: DiagramParser, errors: list[str]) -> None:
    checkable = [
        svg
        for svg in parser.svgs
        if isinstance(svg["attrs"], dict)
        and str(svg["attrs"].get("aria-hidden", "")).casefold() != "true"
    ]
    if not checkable:
        errors.append("diagram file needs at least one accessible (non-aria-hidden) SVG")
    for number, svg in enumerate(checkable, 1):
        attrs = svg["attrs"]
        assert isinstance(attrs, dict)
        if attrs.get("role") != "img":
            errors.append(f"svg {number} needs role=img")
        labelled = attrs.get("aria-labelledby", "").split()
        title = svg["title"]
        desc = svg["desc"]
        assert isinstance(title, dict) and isinstance(desc, dict)
        title_attrs = title.get("attrs", {})
        desc_attrs = desc.get("attrs", {})
        assert isinstance(title_attrs, dict) and isinstance(desc_attrs, dict)
        if svg["first"] != "title":
            errors.append(f"svg {number} title must be its first child")
        if not str(title.get("text", "")).strip() or not str(desc.get("text", "")).strip():
            errors.append(f"svg {number} needs non-empty title and desc")
        title_id = title_attrs.get("id", "")
        desc_id = desc_attrs.get("id", "")
        if title_id in {"", "title"} or desc_id in {"", "desc"}:
            errors.append(f"svg {number} title/desc IDs must be diagram-prefixed, never bare")
        if labelled != [title_id, desc_id]:
            errors.append(f"svg {number} aria-labelledby must name title then desc")


def check_scripts(parser: DiagramParser, errors: list[str]) -> None:
    if not parser.scripts:
        return
    if len(parser.scripts) > 1:
        errors.append(f"at most one script is allowed; found {len(parser.scripts)}")
    for number, script in enumerate(parser.scripts, 1):
        attrs = script["attrs"]
        attr_names = script["attr_names"]
        body = script["body"]
        ancestors = script["ancestors"]
        assert isinstance(attrs, dict) and isinstance(attr_names, list) and isinstance(body, list)
        assert isinstance(ancestors, tuple)
        if ancestors != ("html", "body"):
            errors.append(f"script {number} must be a direct child of body")
        if not script["closed"]:
            errors.append(f"script {number} must have a closing script tag")
        if attr_names != ["data-diagram-controls"] or attrs.get("data-diagram-controls") != "":
            errors.append(f"script {number} must carry only the canonical data-diagram-controls attribute")
            continue
        try:
            if normalized_controller("".join(body)) != canonical_controller():
                errors.append(f"script {number} must exactly match the controller in template-motion.html")
        except RuntimeError as exc:
            errors.append(str(exc))


def check_motion(parser: DiagramParser, source: str, errors: list[str]) -> None:
    has_motion_markup = bool(parser.roots or parser.items or parser.scripts)
    if not has_motion_markup:
        return
    if len(parser.roots) != 1:
        errors.append(f"expected exactly one data-motion-root; found {len(parser.roots)}")
        return
    root = parser.roots[0]
    if root.get("_tag") != "main" or root.get("_ancestors") != "html/body":
        errors.append("data-motion-root must be a direct-body <main> element")
    mode = root.get("data-motion-mode", "")
    if mode not in MODES:
        errors.append(f"data-motion-mode must be one of {sorted(MODES)}; got {mode!r}")
    raw_count = root.get("data-step-count", "")
    if not ASCII_DECIMAL_RE.fullmatch(raw_count):
        count = -1
        errors.append("data-step-count must be an ASCII decimal integer")
    else:
        count = int(raw_count)
    minimum_count = 0 if mode == "none" else 1
    if count < minimum_count or count > 8:
        errors.append(f"semantic step count must be {minimum_count}..8; got {count}")

    if len(parser.items) > 12:
        errors.append(f"motion item budget is 12; found {len(parser.items)}")
    semantic_steps: list[int] = []
    for index, item in enumerate(parser.items, 1):
        raw_step = item.get("data-step", "")
        if not ASCII_DECIMAL_RE.fullmatch(raw_step):
            errors.append(f"motion item {index} has a non-ASCII-decimal data-step")
            continue
        step = int(raw_step)
        decorative = "data-motion-decorative" in item
        if not decorative:
            semantic_steps.append(step)
            if not item.get("aria-label", "").strip():
                errors.append(f"semantic motion item {index} needs a non-color aria-label")
        elif item.get("aria-hidden") != "true" or item.get("focusable") != "false":
            errors.append(f"decorative motion item {index} needs aria-hidden=true and focusable=false")
        inline = item.get("style", "").replace(" ", "").lower()
        if any(token in inline for token in ("display:none", "visibility:hidden", "opacity:0")):
            errors.append(f"motion item {index} is hidden in source; the fallback must be visible")

    expected = set(range(1, count + 1)) if count > 0 else set()
    if set(semantic_steps) != expected:
        errors.append(f"semantic steps must be contiguous 1..{count}; found {sorted(set(semantic_steps))}")
    crowded = {step: n for step, n in Counter(semantic_steps).items() if n > 2}
    if crowded:
        errors.append(f"no more than two semantic items may share a step; found {crowded}")

    if mode in {"none", "loop"} and parser.scripts:
        errors.append(f"{mode} mode must be script-free")
    if mode in {"none", "loop"} and (parser.controls or parser.actions or parser.statuses):
        errors.append(f"{mode} mode must not expose playback controls or live status")
    controlled = mode == "step" or (mode == "reveal" and bool(parser.scripts))
    if controlled:
        if parser.controls != 1:
            errors.append(f"controlled mode needs one in-root control group; found {parser.controls}")
        missing = ACTIONS - parser.actions
        if missing:
            errors.append(f"controlled mode is missing actions: {', '.join(sorted(missing))}")
        if not parser.statuses:
            errors.append("controlled mode needs data-motion-status")
        else:
            status = parser.statuses[0]
            if (
                status.get("role") != "status"
                or status.get("aria-live") != "polite"
                or status.get("aria-atomic") != "true"
            ):
                errors.append("motion status needs role=status, aria-live=polite, aria-atomic=true")
            if parser.statuses_in_controls:
                errors.append("motion status must sit outside data-motion-controls")
        if not parser.scripts:
            errors.append("controlled mode needs the scoped control script")

    if parser.scripts:
        reduced_motion = False
        print_fallback = False
        for stylesheet in parser.stylesheets:
            attrs = stylesheet["attrs"]
            ancestors = stylesheet["ancestors"]
            body = stylesheet["body"]
            assert isinstance(attrs, dict)
            assert isinstance(ancestors, tuple)
            assert isinstance(body, list)
            style_type = attrs.get("type", "").strip().casefold()
            if (
                not stylesheet["closed"]
                or attrs.get("media", "").strip().casefold() not in {"", "all"}
                or style_type not in {"", "text/css"}
                or attrs.get("title", "").strip()
                or "disabled" in attrs
                or ancestors != ("html", "head")
            ):
                continue
            style_source = "".join(body)
            reduced_motion = reduced_motion or bool(
                css_at_rule_blocks(
                    style_source,
                    r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)",
                )
            )
            print_fallback = print_fallback or bool(
                css_at_rule_blocks(style_source, r"@media\s+print\b")
            )
        if not reduced_motion:
            errors.append("missing reduced-motion CSS fallback (prefers-reduced-motion)")
        if not print_fallback:
            errors.append("missing print CSS fallback (@media print)")
        if "<noscript" not in source.casefold():
            errors.append("motion file needs a <noscript> explanation of the complete static frame")


def safety_errors(source: str) -> list[str]:
    """Return the context-aware self-containment findings shared by repository lint."""
    errors = ambiguous_markup_errors(source)
    if errors:
        return errors
    parser = parsed_document(source)
    errors.extend(parser.unsafe)
    for tag, rel, value in parser.references:
        finding = reference_error(tag, rel, value)
        if finding:
            errors.append(finding)
    check_css_references(parser, errors)
    return errors


def verify(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    preflight_errors = ambiguous_markup_errors(source)
    if preflight_errors:
        return preflight_errors
    parser = parsed_document(source)
    errors = safety_errors(source)
    check_svgs(parser, errors)
    check_scripts(parser, errors)
    check_motion(parser, source, errors)
    return errors


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("files", nargs="+", type=Path)
    args = argument_parser.parse_args()
    failed = False
    for path in args.files:
        try:
            errors = verify(path)
        except (OSError, UnicodeError) as exc:
            errors = [str(exc)]
        if errors:
            failed = True
            print(f"FAIL {path}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"OK {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
