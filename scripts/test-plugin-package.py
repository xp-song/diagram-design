#!/usr/bin/env python3
"""Regression tests for plugin versioning and marketplace package verification."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = ROOT / "scripts/verify-plugin-package.py"
BUMP_SCRIPT = ROOT / "scripts/bump-plugin-version.py"
PLUGIN_NAME = "diagram-design"


def load_module(name: str, path: Path) -> ModuleType:
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load_module("verify_plugin_package", VERIFY_SCRIPT)
BUMP = load_module("bump_plugin_version", BUMP_SCRIPT)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def manifest(version: str) -> dict:
    return {
        "name": PLUGIN_NAME,
        "description": "Create editorial diagrams.",
        "version": version,
        "author": {"name": "Cathryn Lavery"},
        "repository": VERIFY.CONTROLLED_REPOSITORY_URL,
    }


def seed_factory(root: Path, version: str) -> None:
    write_json(root / ".factory-plugin/plugin.json", manifest(version))
    write_json(
        root / ".factory-plugin/marketplace.json",
        {
            "name": PLUGIN_NAME,
            "plugins": [{"name": PLUGIN_NAME, "source": "./"}],
        },
    )


def seed_package(
    root: Path, version: str = "1.2.3", include_factory: bool = True
) -> None:
    write_json(root / ".claude-plugin/plugin.json", manifest(version))
    if include_factory:
        seed_factory(root, version)
    write_json(
        root / ".claude-plugin/marketplace.json",
        {
            "name": PLUGIN_NAME,
            "plugins": [{"name": PLUGIN_NAME, "source": "./"}],
        },
    )
    write_json(
        root / ".maintainer-policy.json",
        {
            "repository": VERIFY.CONTROLLED_REPOSITORY,
            "versioning": {
                "manifests": [
                    {"path": ".claude-plugin/plugin.json", "json_pointer": "/version"},
                    {"path": ".factory-plugin/plugin.json", "json_pointer": "/version"},
                ]
            }
        },
    )
    (root / "README.md").write_text(
        f"/plugin marketplace add {VERIFY.CONTROLLED_REPOSITORY}\n"
        f"git clone {VERIFY.CONTROLLED_REPOSITORY_URL}.git\n"
        "~/.agents/skills/diagram-design\n"
        "$HOME\\.agents\\skills\\diagram-design\n"
        f"droid plugin marketplace add {VERIFY.CONTROLLED_REPOSITORY_URL}\n"
        f"pi install {VERIFY.CONTROLLED_REPOSITORY_URL}\n"
        f"git clone git@github.com:{VERIFY.CONTROLLED_REPOSITORY}.git\n",
        encoding="utf-8",
    )
    write_skill(root, version)
    command = root / "commands" / "export-diagram.md"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text("Export the diagram.\n", encoding="utf-8")


def write_skill(root: Path, version: str) -> None:
    """Fixture SKILL.md, whose metadata.version tracks the manifest MAJOR.MINOR.

    The verifier gates that relationship, so a fixture that omits it is not a
    valid package and would only prove the gate can be tripped by its own
    test data.
    """
    skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    minor = ".".join(version.split(".")[:2])
    skill.write_text(
        f'---\nname: {PLUGIN_NAME}\ndescription: Create editorial diagrams.\n'
        f'disable-model-invocation: true\n'
        f'user-invocable: true\n'
        f'metadata:\n  invocation: manual\n  version: "{minor}"\n---\n',
        encoding="utf-8",
    )
    openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
    openai.parent.mkdir(parents=True, exist_ok=True)
    openai.write_text(
        'interface:\n  display_name: "Diagram Design"\n'
        '  short_description: "Create a diagram"\n'
        '  default_prompt: "$diagram-design Create a diagram."\n'
        "policy:\n  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )


@contextmanager
def package_repo(include_factory: bool = True) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        seed_package(root, include_factory=include_factory)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Package Test"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "package-test@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base package"], cwd=root, check=True)
        yield root


def set_versions(root: Path, claude: str, factory: str) -> None:
    for relative, version in (
        (Path(".claude-plugin/plugin.json"), claude),
        (Path(".factory-plugin/plugin.json"), factory),
    ):
        payload = json.loads((root / relative).read_text(encoding="utf-8"))
        payload["version"] = version
        write_json(root / relative, payload)
    # Keep the fixture's SKILL.md in step with the Claude manifest, so these
    # cases exercise the version rules they are about rather than tripping the
    # metadata-drift gate every time.
    write_skill(root, claude)


def set_existing_versions(root: Path, version: str) -> None:
    for relative in VERIFY.MANIFEST_PATHS.values():
        path = root / relative
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["version"] = version
        write_json(path, payload)


def expect_failure(label: str, errors: list[str], needle: str) -> None:
    if not any(needle in error for error in errors):
        raise AssertionError(f"{label}: expected {needle!r}, got {errors}")
    print(f"OK: {label} rejected")


def test_verifier() -> None:
    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        errors = VERIFY.verify_package(root, "HEAD")
        if errors:
            raise AssertionError(f"valid bump failed: {errors}")
        print("OK: valid synchronized bump accepted")

    for relative in VERIFY.FORBIDDEN_NATIVE_CODEX_PATHS:
        with package_repo() as root:
            set_versions(root, "1.2.4", "1.2.4")
            forbidden = root / relative
            forbidden.parent.mkdir(parents=True, exist_ok=True)
            forbidden.write_text("{}\n", encoding="utf-8")
            expect_failure(
                f"native Codex surface {relative}",
                VERIFY.verify_package(root, "HEAD"),
                "must remain absent",
            )

    for relative in VERIFY.FORBIDDEN_NATIVE_CODEX_PATHS:
        with package_repo() as root:
            set_versions(root, "1.2.4", "1.2.4")
            forbidden = root / relative
            forbidden.parent.mkdir(parents=True, exist_ok=True)
            try:
                forbidden.symlink_to(root / "missing-native-codex-target")
            except OSError:
                print(f"SKIP: broken-symlink gate unavailable for {relative}")
                continue
            expect_failure(
                f"broken-symlink native Codex surface {relative}",
                VERIFY.verify_package(root, "HEAD"),
                "must remain absent",
            )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        policy_path = root / VERIFY.MAINTAINER_POLICY
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["versioning"]["manifests"][1]["path"] = ".codex-plugin/plugin.json"
        write_json(policy_path, policy)
        expect_failure(
            "maintainer version-policy drift",
            VERIFY.verify_package(root, "HEAD"),
            "versioning manifests must match the package gate",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        policy_path = root / VERIFY.MAINTAINER_POLICY
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["versioning"]["manifests"][1]["json_pointer"] = "/release"
        write_json(policy_path, policy)
        expect_failure(
            "maintainer version-pointer drift",
            VERIFY.verify_package(root, "HEAD"),
            "must target /version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        for relative in VERIFY.MANIFEST_PATHS.values():
            path = root / relative
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["repository"] = "https://github.com/example/diagram-design"
            write_json(path, payload)
        policy_path = root / VERIFY.MAINTAINER_POLICY
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["repository"] = "example/diagram-design"
        write_json(policy_path, policy)
        readme = root / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8").replace("xp-song", "example"),
            encoding="utf-8",
        )
        expect_failure(
            "coordinated controlled-fork origin drift",
            VERIFY.verify_package(root, "HEAD"),
            "must remain 'https://github.com/xp-song/diagram-design'",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "disable-model-invocation: true",
                "disable-model-invocation: false",
            ),
            encoding="utf-8",
        )
        expect_failure(
            "implicit generic invocation",
            VERIFY.verify_package(root, "HEAD"),
            "disable-model-invocation: true",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
        openai.write_text(
            'interface:\n  display_name: "Diagram Design"\n'
            '  short_description: "Create a diagram"\n'
            '  default_prompt: "$diagram-design Create a diagram."\n'
            "policy:\n  allow_implicit_invocation: true\n",
            encoding="utf-8",
        )
        expect_failure(
            "implicit Codex invocation",
            VERIFY.verify_package(root, "HEAD"),
            "policy.allow_implicit_invocation must be false",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
        openai.write_text(
            'interface:\n  display_name: "unterminated\n'
            "policy:\n  allow_implicit_invocation: false\n",
            encoding="utf-8",
        )
        expect_failure(
            "malformed Codex policy YAML",
            VERIFY.verify_package(root, "HEAD"),
            "not valid invocation-policy YAML",
        )

    for label, separator in (
        ("YAML next-line character", "\x85"),
        ("Unicode line separator", "\u2028"),
        ("Unicode paragraph separator", "\u2029"),
    ):
        with package_repo() as root:
            set_versions(root, "1.2.4", "1.2.4")
            openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
            openai.write_text(
                'interface:' + separator
                + '  default_prompt: "$diagram-design Create a diagram."\n'
                + "policy:\n  allow_implicit_invocation: false\n",
                encoding="utf-8",
            )
            expect_failure(
                label,
                VERIFY.verify_package(root, "HEAD"),
            "not valid invocation-policy YAML",
        )

    for label, escaped_name in (
        ("escaped UTF-16 high surrogate", r'"Review \uD800 Skill"'),
        ("escaped UTF-16 surrogate pair", r'"Review \uD83D\uDE00 Skill"'),
    ):
        with package_repo() as root:
            set_versions(root, "1.2.4", "1.2.4")
            openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
            source = openai.read_text(encoding="utf-8").replace(
                'display_name: "Diagram Design"',
                f"display_name: {escaped_name}",
            )
            openai.write_text(source, encoding="utf-8")
            expect_failure(
                label,
                VERIFY.verify_package(root, "HEAD"),
                "not valid invocation-policy YAML",
            )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
        source = openai.read_text(encoding="utf-8").replace(
            '  display_name: "Diagram Design"\n',
            "  display_name:\n    nested: invalid\n",
        )
        openai.write_text(source, encoding="utf-8")
        expect_failure(
            "mapping-valued OpenAI display name",
            VERIFY.verify_package(root, "HEAD"),
            "interface.display_name must be a quoted scalar string",
        )

    for field, malformed in (
        ("display_name", "false"),
        ("display_name", "123"),
        ("display_name", "1.25"),
        ("short_description", "false"),
        ("short_description", "123"),
        ("short_description", "1.25"),
    ):
        with package_repo() as root:
            set_versions(root, "1.2.4", "1.2.4")
            openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
            source = openai.read_text(encoding="utf-8")
            source = re.sub(
                rf"^(  {field}:).*$",
                rf"\1 {malformed}",
                source,
                count=1,
                flags=re.MULTILINE,
            )
            openai.write_text(source, encoding="utf-8")
            expect_failure(
                f"non-string OpenAI {field} {malformed}",
                VERIFY.verify_package(root, "HEAD"),
                f"interface.{field} must be a quoted scalar string",
            )

    for label, mutate in (
        (
            "scalar Codex dependencies",
            lambda source: source + "dependencies: false\n",
        ),
        (
            "scalar Codex policy products",
            lambda source: source.replace(
                "policy:\n", "policy:\n  products: false\n", 1
            ),
        ),
        (
            "mapping-valued optional Codex interface field",
            lambda source: source.replace(
                "  default_prompt:",
                "  icon_small:\n    nested: invalid\n  default_prompt:",
                1,
            ),
        ),
    ):
        with package_repo() as root:
            set_versions(root, "1.2.4", "1.2.4")
            openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
            openai.write_text(
                mutate(openai.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            expect_failure(
                label,
                VERIFY.verify_package(root, "HEAD"),
                "exact minimal metadata schema",
            )

    for separator_name, separator in (
        ("next-line", "\x85"),
        ("line-separator", "\u2028"),
        ("paragraph-separator", "\u2029"),
    ):
        for surface, placement in (
            ("openai", "full-line-comment"),
            ("openai", "inline-comment"),
            ("skill", "full-line-comment"),
            ("skill", "inline-comment"),
        ):
            with package_repo() as root:
                set_versions(root, "1.2.4", "1.2.4")
                if surface == "openai":
                    target = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
                    source = target.read_text(encoding="utf-8")
                    if placement == "full-line-comment":
                        source += (
                            f"# ignored{separator}policy:{separator}"
                            "  allow_implicit_invocation: true\n"
                        )
                    else:
                        source = source.replace(
                            'Create a diagram."',
                            f'Create a diagram." # ignored{separator}policy:'
                            f'{separator}  allow_implicit_invocation: true',
                        )
                else:
                    target = root / "skills" / PLUGIN_NAME / "SKILL.md"
                    source = target.read_text(encoding="utf-8")
                    hidden = (
                        f"# ignored{separator}disable-model-invocation: false"
                        f"{separator}user-invocable: false\n"
                    )
                    if placement == "full-line-comment":
                        source = source.replace("metadata:\n", hidden + "metadata:\n", 1)
                    else:
                        source = source.replace(
                            "description: Create editorial diagrams.\n",
                            "description: Create editorial diagrams. "
                            + hidden,
                            1,
                        )
                target.write_text(source, encoding="utf-8")
                expect_failure(
                    f"{surface} {placement} {separator_name}",
                    VERIFY.verify_package(root, "HEAD"),
                    "not valid invocation-policy YAML",
                )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\ndescription: Create editorial diagrams.\n'
            'disable-model-invocation: true\nuser-invocable: true\n'
            'metadata:\n  invocation: manual\n  version: "1.2"\n'
            '  ---\ndisable-model-invocation: false\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "indented frontmatter delimiter",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    for label, malformed in (
        ("comment-only scalar", "description: # missing\n"),
        ("undefined YAML alias", "description: *missing\n"),
        ("explicit YAML null", "description: null\n"),
        ("leading comma scalar", "description: ,broken\n"),
        ("leading sequence indicator", "description: - broken\n"),
        ("trailing colon scalar", "description: broken:\n"),
        ("raw control in quoted scalar", 'description: "bad\x01value"\n'),
        ("raw C1 control scalar", 'description: "bad\x80value"\n'),
        ("raw noncharacter scalar", 'description: "bad\ufffevalue"\n'),
        ("record separator before line split", "description: valid\x1c"),
    ):
        with package_repo() as root:
            set_versions(root, "1.2.4", "1.2.4")
            openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
            openai.write_text(
                "interface:\n  " + malformed
                + "policy:\n  allow_implicit_invocation: false\n",
                encoding="utf-8",
            )
            expect_failure(
                label,
                VERIFY.verify_package(root, "HEAD"),
                "not valid invocation-policy YAML",
            )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
        openai.write_text(
            'interface:\n  default_prompt: "Create a diagram."\n'
            "policy:\n  allow_implicit_invocation: false\n",
            encoding="utf-8",
        )
        expect_failure(
            "implicit OpenAI skill starter prompt",
            VERIFY.verify_package(root, "HEAD"),
            "interface.default_prompt must explicitly invoke $diagram-design",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "description: Create editorial diagrams.",
                "description: " + ("long " * 40).strip(),
            ),
            encoding="utf-8",
        )
        expect_failure(
            "long runtime skill description",
            VERIFY.verify_package(root, "HEAD"),
            "must stay compact",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "disable-model-invocation: true\nuser-invocable: true\n",
                "",
            )
            + "\ndisable-model-invocation: true\nuser-invocable: true\n",
            encoding="utf-8",
        )
        expect_failure(
            "policy outside frontmatter",
            VERIFY.verify_package(root, "HEAD"),
            "disable-model-invocation: true in frontmatter",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "  invocation: manual", "  invocation: auto"
            ),
            encoding="utf-8",
        )
        expect_failure(
            "automatic metadata invocation",
            VERIFY.verify_package(root, "HEAD"),
            "metadata.invocation must be manual",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "user-invocable: true", "user-invocable: false"
            ),
            encoding="utf-8",
        )
        expect_failure(
            "disabled user invocation",
            VERIFY.verify_package(root, "HEAD"),
            "user-invocable: true",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        openai = root / "skills" / PLUGIN_NAME / "agents/openai.yaml"
        openai.write_text(
            "not_policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
        )
        expect_failure(
            "Codex policy under wrong parent",
            VERIFY.verify_package(root, "HEAD"),
            "policy.allow_implicit_invocation must be false",
        )

    with package_repo() as root:
        expect_failure(
            "missing bump",
            VERIFY.verify_package(root, "HEAD"),
            "must increase",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.5")
        expect_failure(
            "mismatched manifests",
            VERIFY.verify_package(root, "HEAD"),
            "versions must match",
        )

    with package_repo() as root:
        set_versions(root, "1.2", "1.2")
        expect_failure(
            "malformed versions",
            VERIFY.verify_package(root, "HEAD"),
            "strict MAJOR.MINOR.PATCH",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        marketplace_path = root / ".factory-plugin/marketplace.json"
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        marketplace["plugins"][0]["source"] = {
            "source": "local",
            "path": "./",
        }
        write_json(marketplace_path, marketplace)
        expect_failure(
            "non-native Factory marketplace source",
            VERIFY.verify_package(root, "HEAD"),
            "must be a non-empty local path string",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        (root / "commands/export-diagram.md").unlink()
        expect_failure(
            "missing shared command surface",
            VERIFY.verify_package(root, "HEAD"),
            "packaged commands are missing",
        )

    with package_repo(include_factory=False) as root:
        set_existing_versions(root, "1.2.4")
        seed_factory(root, "1.2.4")
        errors = VERIFY.verify_package(root, "HEAD")
        if errors:
            raise AssertionError(f"valid Factory bootstrap failed: {errors}")
        print("OK: synchronized Factory bootstrap accepted")

    with package_repo(include_factory=False) as root:
        seed_factory(root, "1.2.3")
        expect_failure(
            "Factory bootstrap without package bump",
            VERIFY.verify_package(root, "HEAD"),
            "must increase",
        )

    with package_repo(include_factory=False) as root:
        set_existing_versions(root, "1.2.4")
        seed_factory(root, "1.2.4")
        factory_path = root / ".factory-plugin/plugin.json"
        factory = json.loads(factory_path.read_text(encoding="utf-8"))
        factory["description"] = "Drifted Factory description."
        write_json(factory_path, factory)
        expect_failure(
            "Factory bootstrap with manifest drift",
            VERIFY.verify_package(root, "HEAD"),
            "must match Claude",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        (root / ".factory-plugin/plugin.json").unlink()
        expect_failure(
            "Factory manifest deletion",
            VERIFY.verify_package(root, "HEAD"),
            "could not read",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\n---\n\nExample only:\nversion: "1.2"\n',
            encoding="utf-8",
        )
        expect_failure(
            "version line outside frontmatter",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\nversion: "1.2"\nmetadata:\n  owner: test\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "version outside metadata mapping",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\nmetadata:\n  version: [broken\n'
            '  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "malformed version plus valid duplicate",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\nmetadata:\n  version: "1.2"\n'
            '  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "duplicate valid metadata versions",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\ndescription: [unterminated\n'
            'metadata:\n  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "malformed surrounding frontmatter",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\nmetadata:\n  version:"1.2"\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "version value without YAML separation",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\ndescription: broken: value\n'
            'metadata:\n  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "unquoted colon in frontmatter scalar",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\ndescription: "bad\\q"\n'
            'metadata:\n  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "invalid double-quoted YAML escape",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\ndescription: "line one\\nline two and '
            '\\"quoted\\" with unicode \\u2192"\n'
            'disable-model-invocation: true\nuser-invocable: true\n'
            'metadata:\n  invocation: manual\n'
            '  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        errors = VERIFY.verify_package(root, "HEAD")
        if errors:
            raise AssertionError(f"valid escaped YAML scalar failed: {errors}")
        print("OK: valid double-quoted YAML escapes accepted")

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\ndescription: "highest scalar \\U0010FFFF"\n'
            'disable-model-invocation: true\nuser-invocable: true\n'
            'metadata:\n  invocation: manual\n'
            '  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        errors = VERIFY.verify_package(root, "HEAD")
        if errors:
            raise AssertionError(f"maximum Unicode YAML escape failed: {errors}")
        print("OK: maximum Unicode YAML escape U+10FFFF accepted")

    with package_repo() as root:
        set_versions(root, "1.2.4", "1.2.4")
        skill = root / "skills" / PLUGIN_NAME / "SKILL.md"
        skill.write_text(
            f'---\nname: {PLUGIN_NAME}\ndescription: "out of range \\U00110000"\n'
            'metadata:\n  version: "1.2"\n---\n',
            encoding="utf-8",
        )
        expect_failure(
            "out-of-range Unicode YAML escape",
            VERIFY.verify_package(root, "HEAD"),
            "frontmatter has no metadata.version",
        )


def test_bumper() -> None:
    cases = (("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0"))
    for part, expected in cases:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            seed_package(root)
            actual = BUMP.bump(root, part)
            versions = {
                json.loads((root / relative).read_text(encoding="utf-8"))["version"]
                for relative in BUMP.MANIFEST_PATHS
            }
            if actual != expected or versions != {expected}:
                raise AssertionError(
                    f"{part} bump: expected {expected}, got {actual} and {versions}"
                )
            print(f"OK: {part} bump produced {expected}")

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        seed_package(root)
        set_versions(root, "1.2.3", "1.2.4")
        try:
            BUMP.bump(root)
        except BUMP.PackageVersionError as exc:
            if "not synchronized" not in str(exc):
                raise AssertionError(f"unexpected mismatch error: {exc}") from exc
        else:
            raise AssertionError("version bumper accepted mismatched manifests")
        print("OK: version bumper rejects mismatched manifests")


def main() -> int:
    test_verifier()
    test_bumper()
    print("All plugin package tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
