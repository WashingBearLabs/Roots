# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** — do not open a public issue.

Use GitHub's private vulnerability reporting:
**[Security ▸ Report a vulnerability](https://github.com/WashingBearLabs/Roots/security/advisories/new)**

We'll acknowledge your report, investigate, and keep you updated on a fix. Please
give us a reasonable window to address the issue before any public disclosure.

When reporting, include:

- A description of the vulnerability and its impact.
- Steps to reproduce (a minimal process definition and commands, if applicable).
- The affected version or commit.

## Supported versions

Roots is in active beta (`0.x`). Security fixes are applied to the latest release on
`main`. There is no long-term-support branch yet.

## Known limitations (by design, current release)

These are documented trust-model assumptions, not vulnerabilities:

- **No API authentication.** The HTTP API has no built-in auth and binds to
  `127.0.0.1` by default. Do not expose `roots serve` to an untrusted network without
  your own authentication layer in front of it.
- **Trusted input.** Process definitions, event sinks, and MCP command-agents are
  treated as trusted configuration. Do not load process definitions or `.root`
  packages from untrusted sources without review.

If you believe one of these boundaries can be crossed in a way that isn't documented
above, that's a vulnerability — please report it.
