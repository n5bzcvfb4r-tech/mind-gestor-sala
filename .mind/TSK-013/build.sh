#!/usr/bin/env bash
# Helper de build/test de la sesion TSK-013. Envuelve el comando REAL del repo.
# Uso:  ./.mind/TSK-013/build.sh check            -> django system check (boot-gate)
#       ./.mind/TSK-013/build.sh test [args...]   -> pytest ACOTADO a lo que se le pase
set -u
VENV=/tmp/tsk013venv
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/sources" || exit 1
[ -f "$ROOT/.mind/TSK-013/env.sh" ] && . "$ROOT/.mind/TSK-013/env.sh"
case "${1:-}" in
  check) shift; exec "$VENV/bin/python" manage.py check "$@" ;;
  test)  shift; exec "$VENV/bin/python" -m pytest -p no:cacheprovider "$@" ;;
  lint)  shift; exec "$VENV/bin/python" -m ruff check "$@" ;;
  *)     echo "uso: build.sh {check|test|lint} [args]" >&2; exit 2 ;;
esac
