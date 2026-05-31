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

echo "==> building image kofta-smoke"
docker build -t kofta-smoke "$REPO/docker"

echo "==> running smoke test (repo mounted read-only at /repo)"
docker run --rm -v "$REPO:/repo:ro" kofta-smoke
