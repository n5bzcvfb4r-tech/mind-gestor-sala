#!/usr/bin/env bash
# Helper de build/test de la sesion TSK-005.
# Envuelve el comando REAL de este repo (pytest + pytest-django sobre `sources/`),
# con el interprete de la sesion (/tmp/tsk005venv) que trae las dependencias pineadas
# de sources/pyproject.toml mas `hypothesis` (PBT-011, PBT-012).
#
#   ./.mind/TSK-005/build.sh check                -> import del composition root (smoke)
#   ./.mind/TSK-005/build.sh lint                 -> ruff check + format --check
#   ./.mind/TSK-005/build.sh test <expr|rutas...> -> pytest ACOTADO a lo indicado
#
# ACOTA SIEMPRE: nunca se lanza la suite del repo entero desde aqui.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${MIND_PY:-/tmp/tsk005venv/bin/python}"
cd "${RAIZ}/sources"

# El entorno de prueba de la sesion (Oracle) puede no existir: status=unavailable.
if [[ -f "${RAIZ}/.mind/TSK-005/env.sh" ]]; then
  # shellcheck disable=SC1091
  source "${RAIZ}/.mind/TSK-005/env.sh"
fi

case "${1:-test}" in
  check)
    # Smoke del composition root SIN tocar la base: `config.wsgi` ejecuta ademas la
    # verificacion de catalogos maestros contra Oracle, que en esta sesion no existe
    # (env.json status=unavailable). Se comprueba lo que si depende del codigo: que
    # Django arranca, que `config.urls` carga y que el arbol de rutas se resuelve.
    exec "${PY}" -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.urls import get_resolver
rutas = sorted(str(p.pattern) for p in get_resolver().url_patterns)
print('composition root OK:', len(rutas), 'includes ->', rutas)
"
    ;;
  lint)
    "${PY}" -m ruff check . && exec "${PY}" -m ruff format --check .
    ;;
  test)
    shift
    exec "${PY}" -m pytest "$@"
    ;;
  *)
    echo "uso: build.sh {check|lint|test <args>}" >&2
    exit 2
    ;;
esac
