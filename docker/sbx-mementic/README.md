# mementic sbx mixin

A Docker Sandboxes (`sbx`) v3 mixin that adds the mementic CLI to a workload.
The workload supplies the agent, Python 3, and its launch command; this mixin
only adds mementic.

## Requirements

- `sbx` with v3 kit support (v0.45 or later).
- Docker with Buildx.
- A workload that provides `python3`. Docker's default agent templates do; the
  `*-minimal` variants may not.

## Use

Run from the workspace root — the directory that should own `.mementic/`:

```sh
sbx run docker.io/docker/sbx-kit-shell:1.0.0 --kit ./docker/sbx-mementic --name mementic
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

## Version pin

`mementic.yaml` pins `args.version` to the current release, and the Dockerfile
installs the matching GitHub tag archive. There is no registry publishing,
multi-arch build, or release-script integration in this repository.
