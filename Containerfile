# SPDX-License-Identifier: Apache-2.0
# Reach Around image. Builds with Docker and Podman, runs rootless, needs no socket.
#
#   build:  podman build -f Containerfile -t reach-around .
#   run:    podman run --rm --read-only --cap-drop ALL \
#             --security-opt no-new-privileges:true --tmpfs /tmp \
#             -v ./examples/instance:/config:ro -v ./out:/out \
#             reach-around build --config /config/site.yaml --out /out
#
# Base: python:3.13-slim, pinned by the multi-architecture index digest.
# To bump it, pull the new tag, copy the index digest here, and rebuild.
ARG PYTHON_IMAGE=docker.io/library/python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39

# ---- builder: hashed installs into a virtualenv ----
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /src
COPY requirements.txt requirements-build.txt ./
# Every dependency, runtime and build backend, is installed by hash.
RUN pip install --require-hashes -r requirements-build.txt \
 && pip install --require-hashes -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
# Dependencies are already installed and verified; install only this package.
RUN pip install --no-deps --no-build-isolation .

# ---- runtime: venv only, fixed non-root user ----
FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="reach-around" \
      org.opencontainers.image.description="Build a static link page from a validated config." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/ChiefGyk3D/Reach-Around"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
RUN groupadd --gid 10001 reach \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin reach \
 && mkdir /config /out \
 && chown 10001:10001 /out
COPY --from=builder /opt/venv /opt/venv
USER 10001:10001
WORKDIR /out
# Writable paths at run time are /out (the site) and /tmp (a tmpfs); the root
# filesystem can be read-only. /config is mounted read-only by the caller.
VOLUME ["/out"]
ENTRYPOINT ["reach-around"]
CMD ["--help"]
