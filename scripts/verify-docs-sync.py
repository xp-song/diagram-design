#!/usr/bin/env python3
"""Verify that routing and browsing surfaces stay in sync with the skill.

Eleven drift classes, each of which has shipped before or breaks a distribution boundary:

1. The SKILL.md frontmatter description stays compact for selectors and
   conservative host compatibility even though invocation is explicit-only.
2. The gallery (assets/index.html) must reach every shipped example, and every
   gallery tab must point at a file that exists.
3. Every concrete file named in README.md's architecture tree must exist.
4. Every relative references/*.md link in SKILL.md must resolve.
5. Claude and Pi command/prompt surfaces must route to the matching reference.
6. The plugin manifests are the detailed pre-install discovery surface, so each
   long description keeps every type's lexical hook independently of the compact
   runtime skill description.
7. Factory Droid's README install commands and native manifest path must agree
   with the package metadata instead of becoming a second hand-maintained API.
8. Every support path a strict skill bundler can extract from SKILL.md must be
   a literal file shipped inside the skill package.
9. Import command surfaces must route to the visual-type taxonomy instead of
   hardcoding a count that becomes stale when a type is added.
10. The High-Level reproducibility checklist must agree with its canvas formula
   and retain sequential numbering.
11. An immutable profile-only distribution must keep the policy packaged and
   routed from every instruction surface that otherwise writes the checkout.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills/diagram-design/SKILL.md"
GALLERY = ROOT / "skills/diagram-design/assets/index.html"
ASSET_DIR = ROOT / "skills/diagram-design/assets"
README = ROOT / "README.md"
HIGH_LEVEL_REFERENCE = ROOT / "skills/diagram-design/references/type-high-level.md"
VARIANTS = ("", "-dark", "-full")
VISUAL_TYPE_COUNT = 39
# Types whose selection-table name differs from its description vocabulary.
DESCRIPTION_ALIASES = {
    "bar chart": "bar",
    "line chart": "line",
    "scatter plot": "scatter",
}
MAX_SKILL_DESCRIPTION_CHARS = 160
DISCOVERY_VOCABULARY = {
    "draw.io import": ("draw.io", ".drawio"),
    "Mermaid import": ("mermaid",),
    "branding": ("brand", "branding"),
    "semantic patterns": ("semantic pattern", "semantic patterns"),
    "callouts": ("callout", "callouts"),
    "motion": ("motion", "animation"),
    "sketchy styling": ("sketchy", "hand-drawn"),
}
EXPLICIT_POLICY_DOCS = (
    Path("README.md"),
    Path("CONTRIBUTING.md"),
    Path("docs/adr/0010-explicit-only-skill-invocation.md"),
    Path("docs/adr/0007-new-layout-grammars.md"),
    Path("docs/superpowers/specs/2026-08-18-polar-chart-design.md"),
    Path("docs/superpowers/plans/2026-08-18-polar-chart.md"),
)
STALE_RUNTIME_DESCRIPTION_PHRASES = (
    "do not remove routing vocabulary from frontmatter",
    "type's name to the frontmatter `description`",
    "SKILL.md description loses a type's lexical hook",
    "never by trimming the frontmatter description",
    "SKILL.md visual-type guide and frontmatter description",
    "Add `polar/radial lollipop` to the frontmatter description.",
    "When a request matches, it loads `SKILL.md`",
    ".codex-plugin/plugin.json",
)
REQUIRED_EXPLICIT_POLICY_PHRASES = {
    Path("README.md"): (
        "Diagram Design is user-invoked only.",
        "an ordinary matching prompt does not load it.",
        "Diagram Design is explicit-only: invoke the skill by name when you want it.",
        "~/.agents/skills/diagram-design",
        "$HOME\\.agents\\skills\\diagram-design",
    ),
    Path("CONTRIBUTING.md"): (
        "compact explicit-only runtime metadata",
        "Keep the runtime frontmatter description under its compact explicit-only budget",
    ),
    Path("docs/adr/0010-explicit-only-skill-invocation.md"): (
        "Diagram Design is user-invoked only:",
        "Ordinary natural-language prompts do not promise to activate Diagram Design.",
        "policy.allow_implicit_invocation: false",
    ),
}
ROUTING_SURFACES = {
    Path("commands/export-diagram.md"): "references/export.md",
    Path("commands/import-drawio.md"): "references/import-drawio.md",
    Path("commands/import-mermaid.md"): "references/import-mermaid.md",
    Path("commands/profile.md"): "references/profiles.md",
    Path("commands/doctor.md"): "references/doctor.md",
    Path("prompts/export-diagram.md"): "references/export.md",
    Path("prompts/import-mermaid.md"): "references/import-mermaid.md",
    Path("prompts/profile.md"): "references/profiles.md",
    Path("prompts/doctor.md"): "references/doctor.md",
}
FACTORY_MANIFEST = Path(".factory-plugin/plugin.json")
FACTORY_MARKETPLACE = Path(".factory-plugin/marketplace.json")
SUPPORT_DIRECTORIES = frozenset(
    {"references", "templates", "scripts", "assets", "examples"}
)
# Mirrors Hermes Agent's support-file scanner. It intentionally sees Markdown
# links, code spans, and path-like prose because strict bundlers may require
# every extracted path before they install any part of the skill.
SCANNER_VISIBLE_SUPPORT_REFERENCE = re.compile(
    r"(?:\]\(|`|(?:^|[\s\"']))"
    r"((?:references|templates|scripts|assets|examples)/[^\s)`\"'<>]+)",
    re.MULTILINE,
)
REQUIRED_PACKAGED_RUNTIME_FILES = frozenset(
    {
        "scripts/self_check.py",
        "scripts/drawio_extract.py",
        "scripts/mermaid_extract.py",
        "scripts/profile_state.py",
        "assets/template.html",
        "assets/template-dark.html",
        "assets/template-full.html",
        "assets/template-motion.html",
        "assets/template-terminal.html",
    }
)


def normalized(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"\s*/\s*", "/", text)
    return re.sub(r"\s+", " ", text)


def frontmatter_description(markdown: str) -> str:
    parts = markdown.split("---")
    if len(parts) < 3:
        return ""
    match = re.search(r"^description:\s*(.+)$", parts[1], re.MULTILINE)
    return match.group(1).strip() if match else ""


def selection_table_types(markdown: str) -> list[str]:
    start = markdown.find("### Visual-type guide")
    end = markdown.find("Rules of thumb", start)
    if start < 0 or end < 0:
        return []
    names = re.findall(r"^\|[^|]*\|\s*\*\*([^*]+)\*\*\s*\|", markdown[start:end], re.MULTILINE)
    return [name.strip() for name in names]


def check_description(errors: list[str]) -> None:
    markdown = SKILL.read_text(encoding="utf-8")
    description = normalized(frontmatter_description(markdown))
    if not description:
        errors.append("SKILL.md frontmatter description is missing")
        return
    if len(frontmatter_description(markdown)) > MAX_SKILL_DESCRIPTION_CHARS:
        errors.append(
            "SKILL.md frontmatter description exceeds the compact selector "
            f"budget of {MAX_SKILL_DESCRIPTION_CHARS} characters"
        )
    types = selection_table_types(markdown)
    if len(types) != VISUAL_TYPE_COUNT:
        errors.append(
            f"expected {VISUAL_TYPE_COUNT} visual types in the selection table; found {len(types)}"
        )


def gallery_types(source: str) -> list[str]:
    return re.findall(r'data-type="([^"]+)"', source)


def check_gallery(errors: list[str]) -> None:
    source = GALLERY.read_text(encoding="utf-8")
    types = gallery_types(source)
    if not types:
        errors.append("gallery has no data-type tabs")
        return
    reachable = {f"example-{name}{variant}.html" for name in types for variant in VARIANTS}
    on_disk = {path.name for path in ASSET_DIR.glob("example-*.html")}
    for name in sorted(on_disk - reachable):
        errors.append(f"gallery cannot reach shipped example {name}; add a tab to assets/index.html")
    for name in sorted(types):
        if f"example-{name}.html" not in on_disk:
            errors.append(f"gallery tab {name!r} points at a missing example-{name}.html")


def readme_tree_tokens(markdown: str) -> list[str]:
    blocks = re.findall(r"```\n(diagram-design/\n.*?)```", markdown, re.DOTALL)
    tokens: list[str] = []
    for block in blocks:
        tokens.extend(
            re.findall(r"([A-Za-z0-9][A-Za-z0-9_.*-]*\.(?:md|html|py|yml|yaml|json|txt|mmd|drawio|png))", block)
        )
    return tokens


def check_readme_tree(errors: list[str]) -> None:
    markdown = README.read_text(encoding="utf-8")
    tokens = readme_tree_tokens(markdown)
    if not tokens:
        errors.append("README architecture tree not found or names no files")
        return
    for token in sorted(set(tokens)):
        matches = list(ROOT.rglob(token))
        if not matches:
            errors.append(f"README architecture tree names {token!r} but no such file exists")


def skill_reference_links(markdown: str) -> list[str]:
    """Return direct relative links from SKILL.md into references/."""
    return re.findall(
        r"\]\((references/[A-Za-z0-9][A-Za-z0-9_.-]*\.md)(?:#[^)]*)?\)",
        markdown,
    )


def check_skill_reference_links(
    errors: list[str], markdown: str, skill_directory: Path
) -> None:
    for target in sorted(set(skill_reference_links(markdown))):
        if not (skill_directory / target).is_file():
            errors.append(f"SKILL.md links to missing reference {target!r}")


def scanner_visible_support_references(markdown: str) -> list[str]:
    """Return the local support paths a strict skill bundler will request."""
    normalized_markdown = markdown.replace("\\", "/")
    references: set[str] = set()
    for match in SCANNER_VISIBLE_SUPPORT_REFERENCE.finditer(normalized_markdown):
        raw = match.group(1).rstrip(".,;:")
        references.add(unquote(urlsplit(raw).path))
    return sorted(references)


def check_packaged_support_references(
    errors: list[str], markdown: str, skill_directory: Path
) -> None:
    """Require every scanner-visible path to be a safe, packaged file."""
    scanner_references = scanner_visible_support_references(markdown)
    for target in scanner_references:
        normalized_target = target.replace("\\", "/")
        path = PurePosixPath(normalized_target)
        parts = [part for part in path.parts if part not in {"", "."}]
        if (
            not parts
            or parts[0] not in SUPPORT_DIRECTORIES
            or normalized_target.startswith("/")
            or path.is_absolute()
            or any(part == ".." or ":" in part for part in parts)
        ):
            errors.append(f"SKILL.md exposes unsafe packaged support path {target!r}")
        elif not (skill_directory / "/".join(parts)).is_file():
            errors.append(
                f"SKILL.md exposes missing packaged support file {target!r}; "
                "strict skill bundlers will abort installation"
            )
    for target in sorted(REQUIRED_PACKAGED_RUNTIME_FILES - set(scanner_references)):
        errors.append(
            f"SKILL.md does not expose required packaged runtime file {target!r}; "
            "strict skill bundlers will omit it"
        )


def check_routing_surfaces(errors: list[str], root: Path) -> None:
    for relative, reference_link in ROUTING_SURFACES.items():
        reference = root / "skills/diagram-design" / reference_link
        if not reference.is_file():
            errors.append(f"routing source of truth is missing: skills/diagram-design/{reference_link}")
        path = root / relative
        if not path.is_file():
            errors.append(f"routing surface is missing: {relative.as_posix()}")
            continue
        if reference_link not in path.read_text(encoding="utf-8"):
            errors.append(
                f"routing surface does not route to {reference_link}: {relative.as_posix()}"
            )


def check_profile_only_policy(errors: list[str], root: Path) -> None:
    """Require the immutable-install policy and all precedence routes."""
    relative_policy = Path("skills/diagram-design/references/profile-only.md")
    policy_path = root / relative_policy
    if not policy_path.is_file():
        errors.append(f"immutable profile-only policy is missing: {relative_policy.as_posix()}")
        return

    policy = policy_path.read_text(encoding="utf-8")
    required_policy_terms = (
        "byte-for-byte unchanged",
        "~/.diagram-design/profiles/<slug>.md",
        "<project-root>/.diagram-design",
        "Never fall back",
        "Safe marker I/O",
        "lies inside it",
        "symbolic link",
        "O_NOFOLLOW",
        "os.replace",
        "profile_state.py",
        "--expect",
        "--create",
    )
    for term in required_policy_terms:
        if term not in policy:
            errors.append(f"profile-only policy lost required contract text: {term!r}")

    routed_surfaces = (
        Path("skills/diagram-design/SKILL.md"),
        Path("skills/diagram-design/references/onboarding.md"),
        Path("skills/diagram-design/references/profiles.md"),
        Path("commands/profile.md"),
        Path("prompts/profile.md"),
        Path("README.md"),
    )
    for relative in routed_surfaces:
        path = root / relative
        if not path.is_file():
            errors.append(f"profile-only routing surface is missing: {relative.as_posix()}")
        elif "profile-only.md" not in path.read_text(encoding="utf-8"):
            errors.append(
                f"profile-only routing surface lost policy precedence: {relative.as_posix()}"
            )


def check_factory_install_surface(errors: list[str], root: Path) -> None:
    markdown = (root / "README.md").read_text(encoding="utf-8")
    manifest = json.loads((root / FACTORY_MANIFEST).read_text(encoding="utf-8"))
    marketplace = json.loads((root / FACTORY_MARKETPLACE).read_text(encoding="utf-8"))
    code_blocks = re.findall(
        r"^```[^\n]*\n(.*?)^```[ \t]*$", markdown, re.MULTILINE | re.DOTALL
    )

    marketplace_command = f"droid plugin marketplace add {manifest['repository']}"
    install_command = f"droid plugin install {manifest['name']}@{marketplace['name']}"
    command_blocks = [
        [line.strip() for line in block.splitlines() if line.strip()]
        for block in code_blocks
    ]
    install_is_documented = any(
        any(
            line == install_command or line.startswith(f"{install_command} ")
            for line in lines[lines.index(marketplace_command) + 1 :]
        )
        for lines in command_blocks
        if marketplace_command in lines
    )
    if not install_is_documented:
        errors.append(
            "README Factory install block must match native metadata: "
            f"`{marketplace_command}` then `{install_command}`"
        )

    native_directory = f"{FACTORY_MANIFEST.parent.as_posix()}/"
    architecture_blocks = [
        block
        for block in code_blocks
        if "diagram-design/" in block and "commands/" in block
    ]
    native_path_is_documented = any(
        line.lstrip(" │├─└").startswith(native_directory)
        for block in architecture_blocks
        for line in block.splitlines()
    )
    if not native_path_is_documented:
        errors.append(
            f"README architecture tree must list Factory's native {native_directory} path"
        )


# A command that spells the type count out has to be edited by every PR that
# adds a type, and is the one file such a PR has no reason to open. Both import
# commands were left at 27 while the selection table moved on.
# The phrasing varies, so match the count rather than the one sentence it went
# stale in. Two forms carry it: the bare count standing in for the table
# (`one of the 27`), and a count attached to the taxonomy noun with room for
# adjectives between, in either order (`28 visual types`, `28 supported visual
# diagram types`, `28 types of visual diagrams`). Those clauses insist on that
# noun so an unrelated quantity — `accepts 2 file types` — is not rejected by a
# gate about the visual taxonomy.
#
# Every gap is whitespace-tolerant because both commands already wrap the
# sentence that carried the stale count, so a count can land just after the
# wrap. That is why the whole file is searched at once and the line is derived
# from the match offset rather than iterating lines.
#
# Word-form numerals (`Twenty-eight visual types`) are out of scope; README and
# the docstring say "numeral" so the gate does not claim more than it checks.
HARDCODED_COUNT_RE = re.compile(
    r"one\s+of\s+(?:the\s+)?\d+\b"
    r"|\b\d+\s+(?:[\w-]+\s+){0,2}?(?:visual|diagram)[\s-]+types?\b"
    r"|\b\d+\s+types?\s+of\s+(?:[\w-]+\s+){0,2}?diagrams?\b",
    re.IGNORECASE,
)
COUNT_SURFACES = (
    Path("commands/import-drawio.md"),
    Path("commands/import-mermaid.md"),
)


def check_type_counts(errors: list[str], root: Path) -> None:
    """No routing surface may write the visual-type count as a numeral."""
    for relative in COUNT_SURFACES:
        path = root / relative
        if not path.is_file():
            errors.append(f"type-count surface is missing: {relative.as_posix()}")
            continue
        text = path.read_text(encoding="utf-8")
        for match in HARDCODED_COUNT_RE.finditer(text):
            number = text.count("\n", 0, match.start()) + 1
            phrase = " ".join(match.group(0).split())
            errors.append(
                f"{relative.as_posix()}:{number} hardcodes the visual-type count "
                f"({phrase!r}); point at SKILL.md \u00a73 instead so adding a type "
                f"cannot leave it stale"
            )


def check_high_level_reference(errors: list[str], markdown: str) -> None:
    width_formula = re.search(
        r"^effective_w\s*=\s*(\d+)\s*-\s*right_strip_w\s*-\s*strip_margin",
        markdown,
        re.MULTILINE,
    )
    right_strip = re.search(r"^right_strip_w\s*=\s*(\d+)\s+if", markdown, re.MULTILINE)
    strip_margin = re.search(r"^strip_margin\s*=\s*(\d+)\s+if", markdown, re.MULTILINE)
    if not all((width_formula, right_strip, strip_margin)):
        errors.append("High-Level canvas is missing the effective-width formula")
        return

    checklist = re.search(
        r"^## 7\. Reproducibility checklist[^\n]*\n(.*?)(?=^## |\Z)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    if checklist is None:
        errors.append("High-Level reproducibility checklist is missing")
        return

    items = re.findall(r"^(\d+)\.\s+(.+)$", checklist.group(1), re.MULTILINE)
    numbers = [int(number) for number, _ in items]
    expected_numbers = list(range(1, len(numbers) + 1))
    if numbers != expected_numbers:
        rendered = ",".join(str(number) for number in numbers)
        errors.append(
            "High-Level reproducibility checklist numbering is not sequential: "
            f"expected 1..{len(numbers)}, found {rendered}"
        )

    item_three = next((text for number, text in items if number == "3"), "")
    checklist_width = re.search(r"effective_w\s*=\s*(\d+)", item_three)
    expected_width = (
        int(width_formula.group(1))
        - int(right_strip.group(1))
        - int(strip_margin.group(1))
    )
    if checklist_width is None:
        errors.append("High-Level checklist item 3 is missing effective_w")
    elif int(checklist_width.group(1)) != expected_width:
        errors.append(
            f"High-Level checklist item 3 has effective_w={checklist_width.group(1)}; "
            f"expected {width_formula.group(1)} - {right_strip.group(1)} - "
            f"{strip_margin.group(1)} = {expected_width}"
        )


MANIFEST_DESCRIPTIONS = (
    (Path(".claude-plugin/plugin.json"), ("description",)),
    (Path(".claude-plugin/marketplace.json"), ("description",)),
    (FACTORY_MANIFEST, ("description",)),
)


def find_key(node: object, key: str) -> str | None:
    """First value for *key* anywhere in a nested JSON document."""
    if isinstance(node, dict):
        if isinstance(node.get(key), str):
            return node[key]
        for value in node.values():
            found = find_key(value, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_key(value, key)
            if found is not None:
                return found
    return None


def check_manifest_descriptions(errors: list[str], root: Path) -> None:
    markdown = SKILL.read_text(encoding="utf-8")
    types = selection_table_types(markdown)
    for relative, keys in MANIFEST_DESCRIPTIONS:
        path = root / relative
        if not path.exists():
            errors.append(f"missing plugin manifest: {relative.as_posix()}")
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            value = find_key(document, key)
            if value is None:
                errors.append(f"{relative.as_posix()} has no {key!r}")
                continue
            text = normalized(value)
            for name in types:
                hook = DESCRIPTION_ALIASES.get(normalized(name), normalized(name))
                if not contains_lexical_hook(text, hook):
                    errors.append(
                        f"{relative.as_posix()} {key!r} lost the lexical hook for "
                        f"type {name!r} (expected {hook!r}) — detailed marketplace "
                        f"descriptions must name every supported type"
                    )
            for label in missing_discovery_vocabulary(text):
                errors.append(
                    f"{relative.as_posix()} {key!r} lost required discovery "
                    f"vocabulary for {label}"
                )


def missing_discovery_vocabulary(text: str) -> list[str]:
    searchable = normalized(text)
    return [
        label
        for label, aliases in DISCOVERY_VOCABULARY.items()
        if not any(contains_lexical_hook(searchable, alias) for alias in aliases)
    ]


def contains_lexical_hook(text: str, hook: str) -> bool:
    """Match a discovery hook as a term, not inside another type name."""
    searchable = normalized(text)
    term = normalized(hook)
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", searchable) is not None


def check_explicit_invocation_docs(errors: list[str], root: Path) -> None:
    for relative in EXPLICIT_POLICY_DOCS:
        source = (root / relative).read_text(encoding="utf-8")
        for phrase in STALE_RUNTIME_DESCRIPTION_PHRASES:
            if phrase in source:
                errors.append(
                    f"{relative.as_posix()} retains stale runtime-description "
                    f"guidance: {phrase!r}"
                )
        for phrase in REQUIRED_EXPLICIT_POLICY_PHRASES.get(relative, ()):
            if phrase not in source:
                errors.append(
                    f"{relative.as_posix()} lost required explicit-invocation "
                    f"guidance: {phrase!r}"
                )


def main() -> int:
    errors: list[str] = []
    check_description(errors)
    check_manifest_descriptions(errors, ROOT)
    check_explicit_invocation_docs(errors, ROOT)
    check_factory_install_surface(errors, ROOT)
    check_gallery(errors)
    check_readme_tree(errors)
    check_skill_reference_links(
        errors,
        SKILL.read_text(encoding="utf-8"),
        SKILL.parent,
    )
    check_packaged_support_references(
        errors,
        SKILL.read_text(encoding="utf-8"),
        SKILL.parent,
    )
    check_type_counts(errors, ROOT)
    check_high_level_reference(errors, HIGH_LEVEL_REFERENCE.read_text(encoding="utf-8"))
    check_routing_surfaces(errors, ROOT)
    check_profile_only_policy(errors, ROOT)
    if errors:
        print("FAIL docs sync")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(
        "OK docs sync: compact runtime description, marketplace discovery hooks, "
        "explicit-invocation docs, gallery reachability, README tree, "
        "reference links, packaged support files, routing surfaces, manifest descriptions, "
        "Factory install contract, type-count routing, High-Level invariants, "
        "profile-only policy"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
