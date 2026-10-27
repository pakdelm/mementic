# mementic sbx mixin

A Docker Sandboxes (`sbx`) v3 mixin that adds the mementic CLI to a workload.
The workload supplies the agent, Python 3, and its launch command; this mixin
only adds mementic.

## Requirements

- `sbx` with v3 kit support (v0.45 or later).
- A workload that provides `python3`. Docker's default agent templates do; the
  `*-minimal` variants may not.

## Use

The kit image is not published. Stage the kit from the checkout and run it from
the workspace root — the directory that should own `.mementic/` (Docker with
Buildx required):

```sh
uv run python scripts/kit.py stage .sbx-kit
sbx run docker.io/docker/sbx-kit-shell:1.0.0 --kit .sbx-kit --name mementic
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
that owns `.mementic`. If the agent starts in a child repository, that child
has no graph until it is initialized there.

## Working on the kit

The kit is a companion pair: `mementic.yaml` declares it and
`mementic.dockerfile` builds its content. The Kit frontend resolves the recipe
by the descriptor's filename stem, so keep the two names in sync. A mixin with
no discoverable companion builds successfully but is declaration-only and
installs nothing.

The recipe installs mementic from its build context, so the committed directory
alone is not a buildable kit: `scripts/kit.py stage` adds `pyproject.toml`, the
package source, and the license to the context. `sbx` builds the staged
directory on demand and caches it by source hash; create a new sandbox after
editing the kit, because an existing one keeps its recorded kits.

## Version pin

`mementic.yaml` pins `args.version`, which the descriptor exposes as
`provides: mementic@<version>`. The recipe verifies the staged source reports
that exact distribution version, so a pin that drifts from `pyproject.toml` fails
the build. `scripts/release.py` bumps the pin in the release commit.

The image is not published at launch. Building it locally is supported; pushing
to a registry is deferred.
