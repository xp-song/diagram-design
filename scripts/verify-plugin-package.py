#!/usr/bin/env python3
"""Verify synchronized version bumps and native marketplace packaging."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "diagram-design"
CONTROLLED_REPOSITORY = "xp-song/diagram-design"
CONTROLLED_REPOSITORY_URL = f"https://github.com/{CONTROLLED_REPOSITORY}"
MANIFEST_PATHS = {
    "Claude": Path(".claude-plugin/plugin.json"),
    "Factory": Path(".factory-plugin/plugin.json"),
}
CLAUDE_MARKETPLACE = Path(".claude-plugin/marketplace.json")
FACTORY_MARKETPLACE = Path(".factory-plugin/marketplace.json")
MAINTAINER_POLICY = Path(".maintainer-policy.json")
OPENAI_SKILL_METADATA = Path("skills/diagram-design/agents/openai.yaml")
FORBIDDEN_NATIVE_CODEX_PATHS = (
    Path(".codex-plugin"),
    Path(".codex-plugin/plugin.json"),
    Path(".agents/plugins"),
    Path(".agents/plugins/marketplace.json"),
)
MAX_SKILL_DESCRIPTION_CHARS = 160
OPENAI_METADATA_PATHS = frozenset(
    {
        ("interface",),
        ("interface", "display_name"),
        ("interface", "short_description"),
        ("interface", "default_prompt"),
        ("policy",),
        ("policy", "allow_implicit_invocation"),
    }
)
SHARED_MANIFEST_FIELDS = (
    "name",
    "description",
    "version",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"could not read {path}: {exc}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"{path} is not valid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{path} must contain a JSON object")
        return None
    return payload


def base_path_exists(root: Path, base_ref: str, relative: Path) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{base_ref}:{relative.as_posix()}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def load_base_json(
    root: Path, base_ref: str, relative: Path, errors: list[str]
) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{relative.as_posix()}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "file not found"
        errors.append(f"could not read {relative} from {base_ref}: {detail}")
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        errors.append(f"{relative} at {base_ref} is not valid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{relative} at {base_ref} must contain a JSON object")
        return None
    return payload


def parse_semver(value: object, label: str, errors: list[str]) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or (match := SEMVER.fullmatch(value)) is None:
        errors.append(
            f"{label} version must be strict MAJOR.MINOR.PATCH semver; got {value!r}"
        )
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def resolve_local_path(root: Path, raw_path: object, label: str, errors: list[str]) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"{label} must be a non-empty local path string")
        return None
    if raw_path not in {".", "./"} and not raw_path.startswith("./"):
        errors.append(f"{label} must be '.' or start with './'; got {raw_path!r}")
        return None

    relative = raw_path[2:] if raw_path.startswith("./") else ""
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        errors.append(f"{label} must stay inside the marketplace root; got {raw_path!r}")
        return None

    resolved_root = root.resolve()
    resolved = (root / relative_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        errors.append(f"{label} resolves outside the marketplace root: {raw_path!r}")
        return None
    if not resolved.is_dir():
        errors.append(f"{label} target does not exist or is not a directory: {raw_path!r}")
        return None
    return resolved


def find_plugin_entry(
    marketplace: dict[str, Any], label: str, errors: list[str]
) -> dict[str, Any] | None:
    if marketplace.get("name") != PLUGIN_NAME:
        errors.append(
            f"{label} marketplace name must remain {PLUGIN_NAME!r}; "
            f"got {marketplace.get('name')!r}"
        )
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        errors.append(f"{label} marketplace plugins must be an array")
        return None
    matches = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME]
    if len(matches) != 1:
        errors.append(
            f"{label} marketplace must contain exactly one {PLUGIN_NAME!r} entry; "
            f"found {len(matches)}"
        )
        return None
    return matches[0]


def verify_versions(
    root: Path,
    base_ref: str,
    manifests: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    current_versions = {label: payload.get("version") for label, payload in manifests.items()}
    version_values = list(current_versions.values())
    if any(version != version_values[0] for version in version_values[1:]):
        rendered = ", ".join(f"{label}={value!r}" for label, value in current_versions.items())
        errors.append(f"plugin manifest versions must match: {rendered}")

    base_check = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if base_check.returncode != 0:
        detail = base_check.stderr.strip() or "not a commit"
        errors.append(f"could not resolve base ref {base_ref!r}: {detail}")
        return

    base_manifest_count = 0
    for label, relative in MANIFEST_PATHS.items():
        current = parse_semver(current_versions.get(label), f"current {label}", errors)
        if not base_path_exists(root, base_ref, relative):
            continue
        base_manifest_count += 1
        base_manifest = load_base_json(root, base_ref, relative, errors)
        if base_manifest is None:
            continue
        base = parse_semver(base_manifest.get("version"), f"{label} at {base_ref}", errors)
        if current is not None and base is not None and current <= base:
            errors.append(
                f"{label} manifest version must increase relative to {base_ref}: "
                f"{base_manifest.get('version')} -> {current_versions.get(label)}"
            )
    if base_manifest_count == 0:
        errors.append(
            f"no synchronized plugin manifest exists at {base_ref}; "
            "cannot establish that the package version advanced"
        )


def verify_manifest_identity(manifests: dict[str, dict[str, Any]], errors: list[str]) -> None:
    for label, payload in manifests.items():
        if payload.get("name") != PLUGIN_NAME:
            errors.append(
                f"{label} manifest name must remain {PLUGIN_NAME!r}; got {payload.get('name')!r}"
            )
    reference_label = next(iter(MANIFEST_PATHS))
    reference = manifests[reference_label]
    for label, payload in manifests.items():
        if label == reference_label:
            continue
        for field in SHARED_MANIFEST_FIELDS:
            if payload.get(field) != reference.get(field):
                errors.append(
                    f"{label} manifest {field!r} must match {reference_label}; "
                    f"got {payload.get(field)!r}"
                )


def verify_marketplaces(root: Path, errors: list[str]) -> None:
    claude_marketplace = load_json(root / CLAUDE_MARKETPLACE, errors)
    factory_marketplace = load_json(root / FACTORY_MARKETPLACE, errors)
    if claude_marketplace is None or factory_marketplace is None:
        return

    claude_entry = find_plugin_entry(claude_marketplace, "Claude", errors)
    factory_entry = find_plugin_entry(factory_marketplace, "Factory", errors)
    if claude_entry is None or factory_entry is None:
        return

    claude_root = resolve_local_path(root, claude_entry.get("source"), "Claude plugin source", errors)
    factory_root = resolve_local_path(
        root, factory_entry.get("source"), "Factory plugin source", errors
    )

    if claude_root is not None and not (claude_root / MANIFEST_PATHS["Claude"]).is_file():
        errors.append("Claude marketplace target does not contain .claude-plugin/plugin.json")
    if factory_root is not None and not (factory_root / MANIFEST_PATHS["Factory"]).is_file():
        errors.append("Factory marketplace target does not contain .factory-plugin/plugin.json")

    plugin_roots = [
        plugin_root
        for plugin_root in (claude_root, factory_root)
        if plugin_root is not None
    ]
    if plugin_roots and any(plugin_root != plugin_roots[0] for plugin_root in plugin_roots[1:]):
        errors.append("Claude and Factory marketplaces must package the same plugin root")

    plugin_root = claude_root or factory_root
    if plugin_root is None:
        return
    skill = plugin_root / "skills" / PLUGIN_NAME / "SKILL.md"
    if not skill.is_file():
        errors.append(f"packaged skill is missing: {skill.relative_to(root)}")
    commands = plugin_root / "commands"
    if not commands.is_dir() or not any(commands.glob("*.md")):
        errors.append("packaged commands are missing from the shared plugin root")


def has_forbidden_yaml_codepoint(value: str) -> bool:
    """Reject code points outside YAML 1.2's printable character ranges."""
    def allowed(char: str) -> bool:
        codepoint = ord(char)
        # Codex's serde_yaml/libyaml stack treats these YAML-printable code
        # points as line breaks. Reject them to keep this dependency-free
        # verifier and every supported host on the same document structure.
        if codepoint in {0x85, 0x2028, 0x2029}:
            return False
        return (
            0x20 <= codepoint <= 0x7E
            or 0xA0 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        )

    return any(not allowed(char) for char in value)


def has_forbidden_yaml_source_codepoint(value: str) -> bool:
    """Validate a complete YAML document before line splitting consumes controls."""
    return any(
        char not in "\r\n" and has_forbidden_yaml_codepoint(char)
        for char in value
    )


def split_yaml_lines(value: str) -> list[str]:
    """Split only YAML structural CR/LF breaks, never printable Unicode content."""
    return re.split(r"\r\n|\r|\n", value)


def valid_plain_yaml_scalar(value: str) -> bool:
    """Accept only the conservative single-line plain-scalar subset we emit."""
    if not value or has_forbidden_yaml_codepoint(value):
        return False
    if value[0] in "-?:,[]{}#&*!|>'\"%@`":
        return False
    if value in {"~", "null", "Null", "NULL"}:
        return False
    if any(char in value for char in "[]{}") or re.search(r":(?:\s|$)", value):
        return False
    return True


def is_quoted_yaml_string(value: str) -> bool:
    """Return true only for the quoted scalar forms this strict subset accepts."""
    if value.startswith('"'):
        return valid_double_quoted_yaml_scalar(value)
    if value.startswith("'"):
        return (
            re.fullmatch(r"'[^']*'(?:\s+#.*)?", value) is not None
            and not has_forbidden_yaml_codepoint(value)
        )
    return False


def valid_double_quoted_yaml_scalar(value: str) -> bool:
    """Validate escapes in the single-line double-quoted YAML subset we accept."""
    match = re.fullmatch(r'"(?P<body>(?:[^"\\]|\\.)*)"(?:\s+#.*)?', value)
    if match is None:
        return False
    body = match.group("body")
    if has_forbidden_yaml_codepoint(body):
        return False
    simple_escapes = set('0abtnvfre "\\/N_LP')
    index = 0
    while index < len(body):
        if body[index] != "\\":
            index += 1
            continue
        index += 1
        escape = body[index]
        if escape in simple_escapes:
            index += 1
            continue
        digits = {"x": 2, "u": 4, "U": 8}.get(escape)
        if digits is None:
            return False
        encoded = body[index + 1 : index + 1 + digits]
        if len(encoded) != digits or re.fullmatch(r"[0-9A-Fa-f]+", encoded) is None:
            return False
        codepoint = int(encoded, 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return False
        index += 1 + digits
    return True


def parse_frontmatter_metadata_version(text: str) -> str | None:
    """Return exactly one direct ``metadata.version`` from YAML frontmatter.

    This intentionally does not search the Markdown body: examples and prose
    are allowed to mention ``version:``, but they cannot validate package
    metadata. The dependency-free parser accepts the mapping subset this
    skill's frontmatter uses and fails closed on malformed structure, duplicate
    keys, collection syntax, or malformed scalar values instead of guessing.
    """
    if has_forbidden_yaml_source_codepoint(text):
        return None
    lines = split_yaml_lines(text)
    if not lines or lines[0] != "---":
        return None
    try:
        closing = next(
            index for index in range(1, len(lines)) if lines[index] == "---"
        )
    except StopIteration:
        return None

    entries: list[tuple[tuple[str, ...], str, str]] = []
    open_mappings: list[tuple[int, str]] = []
    seen_keys: dict[tuple[str, ...], set[str]] = {}
    previous_indent = -1
    previous_opened_mapping = False

    for raw_line in lines[1:closing]:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            return None
        match = re.fullmatch(
            r"(?P<indent> *)(?P<key>[A-Za-z_][\w.-]*):(?P<value>(?: +.*)?)",
            raw_line,
        )
        if match is None:
            return None

        indent = len(match.group("indent"))
        if previous_indent >= 0 and indent > previous_indent and not previous_opened_mapping:
            return None
        while open_mappings and indent <= open_mappings[-1][0]:
            open_mappings.pop()
        if indent > 0 and not open_mappings:
            return None

        parent = tuple(key for _, key in open_mappings)
        key = match.group("key")
        value = match.group("value").strip()
        duplicate = key in seen_keys.setdefault(parent, set())
        seen_keys[parent].add(key)
        # Count metadata.version rows below even when one value is malformed;
        # all other duplicate mapping keys are immediately ambiguous.
        if duplicate and not (parent == ("metadata",) and key == "version"):
            return None

        opens_mapping = not value or value.startswith("#")
        is_metadata_version = parent == ("metadata",) and key == "version"
        if not opens_mapping and not is_metadata_version:
            # Flow collections and block scalars are not used by this package's
            # frontmatter. Rejecting them keeps malformed brackets or multiline
            # YAML from being mistaken for valid metadata without a YAML runtime.
            if value[0] in "[{|>" or any(char in value for char in "[]{}"):
                return None
            if value[0] == '"':
                if not valid_double_quoted_yaml_scalar(value):
                    return None
            elif value[0] == "'":
                if (
                    re.fullmatch(r"'[^']*'(?:\s+#.*)?", value) is None
                    or has_forbidden_yaml_codepoint(value)
                ):
                    return None
            elif not valid_plain_yaml_scalar(value):
                return None

        entries.append((parent, key, value))
        if opens_mapping:
            open_mappings.append((indent, key))
        previous_indent = indent
        previous_opened_mapping = opens_mapping

    metadata_rows = [entry for entry in entries if entry[0] == () and entry[1] == "metadata"]
    if len(metadata_rows) != 1 or metadata_rows[0][2]:
        return None

    version_rows = [
        value
        for parent, key, value in entries
        if parent == ("metadata",) and key == "version"
    ]
    if len(version_rows) != 1:
        return None
    match = re.fullmatch(
        r"(?:\"([^\"]+)\"|'([^']+)'|([0-9]+(?:\.[0-9]+)*))\s*(?:#.*)?",
        version_rows[0],
    )
    if match is None:
        return None
    return next(value for value in match.groups() if value is not None)


def verify_skill_metadata_version(root: Path, manifests: dict[str, dict[str, Any]], errors: list[str]) -> None:
    """SKILL.md's metadata version must track the manifests' MAJOR.MINOR.

    The manifests are bumped by a script; SKILL.md's `metadata.version` is
    hand-maintained, so a minor release drifts silently - 2.4 sat against
    2.5.0 manifests until a post-merge sweep caught it. Nothing else reads
    both numbers, so nothing else can notice.
    """
    skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
    if not skill.is_file():
        errors.append(f"missing {skill.relative_to(root).as_posix()}")
        return
    declared = parse_frontmatter_metadata_version(skill.read_text(encoding="utf-8"))
    if declared is None:
        errors.append("SKILL.md frontmatter has no metadata.version")
        return
    manifest_version = str(manifests["Claude"].get("version", ""))
    expected = ".".join(manifest_version.split(".")[:2])
    if declared != expected:
        errors.append(
            f"SKILL.md metadata.version {declared!r} must track the manifest "
            f"MAJOR.MINOR {expected!r} (manifests are at {manifest_version})"
        )


def parse_simple_yaml_paths(
    text: str, *, frontmatter: bool
) -> dict[tuple[str, ...], str] | None:
    """Parse the small nested-mapping YAML subset used by invocation metadata."""
    if has_forbidden_yaml_source_codepoint(text):
        return None
    lines = split_yaml_lines(text)
    if frontmatter:
        if not lines or lines[0] != "---":
            return None
        try:
            closing = next(
                index for index in range(1, len(lines)) if lines[index] == "---"
            )
        except StopIteration:
            return None
        lines = lines[1:closing]

    paths: dict[tuple[str, ...], str] = {}
    parents: list[tuple[int, str]] = []
    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            return None
        match = re.fullmatch(
            r"(?P<indent> *)(?P<key>[A-Za-z_][\w.-]*):(?P<value>(?: +.*)?)",
            raw_line,
        )
        if match is None:
            return None
        indent = len(match.group("indent"))
        while parents and indent <= parents[-1][0]:
            parents.pop()
        if indent and (not parents or indent != parents[-1][0] + 2):
            return None
        if not indent and parents:
            return None

        key = match.group("key")
        path = tuple(parent for _, parent in parents) + (key,)
        if path in paths:
            return None
        value = match.group("value").strip()
        if value:
            if value[0] == '"':
                if not valid_double_quoted_yaml_scalar(value):
                    return None
            elif value[0] == "'":
                if (
                    re.fullmatch(r"'[^']*'(?:\s+#.*)?", value) is None
                    or has_forbidden_yaml_codepoint(value)
                ):
                    return None
            elif not valid_plain_yaml_scalar(value):
                return None
        paths[path] = value
        if not value:
            parents.append((indent, key))
    return paths


def verify_explicit_invocation_policy(root: Path, errors: list[str]) -> None:
    """Keep the packaged skill user-invoked across generic and Codex runtimes."""
    skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
    try:
        skill_text = skill.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"could not read {skill.relative_to(root)}: {exc}")
        return

    skill_paths = parse_simple_yaml_paths(skill_text, frontmatter=True)
    if skill_paths is None:
        errors.append("SKILL.md frontmatter is not valid invocation-policy YAML")
        return
    if skill_paths.get(("disable-model-invocation",)) != "true":
        errors.append(
            "SKILL.md must declare disable-model-invocation: true in frontmatter"
        )
    if skill_paths.get(("user-invocable",)) != "true":
        errors.append("SKILL.md must declare user-invocable: true in frontmatter")
    if skill_paths.get(("metadata", "invocation")) != "manual":
        errors.append(
            "SKILL.md metadata.invocation must be manual"
        )
    description = skill_paths.get(("description",), "")
    if not description:
        errors.append("SKILL.md frontmatter description must be non-empty")
    elif len(description) > MAX_SKILL_DESCRIPTION_CHARS:
        errors.append(
            "SKILL.md frontmatter description must stay compact for selectors "
            f"and host compatibility (maximum {MAX_SKILL_DESCRIPTION_CHARS} characters)"
        )

    openai_path = root / OPENAI_SKILL_METADATA
    try:
        openai_text = openai_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"could not read {OPENAI_SKILL_METADATA}: {exc}")
        return
    openai_paths = parse_simple_yaml_paths(openai_text, frontmatter=False)
    if openai_paths is None:
        errors.append(f"{OPENAI_SKILL_METADATA} is not valid invocation-policy YAML")
        return
    actual_paths = frozenset(openai_paths)
    if actual_paths != OPENAI_METADATA_PATHS:
        unexpected = sorted(".".join(path) for path in actual_paths - OPENAI_METADATA_PATHS)
        missing = sorted(".".join(path) for path in OPENAI_METADATA_PATHS - actual_paths)
        errors.append(
            f"{OPENAI_SKILL_METADATA} must use the exact minimal metadata schema; "
            f"unexpected={unexpected}, missing={missing}"
        )
    for field in ("display_name", "short_description"):
        value = openai_paths.get(("interface", field), "")
        if not value or not is_quoted_yaml_string(value):
            errors.append(
                f"{OPENAI_SKILL_METADATA} interface.{field} must be a quoted scalar string"
            )
    if openai_paths.get(("policy", "allow_implicit_invocation")) != "false":
        errors.append(
            f"{OPENAI_SKILL_METADATA} policy.allow_implicit_invocation must be false"
        )
    default_prompt = openai_paths.get(("interface", "default_prompt"), "")
    if not default_prompt.startswith(f'"${PLUGIN_NAME} '):
        errors.append(
            f"{OPENAI_SKILL_METADATA} interface.default_prompt must explicitly "
            f"invoke ${PLUGIN_NAME}"
        )


def verify_direct_codex_distribution(root: Path, errors: list[str]) -> None:
    """Prevent reintroducing a native plugin that conflicts with the shared flag."""
    for relative in FORBIDDEN_NATIVE_CODEX_PATHS:
        candidate = root / relative
        if candidate.exists() or candidate.is_symlink():
            errors.append(
                f"{relative} must remain absent; Codex consumes the canonical "
                "skill directly so Claude/Pi user-only metadata stays intact"
            )


def verify_maintainer_version_policy(root: Path, errors: list[str]) -> None:
    """Keep automated maintainer versioning aligned with the package gate."""
    policy = load_json(root / MAINTAINER_POLICY, errors)
    if policy is None:
        return
    versioning = policy.get("versioning")
    entries = versioning.get("manifests") if isinstance(versioning, dict) else None
    if not isinstance(entries, list):
        errors.append(f"{MAINTAINER_POLICY} versioning.manifests must be an array")
        return
    actual = {
        entry.get("path")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    expected = {relative.as_posix() for relative in MANIFEST_PATHS.values()}
    if actual != expected or len(entries) != len(expected):
        errors.append(
            f"{MAINTAINER_POLICY} versioning manifests must match the package gate; "
            f"expected={sorted(expected)}, actual={sorted(actual)}"
        )
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("json_pointer") != "/version":
            errors.append(
                f"{MAINTAINER_POLICY} every versioning manifest must target /version"
            )


def verify_controlled_fork_origin(
    root: Path, manifests: dict[str, dict[str, Any]], errors: list[str]
) -> None:
    """Keep every install surface pinned to the reviewed fork identity."""
    for label, manifest in manifests.items():
        if manifest.get("repository") != CONTROLLED_REPOSITORY_URL:
            errors.append(
                f"{label} manifest repository must remain {CONTROLLED_REPOSITORY_URL!r}"
            )

    policy = load_json(root / MAINTAINER_POLICY, errors)
    if policy is not None and policy.get("repository") != CONTROLLED_REPOSITORY:
        errors.append(
            f"{MAINTAINER_POLICY} repository must remain {CONTROLLED_REPOSITORY!r}"
        )

    readme_path = root / "README.md"
    try:
        readme = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"could not read {readme_path}: {exc}")
        return
    required_install_surfaces = (
        f"/plugin marketplace add {CONTROLLED_REPOSITORY}",
        f"git clone {CONTROLLED_REPOSITORY_URL}.git",
        "~/.agents/skills/diagram-design",
        "$HOME\\.agents\\skills\\diagram-design",
        f"droid plugin marketplace add {CONTROLLED_REPOSITORY_URL}",
        f"pi install {CONTROLLED_REPOSITORY_URL}",
        f"git clone git@github.com:{CONTROLLED_REPOSITORY}.git",
    )
    for command in required_install_surfaces:
        if command not in readme:
            errors.append(
                f"README controlled-fork install surface is missing {command!r}"
            )


def verify_package(root: Path, base_ref: str) -> list[str]:
    errors: list[str] = []
    manifests: dict[str, dict[str, Any]] = {}
    for label, relative in MANIFEST_PATHS.items():
        payload = load_json(root / relative, errors)
        if payload is not None:
            manifests[label] = payload

    if len(manifests) == len(MANIFEST_PATHS):
        verify_versions(root, base_ref, manifests, errors)
        verify_manifest_identity(manifests, errors)
        verify_skill_metadata_version(root, manifests, errors)
        verify_explicit_invocation_policy(root, errors)
        verify_controlled_fork_origin(root, manifests, errors)
    verify_direct_codex_distribution(root, errors)
    verify_maintainer_version_policy(root, errors)
    verify_marketplaces(root, errors)
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} <base-ref>", file=sys.stderr)
        return 2
    base_ref = sys.argv[1]
    errors = verify_package(ROOT, base_ref)
    if errors:
        print("FAIL plugin package")
        for error in errors:
            print(f"  - {error}")
        return 1
    versions = load_json(ROOT / MANIFEST_PATHS["Claude"], [])["version"]
    print(
        f"OK plugin package: Claude and Factory {versions}, "
        f"marketplace paths, and packaged skill"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
