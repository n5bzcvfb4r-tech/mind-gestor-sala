#!/usr/bin/env bash
# Helper de build de la sesión TSK-001 (NO entregable, no se commitea).
# Envuelve el comando real del repo: Django (container-python) bajo sources/.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY=/tmp/tsk001venv/bin/python
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONPATH="$ROOT/sources"
cmd="${1:-check}"; shift || true
case "$cmd" in
  check)   cd "$ROOT/sources" && "$PY" manage.py check "$@" ;;
  imports) cd "$ROOT/sources" && "$PY" -c "import django,os;django.setup();import config.urls;print('config.urls OK:',len(config.urls.urlpatterns),'patterns')" ;;
  test)    cd "$ROOT/sources" && "$PY" -m pytest "$@" ;;
  collect) cd "$ROOT/sources" && "$PY" -m pytest --collect-only -q "$@" ;;
  ruff)    cd "$ROOT/sources" && "$(command -v ruff || echo /tmp/tsk062venv/bin/ruff)" check . "$@" ;;
  py)      "$PY" "$@" ;;
  *) echo "uso: build.sh {check|imports|test|collect|ruff|py}"; exit 2 ;;
esac
