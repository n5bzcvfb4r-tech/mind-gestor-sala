#!/usr/bin/env bash
# Helper de build de la sesion TSK-020. Envuelve el comando REAL del repo (pytest sobre `sources/`).
#   ./.mind/TSK-020/build.sh check              -> django check + smoke de import
#   ./.mind/TSK-020/build.sh test <ruta/-k ...> -> pytest acotado a lo indicado
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${MIND_VENV:-/tmp/tsk013venv}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "No hay interprete en $VENV" >&2; exit 2; }
cd "$RAIZ/sources"
export DJANGO_SETTINGS_MODULE=config.settings
[ -f "$RAIZ/.mind/TSK-020/env.sh" ] && . "$RAIZ/.mind/TSK-020/env.sh"
accion="${1:-check}"; shift || true
case "$accion" in
  check) "$PY" -m django check "$@" ;;
  smoke) "$PY" -c "import django;django.setup();import apps.avisos.credenciales.entrega as m;print('import OK', m.__name__)" ;;
  test)  "$PY" -m pytest "$@" ;;
  *)     "$PY" -m "$accion" "$@" ;;
esac
