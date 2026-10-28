# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
ARG MEMENTIC_VERSION

RUN pip install --no-cache-dir --target /opt/mementic \
    "https://github.com/pakdelm/mementic/archive/refs/tags/v${MEMENTIC_VERSION}.tar.gz"
RUN test -d "/opt/mementic/mementic-${MEMENTIC_VERSION}.dist-info"
RUN printf 'from mementic.cli import app\napp()\n' > /opt/mementic/entry.py

FROM scratch
COPY --from=build /opt/mementic /opt/mementic
COPY --chmod=755 mementic /usr/local/bin/mementic
