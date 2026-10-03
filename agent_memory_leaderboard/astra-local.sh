#!/bin/sh
set -eu

astra_bin=${ASTRA_BIN:-}
if [ "${1:-}" = "--astra-dir" ]; then
  if [ "$#" -lt 2 ] || [ -z "$2" ]; then
    echo "Usage: $0 --astra-dir PATH [astra arguments...]" >&2
    exit 2
  fi
  astra_bin=$2/target/debug/astra
  shift 2
fi
if [ -z "$astra_bin" ]; then
  echo "Specify --astra-dir PATH or set ASTRA_BIN to the executable path." >&2
  exit 2
fi
# Resolve relative paths before switching to the benchmark project directory.
case "$astra_bin" in
  /*) ;;
  *) astra_bin=$PWD/$astra_bin ;;
esac
if [ ! -x "$astra_bin" ]; then
  echo "Astra binary not found: $astra_bin (use --astra-dir or ASTRA_BIN)" >&2
  exit 1
fi

# Project MCP discovery uses the CLI's working directory.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"

export ASTRA_LOCAL_STATE_ROOT="${ASTRA_LOCAL_STATE_ROOT:-${ASTRA_STATE_ROOT:-$HOME/.astra-local}}"
# Let an explicit --api-url override the environment through Astra's own parser.
export ASTRA_API_URL="${ASTRA_API_URL:-http://127.0.0.1:17001}"
exec "$astra_bin" --profile local "$@"
