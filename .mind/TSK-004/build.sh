#!/usr/bin/env bash
# Helper de build de la sesion TSK-004. Envuelve el comando REAL de este repo
# (Django + pytest-django bajo `sources/`, arquetipo container-python).
#
#   build.sh check            -> django system check (arranque del composition root)
#   build.sh test [args...]   -> pytest ACOTADO a lo que se le pase (nunca la suite entera)
#   build.sh lint [args...]   -> ruff check
#   build.sh <otro>           -> manage.py <otro>
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="/tmp/tsk004venv"
PY="${VENV}/bin/python"

cd "${RAIZ}/sources"
export DJANGO_SETTINGS_MODULE="config.settings"
export PYTHONPATH="${RAIZ}/sources"

# Entorno de prueba de la sesion, si la plataforma pudo levantarlo.
if [[ -f "${RAIZ}/.mind/TSK-004/env.sh" ]]; then
  # shellcheck disable=SC1091
  source "${RAIZ}/.mind/TSK-004/env.sh"
fi

case "${1:-check}" in
  check) shift || true; exec "${PY}" manage.py check "$@" ;;
  lint)  shift || true; exec "${VENV}/bin/ruff" check "$@" ;;
  test)  shift || true; exec "${PY}" -m pytest "$@" ;;
  *)     exec "${PY}" manage.py "$@" ;;
esac
