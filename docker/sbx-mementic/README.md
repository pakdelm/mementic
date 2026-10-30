# mementic sbx mixin

A Docker Sandboxes (`sbx`) v3 mixin that adds the mementic CLI to a workload.
The workload supplies the agent, Python 3, and its launch command; this mixin
only adds mementic.

## Requirements

- `sbx` with v3 kit support (v0.45 or later).
- A workload that provides `python3`. Docker's default agent templates do; the
  `*-minimal` variants may not.

## Use

The kit image is not published. Pass this directory from your mementic checkout
as `--kit` and run `sbx` from the workspace root — the directory that should own
`.mementic/` (the sbx kit builder handles the image build):

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
that owns `.mementic`. If the agent starts in a child repository, that child
has no graph until it is initialized there.

## Working on the kit

The kit is a companion pair: `mementic.yaml` declares it and
`mementic.dockerfile` builds its content. The Kit frontend resolves the recipe
by the descriptor's filename stem, so keep the two names in sync. A mixin with
no discoverable companion builds successfully but is declaration-only and
installs nothing.

The committed directory is the build context, and the recipe installs mementic
from the pinned public tag archive, so nothing has to be staged first.
`scripts/build_kit.py` builds the descriptor locally or in CI:

```sh
uv run python scripts/build_kit.py --output cacheonly
```

`sbx` builds the kit on demand and caches it by source hash; create a new
sandbox after editing the kit, because an existing one keeps its recorded kits.

## Version pin

`mementic.yaml` pins `args.version`, which the descriptor exposes as
`provides: mementic@<version>`. The recipe downloads
`v<version>.tar.gz` from the public repository and verifies the installed
dist-info reports that exact version, so a pin with no matching tag fails the
build. Drift between the pin and `pyproject.toml` is caught by
`tests/test_sbx_kit.py`, not by the build: a pin behind the release installs the
older tagged source cleanly. `scripts/release.py` bumps the pin in the release
commit. CI builds the pin when its tag exists and falls back to the latest
released tag with `scripts/build_kit.py --version <tag>` only when it does not,
so a release PR never fetches an untagged pin.

The image is not published at launch. Building it locally is supported; pushing
to a registry is deferred.
