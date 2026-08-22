# ADR 0010 — Explicit-only skill invocation

**Status:** accepted (v3.0.0)

## Context

Diagram Design has a long, trigger-rich description because it supports 39 visual types, imports, branding, motion, and several output modes. When a host injects that description into every session for automatic routing, the skill creates steady-state context cost even when no diagram work is requested.

The package still needs direct user invocation across the generic Agent Skills surface and Codex. Installed source must also remain immutable so a controlled distribution can pin and review the complete policy.

## Decision

Diagram Design is user-invoked only:

- `SKILL.md` declares `disable-model-invocation: true`, `user-invocable: true`, and `metadata.invocation: manual`.
- `agents/openai.yaml` declares `policy.allow_implicit_invocation: false` for Codex.
- Codex consumes the canonical directory as a direct user skill; native Codex plugin packaging is intentionally omitted because its validator rejects the generic user-only flag required by Claude Code and Pi.
- The package verifier checks the effective YAML paths and rejects misplaced, nested, duplicated, or contradictory policy values.
- The runtime description is capped at 160 characters for selectors and conservative host compatibility; detailed marketplace descriptions remain separate.
- Public examples begin with the host's explicit skill command or tell the user to select the skill explicitly.

ADR 0004 still governs description completeness and the `SKILL.md` byte cap, but its automatic-routing rationale is superseded. The description remains useful in package metadata, skill pickers, and hosts that show it only on demand.

## Consequences

- Ordinary natural-language prompts do not promise to activate Diagram Design.
- Supported explicit surfaces include `/diagram-design:diagram-design` for a Claude Code plugin, `/diagram-design` for a Claude user skill, `$diagram-design` for Codex, and `/skill:diagram-design` for Pi.
- Claude Code and Pi can hide the description entirely. Codex keeps the skill out of model context by default when `allow_implicit_invocation: false`. Full instructions load only after explicit invocation.
- Any future change that re-enables implicit invocation must update this ADR, the README, both policy surfaces, and the adversarial package tests together.
