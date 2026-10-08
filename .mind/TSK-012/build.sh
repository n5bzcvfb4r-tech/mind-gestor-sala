#!/usr/bin/env bash
# Helper de build de la sesión TSK-012 (NO entregable, no se commitea).
# Envuelve el comando real del repo: Django (container-python) bajo sources/.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV=/tmp/tsk009venv
PY="$VENV/bin/python"
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONPATH="$ROOT/sources"
cmd="${1:-check}"; shift || true
case "$cmd" in
  check)   cd "$ROOT/sources" && "$PY" manage.py check "$@" ;;
  imports) cd "$ROOT/sources" && "$PY" -c "import django;django.setup();import config.urls;print('config.urls OK:',len(config.urls.urlpatterns),'patterns')" ;;
  test)    cd "$ROOT/sources" && "$VENV/bin/pytest" "$@" ;;
  collect) cd "$ROOT/sources" && "$VENV/bin/pytest" --collect-only -q "$@" ;;
  ruff)    cd "$ROOT/sources" && "$VENV/bin/ruff" check "$@" ;;
  py)      cd "$ROOT/sources" && "$PY" "$@" ;;
  *) echo "uso: build.sh {check|imports|test|collect|ruff|py}"; exit 2 ;;
esac
