#!/usr/bin/env bash
set -euo pipefail

# The parity bench can build the same page the wheel ships into an isolated folder.
# All dependencies come from the root workspace lockfile, including workers and weights.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
npm --prefix "$REPO_ROOT" run build -w @text-to-cad/core >/dev/null
node "$REPO_ROOT/scripts/bundle/snapshot-runtime.mjs" "${1:-$REPO_ROOT/tmp/bench/snapshot-page}"
