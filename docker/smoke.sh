#!/usr/bin/env bash
# Host-side one-command wrapper for the SHS C-path runtime smoke test.
#
# Prereqs (Apple Silicon / macOS): brew install colima docker && colima start
# Then just:  ./docker/smoke.sh
#
# Builds the verification image and runs the in-container smoke test against the
# repo (bind-mounted read-only). Exits non-zero if the SHS C seam doesn't fire.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# KOFTA's runtime has x86_64-only inline asm, so the Docker host must be x86_64.
# The brew `docker` CLI has no buildx, so cross-building an amd64 image on an
# arm64 VM isn't possible -- the Colima VM itself has to be x86_64.
ARCH="$(docker version --format '{{.Server.Arch}}' 2>/dev/null || true)"
if [ "$ARCH" != "amd64" ]; then
  echo "==> ERROR: Docker server arch is '${ARCH:-unknown}', but KOFTA's runtime" >&2
  echo "    needs x86_64. Restart Colima as an x86_64 VM, then re-run:" >&2
  echo "        colima stop && colima start --arch x86_64 --memory 4" >&2
  exit 1
fi

echo "==> building image kofta-smoke"
docker build -t kofta-smoke "$REPO/docker"

# Fail loudly if the build silently didn't produce the tag.
docker image inspect kofta-smoke >/dev/null 2>&1 || {
  echo "==> ERROR: build did not produce image kofta-smoke" >&2; exit 1; }

echo "==> running smoke test (repo mounted read-only at /repo)"
docker run --rm -v "$REPO:/repo:ro" kofta-smoke
