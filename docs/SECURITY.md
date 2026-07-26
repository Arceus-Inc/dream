# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.**

Email **security@arceus.inc** with:

- A description of the issue and impact
- Steps to reproduce (proof-of-concept if available)
- Affected versions and components

We aim to acknowledge reports within **72 hours** and provide a remediation timeline when
confirmed.

## Scope

In scope:

- Path traversal, sandbox escape, or permission bypass in built-in tools
- Secret leakage via logs, traces, or error messages
- Unsafe defaults in hooks, MCP, or plugin loading
- Credential handling in `dream.config` and provider adapters

Out of scope:

- Misconfiguration by operators (exposed API keys in `.env.local`, disabled sandboxes)
- Vulnerabilities in third-party MCP servers you choose to connect
- Issues in sibling Arceus repos (`chorus`, `horizon`, `lattice`) — report to those projects

## Safe defaults

- Never commit secrets. `.env.local` and `.dream/` are git-ignored.
- Run agents in sandbox mode for untrusted code paths when available.
- Review MCP server allowlists before enabling them in production workspaces.
