# ADR 0009: Profile-only immutable installs

## Status

Accepted.

## Context

A commit-pinned external skill can be validated as clean before it is linked.
The normal onboarding and profile flows may then write the installed
`references/style-guide.md`, making the selected subtree dirty and causing a
later integrity check to reject it. A writable checkout also blurs the boundary
between reviewed distribution source and user-specific branding state.

## Decision

Distributions that include `references/profile-only.md` treat the installed
skill checkout as immutable. Onboarding constructs a complete guide in memory
and writes it directly to the validated home profile library. A project selects
that profile only through a minimal `.diagram-design` marker written with the
user's explicit consent. Profile verbs never copy content into, or add headers
to, the installed `style-guide.md`.

The policy is an optional shipped reference rather than an implicit host rule.
`SKILL.md` routes to it, onboarding and profile references announce its
precedence, and the docs-sync gate rejects incomplete routing. A shipped helper
owns profile and marker I/O so symlinks, special files, unsafe directory
components, checkout overlap, and concurrent replacement fail closed.

## Consequences

- A pinned checkout stays byte-for-byte clean across onboarding and profile use.
- User state survives checkout replacement and remains outside reviewed source.
- Saving a profile does not activate it when the user declines the project marker;
  the agent must report that distinction.
- Distributions that omit the policy retain the upstream editable-working-copy
  behavior.
