#!/usr/bin/env bash
# Helper de build de la sesion TSK-003. Envuelve el comando REAL de este repo
# (Django + pytest-django bajo `sources/`, arquetipo container-python).
#
#   build.sh check            -> django system check (arranque del composition root)
#   build.sh smoke            -> resuelve las URLs del contrato sin levantar servidor
#   build.sh test [args...]   -> pytest ACOTADO a lo que se le pase (nunca la suite entera)
#   build.sh lint [args...]   -> ruff check
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="/tmp/tsk003venv"
PY="${VENV}/bin/python"

cd "${RAIZ}/sources"
export DJANGO_SETTINGS_MODULE="config.settings"
export PYTHONPATH="${RAIZ}/sources"

# Entorno de prueba de la sesion, si la plataforma pudo levantarlo.
if [[ -f "${RAIZ}/.mind/TSK-003/env.sh" ]]; then
  # shellcheck disable=SC1091
  source "${RAIZ}/.mind/TSK-003/env.sh"
fi

case "${1:-check}" in
  check) shift || true; exec "${PY}" manage.py check "$@" ;;
  smoke) shift || true; exec "${PY}" "${RAIZ}/.mind/TSK-003/smoke.py" "$@" ;;
  lint)  shift || true; exec "${VENV}/bin/ruff" check "$@" ;;
  test)  shift || true; exec "${PY}" -m pytest "$@" ;;
  *)     exec "${PY}" manage.py "$@" ;;
esac
