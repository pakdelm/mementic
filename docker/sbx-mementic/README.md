# mementic sbx mixin

A Docker Sandboxes (`sbx`) v3 mixin that adds the mementic CLI to a workload.
The workload supplies the agent, Python 3, and its launch command; this mixin
only adds mementic.

## Requirements

- `sbx` with v3 kit support (v0.45 or later).
- A workload that provides `python3`. Docker's default agent templates do; the
  `*-minimal` variants may not.

## Use

Each release publishes `docker.io/pakdelm/sbx-kit-mementic:<version>`. Run it
from the workspace root — the directory that should own `.mementic/`:

```sh
sbx run docker.io/docker/sbx-kit-shell:1.0.0 \
  --kit docker.io/pakdelm/sbx-kit-mementic:0.4.1 --name mementic
```

Inside the sandbox, initialize the graph once:

```sh
mementic init
mementic query keywords auth session
```

The graph lives at `<workspace>/.mementic/graph.db` and persists on the host.
Use direct mode, not `--clone`: a clone breaks graph persistence because the
agent edits the clone while the graph stays in the original workspace.

The mixin does not set `MEMENTIC_WORKSPACE_ROOT` or change the agent's working
directory. The primary workspace is the scope: run mementic from the directory
that owns `.mementic`. If the agent starts in a child repository, that child
has no graph until it is initialized there.

## Working on the kit

The kit is a companion pair: `mementic.yaml` declares it and
`mementic.dockerfile` builds its content. The Kit frontend resolves the recipe
by the descriptor's filename stem, so keep the two names in sync. A mixin with
no discoverable companion builds successfully but is declaration-only and
installs nothing.

Build the local directory while iterating (Docker with Buildx required):

```sh
sbx run docker.io/docker/sbx-kit-shell:1.0.0 \
  --kit ./docker/sbx-mementic --name mementic-dev
```

`sbx` builds the directory on demand and caches it by source hash. Create a new
sandbox after editing the kit; an existing sandbox keeps its recorded kits.

## Version pin and publishing

`mementic.yaml` pins `args.version` to the current release; the recipe installs
the matching GitHub tag archive. `scripts/release.py` bumps the pin in the
release commit and, after pushing the tag, publishes a multi-arch image
(`linux/amd64,linux/arm64`) to `docker.io/pakdelm/sbx-kit-mementic:<version>`.

Publishing needs a buildx builder backed by the `docker-container` driver; the
default `docker` driver refuses multi-platform pushes. Create one once with:

```sh
docker buildx create --name mementic-kit --driver docker-container --use
```
