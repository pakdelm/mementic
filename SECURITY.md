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

There are no public releases yet. Once the first release ships, only the latest
release line receives security updates.

| Version          | Supported          |
| ---------------- | ------------------ |
| Latest release   | :white_check_mark: |
| Older releases   | :x:                |

## Security Model

mementic is a local CLI. It runs no servers and collects no telemetry. The
knowledge graph is a local SQLite database that lives entirely on your machine.
API keys are stored in your operating system keychain (with environment
variables as a CI fallback) and are never written to disk.

LLM extraction is the only network activity, and it is always direct: requests
go straight from your machine to the provider you configured (e.g., OpenAI,
DeepSeek, OpenRouter) using your own API key. Depending on the command, a
request can include session-log excerpts, the current git diff (`mementic
capture`), or repository markdown and a codebase summary (`mementic init
--deep`). Nothing is ever sent to a mementic-hosted server — mementic has none.

If your code must not leave your machine, run mementic without an API key:
extraction then runs fully locally in deterministic mode (no LLM calls), or
point the provider at a local model such as Ollama.
