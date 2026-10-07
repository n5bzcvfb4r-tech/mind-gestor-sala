# Scaffolding MIND — incidencias_project

- **Arquetipos ArqRef que se construirán:** `batch`, `container-python`, `database-relational`, `frontend-application-spa` — el esqueleto de cada
  arquetipo lo aporta el API corporativo (o clónalo de ArqRef); cada repo debe
  adecuarse a su arquetipo y NUNCA salirse.
- **Contratos congelados:** 56 endpoints (openapi.yaml, 0 schemas con campos), 6 eventos (asyncapi.yaml).
- **Base pública de la API:** `/api` (`servers[0].url`). El back monta los routers para que la URL pública sea `base + path`; el front la lee del entorno.

## Layout y disciplina (PR #0)

- El layout es el del arquetipo del repo (ver «Contrato de salida del arquetipo» en cada brief): composition root y raíces de código las fija el arquetipo, no este resumen.
- Declara cada dependencia de terceros con pin en el manifiesto del arquetipo.
- `apps/app/public/assets/environments.json` trae `apiBaseUrl` por entorno; `proxy.conf.json` reenvía esa base al backend en `nx serve` (añade `"proxyConfig": "proxy.conf.json"` al target `serve` si el arquetipo no lo trae).
- `libs/api-types/src/index.ts`: los DTO del contrato, generados. Impórtalos; no declares `interface`s con el nombre de un schema.
- `playwright.config.ts` + `e2e/*.e2e-spec.ts`: seed de validación browser (smoke/functional/visual). Declara `@playwright/test` en `devDependencies` y los scripts `test:e2e*`; el runtime los ejecuta post-build cuando la policy de Playwright se dispara. Amplía specs por pantalla; no borres el seed.

## Endpoints

- `EP-001` · POST /auth/sessions → ARC-012
- `EP-002` · DELETE /auth/sessions/current → ARC-012
- `EP-003` · GET /auth/sessions/current → ARC-012
- `EP-004` · GET /auth/permissions → ARC-012
- `EP-005` · PUT /auth/password → ARC-012
- `EP-006` · PUT /auth/initial-password → ARC-012
- `EP-007` · POST /users → ARC-012
- `EP-008` · GET /users → ARC-012
- `EP-009` · GET /users/{userId} → ARC-012
- `EP-010` · PUT /users/{userId} → ARC-012
- `EP-011` · PUT /users/{userId}/role → ARC-012
- `EP-012` · GET /users/{userId}/role-history → ARC-012
- `EP-013` · GET /users/{userId}/deactivation-impact → ARC-012
- `EP-014` · POST /users/{userId}/deactivation → ARC-012
- `EP-015` · POST /users/{userId}/reactivation → ARC-012
- `EP-016` · GET /users/{userId}/status-history → ARC-012
- `EP-017` · POST /users/{userId}/password-reset → ARC-012
- `EP-018` · POST /users/{userId}/unlock → ARC-012
- `EP-019` · GET /users/credential-status → ARC-012
- `EP-020` · GET /access-audit-events → ARC-012
- `EP-021` · GET /notification-recipients/{userId} → ARC-012
- `EP-022` · GET /notification-groups/maintenance-team → ARC-012
- `EP-023` · GET /notification-recipients → ARC-012
- `EP-024` · GET /recipient-resolutions → ARC-012
- `EP-025` · POST /incidents → ARC-012
- `EP-026` · GET /incidents → ARC-012
- `EP-027` · GET /my-incidents → ARC-012
- `EP-028` · GET /incidents/{incidentId} → ARC-012
- `EP-029` · GET /incidents/{incidentId}/history → ARC-012
- `EP-030` · GET /incidents/{incidentId}/photo → ARC-012
- `EP-031` · POST /incidents/{incidentId}/assignment → ARC-012
- `EP-032` · PUT /incidents/{incidentId}/assignment → ARC-012
- `EP-033` · DELETE /incidents/{incidentId}/assignment → ARC-012
- `EP-034` · POST /incidents/{incidentId}/transitions → ARC-012
- `EP-035` · POST /incidents/{incidentId}/closure → ARC-012
- `EP-036` · PUT /incidents/{incidentId}/classification → ARC-012
- `EP-037` · GET /incidents/{incidentId}/similar-closures → ARC-012
- `EP-038` · GET /incident-activities → ARC-012
- `EP-039` · GET /incident-statistics → ARC-012
- `EP-040` · GET /incident-categories → ARC-012
- `EP-041` · PUT /incident-categories/{categoryCode} → ARC-012
- `EP-042` · GET /rooms → ARC-012
- `EP-043` · POST /rooms → ARC-012
- `EP-044` · PUT /rooms/{roomId} → ARC-012
- `EP-045` · GET /offices → ARC-012
- `EP-046` · PUT /offices/{officeId} → ARC-012
- `EP-047` · GET /notification-dispatches → ARC-012
- `EP-048` · GET /notification-dispatches/{dispatchId} → ARC-012
- `EP-049` · POST /notification-dispatches/{dispatchId}/resend → ARC-012
- `EP-050` · GET /mail-settings → ARC-012
- `EP-051` · PUT /mail-settings → ARC-012
- `EP-052` · POST /mail-settings/test-messages → ARC-012
- `EP-053` · GET /incident-retentions → ARC-012
- `EP-054` · POST /integrity-checks → ARC-012
- `EP-055` · GET /integrity-checks → ARC-012
- `EP-056` · GET /integrity-checks/{checkId} → ARC-012

## Eventos

- `EVT-001` · AvisoSolicitado (facilities.avisos.solicitado.v1)
- `EVT-002` · IntentoDeEnvioRegistrado (facilities.avisos.intento-registrado.v1)
- `EVT-003` · VencimientoRetencionRecalculado (facilities.retencion.vencimiento-recalculado.v1)
- `EVT-004` · AvisoAltaIncidenciaEntregado (facilities.avisos.alta-incidencia-entregado.v1)
- `EVT-005` · AvisoCambioEstadoEntregado (facilities.avisos.cambio-estado-entregado.v1)
- `EVT-006` · CredencialInicialEntregada (facilities.avisos.credencial-entregada.v1)
