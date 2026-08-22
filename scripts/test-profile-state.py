#!/usr/bin/env python3
"""Adversarial tests for immutable profile and marker state operations."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "skills/diagram-design/scripts/profile_state.py"


def load_helper():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("diagram_design_profile_state", HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load profile_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect_state_error(helper, label: str, operation) -> None:
    try:
        operation()
    except helper.StateError:
        print(f"OK: {label} rejected")
        return
    raise AssertionError(f"{label} was not rejected")


def main() -> int:
    helper = load_helper()
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        print("SKIP: platform lacks required no-follow dir-fd primitives")
        return 0

    with tempfile.TemporaryDirectory(prefix="profile-state-") as temporary:
        root = Path(temporary)
        home = root / "home"
        project = root / "project"
        home.mkdir(mode=0o700)
        project.mkdir()

        ancestor_before = os.stat(root)
        (root / "unrelated-sibling").mkdir()
        ancestor_after = os.stat(root)
        if not helper._same_directory_identity(ancestor_before, ancestor_after):
            raise AssertionError("sibling churn changed stable directory identity")
        print("OK: unrelated ancestor churn preserves stable directory identity")

        body = b"# Style Guide\n\nSafe profile.\n"
        helper.write_profile("acme", body, home, create_only=True)
        if helper.read_profile("acme", home) != body:
            raise AssertionError("profile round trip changed bytes")
        if helper.list_profiles(home) != ["acme"]:
            raise AssertionError("safe profile listing failed")
        invalid_profile_name = home / ".diagram-design/profiles/not a slug.md"
        invalid_profile_name.write_bytes(b"ignored")
        listing = helper.inspect_profile_listing(home)
        if listing != {"profiles": ["acme"], "ignored": ["not a slug.md"]}:
            raise AssertionError(f"invalid profile listing was not reported: {listing!r}")
        invalid_profile_name.unlink()
        print("OK: invalid profile filenames are ignored and reported")
        cli_home = root / "cli-home"
        cli_home.mkdir(mode=0o700)
        cli_environment = os.environ.copy()
        cli_environment["HOME"] = str(cli_home)
        invalid_utf8 = subprocess.run(
            [sys.executable, str(HELPER), "profile-write", "invalid", "--create"],
            input=b"\xff",
            capture_output=True,
            check=False,
            env=cli_environment,
        )
        if invalid_utf8.returncode != 2 or b"valid UTF-8" not in invalid_utf8.stderr:
            raise AssertionError(
                "profile CLI did not reject invalid UTF-8 cleanly: "
                f"{invalid_utf8.returncode}, {invalid_utf8.stderr!r}"
            )
        if (cli_home / ".diagram-design/profiles/invalid.md").exists():
            raise AssertionError("profile CLI persisted invalid UTF-8")
        print("OK: profile CLI rejects invalid UTF-8 before mutation")
        cli_profiles = cli_home / ".diagram-design/profiles"
        cli_profiles.mkdir(parents=True, mode=0o700)
        invalid_profile = cli_profiles / "broken.md"
        invalid_profile.write_bytes(b"\xff")
        invalid_profile_read = subprocess.run(
            [sys.executable, str(HELPER), "profile-read", "broken"],
            capture_output=True,
            text=True,
            check=False,
            env=cli_environment,
        )
        invalid_profile_record = json.loads(invalid_profile_read.stdout)
        if (
            invalid_profile_read.returncode != 0
            or invalid_profile_record.get("encoding") != "invalid-utf-8"
            or invalid_profile_record.get("content") is not None
            or not invalid_profile_record.get("token")
        ):
            raise AssertionError(f"invalid profile did not return a recovery record: {invalid_profile_read}")
        recovered_profile = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "profile-write",
                "broken",
                "--expect",
                invalid_profile_record["token"],
            ],
            input=body,
            capture_output=True,
            check=False,
            env=cli_environment,
        )
        if recovered_profile.returncode != 0 or invalid_profile.read_bytes() != body:
            raise AssertionError("token-bound invalid profile recovery failed")

        invalid_marker_project = root / "invalid-marker-project"
        invalid_marker_project.mkdir()
        invalid_marker = invalid_marker_project / ".diagram-design"
        invalid_marker.write_bytes(b"\xff")
        invalid_marker_read = subprocess.run(
            [sys.executable, str(HELPER), "marker-read", str(invalid_marker_project)],
            capture_output=True,
            text=True,
            check=False,
        )
        invalid_marker_record = json.loads(invalid_marker_read.stdout)
        if (
            invalid_marker_read.returncode != 0
            or invalid_marker_record.get("encoding") != "invalid-utf-8"
            or invalid_marker_record.get("content") is not None
            or not invalid_marker_record.get("token")
        ):
            raise AssertionError(f"invalid marker did not return a recovery record: {invalid_marker_read}")
        recovered_marker = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "marker-write",
                str(invalid_marker_project),
                "acme",
                "--expect",
                invalid_marker_record["token"],
            ],
            capture_output=True,
            check=False,
        )
        if recovered_marker.returncode != 0 or invalid_marker.read_bytes() != b"profile: acme\n":
            raise AssertionError("token-bound invalid marker recovery failed")
        print("OK: invalid UTF-8 profile and marker state have token-bound recovery")
        print("OK: profile round trip is byte exact")

        profile_path = home / ".diagram-design/profiles/acme.md"
        sentinel = root / "sentinel"
        sentinel.write_bytes(b"sentinel")
        profile_path.unlink()
        profile_path.symlink_to(sentinel)
        expect_state_error(helper, "symlink profile read", lambda: helper.read_profile("acme", home))
        expect_state_error(
            helper,
            "symlink profile write",
            lambda: helper.write_profile("acme", body, home, create_only=True),
        )
        if sentinel.read_bytes() != b"sentinel":
            raise AssertionError("symlink target was modified")

        profile_path.unlink()
        os.link(sentinel, profile_path)
        expect_state_error(helper, "hard-link profile read", lambda: helper.read_profile("acme", home))
        profile_path.unlink()

        if hasattr(os, "mkfifo"):
            os.mkfifo(profile_path)
            expect_state_error(
                helper, "special-file profile read", lambda: helper.read_profile("acme", home)
            )
            profile_path.unlink()

        helper.write_marker(project, "acme", create_only=True)
        if helper.read_marker(project) != "profile: acme\n":
            raise AssertionError("marker round trip changed bytes")
        mode = os.lstat(project / ".diagram-design").st_mode
        if not stat.S_ISREG(mode):
            raise AssertionError("marker is not a regular file")
        print("OK: marker round trip is byte exact")
        if {path.name for path in project.iterdir()} != {".diagram-design"}:
            raise AssertionError("marker write persisted unexpected project bookkeeping")

        marker = project / ".diagram-design"
        marker.unlink()
        marker.symlink_to(sentinel)
        expect_state_error(helper, "symlink marker read", lambda: helper.read_marker(project))
        expect_state_error(
            helper,
            "symlink marker write",
            lambda: helper.write_marker(project, "acme", create_only=True),
        )
        if sentinel.read_bytes() != b"sentinel":
            raise AssertionError("marker symlink target was modified")

        marker.unlink()
        os.link(sentinel, marker)
        expect_state_error(helper, "hard-link marker read", lambda: helper.read_marker(project))
        marker.unlink()

        token_project = root / "token-project"
        token_project.mkdir()
        helper.write_marker(token_project, "acme", create_only=True)
        marker_record = helper.inspect_marker(token_project)
        stale_marker_token = str(marker_record["token"])
        token_marker = token_project / ".diagram-design"
        token_marker.write_text("profile: replacement\n", encoding="utf-8")
        expect_state_error(
            helper,
            "stale marker approval token",
            lambda: helper.write_marker(
                token_project, "acme", expected_token=stale_marker_token
            ),
        )
        if token_marker.read_text(encoding="utf-8") != "profile: replacement\n":
            raise AssertionError("stale-token rejection overwrote newer marker state")

        expect_state_error(
            helper,
            "installed-checkout marker",
            lambda: helper.write_marker(ROOT, "acme", create_only=True),
        )

        unsafe_home = root / "unsafe-home"
        unsafe_home.mkdir(mode=0o700)
        (unsafe_home / ".diagram-design").symlink_to(root / "outside")
        expect_state_error(
            helper,
            "symlink profile directory",
            lambda: helper.write_profile("acme", body, unsafe_home, create_only=True),
        )

        safe_home = root / "delete-home"
        safe_home.mkdir(mode=0o700)
        helper.write_profile("acme", body, safe_home, create_only=True)
        _data, approved = helper.inspect_profile("acme", safe_home)
        helper.delete_profile("acme", approved, safe_home)
        if helper.list_profiles(safe_home):
            raise AssertionError("profile delete left an entry")
        expect_state_error(
            helper,
            "default profile deletion",
            lambda: helper.delete_profile("default", "unused", safe_home),
        )

        token_home = root / "token-home"
        token_home.mkdir(mode=0o700)
        helper.write_profile("acme", body, token_home, create_only=True)
        _data, stale_token = helper.inspect_profile("acme", token_home)
        profile = token_home / ".diagram-design/profiles/acme.md"
        profile.write_bytes(b"replacement after approval")
        expect_state_error(
            helper,
            "stale profile approval token",
            lambda: helper.write_profile(
                "acme", body, token_home, expected_token=stale_token
            ),
        )
        if profile.read_bytes() != b"replacement after approval":
            raise AssertionError("stale-token rejection overwrote newer profile state")

        default_home = root / "default-home"
        default_home.mkdir(mode=0o700)
        helper.write_profile("default", body, default_home, create_only=True)
        _data, default_token = helper.inspect_profile("default", default_home)
        expect_state_error(
            helper,
            "default profile overwrite",
            lambda: helper.write_profile(
                "default", b"changed", default_home, expected_token=default_token
            ),
        )

        project_fd = helper._open_existing_directory(project.resolve(strict=True))
        checkout_fd = helper._open_existing_directory(ROOT.resolve(strict=True))
        try:
            if helper._directory_contains_identity(project_fd, os.fstat(checkout_fd)):
                raise AssertionError("safe project was classified inside checkout")
            if not helper._directory_contains_identity(checkout_fd, os.fstat(checkout_fd)):
                raise AssertionError("descriptor-bound checkout overlap was missed")
        finally:
            os.close(project_fd)
            os.close(checkout_fd)
        print("OK: checkout overlap is bound to opened directory identities")

        fake_distribution = root / "fake-distribution"
        nested_home = fake_distribution / "nested-home"
        nested_home.mkdir(parents=True, mode=0o700)
        original_distribution_root = helper.distribution_root
        helper.distribution_root = lambda: fake_distribution.resolve(strict=True)
        try:
            expect_state_error(
                helper,
                "profile home inside installed checkout",
                lambda: helper.write_profile("acme", body, nested_home, create_only=True),
            )
            linked_home = root / "linked-home"
            linked_home.symlink_to(nested_home)
            expect_state_error(
                helper,
                "symlinked profile home inside installed checkout",
                lambda: helper.write_profile("acme", body, linked_home, create_only=True),
            )
        finally:
            helper.distribution_root = original_distribution_root

        overlap_home = root / "overlap-home"
        overlap_distribution = overlap_home / ".diagram-design"
        overlap_distribution.mkdir(parents=True, mode=0o700)
        helper.distribution_root = lambda: overlap_distribution.resolve(strict=True)
        try:
            expect_state_error(
                helper,
                "profile state directory equal to installed checkout",
                lambda: helper.write_profile("acme", body, overlap_home, create_only=True),
            )
        finally:
            helper.distribution_root = original_distribution_root

        nested_distribution_home = root / "nested-distribution-home"
        nested_distribution = nested_distribution_home / ".diagram-design/profiles"
        nested_distribution.mkdir(parents=True, mode=0o700)
        helper.distribution_root = lambda: nested_distribution.resolve(strict=True)
        try:
            expect_state_error(
                helper,
                "profile library equal to installed checkout",
                lambda: helper.write_profile(
                    "acme", body, nested_distribution_home, create_only=True
                ),
            )
        finally:
            helper.distribution_root = original_distribution_root

        concurrent_home = root / "concurrent-home"
        concurrent_home.mkdir(mode=0o700)

        def run_concurrently(operations):
            barrier = threading.Barrier(len(operations))
            results = []

            def worker(operation):
                barrier.wait()
                try:
                    operation()
                    results.append("success")
                except helper.StateError:
                    results.append("rejected")

            threads = [threading.Thread(target=worker, args=(operation,)) for operation in operations]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            if sorted(results) != ["rejected", "success"]:
                raise AssertionError(f"concurrent helper mutation was not serialized: {results}")

        run_concurrently(
            (
                lambda: helper.write_profile("race", b"one", concurrent_home, create_only=True),
                lambda: helper.write_profile("race", b"two", concurrent_home, create_only=True),
            )
        )
        _data, race_token = helper.inspect_profile("race", concurrent_home)
        run_concurrently(
            (
                lambda: helper.write_profile(
                    "race", b"three", concurrent_home, expected_token=race_token
                ),
                lambda: helper.write_profile(
                    "race", b"four", concurrent_home, expected_token=race_token
                ),
            )
        )
        _data, delete_token = helper.inspect_profile("race", concurrent_home)
        run_concurrently(
            (
                lambda: helper.delete_profile("race", delete_token, concurrent_home),
                lambda: helper.write_profile(
                    "race", b"five", concurrent_home, expected_token=delete_token
                ),
            )
        )
        print("OK: cooperating create, replace, and delete races are serialized")

    print("All profile-state safety tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
