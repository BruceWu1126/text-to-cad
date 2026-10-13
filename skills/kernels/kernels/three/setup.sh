#!/bin/sh
# One-time setup for the three.js kernel: its packages (package.json beside
# this script) in the kernel's runtime folder
# ($CAD_KERNELS_HOME/three, by default ~/.cache/text-to-cad/kernels/three).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
runtime="${CAD_KERNELS_HOME:-$HOME/.cache/text-to-cad/kernels}/three"
mkdir -p "$runtime"
cp "$here/package.json" "$runtime/package.json"
npm install --prefix "$runtime" --no-package-lock --no-audit --no-fund
echo "three.js kernel ready: $runtime/node_modules"
