#!/usr/bin/env bash
# Thin entry point: all validation and packaging live in the stdlib Python CLI.
set -euo pipefail
exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/package-proton.py" "$@"
