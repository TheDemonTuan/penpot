#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { printf '%s\n' 'usage: smoke-stack.sh <pinned-platform-compose>' >&2; exit 1; }
exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/smoke-stack.py" --compose "$1"
