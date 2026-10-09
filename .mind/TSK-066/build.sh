#!/usr/bin/env bash
# Helper de build de la sesion TSK-066.
# Envuelve el comando REAL del repo (Django + pytest) con un interprete disponible en la
# sesion. El arquetipo usa poetry, que NO esta instalado en este contenedor; se usa un venv
# equivalente con las dependencias del manifiesto del host (sources/pyproject.toml).
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${MIND_PY:-/tmp/tsk064venv/bin/python}"

cd "$RAIZ/sources"
export DJANGO_SETTINGS_MODULE="config.settings"
export PYTHONPATH="$RAIZ/sources"

case "${1:-check}" in
  check)  exec "$PY" manage.py check "${@:2}" ;;
  smoke)  exec "$PY" -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from config.urls import urlpatterns;print('urls OK', len(urlpatterns))" ;;
  test)
    # `--no-cov` solo es valido si pytest-cov esta instalado; en este venv NO lo esta y el flag
    # abortaba pytest con "unrecognized arguments". Se anade solo cuando el plugin existe.
    SIN_COV=()
    if "$PY" -c "import pytest_cov" >/dev/null 2>&1; then SIN_COV=(--no-cov); fi
    exec "$PY" -m pytest -p no:cacheprovider "${SIN_COV[@]}" "${@:2}"
    ;;
  lint)   exec ruff check "${@:2}" ;;
  *)      exec "$PY" "$@" ;;
esac
