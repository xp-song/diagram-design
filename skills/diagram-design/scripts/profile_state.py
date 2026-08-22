#!/usr/bin/env python3
"""Safely read and write Diagram Design profiles and project markers."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - unsupported platforms fail closed
    fcntl = None

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
MAX_PROFILE_BYTES = 1_000_000
MAX_MARKER_BYTES = 96
PROFILE_COMPONENTS = (".diagram-design", "profiles")


class StateError(RuntimeError):
    """Raised when safe state access cannot be guaranteed."""


def validate_slug(slug: str) -> str:
    if SLUG_RE.fullmatch(slug) is None:
        raise StateError("slug must match [a-z0-9][a-z0-9-]{0,63}")
    return slug


def distribution_root() -> Path:
    script = Path(__file__).resolve(strict=True)
    candidate = script.parents[3]
    if (candidate / "skills/diagram-design/SKILL.md").is_file():
        return candidate
    return script.parent.parent


def _required_primitives() -> None:
    required = (os.open, os.stat, os.mkdir, os.rename, os.unlink)
    if not all(function in os.supports_dir_fd for function in required):
        raise StateError("safe fd-relative state operations are unavailable on this platform")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise StateError("safe no-follow state operations are unavailable on this platform")
    if fcntl is None:
        raise StateError("safe exclusive state locking is unavailable on this platform")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_nlink,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _same_directory_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
    )


def _open_existing_directory(path: Path) -> int:
    """Open an absolute canonical directory without following any component."""
    _required_primitives()
    canonical = path
    if not canonical.is_absolute():
        raise StateError("state directory must resolve to an absolute path")

    parts = canonical.parts
    fd = os.open(parts[0], _directory_flags())
    try:
        for component in parts[1:]:
            before = os.stat(component, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise StateError(f"unsafe directory component: {component}")
            child = os.open(component, _directory_flags(), dir_fd=fd)
            after = os.fstat(child)
            if not _same_directory_identity(before, after):
                os.close(child)
                raise StateError(f"directory changed while opening: {component}")
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_child_directory(parent_fd: int, name: str, create: bool) -> int:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise StateError(f"state directory does not exist: {name}") from None
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise StateError(f"unsafe state directory: {name}")
    if hasattr(os, "getuid") and before.st_uid != os.getuid():
        raise StateError(f"state directory is not owned by the current user: {name}")
    if before.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StateError(f"state directory is group/world writable: {name}")
    child = os.open(name, _directory_flags(), dir_fd=parent_fd)
    after = os.fstat(child)
    if not _same_directory_identity(before, after):
        os.close(child)
        raise StateError(f"state directory changed while opening: {name}")
    if hasattr(os, "getuid") and after.st_uid != os.getuid():
        os.close(child)
        raise StateError(f"state directory ownership changed while opening: {name}")
    if after.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        os.close(child)
        raise StateError(f"state directory permissions changed while opening: {name}")
    return child


def _profile_directory(home: Path | None, create: bool) -> int:
    base = (home if home is not None else Path.home()).resolve(strict=True)
    fd = _open_existing_directory(base)
    installed_fd = _open_existing_directory(distribution_root())
    try:
        if _directory_contains_identity(fd, os.fstat(installed_fd)):
            raise StateError("profile home may not be the installed distribution or lie inside it")
        for component in PROFILE_COMPONENTS:
            child = _open_child_directory(fd, component, create)
            installed_info = os.fstat(installed_fd)
            if _directory_contains_identity(child, installed_info) or _directory_contains_identity(
                installed_fd, os.fstat(child)
            ):
                os.close(child)
                raise StateError(
                    "profile state directory may not overlap the installed distribution"
                )
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise
    finally:
        os.close(installed_fd)


def _regular_entry(fd: int, name: str, *, missing_ok: bool = False) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise StateError(f"state file does not exist: {name}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise StateError(f"state entry is not a regular no-follow file: {name}")
    if info.st_nlink != 1:
        raise StateError(f"state entry must have exactly one hard link: {name}")
    return info


def _read_entry(fd: int, name: str, limit: int) -> bytes:
    before = _regular_entry(fd, name)
    assert before is not None
    if before.st_size > limit:
        raise StateError(f"state file exceeds {limit} bytes: {name}")
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        opened = os.fstat(file_fd)
        if _identity(before) != _identity(opened):
            raise StateError(f"state file changed while opening: {name}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise StateError(f"state file exceeds {limit} bytes: {name}")
        after = os.fstat(file_fd)
        if _identity(opened) != _identity(after):
            raise StateError(f"state file changed while reading: {name}")
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def _record_token(info: os.stat_result, data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(repr(_identity(info)).encode("ascii"))
    digest.update(b"\0")
    digest.update(data)
    return digest.hexdigest()


def _inspect_entry(fd: int, name: str, limit: int) -> tuple[bytes, str] | None:
    info = _regular_entry(fd, name, missing_ok=True)
    if info is None:
        return None
    data = _read_entry(fd, name, limit)
    return data, _record_token(info, data)


@contextmanager
def _mutation_lock(fd: int):
    """Serialize cooperating helper writers inside one state directory."""
    lock_fd = os.dup(fd)
    try:
        assert fcntl is not None
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _atomic_write(
    fd: int,
    name: str,
    data: bytes,
    limit: int,
    *,
    expected_token: str | None,
    create_only: bool,
) -> None:
    if len(data) > limit:
        raise StateError(f"state content exceeds {limit} bytes: {name}")
    with _mutation_lock(fd):
        before = _inspect_entry(fd, name, limit)
        if create_only:
            if before is not None:
                raise StateError(f"create-only state file already exists: {name}")
        elif expected_token is None:
            raise StateError(f"an approved state token is required to replace: {name}")
        elif before is None or before[1] != expected_token:
            raise StateError(f"state file no longer matches the approved token: {name}")
        temporary = f".diagram-design-tmp-{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(8)}"
        temp_fd = -1
        try:
            temp_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fd,
            )
            view = memoryview(data)
            while view:
                written = os.write(temp_fd, view)
                view = view[written:]
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = -1
            current = _inspect_entry(fd, name, limit)
            if (before is None) != (current is None) or (
                before is not None and current is not None and before[1] != current[1]
            ):
                raise StateError(f"state file changed before replacement: {name}")
            os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            if _read_entry(fd, name, limit) != data:
                raise StateError(f"state file verification failed after replacement: {name}")
            os.fsync(fd)
        finally:
            if temp_fd >= 0:
                os.close(temp_fd)
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def _directory_contains_identity(directory_fd: int, target: os.stat_result) -> bool:
    """Return whether an opened directory is the target or one of its descendants."""
    current = os.dup(directory_fd)
    try:
        while True:
            current_info = os.fstat(current)
            if (current_info.st_dev, current_info.st_ino) == (target.st_dev, target.st_ino):
                return True
            parent = os.open("..", _directory_flags(), dir_fd=current)
            parent_info = os.fstat(parent)
            if (parent_info.st_dev, parent_info.st_ino) == (
                current_info.st_dev,
                current_info.st_ino,
            ):
                os.close(parent)
                return False
            os.close(current)
            current = parent
    finally:
        os.close(current)


def _project_directory(project_root: Path) -> int:
    canonical = project_root.resolve(strict=True)
    if not canonical.is_dir():
        raise StateError("project root is not a directory")
    project_fd = _open_existing_directory(canonical)
    installed_fd = _open_existing_directory(distribution_root())
    try:
        if _directory_contains_identity(project_fd, os.fstat(installed_fd)):
            raise StateError("project root may not be the installed distribution or lie inside it")
        return project_fd
    except Exception:
        os.close(project_fd)
        raise
    finally:
        os.close(installed_fd)


def inspect_marker(project_root: Path) -> dict[str, object]:
    fd = _project_directory(project_root)
    try:
        record = _inspect_entry(fd, ".diagram-design", MAX_MARKER_BYTES)
    finally:
        os.close(fd)
    if record is None:
        return {"exists": False, "token": None, "content": None, "encoding": None}
    data, token = record
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "exists": True,
            "token": token,
            "content": None,
            "encoding": "invalid-utf-8",
            "content_base64": base64.b64encode(data).decode("ascii"),
        }
    return {"exists": True, "token": token, "content": content, "encoding": "utf-8"}


def read_marker(project_root: Path) -> str:
    record = inspect_marker(project_root)
    if not record["exists"]:
        raise StateError("project marker does not exist")
    if record["encoding"] != "utf-8":
        raise StateError("project marker is not valid UTF-8")
    return str(record["content"])


def write_marker(
    project_root: Path,
    slug: str,
    *,
    expected_token: str | None = None,
    create_only: bool = False,
) -> None:
    slug = validate_slug(slug)
    fd = _project_directory(project_root)
    try:
        _atomic_write(
            fd,
            ".diagram-design",
            f"profile: {slug}\n".encode(),
            MAX_MARKER_BYTES,
            expected_token=expected_token,
            create_only=create_only,
        )
    finally:
        os.close(fd)


def read_profile(slug: str, home: Path | None = None) -> bytes:
    record = inspect_profile(slug, home)
    return record[0]


def inspect_profile(slug: str, home: Path | None = None) -> tuple[bytes, str]:
    slug = validate_slug(slug)
    fd = _profile_directory(home, create=False)
    try:
        record = _inspect_entry(fd, f"{slug}.md", MAX_PROFILE_BYTES)
        if record is None:
            raise StateError(f"state file does not exist: {slug}.md")
        return record
    finally:
        os.close(fd)


def write_profile(
    slug: str,
    data: bytes,
    home: Path | None = None,
    *,
    expected_token: str | None = None,
    create_only: bool = False,
) -> None:
    slug = validate_slug(slug)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StateError("profile content must be valid UTF-8") from exc
    fd = _profile_directory(home, create=True)
    try:
        if slug == "default" and not create_only:
            raise StateError("default profile may only be seeded with create-only mode")
        _atomic_write(
            fd,
            f"{slug}.md",
            data,
            MAX_PROFILE_BYTES,
            expected_token=expected_token,
            create_only=create_only,
        )
    finally:
        os.close(fd)


def inspect_profile_listing(home: Path | None = None) -> dict[str, list[str]]:
    try:
        fd = _profile_directory(home, create=False)
    except StateError as exc:
        if "does not exist" in str(exc):
            return {"profiles": [], "ignored": []}
        raise
    try:
        names: list[str] = []
        ignored: list[str] = []
        for name in os.listdir(fd):
            if not name.endswith(".md") or SLUG_RE.fullmatch(name[:-3]) is None:
                ignored.append(name)
                continue
            _regular_entry(fd, name)
            names.append(name[:-3])
        return {"profiles": sorted(names), "ignored": sorted(ignored)}
    finally:
        os.close(fd)


def list_profiles(home: Path | None = None) -> list[str]:
    return inspect_profile_listing(home)["profiles"]


def delete_profile(slug: str, expected_token: str, home: Path | None = None) -> None:
    slug = validate_slug(slug)
    if slug == "default":
        raise StateError("default profile may not be deleted")
    fd = _profile_directory(home, create=False)
    name = f"{slug}.md"
    try:
        with _mutation_lock(fd):
            before = _inspect_entry(fd, name, MAX_PROFILE_BYTES)
            if before is None or before[1] != expected_token:
                raise StateError(f"state file no longer matches the approved token: {name}")
            current = _inspect_entry(fd, name, MAX_PROFILE_BYTES)
            if current is None or current[1] != before[1]:
                raise StateError(f"state file changed before deletion: {name}")
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
    finally:
        os.close(fd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("profile-read", "profile-write", "profile-delete"):
        child = subparsers.add_parser(command)
        child.add_argument("slug")
        if command == "profile-write":
            mode = child.add_mutually_exclusive_group(required=True)
            mode.add_argument("--create", action="store_true")
            mode.add_argument("--expect")
        elif command == "profile-delete":
            child.add_argument("--expect", required=True)
    subparsers.add_parser("profile-list")
    for command in ("marker-read", "marker-write"):
        child = subparsers.add_parser(command)
        child.add_argument("project_root", type=Path)
        if command == "marker-write":
            child.add_argument("slug")
            mode = child.add_mutually_exclusive_group(required=True)
            mode.add_argument("--create", action="store_true")
            mode.add_argument("--expect")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "profile-read":
            data, token = inspect_profile(args.slug)
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                print(
                    json.dumps(
                        {
                            "token": token,
                            "content": None,
                            "encoding": "invalid-utf-8",
                            "content_base64": base64.b64encode(data).decode("ascii"),
                        }
                    )
                )
            else:
                print(json.dumps({"token": token, "content": content, "encoding": "utf-8"}))
        elif args.command == "profile-write":
            data = sys.stdin.buffer.read(MAX_PROFILE_BYTES + 1)
            write_profile(
                args.slug,
                data,
                expected_token=args.expect,
                create_only=args.create,
            )
        elif args.command == "profile-list":
            print(json.dumps(inspect_profile_listing()))
        elif args.command == "profile-delete":
            delete_profile(args.slug, args.expect)
        elif args.command == "marker-read":
            print(json.dumps(inspect_marker(args.project_root)))
        elif args.command == "marker-write":
            write_marker(
                args.project_root,
                args.slug,
                expected_token=args.expect,
                create_only=args.create,
            )
    except (OSError, StateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
