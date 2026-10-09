#!/usr/bin/env bash
# Helper de build/test de la sesion TSK-069.
# Envuelve el comando REAL del repo: pytest sobre `sources/` con DJANGO_SETTINGS_MODULE=config.settings.
# El interprete es el venv de la sesion (/tmp/tsk064venv), que trae las deps pineadas en sources/pyproject.toml.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${MIND_PY:-/tmp/tsk064venv/bin/python}"

# Entorno de prueba de la sesion, si lo hubiera (status=unavailable en esta sesion).
[ -f "$REPO/.mind/TSK-069/env.sh" ] && source "$REPO/.mind/TSK-069/env.sh"

cmd="${1:-test}"
shift || true

cd "$REPO/sources"

case "$cmd" in
  check)   # smoke de arranque: carga de Django + URLconf completa
    exec "$PY" -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from django.urls import get_resolver;get_resolver().url_patterns;print('OK composition root')"
    ;;
  lint)
    exec "$PY" -m ruff check "$@"
    ;;
  test)    # ACOTADO: siempre se pasan rutas/-k de ESTA tarea, nunca la suite del repo entera
    exec "$PY" -m pytest "$@"
    ;;
  *)
    echo "uso: build.sh {check|lint|test} [args]" >&2; exit 2
    ;;
esac
