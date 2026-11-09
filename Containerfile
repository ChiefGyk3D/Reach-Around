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
ARG PYTHON_IMAGE=docker.io/library/python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

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
# The base image (checked 2026-11-09) still ships zlib1g 1:1.3.dfsg+really1.3.1-1+b1, which
# the image scanner flags (CVE-2026-76844, CVE-2026-85091, CVE-2026-27171). Debian
# fixed them in trixie-security; take that build until the base image has it, then
# remove this step.
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade \
        zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 reach \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin reach \
 && mkdir /config /out \
 && chown 10001:10001 /out
COPY --from=builder /opt/venv /opt/venv
# The runtime never installs anything: drop pip, setuptools and wheel from the
# base image and from the venv. They bundle libraries (urllib3, msgpack) that
# the image scanner flags and that nothing here uses.
RUN /usr/local/bin/python -m pip uninstall --yes pip setuptools wheel \
 && rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* \
           /usr/local/lib/python3*/site-packages/pip* \
           /usr/local/lib/python3*/site-packages/setuptools* \
           /usr/local/lib/python3*/site-packages/pkg_resources \
           /usr/local/lib/python3*/site-packages/wheel* \
 && rm -rf /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.* \
           /opt/venv/lib/python3*/site-packages/pip* \
           /opt/venv/lib/python3*/site-packages/setuptools* \
           /opt/venv/lib/python3*/site-packages/pkg_resources \
           /opt/venv/lib/python3*/site-packages/_distutils_hack \
           /opt/venv/lib/python3*/site-packages/distutils-precedence.pth
USER 10001:10001
WORKDIR /out
# Writable paths at run time are /out (the site) and /tmp (a tmpfs); the root
# filesystem can be read-only. /config is mounted read-only by the caller.
VOLUME ["/out"]
ENTRYPOINT ["reach-around"]
CMD ["--help"]
