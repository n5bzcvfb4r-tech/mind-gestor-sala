#!/usr/bin/env bash
#
# Ejecuta el CLI de Liquibase contra el entorno local (Oracle en Docker).
#
# Uso:
#   ./local/liquibase-local.sh update
#   ./local/liquibase-local.sh status
#   ./local/liquibase-local.sh rollback-count 99
#
# Sin argumentos se asume `update`.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

# Levanta la base de datos si todavia no esta arrancada.
docker compose -f "$COMPOSE_FILE" up -d oracle-facilities

if [ "$#" -eq 0 ]; then
  set -- update
fi

docker compose -f "$COMPOSE_FILE" --profile cli run --rm liquibase \
  --defaultsFile=local/facilities.properties "$@"
