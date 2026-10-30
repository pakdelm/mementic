# mementic sbx mixin

A Docker Sandboxes (`sbx`) v3 mixin that adds the mementic CLI to a workload.
The workload supplies the agent, Python 3, and its launch command; this kit only
adds mementic.

## Requirements

- `sbx` with v3 kit support (v0.45 or later).
- A workload that provides `python3`. Docker's default agent templates do; the
  `*-minimal` variants may not.

## Use

The kit image is not published; `sbx` builds it on demand from this directory.
Pass the directory from your mementic checkout as `--kit` and run `sbx` from the
workspace root — the directory that should own `.mementic/`:

```sh
sbx run docker.io/docker/sbx-kit-shell:1.0.0 \
  --kit /path/to/mementic/docker/sbx-mementic --name mementic
```

Inside the sandbox, initialize the graph once:

```sh
mementic init
mementic query keywords auth session
```

The graph lives at `<workspace>/.mementic/graph.db` and persists on the host.
Use direct mode, not `--clone`: a clone breaks graph persistence because the
agent edits the clone while the graph stays in the original workspace.

The mixin does not set a workspace-root variable or change the agent's working
directory. The primary workspace is the scope: run mementic from the directory
that owns `.mementic`. If the agent starts in a child repository, that child has
no graph until it is initialized there.

## What is in this directory

| File | Role |
|---|---|
| `mementic.yaml` | Descriptor: kit name, `args.version`, `provides` |
| `mementic.dockerfile` | Recipe: installs the pinned public release tag |
| `mementic` | Wrapper: the `mementic` command inside the sandbox |
| `README.md` | This document |

Nothing else is needed: the committed directory is the build context, and the
recipe downloads its own source. `.gitattributes` at the repository root pins the
wrapper to LF, because a CRLF shebang makes the sandbox kernel look for an
interpreter named `/bin/sh\r`.

## How sbx builds it

1. `sbx` reads `mementic.yaml`. The descriptor's `# syntax=docker/sandbox-kit:3`
   line lets Buildx parse it as a build definition.
2. The frontend passes `args.version.default` to the recipe as
   `MEMENTIC_VERSION`.
3. The recipe is resolved by filename stem: `mementic.yaml` pairs with
   `mementic.dockerfile`. Keep the two names in sync; a descriptor without its
   companion still builds, but the kit then declares content it never installs.
4. The `python:3.13-slim` stage installs
   `.../archive/refs/tags/v${MEMENTIC_VERSION}.tar.gz` into `/opt/mementic`, then
   asserts `mementic-${MEMENTIC_VERSION}.dist-info` exists. A missing tag or a
   version mismatch fails the build here.
5. The final `scratch` stage contains only `/opt/mementic` and
   `/usr/local/bin/mementic`. `sbx` adds those paths to the workload's
   filesystem, so the sandbox keeps the workload's Python and gains `mementic`
   on `PATH`. `scripts/sbx_smoke.py` verifies that end to end.

## Why a kit directory instead of instructions

Because the version has to track the release. `scripts/release.py` bumps
`args.version` in the release commit and CI builds this same directory, so a
hand-copied recipe would go stale and untested.

## Working on the kit

`sbx` builds the kit on demand and caches it by source hash; create a new sandbox
after editing the kit, because an existing one keeps its recorded kits.

`scripts/build_kit.py` builds the same context locally or in CI:

```sh
uv run python scripts/build_kit.py --output cacheonly
```

### Version pin

`mementic.yaml` pins `args.version`, which the descriptor exposes as
`provides: mementic@<version>`. The recipe downloads `v<version>.tar.gz` from the
public repository and verifies the installed dist-info reports that exact
version, so a pin with no matching tag fails the build. Drift between the pin and
`pyproject.toml` is caught by `tests/test_sbx_kit.py`, not by the build: a pin
behind the release installs the older tagged source cleanly. `scripts/release.py`
bumps the pin in the release commit.

CI builds the pin when its tag exists and falls back to the latest released tag
with a warning only when it does not, so a release PR never fetches an untagged
pin.

Building the image locally is supported; pushing it to a registry is deferred.
