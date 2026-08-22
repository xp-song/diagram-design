# Profile-only immutable install policy

This distribution treats the installed skill checkout as immutable. This policy
overrides any working-copy write described in [`onboarding.md`](onboarding.md)
or [`profiles.md`](profiles.md).

All profile-library and project-marker access must go through the shipped
[`profile_state.py`](../scripts/profile_state.py) helper. Never use generic
Read/Write/Edit/Bash filesystem operations for those paths. If the helper exits
non-zero because safe primitives are unavailable, stop and offer exact content
for the user to save manually; do not weaken or reimplement its checks.

## Non-negotiable boundary

- Keep the installed `references/style-guide.md` byte-for-byte unchanged.
- Store user-created guides only in `~/.diagram-design/profiles/<slug>.md`.
- Select a profile for a project only through `<project-root>/.diagram-design`.
- Never fall back to editing, copying over, or adding a profile header to the
  installed working copy, even when it is writable.
- Treat home profiles and project markers as user state. Write either only as
  part of the requested profile/onboarding flow and use the confirmations below.

## Safe marker I/O

Before every marker read or write, resolve the installed skill path and the
canonical project root. Define the installed distribution root as the checkout
root that contains `skills/diagram-design/SKILL.md` (or the installed skill
directory when that package layout is unavailable). Refuse marker access when
the canonical project root is the distribution root or lies inside it. This
prevents a marker from dirtying the reviewed checkout.

Treat every path component and the final `.diagram-design` entry as untrusted:

1. Operate relative to an already opened canonical project-directory descriptor;
   do not re-resolve a repository-provided path for the write.
2. Inspect the final entry with `lstat`. Refuse a symbolic link, directory,
   device, socket, FIFO, or any other non-regular file. An absent entry is valid.
3. After consent, create a same-directory temporary regular file with exclusive
   creation and `O_NOFOLLOW`, mode `0600`, containing only `profile: <slug>\n`.
4. Immediately before activation, repeat the `lstat` check and verify any
   existing regular file still has the device/inode approved for replacement.
   Abort on any change.
5. Atomically replace the final entry with fd-relative `os.replace` (or the
   POSIX-equivalent fd-relative `os.rename`) in that open directory, then reopen
   with `O_NOFOLLOW` and verify the exact bytes. Clean up the temporary file on
   failure.

The helper enforces these checks with fd-relative, no-follow, inode-verified
reads and atomic writes. Instruct it with `marker-read` or `marker-write`; never
bypass it. When safe operations are unavailable, show the exact one-line marker
content for the user to create manually and report it as not activated.

The same boundary applies to the entire home profile library. Use the helper's
`profile-read`, `profile-write`, `profile-list`, and `profile-delete` commands.
It rejects symlink/special-file entries, unsafe directory components, oversized
content, concurrent replacement, and invalid slugs. `profile-write` accepts the
complete profile on standard input and verifies a byte-exact atomic replacement.
`profile-list` returns JSON with `profiles` and `ignored` arrays so invalid
filenames cannot disappear silently.

Reads return JSON containing the content and an opaque approval token. Show the
content and obtain any required confirmation before mutation, then pass that
exact token with `--expect`; a changed file makes the operation fail. Use
`--create` only after the same helper reported the target absent. These modes are
mutually exclusive. Seed `default.md` only with create-only mode; never replace
or delete it. Marker reads use the same token contract, so consent is bound to
the exact marker inspected rather than merely its path.
If existing state is not valid UTF-8, the read record sets `content` to `null`,
reports `encoding: invalid-utf-8`, and includes `content_base64` plus the same
exact-state token. Do not display or trust the decoded bytes; use the token only
for an explicitly approved replacement or deletion.

The helper's exclusive lock serializes other cooperating helper mutations and
checks the approval token only after acquiring that lock. A separate process
with direct write access that deliberately bypasses the helper can still race an
ordinary filesystem rename; hostile concurrent local writers are outside this
policy's threat model. Do not run a profile operation while another non-helper
process is mutating the same state directory.

## Resolution and first use

Resolve a valid project marker exactly as specified in [`profiles.md`](profiles.md).
Without one, read the shipped `style-guide.md` only as the pristine schema and
default skin; do not use working-copy headers or token differences as state.

At the first-run gate:

- **Default:** ensure the home `default.md` snapshot exists from the pristine
  shipped guide, use it for this generation, and ask for explicit consent before
  writing `.diagram-design` with exactly `profile: default`, using Safe marker I/O.
- **Existing profile:** validate and use the selected home profile, then ask for
  explicit consent before writing or replacing the project marker via Safe marker I/O.
- **Custom onboarding or manual tokens:** build the complete guide in memory.
  Require an explicit valid slug and display name, write the result directly to
  `~/.diagram-design/profiles/<slug>.md`, re-read and verify it, then ask for
  explicit consent before writing or replacing the project marker. If either
  value is missing, ask; never invent it or write a temporary installed copy.

If the user declines the marker, the profile may still be saved, but report
that it is not activated for the project. Use the in-memory guide only for the
current generation when the user explicitly approved that generation.

## Profile verbs

- `save` and `update` write only the validated home-library profile through
  `profile-write`.
- `load`, `switch`, and `reset` activate only by offering to write the project
  marker. If consent is declined, report that the project selection is unchanged.
- `list`, `show`, and `delete` follow [`profiles.md`](profiles.md) through the
  safe helper, but never use an installed working-copy header as active state.
- An unwritable profile library or project marker is a visible failure. Offer a
  manual profile-file paste when useful; never redirect the write into the skill
  checkout.

All slug validation, metadata escaping, structural checks, overwrite/delete
confirmations, and marker-as-untrusted-data rules in [`profiles.md`](profiles.md)
remain in force.
