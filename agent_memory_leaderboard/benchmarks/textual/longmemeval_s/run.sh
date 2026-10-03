#!/bin/sh
set -eu
benchmark_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$benchmark_dir/../../../scripts/run-local.sh" benchmark --kind longmemeval --output "$benchmark_dir/results/run.json" "$@"
