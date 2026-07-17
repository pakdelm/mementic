# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in mementic, please report it via
**GitHub Security Advisories** — go to the [Security tab](https://github.com/pakdelm/mementic/security/advisories/new)
and create a private advisory. Do **not** open a public GitHub issue — this
allows us to address the issue before it is publicly disclosed.

You can expect:

- **Acknowledgment** within 48 hours of your report.
- A **timeline and assessment** of the issue once we have triaged it.
- Coordinated disclosure so that a fix is available before the vulnerability is
  announced publicly.

We appreciate responsible disclosure and will credit reporters (with permission)
in the release notes.

## Supported Versions

Only the latest release line receives security updates.

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

## Security Model

mementic is a local CLI. It never sends your source code, project data, or personal 
files to any remote server. LLM API calls go directly from your machine to the
configured provider (e.g., OpenAI, Anthropic) using your own API keys. API keys are 
stored in the operating system keychain — they are never written to disk or transmitted 
anywhere except to the provider you configured. The mementic knowledge graph is a local 
SQLite database that lives entirely on your machine. No analytics, telemetry, or usage 
data is ever collected or transmitted.
