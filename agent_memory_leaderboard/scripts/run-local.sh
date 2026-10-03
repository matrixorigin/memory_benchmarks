#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
set -a
. "${AML_ENV_FILE:-./var/local.env}"
set +a
case "${1:-adapter}" in
  memoria) exec "$MEMORIA_BIN" serve --port 8100 ;;
  adapter) exec .venv/bin/uvicorn src.app:create_app --factory --host 127.0.0.1 --port 8000 ;;
  smoke) exec .venv/bin/python -m src.smoke ;;
  benchmark) shift; exec .venv/bin/python -m src.benchmark "$@" ;;
  suite) shift; exec .venv/bin/python -m src.suite "$@" ;;
  *) echo "Usage: $0 [memoria|adapter|smoke|benchmark|suite]" >&2; exit 2 ;;
esac
