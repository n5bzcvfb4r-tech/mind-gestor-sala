#!/usr/bin/env bash
# Helper de build/test de la sesion TSK-072. Envuelve el comando REAL del repo:
#   - toolchain: Django 5.2 + pytest (pyproject de `sources/`)
#   - interprete: venv efimero de la sesion (/tmp/venv072), porque el runtime no trae
#     las dependencias del proyecto instaladas ni poetry.
#   - conexion: variables del entorno de prueba (.mind/TSK-072/env.sh); settings.py ya
#     las lee como fallback (DB_HOST -> MIND_ENV_ORACLE_HOST ...).
#
# Uso:
#   ./.mind/TSK-072/build.sh check                 -> django check (smoke de arranque)
#   ./.mind/TSK-072/build.sh test <ruta-o-k-expr>  -> pytest ACOTADO a esas pruebas
#   ./.mind/TSK-072/build.sh shell <script.py>     -> ejecuta un script con django.setup()
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${MIND_PY:-/tmp/venv072/bin/python}"
# shellcheck disable=SC1091
. "$RAIZ/.mind/TSK-072/env.sh"
cd "$RAIZ/sources"
export PYTHONPATH="$RAIZ/sources${PYTHONPATH:+:$PYTHONPATH}"
case "${1:-test}" in
  check) shift; exec "$PY" manage.py check "$@" ;;
  shell) shift; exec "$PY" "$@" ;;
  test)  shift; exec "$PY" -m pytest "$@" ;;
  *)     exec "$PY" -m pytest "$@" ;;
esac
