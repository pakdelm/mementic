# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in mementic, report it through
**GitHub private vulnerability reporting**: open the
[Security tab](https://github.com/pakdelm/mementic/security/advisories/new) and
create a private advisory. Do not open a public issue before the issue is
disclosed.

You can expect:

- **Acknowledgment** within 48 hours of your report.
- A **timeline and assessment** once the issue has been triaged.
- Coordinated disclosure so that a fix is available before public announcement.

## Supported Versions

`0.5.x` is the first public release line. Only the latest release line receives
security updates.

| Version | Supported |
| ------- | --------- |
| 0.5.x   | yes       |
| older   | no        |

## Security Model

mementic is a local CLI. It runs no server, collects no telemetry, and makes no
network requests at runtime. The knowledge graph is a local SQLite database at
`.mementic/graph.db`; it is not encrypted and is readable by anyone with access
to the file.

There is no LLM extraction, no API key handling, no keyring integration, no
`capture` command, and no `init --deep` mode. Decisions and constraints are
written explicitly with `mementic add`.

The graph belongs to the directory where `mementic init` was run. It is not
synced or shared. Back it up like any other local file.
