<!-- SPDX-License-Identifier: Apache-2.0 -->
# Running Reach Around in a container

The image is built from the `Containerfile` and runs the same under Docker and
Podman. Its entry point is `reach-around`, so the arguments after the image name
are the CLI arguments.

What the image guarantees:

- It runs as the fixed non-root user `10001:10001`. It never needs root, and it
  works rootless.
- It needs no Docker socket. Nothing in this repository mounts one.
- The root filesystem can be read-only. The only writable paths are `/out`
  (where the site is written) and `/tmp` (give it a tmpfs).
- The base image is pinned by digest, and every Python dependency is installed
  with `pip install --require-hashes` from `requirements.txt` (compiled from
  `requirements.in` with `pip-compile --generate-hashes`).

Mounts:

| Path      | Mode       | Purpose                                      |
|-----------|------------|----------------------------------------------|
| `/config` | read-only  | your instance directory, with `site.yaml`    |
| `/out`    | read-write | the generated static site; emptied each build |

## Output directory permissions

The container user is uid 10001, not you. Make the output directory writable by it
before the first run:

```sh
mkdir -p out
podman unshare chown 10001:10001 out   # rootless Podman
# Docker:  sudo chown 10001:10001 out
# or, for a throwaway directory on either engine:  chmod 0777 out
```

## Docker Compose

```sh
docker compose build
docker compose run --rm reach-around
ls out/index.html
```

## Podman Compose

```sh
podman compose build
podman compose run --rm reach-around
```

`compose.yaml` sets `read_only: true`, `cap_drop: [ALL]`,
`no-new-privileges`, a tmpfs for `/tmp`, and one bind mount for `/out`. To build
your own site, change the `./examples/instance` mount to your instance directory.

## Plain `docker run` or `podman run`

```sh
podman build -f Containerfile -t reach-around:local .
podman run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp \
  -v ./examples/instance:/config:ro -v ./out:/out \
  reach-around:local build --config /config/site.yaml --out /out
```

## Podman Quadlet (systemd, rootless)

`deploy/podman/reach-around.container` runs the build as a one-shot user service
with the same hardening. It uses `UserNS=keep-id:uid=10001,gid=10001`, so the output
stays owned by you and needs no `chown`.

```sh
podman build -f Containerfile -t localhost/reach-around:local .
mkdir -p ~/.config/containers/systemd ~/reach-around/config ~/reach-around/out
cp examples/instance/* ~/reach-around/config/        # or your own instance
cp deploy/podman/reach-around.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start reach-around.service
journalctl --user -u reach-around.service
```

To rebuild on a schedule, add a `reach-around.timer` that starts the service.

## Tests

`tests/test_container_smoke.sh` builds the image and checks that it runs read-only
as a non-root user, produces `index.html` and `manifest.json`, and cannot write
outside `/out` and `/tmp`. CI runs it with both engines.

```sh
CONTAINER_ENGINE=podman bash tests/test_container_smoke.sh
CONTAINER_ENGINE=docker bash tests/test_container_smoke.sh
```

## Updating pins

- Base image: pull the new tag, copy the multi-architecture index digest into the
  `PYTHON_IMAGE` argument in `Containerfile`.
- Dependencies: edit `requirements.in`, then run
  `pip-compile --generate-hashes --allow-unsafe --output-file=requirements.txt requirements.in`.
  The build backend pin lives in `requirements-build.in` and `requirements-build.txt`.
