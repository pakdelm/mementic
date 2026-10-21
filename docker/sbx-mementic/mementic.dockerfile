# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
ARG MEMENTIC_VERSION

COPY pyproject.toml /src/pyproject.toml
COPY README.md /src/README.md
COPY LICENSE /src/LICENSE
COPY src/mementic /src/mementic
RUN pip install --no-cache-dir --target /opt/mementic /src
RUN test -d "/opt/mementic/mementic-${MEMENTIC_VERSION}.dist-info"
RUN printf 'from mementic.cli import app\napp()\n' > /opt/mementic/entry.py

FROM scratch
COPY --from=build /opt/mementic /opt/mementic
COPY --chmod=755 mementic /usr/local/bin/mementic
