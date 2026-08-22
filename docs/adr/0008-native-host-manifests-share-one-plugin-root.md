# ADR 0008 — Native plugin hosts share one root; Codex uses the direct skill

**Status:** accepted (v2.5.14; amended v3.0.0)

## Context

Diagram Design serves Claude Code, Codex, Pi, and Factory Droid. Factory can translate the Claude plugin layout, but relying on that fallback leaves Droid installation undocumented and outside the package-version gate. Copying the skill or commands into host-specific directories would create multiple sources of truth. Codex's native plugin validator requires `disable-model-invocation` to be absent or false, while Claude Code and Pi require it to be true for the shared skill to remain user-invoked only.

## Decision

Claude and Factory each receive the smallest native manifest and marketplace metadata their host needs. Both marketplaces resolve to the repository root and reuse `skills/diagram-design/` and `commands/` without duplication. Pi continues to use the same root package surfaces. Codex consumes `skills/diagram-design/` directly as a user skill and reads `agents/openai.yaml`; this fork does not publish a native Codex plugin or marketplace entry.

The two native plugin manifests carry identical shared identity, description, version, author, repository, license, and keyword metadata. The package verifier rejects drift, deletion, unsafe marketplace paths, or a version that does not advance from the base ref. A newly tracked native manifest may be absent at the base ref only during bootstrap: its current metadata and version must match the established manifest, and that established manifest must advance.

## Consequences

Each supported host has an explicit install path while diagram behavior remains single-sourced. Codex retains `$diagram-design` explicit invocation without duplicating the implementation or weakening Claude/Pi user-only behavior. Adding another native plugin host requires compatible metadata, package-gate coverage, documentation, and a synchronized version bump; it never justifies copying the skill or command surface. Git-based Factory installs are updated by marketplace commit, while the synchronized manifest version remains release metadata and a review gate.
