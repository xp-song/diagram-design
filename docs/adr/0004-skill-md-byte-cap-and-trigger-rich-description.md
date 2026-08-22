# ADR 0004 — SKILL.md byte cap and the trigger-rich description

**Status:** accepted for detailed marketplace metadata and type coverage; runtime-description rationale superseded by ADR 0010

## Context

`SKILL.md` loads into an agent's context on every skill invocation, so it must stay lean; a byte cap keeps growth honest. In v2.3, automatic-routing hosts also injected the frontmatter `description` before invocation. Slimming it to fit the original 35,000-byte cap deleted all 27 type names and broke those hosts' lexical matching. ADR 0010 later replaced automatic routing with explicit-only invocation and moved complete type coverage to the detailed marketplace descriptions. The runtime skill description is now deliberately compact for selectors and conservative host compatibility.

## Decision

Two rules, in priority order:

1. Detailed marketplace descriptions must name every visual type in the selection table (enforced by `scripts/verify-docs-sync.py`) plus the import formats and major feature vocabulary. The runtime `SKILL.md` description stays within the explicit-only context budget enforced by the package and docs verifiers.
2. `MAX_SKILL_BYTES` is 40,000 (enforced by `scripts/verify-semantic-motion.py`). When the file approaches the cap, cut body prose or move detail into `references/` — never the description.

## Consequences

- Adding a visual type requires updating the detailed marketplace descriptions; CI fails otherwise, by design.
- The cap is measured on raw bytes with `core.autocrlf=false` pinned in CI checkout; Windows contributors should keep LF endings for `SKILL.md`.
