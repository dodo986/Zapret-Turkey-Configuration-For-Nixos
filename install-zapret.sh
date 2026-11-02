#!/usr/bin/env bash
# Compatibility wrapper for the old one-shot installer.
#
# The real implementation lives in nix/zapret_turkey.py and is run through
# the flake, so `nix run` and `./install-zapret.sh` behave identically.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v nix >/dev/null 2>&1; then
  echo "error: nix is not installed or not in PATH" >&2
  exit 1
fi

exec nix \
  --extra-experimental-features "nix-command flakes" \
  run "$repo_dir" -- "$@"
