#!/usr/bin/env bash
# Helper de build de la sesion TSK-063.
# Envuelve el comando REAL del repo (Django + pytest) con un interprete disponible en la
# sesion. El arquetipo usa poetry, que NO esta instalado en este contenedor; se usa un venv
# equivalente con las dependencias del manifiesto del host.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${MIND_PY:-/tmp/tsk063venv/bin/python}"

cd "$RAIZ/sources"
export DJANGO_SETTINGS_MODULE="config.settings"
export PYTHONPATH="$RAIZ/sources"

case "${1:-check}" in
  check)  exec "$PY" manage.py check "${@:2}" ;;
  smoke)  exec "$PY" -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from config.urls import urlpatterns;from django.urls import reverse;print('urls OK', len(urlpatterns))" ;;
  test)   exec "$PY" -m pytest -p no:cacheprovider --no-cov "${@:2}" ;;
  *)      exec "$PY" "$@" ;;
esac
