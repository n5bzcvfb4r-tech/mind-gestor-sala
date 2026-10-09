# TSK-071 · Asignación y cambio de rol funcional con histórico inmutable

- Componente dueño: `ARC-013`
- Arquetipo del repo: `container-python` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/apps/usuarios/roles/`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
Asignación en el acto del alta que deja exactamente una fila de rol vigente con `assigned_by_user_id` y `assigned_at`, verificada por consulta a BD que devuelve 1 y solo 1 rol vigente (AC-ROL-01); tests 400 sin `role_code` o con valor fuera de `cat_rol` y 409 «El usuario ya tiene un rol asignado», sin crear asignación (AC-ROL-02). Cambio de rol transaccional que cierra la asignación anterior informando `valid_to` y abre la nueva en el mismo instante, con test de que nunca coexisten dos vigentes y de que el listado recargado muestra el rol nuevo (AC-ROL-03); tests 400 rol inexistente, 409 mismo rol y 422 autorretirada del rol de administración (AC-ROL-03 de REQ-043). Histórico en `usuario_historico` con test que, tras alta y cambio, devuelve exactamente dos entradas en orden descendente con `previous_role_code`, `new_role_code`, `changed_by_user_id` y `changed_at`, y que todo intento de modificarlas o borrarlas responde 403/405 dejando el histórico intacto (AC-ROL-05). Test 403 uniforme para EMPLEADO y TECNICO sobre asignación, cambio e histórico, sin revelar la existencia del recurso (AC-ROL-06). Persistencia verificada con Testcontainers Oracle sobre los nombres físicos `usuario` y `usuario_historico`. Extiende el servicio de usuarios de TSK-08 sin redeclarar sus modelos.

## Oráculos de verificación (dod-oracles) — OBLIGATORIO

El DoD se evalúa por **comportamiento**, no porque exista un fichero o un string «implementado». Lo siguiente es **Blocker** si lo usas como entrega de producto (los dobles solo valen en tests):

- **Email / notificación:** cliente real o puerto inyectable (`aiosmtplib`, SES, SendGrid, …) + test que verifica que se invocó el envío. **`log.info` / `print` / «Simula el envío» ≠ email.**
- **Auth / rol (p. ej. ADMINISTRADOR):** dependency o middleware que devuelve 401/403 sin credencial/rol; tests con y sin permiso. **Un CRUD abierto no cumple «solo admin».**
- **Evento / AsyncAPI:** productor que publica al canal declarado; test que captura el publish. **Loguear el payload ≠ publicar el evento.**
- **Persistencia:** driver del stack del arquetipo (Motor/SQLAlchemy/…) contra el motor de prueba o Testcontainers. **`dict` / `db_*` in-memory en el módulo de producto ≠ base de datos.**

Si el entorno de prueba no levanta el servicio necesario: escribe el código de producto real + tests, declara Warning `entorno-de-prueba`, y **NO** sustituyas el DoD con un fake en el código entregado.

## Composition root (composition-root) — OBLIGATORIO

Un módulo con router/controller que **no está montado** en el composition root del proceso NO cuenta como entregado.

Raíces reales del arquetipo `container-python`: `docker/`, `sources/`. El composition root y el código nuevo viven BAJO esas raíces; no abras un segundo árbol (`src/` junto a `sources/`, `backend/` junto a `apps/`).

En ESTA misma tarea (aunque `zone_paths` no lo liste):
1. Localiza el composition root (`src/main.py`, `app/main.py`, `*Application.java` + scan, `Program.cs`, …).
2. Si no existe (repo vacío / BYO), créalo siguiendo el arquetipo y monta ahí tu router — no dejes el módulo huérfano.
3. Registra el router/controller nuevo (`include_router`, bean MVC, route config…). Si defines `public_router` (o equivalente público), **móntalo también**.
4. Prefijos/paths alineados con `openapi.yaml` del repo (o `.mind`).
5. Smoke: import/arranque del composition root no falla por tu cambio (p. ej. `from src.main import app` / `./mvnw -q compile`).

**Excepción a zone_paths:** el composition root y el manifiesto de deps del host (`requirements.txt` / `pom.xml` / …) SÍ se tocan para cablear lo entregado. No refactores módulos ajenos ni amplíes alcance de negocio.

## Lo entregado tiene que ARRANCAR (boot-gate) — OBLIGATORIO

Compilar no es arrancar. Antes de entregar, arranca el proceso y golpea un endpoint: es lo que separa «el módulo compila» de «el producto funciona», y no lo ve ningún compilador.

1. **Configuración por entorno.** Toda conexión a un sistema externo (base de datos, cola, API) parametrizada con valor por defecto (`${VAR:default}`). Nada de hosts fijos en el fuente.
2. **Nada a medio cablear.** Si escribes una consulta, un repositorio o un cliente, INVÓCALO desde el camino real. El trabajo hecho y sin cablear es la firma de una feature entregada a medias.
3. **Despliegue coherente.** Si tocas `docker-compose.yml`: el motor de la imagen, la URL de conexión y el driver declarado en el manifiesto son UN SOLO hecho. `depends_on` sobre un servicio con estado lleva `condition: service_healthy`. Imágenes base multiarch, y no referencies contextos ni Dockerfile que no existan.

**Un `TODO`, un `not implemented` o un retorno vacío como cuerpo ÚNICO de una función es un bloqueante de entrega, no una nota.** Un endpoint que responde `200 OK` con algo que no depende de ninguna entrada ni de ninguna consulta no es una feature: es una fachada.

## Imports de tests (test-imports) — OBLIGATORIO

Los tests y el `conftest` deben usar **el mismo composition root y el mismo prefijo de paquete** que el código productivo. Un árbol inventado (`src.app.main` cuando el app está en `src/main.py`) no es entrega.

1. Localiza el composition root real (`src/main.py`, `app/main.py`, …) — el mismo composition root.
2. Importa la app **solo** desde ese módulo (p. ej. si existe `src/main.py` y NO `src/app/main.py` → `from src.main import app`). **PROHIBIDO** `from src.app.main import app`.
3. Elige UN prefijo de paquete alineado con el código productivo (`from src.app.<mod>…` **o** `from app.<mod>…` con `PYTHONPATH`/`pytest.ini` coherente). No mezcles ambos en la misma suite.
4. Declara la política en `pytest.ini` / `pyproject.toml` (`pythonpath`) si aún no existe.
5. Importa solo símbolos que **existen** en el fuente (lee el fichero): no inventes `get_page_service` en `service.py` si vive en `router.py`; no inventes clases (`Class` vs `ClassModel`).
6. Smoke: `python -c "from src.main import app"` (o el import canónico) y, si hay pytest, una recolección sin `ModuleNotFoundError`.

## Los tests tienen que EJECUTARSE (suite-exec) — OBLIGATORIO

Un fichero de test que ningún runner recoge es PEOR que no tenerlo: da una señal de cobertura falsa. Antes de entregar:

1. **Cuenta.** Los tests que has escrito y los que el runner ejecuta tienen que ser los mismos. Ejecuta la suite y comprueba el número.
2. **Corre en un clon limpio.** Sin variables de entorno de la plataforma. Todo `process.env.X` con valor por defecto, y toda dependencia externa de test declarada y levantada por el propio repo.
3. **No parchees la aplicación desde el test.** Si el contexto no levanta, el arreglo va en la configuración productiva. Un `@ComponentScan`/`@EntityScan`/`@EnableJpaRepositories` en una clase de test pone el test en verde y deja el producto roto.
4. **Surefire vs failsafe.** Surefire recoge `*Test`/`Test*`/`*Tests`. Para `*IT` hace falta `maven-failsafe-plugin` **con sus ejecuciones declaradas** (`integration-test` + `verify`): sin él, un `AlgoIT.java` se compila, se commitea y no se ejecuta jamás.
   - **Comprueba primero si el `pom.xml` ya lo trae**, que es lo normal en el esqueleto del arquetipo. Si no está, **configúralo**: el `pom.xml` del host es una EXCEPCIÓN explícita a `zone_paths` (ver composition-root), así que tocarlo para cablear lo que entregas es parte del encargo, no salirse de alcance.
   - Y ojo al comando: `mvn test` ejecuta SOLO los unitarios y sale en verde aunque el IT no haya corrido. El que valida la entrega es **`mvn verify`**. Si cuentas los tests ejecutados con `mvn test`, vas a contar de menos.
5. **Datasource de test propio.** Aislado y efímero (Testcontainers con la imagen real; H2 en modo Oracle como mínimo). Sin él los tests heredan el datasource de producción y salen a buscar la base de datos real.

## Pruebas que debes implementar (TEST_SUITES)
La fase TEST_SUITES ya definió estas pruebas para los requisitos/historias/casos de uso que esta tarea cubre. Impleméntalas como código de test EJECUTABLE con el framework real del arquetipo (ver «Política de imports en tests» y «Ejecución de la suite» arriba) — no basta con que la lógica pase, tiene que existir el test que lo demuestre.

### PBT-003 — Para toda secuencia de altas y cambios de rol cuando se aplica sobre un usuario entonces este tiene exactamente un rol vigente y su vigencia es continua y sin solapes
**Capa:** backend · **Prioridad:** critical · **Categoría:** data_integrity
**Componente objetivo:** Servicio de asignación y cambio de rol de usuario
Propiedad con estado del modelo de roles: tras cualquier secuencia válida de alta y cambios sucesivos, el usuario mantiene siempre exactamente una asignación vigente, el cierre de la anterior coincide en el instante con la apertura de la nueva, y el histórico reconstruye la secuencia sin huecos ni solapes.
**Resultado esperado:** Para toda secuencia generada y en cualquier punto intermedio de ella, el número de asignaciones vigentes del usuario es exactamente 1, el instante de cierre de cada asignación coincide con el de apertura de la siguiente y el número de entradas de histórico es igual al número de operaciones aceptadas
**Precondiciones:**
- Cada operación de la secuencia la ejecuta un ADMINISTRADOR distinto del usuario destino
- Cada rol destino generado pertenece al catálogo cerrado de roles
- El usuario destino existe y no está eliminado
**Datos de prueba:**
- Secuencias de 1 a 15 operaciones compuestas por un alta inicial y cambios de rol posteriores
- Roles destino extraídos del catálogo cerrado, incluyendo repeticiones del rol vigente
- Instantes de ejecución estrictamente crecientes y, en parte de los casos, muy próximos entre sí
**Herramientas sugeridas:** Hypothesis, hypothesis.stateful, pytest
```gherkin
@property @stateful @invariant
Scenario Outline: Una persona tiene siempre exactamente un rol vigente, haga los cambios que haga
  Given cualquier usuario dado de alta con un rol del catalogo cerrado
  And cualquier secuencia de 1 a 15 cambios de rol ejecutados por un ADMINISTRADOR distinto del usuario destino
  When se aplica la secuencia completa paso a paso
  Then tras cada paso el usuario tiene exactamente una asignacion de rol vigente
  And el instante de cierre de cada asignacion coincide con el de apertura de la siguiente, sin solape ni hueco
  And el historico contiene una entrada por cada cambio aceptado, con autor y fecha
```

## Imports canónicos (canonical-imports) — OBLIGATORIO

Usa **solo** módulos que existen en el repo o que sembró el scaffolding PR #0.

- Entrypoint típico: `src/backend/app/main.py` con paquete `app` (PYTHONPATH = `src/backend/`).
- DB / email / deps: `app.database`, `app.email` — **NO** inventar `app.core.database`, `app.core.email` ni `app.dependencies` si no hay fichero.
- Tests y producto: **el mismo prefijo** (`from app.modules…`, no mezclar con `app.core…` fantasma).
- Si importas un paquete de terceros (`sqlalchemy`, `motor`, …), decláralo pineado en `requirements.txt` / `pyproject.toml` del host.

## Dependencias pineadas (dependency-pins) — OBLIGATORIO

Un manifiesto sin versiones (o incompleto frente a los imports) NO es entrega válida.

1. **Python:** en `requirements.txt` / deps de `pyproject.toml` cada paquete lleva pin (`==` preferido, o rango acotado `>=x,<y`). **PROHIBIDO** listar solo el nombre (`fastapi`, `uvicorn`, `pydantic`).
2. **Completitud:** toda librería de terceros que importes (`sqlalchemy`, `pydantic`, `motor`, …) debe figurar en el manifiesto.
3. **API ↔ major:** el código debe ser compatible con el major pineado. Si usas `__modify_schema__` / `@validator` / `from_orm` (Pydantic v1), pinea `pydantic>=1.10,<2` (o equivalente). Si pegas Pydantic 2, usa APIs v2 (`field_validator`, `model_validate`). NUNCA código v1 + install latest.
4. **Node:** versiones en `package.json` + `package-lock.json` cuando toques deps; no dejes dependencias sin versión.
5. Actualiza el manifiesto del host en ESTA tarea (misma excepción de zona que el composition root).

## Extiende la zona, no la reimplementes (zone-extend) — OBLIGATORIO

Tus zonas (`sources/apps/usuarios/roles/`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

1. **Lista y lee** los ficheros bajo la zona (`ls` / abre `service.py`, `router.py`, …).
2. **EDIT/EXTIENDE** clases, funciones y exports existentes. **PROHIBIDO** una segunda `class`/`def`/export con el **mismo nombre** en el mismo fichero (en Python gana la última y el resto es basura).
3. Si esta tarea es «API pública» / «consulta» sobre el mismo dominio que un CRUD previo, **reutiliza** servicios y modelos; añade solo el router/handlers públicos (montados en el composition root).
4. Un módulo = un dueño semántico de cada símbolo top-level. Si hace falta otro tipo, **nómbralo distinto** o factoriza — no pegues un duplicado al EOF.

El runtime puede anexar la lista real de ficheros presentes en la zona al arrancar la sesión.

## Contrato API congelado (api-contract) — LEY

El `openapi.yaml` del repo (PR #0 / C.2) es el contrato de esta API. Esta tarea no es dueña de ningún endpoint: la tabla va como CONTEXTO — no montes rutas nuevas, y si tocas las que hay, respétalas al pie de la letra.

| EP | Método | Path | Request | Response | Status | Public | Roles |
|----|--------|------|---------|----------|--------|--------|-------|
| `EP-001` | **POST** | `/auth/sessions` | `LoginRequest` | `SessionDetail` | 201 | N | deny-all |
| | | _Inicia sesión con usuario y contraseña propios y devuelve la sesión con el rol vigente_ | | | | | |
| `EP-002` | **DELETE** | `/auth/sessions/current` | `—` | `—` | 204 | N | deny-all |
| | | _Cierra la sesión del usuario y la revoca en servidor_ | | | | | |
| `EP-003` | **GET** | `/auth/sessions/current` | `—` | `SessionContext` | 200 | N | deny-all |
| | | _Devuelve el contexto del usuario autenticado con su identidad y rol vigente_ | | | | | |
| `EP-004` | **GET** | `/auth/permissions` | `—` | `EffectivePermissions` | 200 | N | deny-all |
| | | _Devuelve las operaciones permitidas y el alcance de datos del rol vigente_ | | | | | |
| `EP-005` | **PUT** | `/auth/password` | `PasswordChangeRequest` | `PasswordChangeResult` | 200 | N | deny-all |
| | | _Cambia la contraseña del propio usuario aportando la actual_ | | | | | |
| `EP-006` | **PUT** | `/auth/initial-password` | `InitialPasswordRequest` | `PasswordChangeResult` | 200 | N | deny-all |
| | | _Establece la contraseña definitiva en el primer acceso tras alta o restablecimiento_ | | | | | |
| `EP-007` | **POST** | `/users` | `UserCreateRequest` | `UserDetail` | 201 | N | deny-all |
| | | _Da de alta un usuario con nombre, correo corporativo y rol, y emite su credencial inicial_ | | | | | |
| `EP-008` | **GET** | `/users` | `—` | `UserListPage` | 200 | N | deny-all |
| | | _Lista el censo de usuarios con su rol y estado, con búsqueda, filtros y paginación_ | | | | | |
| `EP-009` | **GET** | `/users/{userId}` | `—` | `UserDetail` | 200 | N | deny-all |
| | | _Consulta la ficha de un usuario con su rol vigente y su trazabilidad_ | | | | | |
| `EP-010` | **PUT** | `/users/{userId}` | `UserUpdateRequest` | `UserDetail` | 200 | N | deny-all |
| | | _Modifica el nombre y el correo corporativo de un usuario activo_ | | | | | |
| `EP-011` | **PUT** | `/users/{userId}/role` | `RoleChangeRequest` | `UserRoleDetail` | 200 | N | deny-all |
| | | _Cambia el rol funcional vigente de un usuario_ | | | | | |
| `EP-012` | **GET** | `/users/{userId}/role-history` | `—` | `RoleHistoryPage` | 200 | N | deny-all |
| | | _Consulta el histórico inmutable de asignaciones y cambios de rol de un usuario_ | | | | | |
| `EP-013` | **GET** | `/users/{userId}/deactivation-impact` | `—` | `DeactivationImpact` | 200 | N | deny-all |
| | | _Devuelve el impacto de dar de baja a un usuario sobre sus incidencias y avisos_ | | | | | |
| `EP-014` | **POST** | `/users/{userId}/deactivation` | `UserDeactivationRequest` | `UserDetail` | 201 | N | deny-all |
| | | _Desactiva lógicamente un usuario con motivo, revocando sus sesiones_ | | | | | |
| `EP-015` | **POST** | `/users/{userId}/reactivation` | `UserReactivationRequest` | `UserDetail` | 201 | N | deny-all |
| | | _Reactiva un usuario inactivo devolviéndole su rol previo_ | | | | | |
| `EP-016` | **GET** | `/users/{userId}/status-history` | `—` | `AccountStatusHistoryPage` | 200 | N | deny-all |
| | | _Consulta el histórico de cambios de estado de cuenta y los datos de baja_ | | | | | |
| `EP-017` | **POST** | `/users/{userId}/password-reset` | `PasswordResetRequest` | `PasswordResetResult` | 201 | N | deny-all |
| | | _Restablece la contraseña de un usuario activo generando una credencial temporal_ | | | | | |
| `EP-018` | **POST** | `/users/{userId}/unlock` | `AccountUnlockRequest` | `AccountLockStatus` | 201 | N | deny-all |
| | | _Desbloquea una cuenta bloqueada por intentos fallidos sin alterar su contraseña_ | | | | | |
| `EP-019` | **GET** | `/users/credential-status` | `—` | `CredentialStatusPage` | 200 | N | deny-all |
| | | _Lista el estado de credencial de los usuarios (cambio pendiente, bloqueo, último acceso)_ | | | | | |
| `EP-020` | **GET** | `/access-audit-events` | `—` | `AccessAuditEventPage` | 200 | N | deny-all |
| | | _Consulta la auditoría inmutable de accesos y operaciones sensibles_ | | | | | |
| `EP-021` | **GET** | `/notification-recipients/{userId}` | `—` | `RecipientDetail` | 200 | N | deny-all |
| | | _Resuelve el destinatario de aviso de un usuario y su notificabilidad_ | | | | | |
| `EP-022` | **GET** | `/notification-groups/maintenance-team` | `—` | `RecipientGroupDetail` | 200 | N | deny-all |
| | | _Resuelve la composición vigente del colectivo equipo de mantenimiento_ | | | | | |
| `EP-023` | **GET** | `/notification-recipients` | `—` | `RecipientVerificationPage` | 200 | N | deny-all |
| | | _Verifica qué usuarios reciben avisos y cuáles quedan excluidos con su motivo_ | | | | | |
| `EP-024` | **GET** | `/recipient-resolutions` | `—` | `RecipientResolutionPage` | 200 | N | deny-all |
| | | _Consulta el registro inmutable de resoluciones de destinatarios del directorio_ | | | | | |
| `EP-025` | **POST** | `/incidents` | `IncidentCreateRequest` | `IncidentDetail` | 201 | N | deny-all |
| | | _Da de alta una incidencia de sala con categoría, descripción y foto opcional_ | | | | | |
| `EP-026` | **GET** | `/incidents` | `—` | `IncidentListPage` | 200 | N | deny-all |
| | | _Consulta la bandeja completa de incidencias con filtros, búsqueda, orden y paginación_ | | | | | |
| `EP-027` | **GET** | `/my-incidents` | `—` | `IncidentListPage` | 200 | N | deny-all |
| | | _Consulta el listado de las incidencias reportadas por el propio empleado_ | | | | | |
| `EP-028` | **GET** | `/incidents/{incidentId}` | `—` | `IncidentDetail` | 200 | N | deny-all |
| | | _Consulta el detalle de una incidencia con su estado, responsable y transiciones disponibles_ | | | | | |
| `EP-029` | **GET** | `/incidents/{incidentId}/history` | `—` | `IncidentHistoryPage` | 200 | N | deny-all |
| | | _Consulta el historial cronológico de cambios de una incidencia con autor y fecha_ | | | | | |
| `EP-030` | **GET** | `/incidents/{incidentId}/photo` | `—` | `IncidentPhotoContent` | 200 | N | deny-all |
| | | _Descarga la foto adjunta de una incidencia previa verificación de integridad_ | | | | | |
| `EP-031` | **POST** | `/incidents/{incidentId}/assignment` | `SelfAssignmentRequest` | `IncidentAssignmentDetail` | 201 | N | deny-all |
| | | _Permite a un técnico autoasignarse una incidencia sin responsable_ | | | | | |
| `EP-032` | **PUT** | `/incidents/{incidentId}/assignment` | `AssignmentChangeRequest` | `IncidentAssignmentDetail` | 200 | N | deny-all |
| | | _Reasigna una incidencia no cerrada a otro técnico de mantenimiento activo_ | | | | | |
| `EP-033` | **DELETE** | `/incidents/{incidentId}/assignment` | `AssignmentReleaseRequest` | `IncidentAssignmentDetail` | 204 | N | deny-all |
| | | _Libera la incidencia asignada indicando el motivo y la devuelve a tomable_ | | | | | |
| `EP-034` | **POST** | `/incidents/{incidentId}/transitions` | `StatusTransitionRequest` | `IncidentDetail` | 201 | N | deny-all |
| | | _Ejecuta una transición de estado válida del ciclo de vida de la incidencia_ | | | | | |
| `EP-035` | **POST** | `/incidents/{incidentId}/closure` | `IncidentClosureRequest` | `IncidentDetail` | 201 | N | deny-all |
| | | _Cierra una incidencia resuelta aportando el comentario de resolución obligatorio_ | | | | | |
| `EP-036` | **PUT** | `/incidents/{incidentId}/classification` | `IncidentReclassificationRequest` | `IncidentDetail` | 200 | N | deny-all |
| | | _Reclasifica la sala o la categoría de una incidencia no cerrada_ | | | | | |
| `EP-037` | **GET** | `/incidents/{incidentId}/similar-closures` | `—` | `SimilarClosurePage` | 200 | N | deny-all |
| | | _Consulta los cierres anteriores de la misma sala y categoría con su resolución_ | | | | | |
| `EP-038` | **GET** | `/incident-activities` | `—` | `IncidentActivityPage` | 200 | N | deny-all |
| | | _Consulta el registro de actividad reciente de todas las incidencias con filtros_ | | | | | |
| `EP-039` | **GET** | `/incident-statistics` | `—` | `IncidentCountSummary` | 200 | N | deny-all |
| | | _Devuelve el recuento agregado de incidencias por oficina, sala o categoría_ | | | | | |
| `EP-040` | **GET** | `/incident-categories` | `—` | `IncidentCategoryList` | 200 | N | deny-all |
| | | _Consulta el catálogo cerrado de categorías de incidencia_ | | | | | |
| `EP-041` | **PUT** | `/incident-categories/{categoryCode}` | `IncidentCategoryUpdateRequest` | `IncidentCategoryDetail` | 200 | N | deny-all |
| | | _Renombra, reordena o activa/desactiva una categoría del catálogo cerrado_ | | | | | |
| `EP-042` | **GET** | `/rooms` | `—` | `RoomList` | 200 | N | deny-all |
| | | _Consulta el catálogo de salas agrupadas por oficina con búsqueda_ | | | | | |
| `EP-043` | **POST** | `/rooms` | `RoomCreateRequest` | `RoomDetail` | 201 | N | deny-all |
| | | _Da de alta una sala en una oficina activa del catálogo_ | | | | | |
| `EP-044` | **PUT** | `/rooms/{roomId}` | `RoomUpdateRequest` | `RoomDetail` | 200 | N | deny-all |
| | | _Edita el nombre, la oficina o el estado de activación de una sala_ | | | | | |
| `EP-045` | **GET** | `/offices` | `—` | `OfficeList` | 200 | N | deny-all |
| | | _Consulta el catálogo de oficinas con su estado y sus salas asociadas_ | | | | | |
| `EP-046` | **PUT** | `/offices/{officeId}` | `OfficeUpdateRequest` | `OfficeDetail` | 200 | N | deny-all |
| | | _Edita el literal o el estado de activación de una oficina_ | | | | | |
| `EP-047` | **GET** | `/notification-dispatches` | `—` | `NotificationDispatchPage` | 200 | N | deny-all |
| | | _Consulta los avisos por correo emitidos con su estado de entrega y filtros_ | | | | | |
| `EP-048` | **GET** | `/notification-dispatches/{dispatchId}` | `—` | `NotificationDispatchDetail` | 200 | N | deny-all |
| | | _Consulta el detalle de un aviso con su traza inmutable de intentos de entrega_ | | | | | |
| `EP-049` | **POST** | `/notification-dispatches/{dispatchId}/resend` | `NotificationResendRequest` | `NotificationDispatchDetail` | 201 | N | deny-all |
| | | _Reenvía manualmente un aviso fallido o descartado sobre la misma solicitud_ | | | | | |
| `EP-050` | **GET** | `/mail-settings` | `—` | `MailSettingsDetail` | 200 | N | deny-all |
| | | _Consulta la configuración vigente del servidor de correo saliente_ | | | | | |
| `EP-051` | **PUT** | `/mail-settings` | `MailSettingsUpdateRequest` | `MailSettingsDetail` | 200 | N | deny-all |
| | | _Actualiza y activa los parámetros del servidor SMTP y del remitente_ | | | | | |
| `EP-052` | **POST** | `/mail-settings/test-messages` | `MailTestRequest` | `MailTestResult` | 201 | N | deny-all |
| | | _Envía un correo de prueba para verificar la conexión con el servidor SMTP_ | | | | | |
| `EP-053` | **GET** | `/incident-retentions` | `—` | `RetentionOverview` | 200 | N | deny-all |
| | | _Consulta el vencimiento de retención de las incidencias y las próximas a vencer_ | | | | | |
| `EP-054` | **POST** | `/integrity-checks` | `IntegrityCheckRequest` | `IntegrityCheckReport` | 201 | N | deny-all |
| | | _Lanza la verificación diagnóstica de integridad del registro histórico_ | | | | | |
| `EP-055` | **GET** | `/integrity-checks` | `—` | `IntegrityCheckPage` | 200 | N | deny-all |
| | | _Lista los informes de verificación de integridad ejecutados_ | | | | | |
| `EP-056` | **GET** | `/integrity-checks/{checkId}` | `—` | `IntegrityCheckReport` | 200 | N | deny-all |
| | | _Consulta un informe de integridad con sus inconsistencias por incidencia y regla_ | | | | | |

Disciplina (api-contract):
1. Path y verbo **literales** del contrato (`/membership-plans`, no `/plans/`; `PUT`, no `PATCH` si el contrato dice PUT).
2. La base pública de la API es **`servers[0].url` del `openapi.yaml`** (p. ej. `/api`): monta los routers de forma que la URL pública sea `base + path` EXACTAMENTE. No inventes otra base ni añadas versiones (`/v1`) que el contrato no traiga: el cliente concatena `base + path` y cualquier otro prefijo le devuelve 404.
3. Status HTTP de la columna Status (POST→201, DELETE→204, resto→200) salvo que `openapi.yaml` declare otro.
4. Prefijos de montaje (`include_router`) deben hacer que la URL pública coincida con `base + path` del contrato.
5. Los schemas CON campos (tabla de arriba) son LEY: mismos nombres, tipos y obligatoriedad en tus DTO. Los que aún no tienen campos son gérmenes: rellénalos con el modelo real; **no** reescribas `openapi.yaml` para legitimar un path inventado.
6. Si el DoD te pide un comportamiento que ningún endpoint de la tabla cubre (desarchivar, restaurar, desmarcar…), NO lo resuelvas inventando una ruta: entrégalo con el endpoint que más se le parezca y **repórtalo como health check `Warning` con `check: api-contract`** para que arquitectura lo añada. Una ruta inventada es invisible para el cliente.
7. Si el repo trae `.mind/contract-pending.json` (PR #0), **quita de `pending` los EP que implementas en este mismo PR**: el test de contrato del repo (`tests/contract/`) deja de exonerarlos y pasa a exigirlos, y el runtime comprueba que no dejas los tuyos pendientes. No quites los de otras tareas.
7. Monta el guard con los códigos de la columna Roles. No inventes roles. Si Public=N y Roles=`deny-all`, NO expongas la ruta: avísalo en el PR.

## Contrato de salida del arquetipo

> El repo se genera desde el arquetipo `container-python` (ArqRef MAPFRE). Produce EXACTAMENTE ficheros con la estructura, rutas y HERRAMIENTA de este arquetipo, imitando el esqueleto/ejemplos de abajo. NO improvises otra herramienta ni otra disposición (p.ej. si el arquetipo usa Liquibase, NO uses Flyway). Extiende el esqueleto; no lo reinventes.

**Raíz del proyecto**: el código va bajo `sources/`, `local/`, `apps/`, `libs/`, `src/` — donde el arquetipo pone el suyo. Si el repo está vacío y tienes que andamiarlo, respeta esa raíz en vez de elegir una nueva: el resto del aprovisionamiento (pipelines del arquetipo, verificación de build, empaquetado) espera encontrarlo ahí.

### Estructura del proyecto (del arquetipo)
La estructura básica del repositorio destinado a desarrollar una aplicación basada en contenedores es la siguiente:

```sh
.
├── .github
│   └── workflows
│       ├── merge-commit.yml
│       └── pull-request.yml
├── docker
│   ├── Dockerfile
├── sources
│   ├── apps
│   │   └── product
│   ├── config
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── wsgi.py
│   ├── .flake8.cfg
│   ├── .pre-commit-config.yaml
│   ├── manage.py
│   ├── poetry.lock
│   └── pyproject.toml
├── .gitignore
├── README.md
└── security-metadata.toml
```

A continuación se enumeran cada uno de los elementos de esta estructura:

- `.gitignore`: fichero con la configuración por defecto de carpetas y ficheros a ignorar por `git`.
- `.github/workflows`: carpeta con los ficheros de configuración de `Github Actions` para la ejecución de `CI/CD` en la plataforma de Github.

A continuación se explican los ficheros que se encuentran en la carpeta `.github/workflows`:

- `pull-request.yml`: workflow destinado a ejecutarse en cada pull request que se abra en el repositorio de código fuente desde ramas `feature` o `hotfix`.
- `merge-commit.yml`: workflow para realizar las tareas típicas de publicación de artefactos y despliegue en entornos de desarrollo.
- `README.md`: fichero con detalle de la arquitectura de aplicación de contenedores.
- `docker/Dockerfile`: fichero base para construir la imagen de la aplicación.
- `docker/docker-compose.yaml`: `docker compose` para desplegar la aplicación junto con las dependencias que necesite.
- `docker/dockerignore`: archivo para ignorar ficheros y directorios durante el proceso de construcción de la imagen, con el objetivo de evitar que estos se copien a la imagen del contenedor por error.
- `pyproject.toml`: fichero de configuración para del proyecto, donde se incluyen las dependencias para poetry (gestor de dependencias de python).
- `sources`: carpeta donde se aloja el código fuente de la aplicación. Por defecto se genera una aplicación django con la configuración indicada en el wizard de Marketplace durante el proceso de creación.

La estructura de la aplicación base, en caso de no requerir crear módulos adicionales,  sería la que se puede ver a continuación dentro de la carpeta `sources`:

```sh
.
└── sources
    ├── apps
    │   └── product
    │       ├── management
    │       │   └── commands
    │       ├── operators
    │       ├── schemas
    │       ├── serializers
    │       ├── services
    │       │   ├── connectors
    │       │   └── providers
    │       ├── tasks
    │       ├── tests
    │       ├── views
    │       ├── apps.py
    │       └── urls.py
    ├── config
    │   ├── settings.py
    │   ├── urls.py
    │   └── wsgi.py
    └── manage.py
```

La aplicación ```producto``` está compuesta por las siguientes carpetas para estructurar el código:

- `serializer`: Clases que definen y validan los parámetros de entrada y salida de los servicios.
- `schemas`: Define las distintas respuestas y codigos de error que van a devolver los servicios openapi.
- `views`: Implementa las vistas que van asociadas a las urls para los disntintos servicios
- `services/connectors`: Implementa las clases y métodos para integrar servicios externos con los que la aplicación tendrá comunicación.
- `services/providers`: Implementa las clases y métodos para integrar servicios externos que serán consumidos por la aplicación.
- `operators`: Implementa las clases y métodos necesarios para la lógica de los servicios y la transformación de los datos.
- `managements/commands`: Incluye todos los procesos y comandos que requieren del contexto de la aplicación, como los consumidores de mensaje.
- `tasks`: Implementa las funciones para las tareas asíncronas
- `tests`: Implementa las clases de tests unitarios de los demás componentes de la aplicación.

### Esqueleto y ejemplos (imítalos exactamente)
#### `sources/pyproject.toml`
```toml
[project]
name = "container-python-mock"
version = "0.1.0-dev"
description = "Mock container Python Django"
readme = "README.md"
authors = []
license = { text = "" }
requires-python = ">=3.11,<3.12"
dependencies = [
    "gunicorn==22.0.0",
    "requests==2.34.2",
    "django==(>=5.2,<6.0)",
    "djangorestframework==3.17.1",
    "djangorestframework-simplejwt==5.5.1",
    "django-extensions (>=4.1.0,<5.0.0)",
    "drf-spectacular==0.29.0",
    "drf-spectacular-sidecar==2026.5.1",
]

[build-system]
requires = ["poetry-core>=2.0.0,<3.0.0"]
build-backend = "poetry.core.masonry.api"

# --- Poetry - Configuration -----------------------------------------
[tool.poetry]
package-mode = false

[[tool.poetry.source]]
name = "azure"
url = "https://pkgs.dev.azure.com/devopsmapfre/devopsmapfre/_packaging/snapshots/pypi/simple/"
priority = "supplemental"

# --- Development ----------------------------------------------------
[tool.poetry.group.dev]
optional = false

[tool.poetry.group.dev.dependencies]
ipdb = "^0.13.13"
pre-commit = "^4.2.0"

# --- Linters & Formatters -------------------------------------------
[tool.poetry.group.lint]
optional = false

[tool.poetry.group.lint.dependencies]
ruff = "^0.14.6"
pyright = { version = "^1.1.407", extras = ["nodejs"] }

# --- Testing --------------------------------------------------------
[tool.poetry.group.tests]
optional = false

[tool.poetry.group.tests.dependencies]
coverage = "^7.8.0"
pytest = "^9.0.0"
pytest-cov = "^6.0.0"
pytest-django = "^4.12.0"
pytest-mock = "^3.14.1"

# --- Pytest - Unit Testing ------------------------------------------
[tool.pytest.ini_options]
minversion = "8.3.4"
addopts = "-v --cov=apps --cov-branch --cov-report=term-missing"
python_files = ["*/test_*.py"]
cache_dir = "/tmp/.pytest_cache"
DJANGO_SETTINGS_MODULE = "config.settings"

# --- Coverage - Code coverage ---------------------------------------
[tool.coverage.run]
data_file = "/tmp/.coverage"
relative_files = true
omit = [
    "**/tests/**",
    "**/migrations/**",
    "**/conftest.py",
    "config/*",
    "manage.py",
]

[tool.coverage.report]
skip_empty = true
fail_under = 80
precision = 2

# --- Ruff - Linting and Formatting ----------------------------------
[tool.ruff]
line-length = 140
target-version = "py311"
indent-width = 4
exclude = [".git", "__pycache__", "docs"]
include = ["*.py", "*.pyi"]
cache-dir = "/tmp/.ruff"

[tool.ruff.format]
docstring-code-format = true
docstring-code-line-length = 120

[tool.ruff.lint]
# select = ["C", "E", "F", "W", "B", "B950"]
ignore = ["E203", "E501", "W605", "C901"]

```
#### `sources/.pre-commit-config.yaml`
```yaml
# See https://pre-commit.com for more information
# See https://pre-commit.com/hooks.html for more hooks
exclude: "docs|migrations|.git|.tox"
default_install_hook_types:
  - pre-commit
default_stages:
  - pre-commit
fail_fast: true

repos:
  # general checks (see here: https://pre-commit.com/hooks.html)
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-merge-conflict

  # ruff - linting & formatting
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.7
    hooks:
      - id: ruff-check
        args: ["--fix"]
      - id: ruff-format

  # pyupgrade - upgrade syntax for newer versions
  - repo: https://github.com/asottile/pyupgrade
    rev: v3.21.2
    hooks:
      - id: pyupgrade
        description: Automatically upgrade syntax for newer versions.
        args: ["--py311-plus"]

  # local hooks
  - repo: local
    hooks:
      # pyright - static type checker
      - id: pyright
        name: pyright (local)
        entry: bash -c "poetry -C sources/ run pyright"
        language: system
        types: [file, python]
      # pytest - testing
      - id: pytest
        name: pytests (local)
        entry: bash -c "poetry -C sources/ run pytest; err=$? ; if (( $err != 5 )) ; then exit $err ; fi"
        language: system
        pass_filenames: false
        always_run: true

```
#### `sources/__init__.py`
```py

```
#### `sources/manage.py`
```py
#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and " "available on your PYTHONPATH environment variable? Did you " "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

```
#### `sources/apps/__init__.py`
```py

```
#### `sources/config/__init__.py`
```py

```
#### `sources/config/settings.py`
> ⚠️ **Este fichero del arquetipo apunta a SQLite, que NO es el motor de este proyecto (Oracle).** Imita su ESTRUCTURA y su herramienta, nunca su motor: cambia URL, driver y dialecto por los de Oracle. El arquetipo viene pineado a la tecnología canónica del catálogo; este proyecto tiene una excepción aceptada, así que aquí manda el motor declarado.
```py
import json
import os
from pathlib import Path


def evaluate_bool(varname: str) -> bool:
    """
    Evaluates whether the value of an environment variable is considered truthy.

    The function interprets common false-like values such as:
    "", "0", "false", "no", "off", "none", "[]", "()" (case-insensitive, stripped).

    Additionally, if the value is numeric, it is considered falsy if equal to zero.

    Args:
        varname (str): The name of the environment variable.

    Returns:
        bool: True if the variable is considered truthy, False otherwise.
    """

    value = os.environ.get(varname, "False").strip().lower()
    if value in {"", "0", "false", "f", "no", "n", "off", "none", "[]", "()"}:
        return False
    try:
        return float(value) != 0
    except ValueError:
        return True


def evaluate_dict(varname: str) -> dict:
    """
    Evaluates a dictionary from a string environment variable.

    The function attempts to parse the value of the environment variable as JSON.
    If parsing fails, it returns an empty dictionary.

    Args:
        varname (str): The name of the environment variable.
    Returns:
        dict: The parsed dictionary, or an empty dictionary if parsing fails.
    """

    value = os.environ.get(varname, "{}").strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


# GENERAL CONFIGURATION
# -------------------------------------------------------------
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# NAMING CONFIGURATION
# -------------------------------------------------------------
ENVIRONMENT = os.environ.get("ENVIRONMENT")
SERVICE_NAME = os.environ.get("SERVICE_NAME")
APPLICATION_NAME = os.environ.get("APPLICATION_NAME")

LOCAL_ENVIRONMENT = (ENVIRONMENT or "local").lower() == "local"

# SECURITY CONFIGURATION
# -------------------------------------------------------------
# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", "INSECURE")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = evaluate_bool("DEBUG")

ALLOWED_HOSTS = ["*"]

# APPLICATION CONFIGURATION
# -------------------------------------------------------------
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    # API
    "rest_framework",
    # Code First
    "drf_spectacular",
    "drf_spectacular_sidecar",
]

LOCAL_APPS = [
    "apps.product",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS
if DEBUG:
    INSTALLED_APPS += ["django_extensions"]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# SECURITY AUTHENTICATION CONFIG
# -------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",  # "rest_framework.schemas.coreapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
}

# DATABASE CONFIGURATION
# -------------------------------------------------------------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field
# DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CACHE
# -------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": "/var/tmp/django_cache",
        "TIMEOUT": os.environ.get("CACHE_DEFAULT_TIMEOUT", 300),
    }
}
CACHE_OAUTH_TTL = os.environ.get("CACHE_OAUTH_TTL", 60 * 60)  # 1h

# INTERNATIONALIZATION CONFIGURATION
# -------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# MEDIA & STATIC CONFIGURATION
# -------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = os.path.join(BASE_DIR, STATIC_URL)

# CODE FIRST
# -------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    'TITLE': 'API',
    'DESCRIPTION': 'Interfaz con funcionalidades relacionadas con el TENANT',
    'VERSION': '1.0.0',
    'SWAGGER_UI_DIST': 'SIDECAR',  # shorthand to use the sidecar instead
    'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    'REDOC_DIST': 'SIDECAR',
}

# LOGGING
# -------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s %(data)s"},
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose" if LOCAL_ENVIRONMENT else "json",
        },
        'null': {'class': 'logging.NullHandler'},
    },
    "loggers": {
        "": {
            "handlers": ["console"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
        "django": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
        },
        "django.server": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
        "django.utils.autoreload": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
    },
}

```
#### `sources/config/urls.py`
```py
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("api/", include("apps.product.urls"), name="api"),
    path('docs/schema/', SpectacularAPIView.as_view(), name="schema"),
    path('docs/swagger/', SpectacularSwaggerView.as_view(url_name='schema'), name="swagger"),
]

if settings.LOCAL_ENVIRONMENT:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

```
#### `sources/config/wsgi.py`
```py
"""
WSGI config for uservices project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/2.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
application = get_wsgi_application()

```
#### `sources/apps/product/__init__.py`
```py

```
#### `sources/apps/product/apps.py`
```py
from django.apps import AppConfig


class ProductConfig(AppConfig):
    name = "apps.product"

```
#### `sources/apps/product/urls.py`
```py
from django.urls import include, path  # noqa

urlpatterns = []

```
#### `sources/apps/product/management/__init__.py`
```py

```
#### `sources/apps/product/operators/__init__.py`
```py

```
#### `sources/apps/product/schemas/__init__.py`
```py

```
#### `sources/apps/product/serializers/__init__.py`
```py

```
#### `sources/apps/product/services/__init__.py`
```py

```
#### `sources/apps/product/tasks/__init__.py`
```py

```
#### `sources/apps/product/tests/__init__.py`
```py

```
#### `sources/apps/product/tests/conftest.py`
```py
import pytest
from rest_framework.test import APIClient


@pytest.fixture(scope="function")
def api_client() -> APIClient:
    """
    Fixture to provide an API client
    :return: APIClient
    """
    return APIClient()

```
#### `sources/apps/product/views/__init__.py`
```py

```
#### `sources/apps/product/management/commands/__init__.py`
```py

```
#### `sources/apps/product/services/connectors/__init__.py`
```py

```
#### `sources/apps/product/services/providers/__init__.py`
```py

```
#### `sources/apps/product/tests/commands/__init__.py`
```py

```
#### `sources/apps/product/tests/serializers/__init__.py`
```py

```
#### `sources/apps/product/tests/tasks/__init__.py`
```py

```
#### `sources/apps/product/tests/views/__init__.py`
```py

```
#### `sources/apps/product/tests/views/test_sample.py`
```py
from django.urls import reverse


def test_ok(api_client):
    # Arrange // Given
    url = reverse('swagger')

    # Act // When

    # Assert // Then
    assert isinstance(url, str)

```

## Guía del programador del proyecto (convenciones — T.7, aprobada)

**Precedencia (handbook-filter):** si esta guía choca con el **contrato ArqRef** o el **DoD de ESTA tarea**, ganan ArqRef y el DoD. Solo se incluyen convenciones del stack de esta TSK; se omiten slices de otros lenguajes/frameworks (p. ej. Angular HttpClient en una TSK React, FastAPI en un SPA).

### Librerías del handbook (pines — dependency-pins)
Usa estas coordenadas/versiones en el manifiesto del host; no improvises latest sin pin.
- **FastAPI** `0.115.x o superior` [backend] · `fastapi`
- **Uvicorn (standard)** `0.34.x o superior` [backend] · `uvicorn`
- **Pydantic v2** `2.10.x o superior` [backend] · `pydantic`
- **pydantic-settings** `2.7.x o superior` [backend] · `pydantic-settings`
- **SQLAlchemy 2.0 (ORM sincrono, estilo select())** `2.0.36 o superior` [backend] · `SQLAlchemy`
- **python-oracledb (thin mode)** `2.5.x o superior` [bbdd] · `oracledb`
- **Alembic** `1.14.x o superior` [bbdd] · `alembic`
- **argon2-cffi (Argon2id)** `23.1.x o superior` [backend] · `argon2-cffi`
- **APScheduler** `3.11.x o superior` [backend] · `APScheduler`
- **Evaluador de reglas propio (grafo de transiciones + matriz rol x operacion sobre tablas Oracle)** `n/a — codigo del proyecto, sin dependencia externa` [backend]
- **structlog + python-json-logger** `structlog 24.4.x o superior` [backend] · `structlog`
- **OpenTelemetry (distro + instrumentacion FastAPI y SQLAlchemy)** `opentelemetry-distro 0.50b0 o superior` [observability] · `opentelemetry-distro`
- **python-multipart** `0.0.20 o superior` [backend] · `python-multipart`
- **Pillow** `11.x o superior` [backend] · `pillow`
- **pytest** `8.3.x o superior` [tests] · `pytest`
- **pytest-cov** `6.0.x o superior` [tests] · `pytest-cov`
- **httpx** `0.28.x o superior` [tests] · `httpx`
- **Testcontainers for Python (modulo Oracle)** `4.9.x o superior` [tests] · `testcontainers`
- **aiosmtpd** `1.4.6 o superior` [tests] · `aiosmtpd`
- **freezegun** `1.5.x o superior` [tests] · `freezegun`
- **Ruff (linter + formatter)** `0.8.x o superior` [backend] · `ruff`
- **mypy** `1.14.x o superior` [backend] · `mypy`
- **OpenAPI 3.0 generado por FastAPI + Redocly CLI para lint del contrato** `Redocly CLI 1.25 o superior` [api] · `@redocly/cli`
- **Prettier** `3.4.x` [frontend] · `prettier`
- **Docker (imagen base python:3.11-slim para backend, nginx:alpine para servir la SPA)** `python 3.11-slim, nginx 1.27-alpine` [infra]
- **GitHub Actions** `runners ubuntu-24.04` [infra]
- **pre-commit** `4.0.x o superior` [infra] · `pre-commit`

### Convenciones
- **BACKEND (Python/FastAPI) — Estructura del codigo POR FEATURE, no por capa. Cada modulo de negocio vive en app/features/<feature>/ y contiene exactamente: router.py, service.py, repository.py, models.py (SQLAlchemy), schemas.py (Pydantic), errors.py. Las features son: auth, usuarios, catalogos, incidencias, ciclo_vida, avisos, retencion. Lo transversal vive en app/core/ (config, db, security, logging, errors, pagination) y NUNCA importa de una feature.** — El proyecto tiene dos modulos de negocio (MOD-001, MOD-002) y muchos casos de uso por modulo. Agrupar por capa (controllers/, services/, repositories/) obliga a abrir tres carpetas para tocar una funcionalidad y favorece las dependencias cruzadas entre features. El limite de import (core nunca importa feature) es lo que impide que el proyecto degenere en un grafo ciclico.
  - Ejemplo correcto: `app/
  core/{config.py,db.py,security.py,errors.py,pagination.py,logging.py}
  features/incidencias/{router.py,service.py,repository.py,models.py,schemas.py,errors.py}
  features/ciclo_vida/{router.py,service.py,repository.py,models.py,schemas.py}`
  - Ejemplo incorrecto (evítalo): `app/
  controllers/{incidencia_controller.py,usuario_controller.py,...}
  services/{incidencia_service.py,...}
  repositories/{incidencia_repository.py,...}   # cada cambio funcional toca 3 carpetas`
- **BACKEND + BBDD (Python/Oracle) — Naming: clases en PascalCase con sufijo explicito (IncidenciaEntity, IncidenciaService, IncidenciaRepository, CrearIncidenciaRequest, IncidenciaDetalleResponse); funciones, variables y modulos en snake_case; constantes en UPPER_SNAKE_CASE; enums como (str, Enum) con miembros en UPPER_SNAKE y valor igual al codigo de catalogo. Tablas y columnas Oracle en snake_case en minusculas dentro del codigo Python (Oracle las normaliza a mayusculas): NUNCA se usan identificadores entrecomillados. Rutas HTTP en kebab-case y en ingles, plural para colecciones (/incidents, /notification-dispatches). Nombres de fichero del frontend en kebab-case.** — Oracle pliega a mayusculas los identificadores no entrecomillados; si alguien declara __tablename__ = '"Incidencia"' el resto del esquema deja de poder consultarse sin comillas y se rompe Alembic. El sufijo explicito en clases evita la colision habitual entre el modelo ORM y el esquema Pydantic del mismo concepto.
  - Ejemplo correcto: `class IncidenciaEntity(Base):
    __tablename__ = "incidencia"
    incidencia_id: Mapped[int] = mapped_column(primary_key=True)
    reported_by_user_id: Mapped[int] = mapped_column(ForeignKey("usuario.usuario_id"))

class EstadoIncidencia(str, Enum):
    ABIERTA = "ABIERTA"
    EN_CURSO = "EN_CURSO"`
  - Ejemplo incorrecto (evítalo): `class Incidencia(Base):            # colisiona con el schema Pydantic Incidencia
    __tablename__ = '"Incidencia"'  # obliga a comillas en TODO el esquema Oracle
    ID: Mapped[int] = mapped_column(primary_key=True)   # UPPER para un campo`
- **BACKEND (Python/FastAPI) — Manejo de errores: NINGUN endpoint devuelve un dict de error ad-hoc. Se lanza siempre AppError(code=ErrorCode.X, http_status=..., message=..., details=[...]) desde el service y un unico exception handler global la traduce al cuerpo {"code", "message", "details", "traceId"}. Los codigos son UPPER_SNAKE con prefijo de modulo: AUTH_, USR_, ROL_, INC_, CVI_, CAT_, AVI_, RET_. Los mensajes van SIEMPRE en espaniol (NFR-004) y NUNCA revelan la existencia del recurso en una denegacion por alcance: se usa INC_NOT_FOUND con 404 tanto para incidencia inexistente como para incidencia ajena.** — REQ-023, REQ-031 y REQ-078 exigen denegacion uniforme e indistinguible. Si cada endpoint compone su propio error, tarde o temprano uno devuelve 403 donde otro devuelve 404 y el sistema queda vulnerable a enumeracion de identificadores. El traceId permite correlacionar el error mostrado al usuario con la traza de OpenTelemetry sin exponer el stack.
  - Ejemplo correcto: `raise AppError(code=ErrorCode.INC_NOT_FOUND, http_status=404,
               message="No hemos encontrado esa incidencia")
# handler global -> {"code":"INC_NOT_FOUND","message":"...","details":[],"traceId":"..."}`
  - Ejemplo incorrecto (evítalo): `return JSONResponse(status_code=403, content={"error": "forbidden"})  # formato distinto,
# en ingles, y revela que la incidencia existe pero es ajena`
- **BACKEND — Mapeo DTO: se hace EXCLUSIVAMENTE con Pydantic v2 desde la entidad ORM. Los esquemas de respuesta declaran model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True) y se construyen con Response.model_validate(entity). Los esquemas de peticion se consumen con request.model_dump() y el service construye la entidad. PROHIBIDO introducir una libreria de mapeo adicional y PROHIBIDO devolver la entidad ORM directamente desde el router.** — Devolver la entidad ORM expone accidentalmente password_hash, password_salt y relaciones lazy (REQ-054, REQ-063). El alias camelCase centralizado evita que cada endpoint decida su propio naming JSON y que el frontend tenga que mapear a mano. Pydantic ya hace el trabajo: anadir un mapper seria una capa redundante.
  - Ejemplo correcto: `class UsuarioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)
    usuario_id: int
    full_name: str
    corporate_email: EmailStr
    role_code: RolCode

return UsuarioResponse.model_validate(entity)   # -> {"usuarioId":1,"fullName":"..."}`
  - Ejemplo incorrecto (evítalo): `return entity            # serializa password_hash y dispara lazy loads
return {"usuario_id": e.usuario_id, "full_name": e.full_name}   # dict a mano, snake_case en JSON`
- **BACKEND — Transacciones: el limite transaccional es el METODO DEL SERVICE. El router abre la sesion via dependencia get_db y la cede al service; el service hace with uow(db): ... y el commit ocurre UNA sola vez al final del caso de uso. El repository NUNCA llama a commit(), rollback() ni flush() salvo flush explicito para obtener la PK generada. Las lecturas puras usan sesion en modo solo lectura (no se hace commit). Toda escritura que deba coexistir con su asiento de historico o con su fila de outbox se hace en el MISMO with uow.** — AC-CIE-05, AC-HIST-02, AC-ACC-03 y REQ-132 exigen atomicidad estricta: si falla el historico, no se consolida el cierre; si falla la revocacion de sesiones, no se consolida la desactivacion; la solicitud de aviso se persiste en la misma transaccion que el alta (patron outbox, ADR-006). Un commit dentro del repository rompe esa garantia sin que nadie lo note hasta produccion.
  - Ejemplo correcto: `def cerrar(self, incidencia_id: int, cmd: CerrarCommand, actor: ContextoSesion) -> IncidenciaDetalle:
    with uow(self.db):
        inc = self.repo.get_for_update(incidencia_id)
        ...
        self.repo.add_historico(asiento)
        self.outbox.enqueue(aviso)      # misma transaccion
    return IncidenciaDetalleResponse.model_validate(inc)`
  - Ejemplo incorrecto (evítalo): `class IncidenciaRepository:
    def save(self, inc):
        self.db.add(inc)
        self.db.commit()     # el repositorio decide la transaccion: el historico puede quedar huerfano`
- **BACKEND — Identificadores: la PK interna de toda tabla es un NUMBER GENERATED ALWAYS AS IDENTITY (mapped_column(Integer, Identity(always=True), primary_key=True)). El identificador visible al usuario es un codigo de negocio VARCHAR2 unico y legible, generado en el backend (por ejemplo reference_code 'INC-2026-000123'). Los tokens de sesion y las credenciales temporales se generan con secrets.token_urlsafe(32) y se persisten SOLO como hash. PROHIBIDO exponer la PK numerica interna como identificador publico en correos o pantallas de confirmacion.** — El alcance mezcla uuid y number segun el requisito; hay que cerrar una sola forma. IDENTITY es la construccion nativa de Oracle 12c+ y evita el mantenimiento de secuencias y triggers. Mantener aparte un reference_code legible cumple AC-INC-01 ('muestra su identificador') y AC-BAN-07 (busqueda por codigo) sin filtrar la cardinalidad interna del sistema.
  - Ejemplo correcto: `incidencia_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
reference_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
session_token = secrets.token_urlsafe(32)   # se guarda sha256(session_token)`
  - Ejemplo incorrecto (evítalo): `incidencia_id = mapped_column(Integer, autoincrement=True)   # AUTOINCREMENT no existe en Oracle
reference_code = str(uuid4())                                 # ilegible para el empleado
db.session_token = session_token                              # token en claro en la tabla`
- **BACKEND — Alcance de datos: el filtro de propiedad (reported_by_user_id = :session_user_id) se aplica SIEMPRE como predicado en la consulta SQL construida por el repository, nunca como filtro sobre un resultado ya materializado y nunca a partir de un identificador recibido del cliente. El service resuelve el alcance (OWN | ALL) desde el rol vigente leido en base de datos y lo pasa al repository como parametro explicito. Un reporter_user_id presente en la query string se IGNORA en silencio, sin error.** — REQ-024 y AC-PERM-02 son explicitos: 'el filtrado por propietario se aplica en la consulta a base de datos (no sobre la respuesta ya construida)'. Filtrar en memoria implica que el total y los contadores revelan el volumen global (AC-ALC-01) y que una paginacion devuelve paginas incompletas.
  - Ejemplo correcto: `stmt = select(IncidenciaEntity)
if scope is DataScope.OWN:
    stmt = stmt.where(IncidenciaEntity.reported_by_user_id == session_user_id)
total = db.scalar(select(func.count()).select_from(stmt.subquery()))`
  - Ejemplo incorrecto (evítalo): `todas = repo.find_all()
mias = [i for i in todas if i.reported_by_user_id == session_user_id]  # trae todo a memoria,
# el totalCount seria el global y filtra despues de haber leido datos ajenos`
- **BACKEND — Paginacion y ordenacion: se resuelven SIEMPRE en Oracle con .order_by(...).offset(...).limit(...) de SQLAlchemy, que el dialecto oracledb traduce a OFFSET :n ROWS FETCH NEXT :m ROWS ONLY. El orden es SIEMPRE determinista: la columna elegida mas un desempate fijo por la PK descendente. El criterio de orden se valida contra una lista blanca de columnas; page_size solo admite 10, 25 o 50 (25 por defecto) y una pagina por encima de totalPages devuelve 200 con items vacio, no un error.** — AC-BAN-02 exige recorrer 3 paginas sin filas repetidas ni omitidas: sin desempate estable, dos filas con el mismo created_at bailan entre paginas. AC-BAN-09 exige ademas que el 100% de las consultas resuelva orden y paginacion en base de datos, sin trocear en la SPA. La lista blanca de sort_by evita inyeccion por nombre de columna.
  - Ejemplo correcto: `SORTABLE = {"created_at": IncidenciaEntity.created_at, "status": IncidenciaEntity.status_order}
col = SORTABLE.get(q.sort_by) or _raise_invalid_sort()
stmt = stmt.order_by(col.desc(), IncidenciaEntity.incidencia_id.desc()).offset((q.page-1)*q.page_size).limit(q.page_size)`
  - Ejemplo incorrecto (evítalo): `rows = db.scalars(select(IncidenciaEntity)).all()
rows.sort(key=lambda r: r.created_at, reverse=True)
page = rows[(p-1)*size : p*size]     # trocea en memoria y el orden no es estable
stmt = text(f"... ORDER BY {q.sort_by} LIMIT 25")   # LIMIT no existe en Oracle + inyeccion`
- **BACKEND — Logging: structlog a stdout en JSON, un evento por linea, con los campos fijos trace_id, span_id, session_user_id, role_code, operation_code y outcome. Nivel INFO en pre y prod, DEBUG solo en dev. Se enmascara SIEMPRE y de forma centralizada (processor de structlog): password, current_password, new_password, new_password_confirmation, temporary_password, password_hash, password_salt, session_token, cabecera Cookie/Authorization y corporate_email (se registra el usuario por usuario_id, nunca por correo). PROHIBIDO loggear el cuerpo completo de una peticion.** — REQ-053, REQ-063, REQ-076 y REQ-079 lo exigen literalmente ('la contrasenia no figura en logs ni trazas', 'ningun correo corporativo figura en registros de log'). Centralizar el enmascarado en un processor es la unica forma de que un log anadido a prisa en un hotfix no filtre PII.
  - Ejemplo correcto: `log.info("incidencia_creada", incidencia_id=inc.incidencia_id,
         reference_code=inc.reference_code, session_user_id=actor.user_id,
         operation_code="INCIDENT_CREATE", outcome="OK")`
  - Ejemplo incorrecto (evítalo): `log.info(f"login de {email} con password {password}")
log.debug("request body: %s", await request.json())   # vuelca PII y credenciales`
- **TRANSVERSAL — Autorizacion: cada operacion de la API declara su operation_code del catalogo cat_operacion mediante la dependencia require(operation_code) del router. La dependencia resuelve la sesion, relee el rol VIGENTE en base de datos (nunca el embebido en la sesion), consulta la matriz permiso_rol_operacion y aplica deny-by-default: un par rol x operacion sin fila explicita se deniega. La dependencia devuelve el ContextoSesion con user_id, role_code y data_scope, que el service usa para acotar la consulta. Un endpoint sin require(...) no se despliega: hay un test de contrato que recorre app.routes y falla si alguna ruta no publica lo declara.** — REQ-021, REQ-060 y AC-PERM-01 exigen fuente unica de verdad y denegacion por defecto, con revision de codigo que demuestre que no hay comprobaciones de rol dispersas por los endpoints. REQ-019 exige ademas que el cambio de rol surta efecto en la siguiente peticion, lo que obliga a releer el rol de BBDD en cada llamada.
  - Ejemplo correcto: `@router.get("/incidents")
def listar(q: Annotated[BandejaQuery, Depends()],
           actor: Annotated[ContextoSesion, Depends(require(Op.INCIDENT_LIST_ALL))],
           svc: Annotated[IncidenciaService, Depends(get_incidencia_service)]) -> Page[IncidenciaResumen]:
    return svc.listar_bandeja(q, actor)`
  - Ejemplo incorrecto (evítalo): `@router.get("/incidents")
def listar(actor = Depends(get_current_user)):
    if actor.role_code != "TECNICO_MANTENIMIENTO":   # comprobacion dispersa, fuera de la matriz
        raise HTTPException(403)`

### Anti-patrones (PROHIBIDOS)
- Usar el driver cx_Oracle o el dialecto SQLAlchemy 'oracle+cx_oracle' para conectar con Oracle 23ai. → usa: Usar python-oracledb en thin mode con el dialecto 'oracle+oracledb'. URL: oracle+oracledb://usuario:clave@host:1521/?service_name=FACILITIES. cx_Oracle es el proyecto predecesor, no recibe las novedades de 23ai y obliga a instalar Oracle Instant Client dentro de la imagen del contenedor.
- Declarar la PK con autoincrement=True, con un tipo SERIAL, o con AUTOINCREMENT en el DDL. → usa: sa.Column('<entity>_id', sa.Integer(), sa.Identity(always=True), primary_key=True), que Oracle traduce a NUMBER GENERATED ALWAYS AS IDENTITY. AUTOINCREMENT es sintaxis de SQLite y SERIAL de PostgreSQL: ninguna existe en Oracle y el DDL falla en el despliegue, no en el test unitario.
- Paginar con LIMIT/OFFSET escritos a mano en SQL textual, o generar identificadores con gen_random_uuid()/uuid_generate_v4() en la base. → usa: Paginar con .offset().limit() de SQLAlchemy, que el dialecto oracledb traduce a OFFSET :n ROWS FETCH NEXT :m ROWS ONLY. Si hiciera falta un identificador aleatorio, generarlo en Python con secrets/uuid4 y persistirlo como VARCHAR2 o RAW(16): gen_random_uuid() es una funcion de PostgreSQL.
- Usar NOW(), CURRENT_TIMESTAMP con zona local o datetime.now() sin UTC para sellar fechas, y mezclar datetime naive con datetime aware en la misma entidad. → usa: Sellar SIEMPRE con app.core.clock.utc_now() (naive UTC) desde el servicio, o con SYS_EXTRACT_UTC(SYSTIMESTAMP) si hace falta default de BBDD. Todas las columnas temporales se declaran DateTime(timezone=False). Restar un datetime naive y uno aware lanza TypeError en runtime, tipicamente en el calculo de days_in_current_status.
- Ejecutar los tests de integracion contra H2, SQLite o un Oracle simulado en memoria porque 'levantar Oracle es lento'. → usa: Testcontainers con la imagen Oracle Database 23ai Free, compartida por sesion de pytest (fixture scope='session') y con el esquema aplicado por Alembic. IDENTITY, OFFSET..FETCH, MERGE, SYSTIMESTAMP, la semantica de VARCHAR2(n CHAR) y el comportamiento de CLOB difieren lo suficiente como para que un test verde en H2 sea un despliegue roto en produccion.
- Codificar las transiciones de estado o la matriz de permisos como cadenas de if/elif en el servicio (if estado == 'ABIERTA' and nuevo == 'EN_CURSO': ...). → usa: Delegar en TransicionRuleService y PermisoRuleService, que leen cat_transicion_incidencia y permiso_rol_operacion con deny-by-default y devuelven la regla aplicada para trazarla. REQ-021 y REQ-117 exigen fuente unica de verdad y 'ninguna comprobacion de rol codificada dispersa en los endpoints', verificable por revision de codigo (AC-PERM-01).
- Comprobar el rol o el alcance dentro del cuerpo del endpoint (if actor.role_code != 'TECNICO_MANTENIMIENTO': raise 403) en lugar de declararlo en la dependencia require(operation_code). → usa: Declarar siempre Depends(require(Op.X)) en la firma del endpoint. Un endpoint sin require(...) y no listado como publico hace fallar el test de contrato de rutas. Esto materializa el 'protegido por defecto' de AC-SES-04 y evita el endpoint nuevo que nadie recordo proteger.
- Cargar todas las incidencias y filtrar por propietario en Python, o construir el filtro a partir de un reporter_user_id recibido en la query string. → usa: Aplicar el predicado en la consulta (where reported_by_user_id == session_user_id) y calcular totalCount sobre esa misma consulta; ignorar en silencio cualquier reporter_user_id del cliente. Filtrar en memoria revela el volumen global en totalCount, rompe la paginacion y ya ha leido datos ajenos de la base.
- Loggear el cuerpo completo de la peticion, el correo corporativo del usuario, la contrasenia, la credencial temporal o la cookie de sesion. → usa: Registrar identificadores (session_user_id, incidencia_id, operation_code, outcome) y confiar el enmascarado al processor central de structlog, que elimina password*, temporary_password, password_hash, password_salt, session_token, Cookie, Authorization y corporate_email. REQ-063, REQ-076 y REQ-079 lo prohiben de forma explicita y es auditable.
- Emitir un JWT autocontenido con el rol embebido y considerar la sesion cerrada solo en el navegador. → usa: Sesion opaca server-side en tabla sesion_usuario, cookie HttpOnly+Secure+SameSite=Strict, y relectura del rol vigente en BBDD en cada peticion. REQ-057, REQ-070 y REQ-086 exigen revocacion inmediata en servidor (logout, cambio de contrasenia, desactivacion) y REQ-019 que el cambio de rol surta efecto en la siguiente peticion: un JWT con rol embebido no puede cumplirlo.
- Enviar el correo SMTP dentro de la transaccion del alta o de la transicion de estado, o encolar el aviso en una lista en memoria del proceso. → usa: Patron outbox: la solicitud de aviso se INSERTA en la misma transaccion que el alta o la transicion; el despachador ARC-014 la toma despues en FIFO con bloqueo y la entrega. Un SMTP caido debe dejar la solicitud PENDIENTE y devolver 2xx al usuario (AC-AVI-05), nunca revertir la operacion de negocio ni perder el aviso al reiniciar el contenedor.
- Hacer commit() o rollback() dentro de un repositorio, o guardar la incidencia y su asiento de historico en transacciones separadas. → usa: El unico with uow(db) esta en el metodo del servicio y abarca la escritura de negocio, su asiento de historico y su fila de outbox. AC-CIE-05 exige que un fallo al escribir el historico deje la incidencia en 'resuelta' sin comentario ni asiento; con commits parciales quedan historicos huerfanos imposibles de conciliar (AC-TRZ-03).

### Matriz de compatibilidad de tipos
- `datetime (naive, UTC)` en `oracle`: **OK** — TIPO CANONICO del proyecto para toda marca temporal. mapped_column(DateTime(timezone=False)) -> TIMESTAMP(6). Se sella con utc_now() en el servidor y se formatea a Europe/Madrid solo en presentacion.
- `datetime (aware, con tzinfo)` en `oracle`: **PROHIBIDO** — Oracle lo soporta como TIMESTAMP WITH TIME ZONE, pero su uso esta PROHIBIDO en este proyecto: mezclar naive y aware en el mismo modelo provoca TypeError al restar fechas (calculo de days_in_current_status, time_to_resolution) y desfases en los filtros de rango inclusivo.
- `date` en `oracle`: **OK** — mapped_column(Date) -> DATE. Usar SOLO para fechas sin hora de negocio: retention_until, date_from/date_to de filtros. Ojo: el tipo DATE de Oracle incluye componente de hora; SQLAlchemy lo trunca correctamente con sa.Date.
- `int (identificador de PK)` en `oracle`: **OK** — mapped_column(Integer, Identity(always=True), primary_key=True) -> NUMBER GENERATED ALWAYS AS IDENTITY. Es la unica forma admitida de generar PKs; no se usan secuencias manuales ni triggers.
- `Decimal` en `oracle`: **OK** — mapped_column(Numeric(precision, scale)) -> NUMBER(p,s), mapeado a decimal.Decimal en Python. Usar para cualquier magnitud exacta. PROHIBIDO float/BINARY_DOUBLE para importes o contadores.
- `bool` en `oracle`: **OK** — Oracle 23ai incorpora BOOLEAN nativo y SQLAlchemy lo emite correctamente con mapped_column(Boolean). Si se necesitara compatibilidad con una 19c heredada, usar NUMBER(1) con CheckConstraint IN (0,1); en este proyecto, al estar fijada la 23ai, se usa BOOLEAN nativo.
- `str corto/medio (<= 4000)` en `oracle`: **OK** — mapped_column(String(n)) -> VARCHAR2(n CHAR). Declarar SIEMPRE la longitud explicita en caracteres, no en bytes (el parametro de sesion NLS_LENGTH_SEMANTICS no debe darse por supuesto). Ej.: description String(500), reference_code String(20), corporate_email String(254).
- `str largo (texto ilimitado) CLOB` en `oracle`: **OK** — mapped_column(CLOB) para resolution_comment y para el cuerpo del correo (body_text/body_html). No se puede indexar ni usar en DISTINCT/GROUP BY directamente; si hace falta buscar en el, usar Oracle Text o una columna VARCHAR2 derivada.
- `bytes BLOB` en `oracle`: **PROHIBIDO** — Tecnicamente soportado por Oracle, pero PROHIBIDO en este proyecto por ADR-005: la foto adjunta se guarda en el volumen persistente del contenedor (ARC-015) con clave opaca, y en Oracle solo viven los metadatos y el checksum SHA-256. Persistir binarios como BLOB cargaria la base y encareceria la copia.
- `UUID` en `oracle`: **PROHIBIDO** — PROHIBIDO como tipo de PK en este proyecto: la PK es NUMBER IDENTITY. Si algun valor opaco necesitara formato UUID (por ejemplo una clave de almacenamiento del adjunto), se genera en Python y se persiste como VARCHAR2(36); nunca se usa una funcion de generacion de UUID de la base.
- `cualquier tipo` en `h2`: **PROHIBIDO** — H2 NO es un motor de este proyecto y no puede usarse como sustituto de Oracle en los tests: no reproduce IDENTITY always, OFFSET..FETCH, SYS_EXTRACT_UTC ni la semantica de VARCHAR2(n CHAR). Los tests de integracion usan Testcontainers con Oracle 23ai Free.
- `cualquier tipo` en `sqlite`: **PROHIBIDO** — SQLite NO es un motor de este proyecto. Se declara explicitamente para bloquear el atajo de 'un sqlite en memoria para los tests': su tipado dinamico, su AUTOINCREMENT y su datetime('now') no tienen equivalencia con Oracle y ocultarian errores de DDL hasta el despliegue.

### Plantillas canónicas del arquetipo
#### `router-fastapi`
```
"""app/features/<feature>/router.py — arquetipo de controller.
Responsabilidad: declarar contrato HTTP, exigir permiso, delegar. NO contiene reglas de negocio.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.auth import ContextoSesion, require
from app.core.catalogs import Op
from app.core.pagination import Page
from app.features.<feature>.deps import get_<entity>_service
from app.features.<feature>.schemas import (
    Crear<Entity>Request,
    <Entity>DetalleResponse,
    <Entity>ResumenResponse,
    <Entity>Query,
)
from app.features.<feature>.service import <Entity>Service

router = APIRouter(prefix="/<endpoint>", tags=["<feature>"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Da de alta un <entity>",
    response_model=<Entity>DetalleResponse,
)
def crear_<entity>(
    payload: Crear<Entity>Request,
    actor: Annotated[ContextoSesion, Depends(require(Op.<ENTITY>_CREATE))],
    svc: Annotated[<Entity>Service, Depends(get_<entity>_service)],
) -> <Entity>DetalleResponse:
    return svc.crear(payload, actor)


@router.get(
    "",
    summary="Lista <entity> aplicando el alcance del rol vigente",
    response_model=Page[<Entity>ResumenResponse],
)
def listar_<entity>(
    q: Annotated[<Entity>Query, Query()],
    actor: Annotated[ContextoSesion, Depends(require(Op.<ENTITY>_LIST))],
    svc: Annotated[<Entity>Service, Depends(get_<entity>_service)],
) -> Page[<Entity>ResumenResponse]:
    # El alcance (OWN | ALL) viaja dentro de `actor`; el service lo traslada al repository
    # como predicado SQL. El router NUNCA filtra ni recorta el resultado.
    return svc.listar(q, actor)


@router.get(
    "/{<entity>_id}",
    summary="Consulta el detalle de un <entity>",
    response_model=<Entity>DetalleResponse,
)
def obtener_<entity>(
    <entity>_id: int,
    actor: Annotated[ContextoSesion, Depends(require(Op.<ENTITY>_VIEW))],
    svc: Annotated[<Entity>Service, Depends(get_<entity>_service)],
) -> <Entity>DetalleResponse:
    # Fuera de alcance e inexistente devuelven el MISMO error (404 <ENTITY>_NOT_FOUND).
    return svc.obtener(<entity>_id, actor)

```
#### `service-python`
```
"""app/features/<feature>/service.py — arquetipo de servicio de dominio.
Responsabilidad: decidir, validar reglas, abrir y cerrar la transaccion, emitir historico y outbox.
"""
from __future__ import annotations

import structlog

from app.core.auth import ContextoSesion, DataScope
from app.core.clock import utc_now
from app.core.errors import AppError, ErrorCode
from app.core.pagination import Page
from app.core.uow import uow
from app.features.<feature>.models import <Entity>Entity
from app.features.<feature>.repository import <Entity>Repository
from app.features.<feature>.schemas import (
    Crear<Entity>Request,
    <Entity>DetalleResponse,
    <Entity>ResumenResponse,
    <Entity>Query,
)

log = structlog.get_logger(__name__)


class <Entity>Service:
    def __init__(self, repo: <Entity>Repository, historico: HistoricoRepository, outbox: OutboxRepository) -> None:
        self._repo = repo
        self._historico = historico
        self._outbox = outbox

    def crear(self, cmd: Crear<Entity>Request, actor: ContextoSesion) -> <Entity>DetalleResponse:
        self._validar_catalogos(cmd)
        entity = <Entity>Entity(
            **cmd.model_dump(exclude={"campo_derivado"}),
            reported_by_user_id=actor.user_id,   # SIEMPRE de la sesion, nunca del payload
            created_at=utc_now(),                # SIEMPRE del servidor
        )
        with uow(self._repo.db):                 # unica frontera transaccional del caso de uso
            self._repo.add(entity)
            self._repo.flush()                   # solo para obtener la PK IDENTITY
            entity.reference_code = self._generar_codigo(entity.<entity>_id)
            self._historico.add_creacion(entity, actor)
            self._outbox.enqueue_aviso(entity, actor)   # patron outbox: misma transaccion
        log.info("<entity>_creado", <entity>_id=entity.<entity>_id, session_user_id=actor.user_id, outcome="OK")
        return <Entity>DetalleResponse.model_validate(entity)

    def listar(self, q: <Entity>Query, actor: ContextoSesion) -> Page[<Entity>ResumenResponse]:
        owner_id = actor.user_id if actor.data_scope is DataScope.OWN else None
        rows, total = self._repo.search(q, owner_id=owner_id)
        return Page.of(
            items=[<Entity>ResumenResponse.model_validate(r) for r in rows],
            page=q.page,
            page_size=q.page_size,
            total_count=total,
        )

    def obtener(self, <entity>_id: int, actor: ContextoSesion) -> <Entity>DetalleResponse:
        owner_id = actor.user_id if actor.data_scope is DataScope.OWN else None
        entity = self._repo.get(<entity>_id, owner_id=owner_id)
        if entity is None:
            # Respuesta indistinguible entre inexistente y fuera de alcance (REQ-023, REQ-031).
            raise AppError(ErrorCode.<ENTITY>_NOT_FOUND, 404, "No hemos encontrado ese recurso")
        return <Entity>DetalleResponse.model_validate(entity)

    def _validar_catalogos(self, cmd: Crear<Entity>Request) -> None:
        if not self._repo.catalogo_vigente(cmd.category_code):
            raise AppError(ErrorCode.CAT_INVALID, 422, "La categoria seleccionada no es valida")

    @staticmethod
    def _generar_codigo(pk: int) -> str:
        return f"<PREFIX>-{utc_now():%Y}-{pk:06d}"

```
#### `entity-sqlalchemy-oracle`
```
"""app/features/<feature>/models.py — arquetipo de entidad SQLAlchemy sobre Oracle 23ai.
Reglas: PK NUMBER IDENTITY, TIMESTAMP naive UTC, VARCHAR2 con longitud explicita en CHAR,
CLOB solo para texto largo, sin borrado fisico (baja logica con is_active/status).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CLOB,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class <Entity>Entity(Base):
    __tablename__ = "<entity>"
    __table_args__ = (
        UniqueConstraint("reference_code", name="uq_<entity>_reference_code"),
        CheckConstraint("status IN ('ABIERTA','EN_CURSO','RESUELTA','CERRADA')", name="ck_<entity>_status"),
        Index("ix_<entity>_reporter_created", "reported_by_user_id", "created_at"),
        Index("ix_<entity>_status_room", "status", "room_id"),
    )

    <entity>_id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    reference_code: Mapped[str] = mapped_column(String(20), nullable=False)

    room_id: Mapped[int] = mapped_column(ForeignKey("cat_sala.sala_id"), nullable=False)
    category_code: Mapped[str] = mapped_column(
        ForeignKey("cat_categoria_incidencia.category_code"), String(20), nullable=False
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    resolution_comment: Mapped[str | None] = mapped_column(CLOB, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ABIERTA")
    reported_by_user_id: Mapped[int] = mapped_column(ForeignKey("usuario.usuario_id"), nullable=False)
    assigned_technician_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.usuario_id"), nullable=True)

    # Marcas de hito: se sellan UNA sola vez y no se sobrescriben.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    # Denominacion historica congelada en el alta (REQ-150): no se recalcula nunca.
    room_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    office_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    category_name_snapshot: Mapped[str] = mapped_column(String(60), nullable=False)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}   # concurrencia optimista (409 en conflicto)

    adjunto: Mapped["<Entity>AdjuntoEntity | None"] = relationship(back_populates="<entity>", lazy="selectin")

```
#### `repository-python`
```
"""app/features/<feature>/repository.py — arquetipo de repositorio.
Responsabilidad: construir consultas y persistir. NUNCA hace commit ni decide reglas.
"""
from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.features.<feature>.models import <Entity>Entity
from app.features.<feature>.schemas import <Entity>Query

SORTABLE = {
    "created_at": <Entity>Entity.created_at,
    "updated_at": <Entity>Entity.status_changed_at,
    "status": <Entity>Entity.status,
}


class <Entity>Repository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, entity: <Entity>Entity) -> None:
        self.db.add(entity)

    def flush(self) -> None:
        self.db.flush()     # unico flush permitido: obtener la PK IDENTITY generada

    def get(self, <entity>_id: int, *, owner_id: int | None) -> <Entity>Entity | None:
        stmt = select(<Entity>Entity).where(<Entity>Entity.<entity>_id == <entity>_id)
        stmt = self._apply_scope(stmt, owner_id)
        return self.db.scalars(stmt).one_or_none()

    def get_for_update(self, <entity>_id: int) -> <Entity>Entity | None:
        stmt = select(<Entity>Entity).where(<Entity>Entity.<entity>_id == <entity>_id).with_for_update()
        return self.db.scalars(stmt).one_or_none()

    def search(self, q: <Entity>Query, *, owner_id: int | None) -> tuple[list[<Entity>Entity], int]:
        stmt = select(<Entity>Entity)
        stmt = self._apply_scope(stmt, owner_id)          # alcance PRIMERO, siempre
        stmt = self._apply_filters(stmt, q)

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

        col = SORTABLE.get(q.sort_by)
        if col is None:
            raise AppError(ErrorCode.QRY_INVALID_SORT, 400, "Criterio de ordenacion no valido")
        ordered = col.desc() if q.sort_dir == "desc" else col.asc()

        stmt = (
            stmt.order_by(ordered, <Entity>Entity.<entity>_id.desc())   # desempate estable
            .offset((q.page - 1) * q.page_size)
            .limit(q.page_size)                                          # -> OFFSET .. FETCH NEXT en Oracle
        )
        return list(self.db.scalars(stmt).all()), total

    @staticmethod
    def _apply_scope(stmt: Select, owner_id: int | None) -> Select:
        if owner_id is None:
            return stmt
        return stmt.where(<Entity>Entity.reported_by_user_id == owner_id)

    @staticmethod
    def _apply_filters(stmt: Select, q: <Entity>Query) -> Select:
        if q.status:
            stmt = stmt.where(<Entity>Entity.status.in_(q.status))
        if q.room_id:
            stmt = stmt.where(<Entity>Entity.room_id.in_(q.room_id))
        if q.created_from:
            stmt = stmt.where(<Entity>Entity.created_at >= q.created_from)
        if q.created_to:
            stmt = stmt.where(<Entity>Entity.created_at < q.created_to_exclusive)
        return stmt

```
#### `schemas-pydantic`
```
"""app/features/<feature>/schemas.py — arquetipo de DTOs.
JSON en camelCase, Python en snake_case, sin mapper externo.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

API_MODEL = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)


class Crear<Entity>Request(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    room_id: int = Field(gt=0)
    category_code: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=10, max_length=500)

    @field_validator("description")
    @classmethod
    def _no_solo_espacios(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Describe brevemente la incidencia")
        return v.strip()


class <Entity>ResumenResponse(BaseModel):
    model_config = API_MODEL

    <entity>_id: int
    reference_code: str
    room_name_snapshot: str
    office_name_snapshot: str
    category_name_snapshot: str
    status: str
    created_at: datetime
    assigned_technician_name: str | None = None


class <Entity>DetalleResponse(<Entity>ResumenResponse):
    description: str
    resolution_comment: str | None = None
    closed_at: datetime | None = None
    available_transitions: list[TransicionDisponible] = Field(default_factory=list)


class <Entity>Query(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    status: list[str] | None = None
    room_id: list[int] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    search_text: Annotated[str | None, Field(min_length=3, max_length=100)] = None
    sort_by: Literal["created_at", "updated_at", "status"] = "created_at"
    sort_dir: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1)
    page_size: Literal[10, 25, 50] = 25

```
#### `rule-engine-service-python`
```
"""app/features/ciclo_vida/rules.py — MOTOR DE REGLAS DEL PROYECTO.
Evaluador propio sobre reglas PERSISTIDAS en Oracle (editables por el administrador),
no sobre if/else en codigo. Dos evaluadores: transiciones de estado y matriz de permisos.
Toda decision devuelve el resultado Y la regla aplicada, para dejarla trazada en el historico.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import ContextoSesion
from app.core.errors import AppError, ErrorCode
from app.features.catalogos.models import PermisoRolOperacionEntity, TransicionIncidenciaEntity


@dataclass(frozen=True, slots=True)
class ResultadoRegla:
    permitida: bool
    regla_id: int | None          # identificador de la fila de catalogo aplicada (trazabilidad)
    motivo_bloqueo: str | None    # literal en espaniol para mostrar la accion deshabilitada


class TransicionRuleService:
    """Evalua el grafo cat_transicion_incidencia. Deny-by-default: un par (origen, destino)
    sin fila declarada NO es una transicion valida."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def evaluar(self, *, from_status: str, to_status: str, actor: ContextoSesion,
                assignee_user_id: int | None, tiene_comentario: bool) -> ResultadoRegla:
        if from_status == to_status:
            return ResultadoRegla(False, None, "La incidencia ya esta en ese estado")

        regla = self._db.scalars(
            select(TransicionIncidenciaEntity).where(
                TransicionIncidenciaEntity.from_status == from_status,
                TransicionIncidenciaEntity.to_status == to_status,
                TransicionIncidenciaEntity.is_active.is_(True),
            )
        ).one_or_none()

        if regla is None:
            return ResultadoRegla(False, None, f"No se puede pasar de «{from_status}» a «{to_status}»")
        if regla.allowed_role != actor.role_code:
            return ResultadoRegla(False, regla.transicion_id, "No tienes permisos para gestionar esta incidencia")
        if regla.requires_assignee and assignee_user_id is None:
            return ResultadoRegla(False, regla.transicion_id,
                                  "La incidencia debe tener un tecnico asignado antes de continuar")
        if regla.requires_assignee and assignee_user_id != actor.user_id:
            return ResultadoRegla(False, regla.transicion_id, "Solo el tecnico asignado puede realizar esta accion")
        if regla.requires_comment and not tiene_comentario:
            return ResultadoRegla(False, regla.transicion_id,
                                  "Debe indicar un comentario de resolucion para cerrar la incidencia")
        return ResultadoRegla(True, regla.transicion_id, None)

    def disponibles(self, *, from_status: str, actor: ContextoSesion,
                    assignee_user_id: int | None) -> list[ResultadoRegla]:
        """Alimenta available_transitions del detalle: devuelve TODAS las transiciones declaradas
        para el rol, incluidas las bloqueadas con su motivo (la SPA las muestra deshabilitadas)."""
        reglas = self._db.scalars(
            select(TransicionIncidenciaEntity).where(
                TransicionIncidenciaEntity.from_status == from_status,
                TransicionIncidenciaEntity.allowed_role == actor.role_code,
                TransicionIncidenciaEntity.is_active.is_(True),
            )
        ).all()
        return [
            self.evaluar(from_status=from_status, to_status=r.to_status, actor=actor,
                         assignee_user_id=assignee_user_id, tiene_comentario=True)
            for r in reglas
        ]


class PermisoRuleService:
    """Evalua la matriz permiso_rol_operacion. Fuente UNICA de autorizacion del sistema."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def resolver(self, *, role_code: str, operation_code: str) -> PermisoRolOperacionEntity:
        permiso = self._db.scalars(
            select(PermisoRolOperacionEntity).where(
                PermisoRolOperacionEntity.role_code == role_code,
                PermisoRolOperacionEntity.operation_code == operation_code,
            )
        ).one_or_none()
        if permiso is None:   # deny-by-default
            raise AppError(ErrorCode.PERM_DENIED, 403, "No tienes permisos para realizar esta accion")
        return permiso

```
#### `test-unit-pytest`
```
"""tests/unit/features/<feature>/test_<entity>_service.py — arquetipo de test unitario.
Sin base de datos: repositorios y colaboradores son dobles. Un caso por regla de negocio.
Nombre del test: test_<ID>_<comportamiento_esperado>. El ID es el del criterio de aceptacion.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.core.auth import ContextoSesion, DataScope
from app.core.errors import AppError, ErrorCode
from app.features.<feature>.service import <Entity>Service


@pytest.fixture
def repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(repo: MagicMock) -> <Entity>Service:
    return <Entity>Service(repo=repo, historico=MagicMock(), outbox=MagicMock())


@pytest.fixture
def empleado() -> ContextoSesion:
    return ContextoSesion(user_id=7, role_code="EMPLEADO", data_scope=DataScope.OWN, session_id=1)


def test_AC_INC_04_el_reportante_se_toma_de_la_sesion_y_no_del_payload(
    service: <Entity>Service, repo: MagicMock, empleado: ContextoSesion
) -> None:
    """[AC-INC-04] Un reported_by_user_id manipulado en el payload se ignora."""
    repo.catalogo_vigente.return_value = True
    cmd = Crear<Entity>Request(room_id=1, category_code="MOBILIARIO", description="Silla rota en la sala")

    service.crear(cmd, empleado)

    creada = repo.add.call_args.args[0]
    assert creada.reported_by_user_id == 7
    assert creada.status == "ABIERTA"
    assert creada.assigned_technician_id is None


def test_AC_ALC_01_el_empleado_no_alcanza_una_incidencia_ajena(
    service: <Entity>Service, repo: MagicMock, empleado: ContextoSesion
) -> None:
    """[AC-ALC-01] Fuera de alcance responde igual que inexistente."""
    repo.get.return_value = None

    with pytest.raises(AppError) as exc:
        service.obtener(99, empleado)

    assert exc.value.code is ErrorCode.<ENTITY>_NOT_FOUND
    assert exc.value.http_status == 404
    repo.get.assert_called_once_with(99, owner_id=7)   # el alcance viaja al repositorio

```
#### `test-integration-pytest-oracle`
> ⚠️ **Esta plantilla está escrita contra SQLite, que NO es el motor de este proyecto (Oracle).** Imita su ESTRUCTURA, nunca su motor: sustituye contenedor, driver y dialecto por el equivalente de Oracle (ver «Entorno de prueba de esta sesión», que manda sobre esta plantilla). Y repórtalo como health check **Warning**: la plantilla del handbook está mal y hay que corregirla aguas arriba.
```
"""tests/integration/features/<feature>/test_<entity>_api.py — arquetipo de test de integracion.
Oracle 23ai Free REAL en Testcontainers. Prohibido sustituirlo por H2/SQLite.
El fichero termina en _api.py y vive bajo tests/integration/ (marcador `integration`).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_AC_BAN_02_la_paginacion_no_repite_ni_omite_filas(
    client: TestClient, sesion_tecnico: dict[str, str], seed_incidencias
) -> None:
    """[AC-BAN-02] 60 incidencias, pageSize 25 -> 3 paginas, 60 ids distintos."""
    seed_incidencias(total=60)

    vistos: list[int] = []
    for pagina in (1, 2, 3):
        r = client.get("/api/incidents", params={"page": pagina, "pageSize": 25}, cookies=sesion_tecnico)
        assert r.status_code == 200
        body = r.json()
        assert body["totalCount"] == 60
        assert body["totalPages"] == 3
        vistos.extend(i["incidenciaId"] for i in body["items"])

    assert len(vistos) == 60
    assert len(set(vistos)) == 60   # ni repetidas ni omitidas


def test_AC_PERM_02_el_empleado_no_puede_cambiar_el_estado_y_no_deja_efecto_lateral(
    client: TestClient, sesion_empleado: dict[str, str], incidencia_propia_abierta: int, db
) -> None:
    """[AC-PERM-02] 403 sin cambio de estado, sin asiento de historico y sin aviso encolado."""
    r = client.post(
        f"/api/incidents/{incidencia_propia_abierta}/transitions",
        json={"toStatus": "EN_CURSO", "expectedVersion": 1},
        cookies=sesion_empleado,
    )

    assert r.status_code == 403
    assert r.json()["code"] == "PERM_DENIED"
    inc = db.get(<Entity>Entity, incidencia_propia_abierta)
    assert inc.status == "ABIERTA"
    assert inc.assigned_technician_id is None
    assert db.scalar(select(func.count()).select_from(HistoricoEntity).where(...)) == 1  # solo el alta
    assert db.scalar(select(func.count()).select_from(AvisoCorreoEntity).where(...)) == 1  # solo el de alta


def test_AC_SES_04_una_ruta_nueva_sin_declarar_como_publica_responde_401(client: TestClient) -> None:
    """[AC-SES-04] Protegido por defecto."""
    r = client.get("/api/incidents")
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_SESSION_INVALID"

```
#### `alembic-migration-oracle`
```
"""migrations/versions/<rev>_<descripcion_en_snake_case>.py — arquetipo de migracion.
Oracle 23ai: IDENTITY para PKs, VARCHAR2 por CHAR, TIMESTAMP sin zona, indices explicitos.
NUNCA se edita una revision ya promocionada: se crea una nueva.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<rev>"
down_revision = "<prev_rev>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "<entity>",
        sa.Column("<entity>_id", sa.Integer(), sa.Identity(always=True), primary_key=True),
        sa.Column("reference_code", sa.String(20), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("category_code", sa.String(20), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("resolution_comment", sa.CLOB(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ABIERTA"),
        sa.Column("reported_by_user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_technician_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False,
                  server_default=sa.text("SYS_EXTRACT_UTC(SYSTIMESTAMP)")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["room_id"], ["cat_sala.sala_id"], name="fk_<entity>_sala"),
        sa.ForeignKeyConstraint(["reported_by_user_id"], ["usuario.usuario_id"], name="fk_<entity>_reporter"),
        sa.UniqueConstraint("reference_code", name="uq_<entity>_reference_code"),
        sa.CheckConstraint("status IN ('ABIERTA','EN_CURSO','RESUELTA','CERRADA')", name="ck_<entity>_status"),
    )
    op.create_index("ix_<entity>_reporter_created", "<entity>", ["reported_by_user_id", "created_at"])
    op.create_index("ix_<entity>_status_room", "<entity>", ["status", "room_id"])


def downgrade() -> None:
    op.drop_index("ix_<entity>_status_room", table_name="<entity>")
    op.drop_index("ix_<entity>_reporter_created", table_name="<entity>")
    op.drop_table("<entity>")

```
#### `error-handler-global-fastapi`
```
"""app/core/errors.py + registro en app/main.py — manejador unico de errores.
Todo error sale con el mismo cuerpo y en espaniol.
"""
from __future__ import annotations

from enum import Enum

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace

log = structlog.get_logger(__name__)


class ErrorCode(str, Enum):
    AUTH_SESSION_INVALID = "AUTH_SESSION_INVALID"
    AUTH_BAD_CREDENTIALS = "AUTH_BAD_CREDENTIALS"
    AUTH_ACCOUNT_LOCKED = "AUTH_ACCOUNT_LOCKED"
    PERM_DENIED = "PERM_DENIED"
    USR_EMAIL_DUPLICATED = "USR_EMAIL_DUPLICATED"
    INC_NOT_FOUND = "INC_NOT_FOUND"
    CVI_TRANSITION_NOT_ALLOWED = "CVI_TRANSITION_NOT_ALLOWED"
    CVI_VERSION_CONFLICT = "CVI_VERSION_CONFLICT"
    CAT_INVALID = "CAT_INVALID"
    QRY_INVALID_SORT = "QRY_INVALID_SORT"
    SYS_UNEXPECTED = "SYS_UNEXPECTED"


class AppError(Exception):
    def __init__(self, code: ErrorCode, http_status: int, message: str,
                 details: list[dict[str, str]] | None = None) -> None:
        self.code, self.http_status, self.message = code, http_status, message
        self.details = details or []
        super().__init__(message)


def _body(code: str, message: str, details: list[dict[str, str]]) -> dict[str, object]:
    span = trace.get_current_span().get_span_context()
    return {"code": code, "message": message, "details": details,
            "traceId": format(span.trace_id, "032x") if span.is_valid else ""}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    def _app_error(_: Request, exc: AppError) -> JSONResponse:
        log.warning("app_error", code=exc.code.value, http_status=exc.http_status)
        return JSONResponse(exc.http_status, _body(exc.code.value, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                   for e in exc.errors()]
        return JSONResponse(400, _body("VAL_INVALID_REQUEST",
                                       "Revisa los datos introducidos", details))

    @app.exception_handler(Exception)
    def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("error_inesperado")   # el stack va al log, NUNCA a la respuesta
        return JSONResponse(500, _body(ErrorCode.SYS_UNEXPECTED.value,
                                       "No se ha podido completar la operacion, intentalo de nuevo", []))

```

## Modelo de datos a respetar (diseñado en arquitectura — T.5, schema-names)

> Nombres FÍSICOS canónicos. Úsalos en modelos, DTOs, queries y contratos. **PROHIBIDO** traducir (`titulo`→`title`, `contenido`→`content`, `estado` `publicada|borrador`→`published|draft`). La migración ya (o va a) crear estas entidades — no improvises un esquema paralelo.

**Inventario T.5 (21):** `cat_rol`, `cat_operacion`, `permiso_rol_operacion`, `cat_oficina`, `cat_sala`, `cat_categoria_incidencia`, `cat_estado_incidencia`, `cat_transicion_incidencia`, `cat_motivo_desactivacion`, `configuracion_smtp`, `usuario`, `usuario_password_historico`, `sesion_usuario`, `incidencia`, `incidencia_adjunto`, `aviso_correo`, `usuario_historico`, `auditoria_acceso`, `incidencia_historico`, `aviso_correo_intento`, `resolucion_destinatario_log`

**Catálogos (`data_kind = catalog`): `cat_rol`, `cat_operacion`, `permiso_rol_operacion`, `cat_oficina`, `cat_sala`, `cat_categoria_incidencia`, `cat_estado_incidencia`, `cat_transicion_incidencia`, `cat_motivo_desactivacion`, `configuracion_smtp`.** Los siembra la migración: LÉELOS de la base de datos (no hardcodees sus valores en enums ni en el front) y, si al arrancar están vacíos, es un defecto de la tarea de datos — repórtalo como health check `Warning` con `check: seeds`, no lo tapes con valores por defecto.

- **Motor de datos del proyecto: Oracle no fijada en MAR2** (capa `db` del stack, aprobada en T.2/T.3). TODO el DDL y el DML que escribas debe ser válido EN ESE MOTOR y en su dialecto. Nada de tipos ni sintaxis de otros motores: si un tipo o construcción no existe en Oracle, usa su equivalente nativo. Y no te fíes de tu criterio: APLICA el changelog contra el motor real del entorno de prueba antes de entregar (ver «Entorno de prueba de esta sesión»).

### ARC-100 · `cat_rol` (table) · **catalog**
Catálogo cerrado de roles funcionales del sistema.
**Columnas:**
- `role_code` VARCHAR2(32) [PK, NOT NULL] — PK; valores cerrados: EMPLEADO, TECNICO_MANTENIMIENTO, ADMINISTRADOR
- `role_name` VARCHAR2(60) [NOT NULL] — Etiqueta legible en español; única
- `role_description` VARCHAR2(200) — Resumen de permisos del rol mostrado en la ficha de usuario
- `display_order` NUMBER [NOT NULL] — Único; orden de presentación en desplegables
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y; baja lógica, sin borrado físico

### ARC-101 · `cat_operacion` (table) · **catalog**
Catálogo de operaciones funcionales autorizables de la API.
**Columnas:**
- `operation_code` VARCHAR2(50) [PK, NOT NULL] — PK; enum: INCIDENT_CREATE|INCIDENT_LIST_OWN|INCIDENT_LIST_ALL|INCIDENT_VIEW|INCIDENT_HISTORY_VIEW|INCIDENT_ASSIGN_SELF|INCIDENT_STATUS_CHANGE|INCIDENT_CLOSE_WITH_COMMENT|USER_MANAGE
- `operation_name` VARCHAR2(100) [NOT NULL] — Etiqueta legible en español
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y

### ARC-102 · `permiso_rol_operacion` (table) · **catalog**
Matriz única rol x operación con el alcance de datos aplicable; fuente de verdad de la autorización.
**Columnas:**
- `permiso_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `role_code` VARCHAR2(32) [NOT NULL, FK→cat_rol] — Único junto a operation_code (una sola entrada por par)
- `operation_code` VARCHAR2(50) [NOT NULL, FK→cat_operacion] — Único junto a role_code
- `data_scope` VARCHAR2(10) [NOT NULL] — enum: OWN|ALL; ausencia de fila = denegado (deny by default)

### ARC-103 · `cat_oficina` (table) · **catalog**
Catálogo de las oficinas de la organización a las que pertenecen las salas.
**Columnas:**
- `office_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `office_code` VARCHAR2(10) [NOT NULL] — Único
- `office_name` VARCHAR2(80) [NOT NULL] — Máx. 80 caracteres
- `city` VARCHAR2(60) — Opcional; máx. 60 caracteres
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y; no desactivable con salas activas; siempre >=1 oficina activa

### ARC-104 · `cat_sala` (table) · **catalog**
Catálogo de salas de reuniones sobre las que se reportan incidencias.
**Columnas:**
- `room_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `room_code` VARCHAR2(20) [NOT NULL] — Único global; inmutable tras el alta
- `room_name` VARCHAR2(80) [NOT NULL] — 2-80 caracteres; único dentro de la misma oficina
- `office_id` NUMBER [NOT NULL, FK→cat_oficina] — Exactamente una oficina, existente y activa en el alta
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y; baja lógica, sin borrado físico
- `created_at` TIMESTAMP [NOT NULL] — Fijada por el servidor
- `created_by` NUMBER [NOT NULL, FK→usuario] — Administrador que da de alta la sala
- `updated_at` TIMESTAMP — Solo tras una modificación
- `updated_by` NUMBER [FK→usuario] — Administrador que modifica la sala

### ARC-105 · `cat_categoria_incidencia` (table) · **catalog**
Catálogo cerrado de categorías de clasificación de la incidencia.
**Columnas:**
- `category_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `category_code` VARCHAR2(20) [NOT NULL] — Único e inmutable; enum: MOBILIARIO|CLIMATIZACION|AUDIOVISUAL|LIMPIEZA|OTROS
- `category_name` VARCHAR2(60) [NOT NULL] — 3-60 caracteres; único sin distinguir mayúsculas; en español
- `display_order` NUMBER [NOT NULL] — Único dentro del catálogo
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y; debe quedar siempre >=1 categoría activa
- `updated_at` TIMESTAMP — Trazabilidad del último cambio
- `updated_by` NUMBER [FK→usuario] — Administrador que modifica el catálogo

### ARC-106 · `cat_estado_incidencia` (table) · **catalog**
Catálogo cerrado de estados del ciclo de vida de la incidencia.
**Columnas:**
- `status_code` VARCHAR2(20) [PK, NOT NULL] — PK; enum: ABIERTA|EN_CURSO|RESUELTA|CERRADA
- `status_name` VARCHAR2(40) [NOT NULL] — Etiqueta de presentación en español
- `sort_order` NUMBER [NOT NULL] — Único; secuencia del ciclo de vida usada para ordenar
- `is_terminal` CHAR(1) [NOT NULL] — Y/N; Y solo para CERRADA
- `is_active` CHAR(1) [NOT NULL] — Y/N; no desactivable si hay incidencias vivas en ese estado

### ARC-107 · `cat_transicion_incidencia` (table) · **catalog**
Grafo de transiciones permitidas entre estados de incidencia y sus precondiciones.
**Columnas:**
- `transition_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `from_status` VARCHAR2(20) [NOT NULL, FK→cat_estado_incidencia] — Único junto a to_status; distinto de to_status
- `to_status` VARCHAR2(20) [NOT NULL, FK→cat_estado_incidencia] — Único junto a from_status
- `allowed_role` VARCHAR2(32) [NOT NULL, FK→cat_rol] — Rol autorizado a ejecutar la transición
- `requires_assignee` CHAR(1) [NOT NULL] — Y/N; Y en ABIERTA->EN_CURSO
- `requires_comment` CHAR(1) [NOT NULL] — Y/N; Y en RESUELTA->CERRADA
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y; par no declarado = transición denegada

### ARC-108 · `cat_motivo_desactivacion` (table) · **catalog**
Catálogo de motivos de desactivación de una cuenta de usuario.
**Columnas:**
- `reason_code` VARCHAR2(30) [PK, NOT NULL] — PK; valor obligatorio en toda desactivación
- `reason_name` VARCHAR2(120) [NOT NULL] — Etiqueta en español mostrada en el desplegable de baja
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y

### ARC-109 · `configuracion_smtp` (table) · **catalog**
Parámetros del servidor de correo y del remitente usados para los avisos.
**Columnas:**
- `config_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `smtp_host` VARCHAR2(255) [NOT NULL] — Sin host no se admite activar el canal
- `smtp_port` NUMBER [NOT NULL] — Entero entre 1 y 65535
- `use_tls` CHAR(1) [NOT NULL] — Y/N
- `smtp_username` VARCHAR2(255) — Solo si el servidor exige autenticación
- `smtp_password_encrypted` VARCHAR2(512) — Cifrada; write-only, nunca devuelta por la API ni escrita en logs
- `sender_address` VARCHAR2(254) [NOT NULL] — Formato de correo válido
- `sender_display_name` VARCHAR2(100) [NOT NULL] — Nombre del remitente mostrado en el correo
- `facilities_fallback_email` VARCHAR2(254) — Buzón de respaldo si no hay técnicos activos notificables
- `max_attempts` NUMBER [NOT NULL] — Máximo de reintentos de entrega; por defecto 3
- `is_active` CHAR(1) [NOT NULL] — Y/N; como máximo una configuración activa
- `updated_at` TIMESTAMP [NOT NULL] — Auditoría del cambio de configuración
- `updated_by` NUMBER [NOT NULL, FK→usuario] — Administrador que modifica la configuración

### ARC-110 · `usuario` (table) · **transactional**
Censo de personas del sistema con su rol vigente, su credencial y su estado de cuenta.
**Columnas:**
- `user_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `full_name` VARCHAR2(120) [NOT NULL] — 2-120 caracteres; espacios normalizados
- `corporate_email` VARCHAR2(150) [NOT NULL] — Único global en minúsculas y sin espacios; formato email; actúa como usuario de acceso
- `role_code` VARCHAR2(32) [NOT NULL, FK→cat_rol] — Exactamente un rol vigente del catálogo cerrado
- `status` VARCHAR2(10) [NOT NULL] — enum: ACTIVO|INACTIVO; valor inicial ACTIVO
- `password_hash` VARCHAR2(255) [NOT NULL] — Hash irreversible con salt; nunca expuesto por la API ni en logs
- `password_salt` VARCHAR2(64) — Obligatorio solo si el algoritmo no lo embebe
- `password_algorithm` VARCHAR2(30) [NOT NULL] — Algoritmo adaptativo (argon2id/bcrypt) para permitir rehash
- `password_updated_at` TIMESTAMP — Referencia para revocar credenciales anteriores
- `must_change_password` CHAR(1) [NOT NULL] — Y/N; Y al alta y tras restablecimiento
- `password_expires_at` TIMESTAMP — Caducidad de la credencial temporal (48 h)
- `failed_login_attempts` NUMBER [NOT NULL] — >=0, por defecto 0; por cuenta, no por navegador
- `last_failed_login_at` TIMESTAMP — Nunca acompañado de la contraseña introducida
- `locked_until` TIMESTAMP — Bloqueo temporal vigente si es futura; no altera status
- `last_login_at` TIMESTAMP — Solo se actualiza tras un acceso correcto
- `role_changed_at` TIMESTAMP — Instante del último cambio de rol
- `role_changed_by` NUMBER [FK→usuario] — Administrador que ejecutó el último cambio de rol
- `deactivated_at` TIMESTAMP — Vacío mientras la cuenta está ACTIVO; se limpia al reactivar
- `deactivated_by` NUMBER [FK→usuario] — Administrador ejecutor; distinto de user_id
- `deactivation_reason_code` VARCHAR2(30) [FK→cat_motivo_desactivacion] — Obligatorio al desactivar
- `deactivation_note` VARCHAR2(500) — Máx. 500 caracteres
- `retention_until` DATE — Derivado = deactivated_at + 2 años; solo lectura; posterior a deactivated_at
- `created_at` TIMESTAMP [NOT NULL] — Automático, no editable
- `created_by` NUMBER [FK→usuario] — Administrador autor del alta; distinto del usuario creado; nulo solo en la cuenta semilla
- `updated_at` TIMESTAMP — Nulo si nunca se modificó
- `updated_by` NUMBER [FK→usuario] — Administrador de la última modificación

### ARC-111 · `usuario_password_historico` (table) · **event_log**
Histórico de hashes de contraseña para impedir la reutilización.
**Columnas:**
- `password_history_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `user_id` NUMBER [NOT NULL, FK→usuario] — Se conservan como máximo las 3 últimas por usuario
- `password_hash` VARCHAR2(255) [NOT NULL] — Hash con salt; nunca en claro
- `created_at` TIMESTAMP [NOT NULL] — Instante en que la contraseña dejó de ser vigente

### ARC-112 · `sesion_usuario` (table) · **transactional**
Sesiones emitidas tras la autenticación, con su vigencia y su revocación.
**Columnas:**
- `session_id` VARCHAR2(36) [PK, NOT NULL] — PK (uuid); nunca viaja en la URL
- `user_id` NUMBER [NOT NULL, FK→usuario] — Una sesión pertenece a un único usuario
- `role_code` VARCHAR2(32) [NOT NULL, FK→cat_rol] — Rol vigente en el instante de la emisión
- `issued_at` TIMESTAMP [NOT NULL] — Instante de emisión
- `expires_at` TIMESTAMP [NOT NULL] — Siempre posterior a issued_at (vencimiento absoluto)
- `last_activity_at` TIMESTAMP [NOT NULL] — Se actualiza en cada petición aceptada
- `permissions_refreshed_at` TIMESTAMP — Última recarga de rol y capacidades tras un cambio de rol
- `revoked_at` TIMESTAMP — Si está informado la sesión es inválida y no se prolonga
- `revocation_reason` VARCHAR2(30) — enum: LOGOUT|EXPIRED|ADMIN|PASSWORD_CHANGE|ACCOUNT_DEACTIVATED

### ARC-113 · `incidencia` (table) · **transactional**
Aviso de problema en una sala con su clasificación, su estado y su responsable.
**Columnas:**
- `incident_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `reference_code` VARCHAR2(20) [NOT NULL] — Único; formato INC-AAAA-NNNNNN
- `room_id` NUMBER [NOT NULL, FK→cat_sala] — NOT NULL; sala activa en el alta; sin borrado en cascada
- `category_id` NUMBER [NOT NULL, FK→cat_categoria_incidencia] — NOT NULL; categoría activa en el alta; sin borrado en cascada
- `room_name_snapshot` VARCHAR2(120) [NOT NULL] — Denominación de la sala vigente en el alta; inmutable
- `office_name_snapshot` VARCHAR2(120) [NOT NULL] — Denominación de la oficina vigente en el alta; inmutable
- `category_name_snapshot` VARCHAR2(60) [NOT NULL] — Denominación de la categoría vigente en el alta; inmutable
- `description` VARCHAR2(500) [NOT NULL] — 10-500 caracteres; no solo espacios; saneada de HTML
- `status_code` VARCHAR2(20) [NOT NULL, FK→cat_estado_incidencia] — Valor inicial ABIERTA fijado por el servidor
- `reported_by_user_id` NUMBER [NOT NULL, FK→usuario] — Tomado de la sesión; nunca del payload; no anulable por desactivación
- `assigned_technician_id` NUMBER [FK→usuario] — Nulo mientras no hay responsable; como máximo un técnico a la vez
- `assigned_at` TIMESTAMP — Obligatorio cuando hay técnico asignado
- `released_at` TIMESTAMP — Instante de la última liberación de responsable
- `released_by` NUMBER [FK→usuario] — Técnico responsable o administrador que libera
- `release_reason` VARCHAR2(500) — 10-500 caracteres; valor fijo TECNICO_DESACTIVADO en la liberación automática
- `resolution_comment` CLOB — Obligatorio y no vacío cuando status_code = CERRADA; inmutable tras el cierre
- `closed_by_user_id` NUMBER [FK→usuario] — Obligatorio cuando status_code = CERRADA
- `closed_at` TIMESTAMP — Obligatorio cuando status_code = CERRADA; resolved_at <= closed_at
- `created_at` TIMESTAMP [NOT NULL] — Sello de servidor; hito de apertura
- `in_progress_at` TIMESTAMP — Se sella una sola vez; created_at <= in_progress_at
- `resolved_at` TIMESTAMP — Se sella una sola vez; in_progress_at <= resolved_at
- `status_changed_at` TIMESTAMP [NOT NULL] — Fecha del último cambio de estado; base del tiempo en estado
- `status_changed_by` NUMBER [FK→usuario] — Usuario de la sesión que ejecutó la última transición
- `version` NUMBER [NOT NULL] — Concurrencia optimista; se incrementa en cada transición aceptada
- `retention_expires_at` DATE [NOT NULL] — Derivado = created_at + 24 meses; solo lectura, no editable por API
- `updated_at` TIMESTAMP [NOT NULL] — Fecha de la última modificación de la incidencia

### ARC-114 · `incidencia_adjunto` (table) · **transactional**
Metadatos de la foto opcional de una incidencia, cuyo binario reside en el almacén de ficheros.
**Columnas:**
- `attachment_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `incident_id` NUMBER [NOT NULL, FK→incidencia] — Único: como máximo un adjunto por incidencia
- `file_name` VARCHAR2(255) [NOT NULL] — Saneado; máx. 255 caracteres
- `mime_type` VARCHAR2(50) [NOT NULL] — enum: image/jpeg|image/png; debe coincidir con el mime real
- `file_size_bytes` NUMBER [NOT NULL] — Máximo 5 MB
- `file_checksum` VARCHAR2(64) [NOT NULL] — SHA-256; se verifica en cada descarga
- `storage_key` VARCHAR2(255) [NOT NULL] — Única y opaca; no adivinable ni expuesta en ruta pública
- `uploaded_by_user_id` NUMBER [NOT NULL, FK→usuario] — Coincide con el reportante de la incidencia
- `stored_at` TIMESTAMP [NOT NULL] — Sello de servidor

### ARC-115 · `aviso_correo` (table) · **transactional**
Solicitud de aviso por correo con su contenido congelado y su estado de entrega.
**Columnas:**
- `notification_id` VARCHAR2(36) [PK, NOT NULL] — PK (uuid)
- `notification_key` VARCHAR2(120) [NOT NULL] — Única; idempotencia por tipo + incidencia + entrada de historial
- `notification_type` VARCHAR2(40) [NOT NULL] — enum: NEW_INCIDENT_ALERT|STATUS_CHANGE_ALERT|CREDENTIAL_ISSUED|PASSWORD_RESET
- `incident_id` NUMBER [FK→incidencia] — Informado en los avisos de incidencia
- `history_entry_id` NUMBER [FK→incidencia_historico] — Informado en los avisos de cambio de estado; un aviso por entrada
- `recipient_user_id` NUMBER [FK→usuario] — Reportante en avisos individuales; vacío en avisos a colectivo
- `recipient_email` VARCHAR2(254) — Formato RFC 5322; nulo solo si el aviso está suprimido
- `subject` VARCHAR2(255) [NOT NULL] — Máx. 255 caracteres; en español
- `body_text` CLOB [NOT NULL] — En español; sin marcadores de plantilla sin resolver; inmutable tras componerse
- `body_html` CLOB — Opcional; mismo contenido escapado
- `status_code` VARCHAR2(20) [NOT NULL] — enum: PENDIENTE|ENVIANDO|ENVIADO|FALLIDO|DESCARTADO|SUPRIMIDO; ENVIADO es final
- `suppression_reason_code` VARCHAR2(30) — enum: USUARIO_DESACTIVADO|SIN_CORREO|DESTINATARIO_NO_RESOLUBLE|NO_RECIPIENTS|COMPOSICION_INCOMPLETA
- `attempt_count` NUMBER [NOT NULL] — >=0, por defecto 0; nunca supera max_attempts
- `next_attempt_at` TIMESTAMP — Siempre futura; vacía si no procede reintento
- `last_error_code` VARCHAR2(50) — Código del último fallo de entrega
- `last_error_message` VARCHAR2(500) — Máx. 500 caracteres; nunca contiene credenciales SMTP
- `locked_by` VARCHAR2(60) — Informado solo mientras el estado es ENVIANDO
- `locked_at` TIMESTAMP — Base de la ventana de recuperación de solicitudes atascadas
- `message_id` VARCHAR2(255) — Obligatorio cuando el estado es ENVIADO
- `sent_at` TIMESTAMP — Obligatorio cuando el estado es ENVIADO
- `resent_by_user_id` NUMBER [FK→usuario] — Administrador que ordena el reenvío manual
- `resent_at` TIMESTAMP — Instante del reenvío manual
- `created_at` TIMESTAMP [NOT NULL] — Orden FIFO de procesamiento

### ARC-116 · `usuario_historico` (table) · **event_log**
Traza inmutable de los cambios de rol y de estado de cuenta de cada usuario.
**Columnas:**
- `history_id` NUMBER [PK, NOT NULL] — PK autogenerada; entrada inmutable, sin borrado físico
- `user_id` NUMBER [NOT NULL, FK→usuario] — Usuario al que pertenece la entrada
- `event_type` VARCHAR2(20) [NOT NULL] — enum: ROL|ESTADO
- `previous_value` VARCHAR2(32) — Rol o estado anterior; vacío solo en la entrada de alta; distinto de new_value
- `new_value` VARCHAR2(32) [NOT NULL] — Rol de cat_rol o estado ACTIVO|INACTIVO
- `reason_code` VARCHAR2(30) [FK→cat_motivo_desactivacion] — Obligatorio en desactivación; vacío en reactivación y en cambios de rol
- `note` VARCHAR2(500) — Máx. 500 caracteres
- `valid_to` TIMESTAMP — Cierre de la asignación de rol anterior; nunca dos vigentes a la vez
- `changed_by` NUMBER [NOT NULL, FK→usuario] — Usuario de la sesión; nunca valor del cliente
- `changed_at` TIMESTAMP [NOT NULL] — Sello de servidor; orden cronológico descendente; retención 2 años

### ARC-117 · `auditoria_acceso` (table) · **event_log**
Registro inmutable de eventos de acceso y de operaciones sensibles sobre credenciales.
**Columnas:**
- `audit_id` VARCHAR2(36) [PK, NOT NULL] — PK (uuid); entrada inmutable, sin edición ni borrado
- `user_id` NUMBER [FK→usuario] — Nulo solo en intentos fallidos sobre usuario inexistente
- `username_attempted` VARCHAR2(150) — Nunca acompañado de la contraseña introducida
- `event_type` VARCHAR2(30) [NOT NULL] — enum: LOGIN_OK|LOGIN_FAILED|ACCOUNT_LOCKED|ACCOUNT_UNLOCKED|LOGOUT|PERMISSION_DENIED|PASSWORD_CHANGED|PASSWORD_RESET
- `operation` VARCHAR2(100) — Operación o ruta denegada
- `outcome` VARCHAR2(20) [NOT NULL] — enum: OK|DENIED_401|DENIED_403
- `occurred_at` TIMESTAMP [NOT NULL] — Fijado por el servidor, nunca por el cliente
- `ip_address` VARCHAR2(45) — Opcional
- `user_agent` VARCHAR2(255) — Opcional
- `session_id` VARCHAR2(36) [FK→sesion_usuario] — Nulo en eventos previos a la emisión de sesión; nunca la credencial
- `retention_until` DATE [NOT NULL] — occurred_at + 2 años

### ARC-118 · `incidencia_historico` (table) · **event_log**
Traza append-only de alta, cambios de estado, asignaciones y reclasificaciones de la incidencia.
**Columnas:**
- `history_id` NUMBER [PK, NOT NULL] — PK autogenerada; desempate del orden cronológico; append-only
- `incident_id` NUMBER [NOT NULL, FK→incidencia] — Índice por (incident_id, changed_at)
- `entry_type` VARCHAR2(30) [NOT NULL] — enum: CREACION|CAMBIO_ESTADO|ASIGNACION|RECLASIFICACION|COMENTARIO_RESOLUCION; una sola entrada CREACION por incidencia
- `from_status` VARCHAR2(20) [FK→cat_estado_incidencia] — Nulo solo en CREACION; coincide con el estado real previo
- `to_status` VARCHAR2(20) [FK→cat_estado_incidencia] — ABIERTA en CREACION; igual a from_status en ASIGNACION
- `value_before` VARCHAR2(200) — Máx. 200; valor anterior en reclasificaciones
- `value_after` VARCHAR2(200) — Máx. 200; valor nuevo en reclasificaciones
- `previous_technician_id` NUMBER [FK→usuario] — Informado en reasignaciones y liberaciones
- `assigned_technician_id` NUMBER [FK→usuario] — Obligatorio en entradas de tipo ASIGNACION
- `entry_comment` VARCHAR2(500) — Máx. 500; nota de avance o motivo de liberación
- `resolution_comment_ref` NUMBER [FK→incidencia] — Informado solo en la transición RESUELTA->CERRADA
- `actor_user_id` NUMBER [NOT NULL, FK→usuario] — Usuario de la sesión; nunca del payload; no anulable por desactivación
- `actor_display_name` VARCHAR2(150) [NOT NULL] — Snapshot del nombre para conservar la atribución tras una baja
- `changed_at` TIMESTAMP [NOT NULL] — Sello de servidor en UTC; inmutable; retención 2 años
- `entry_hash` VARCHAR2(64) — SHA-256 encadenado con el hash de la entrada anterior de la misma incidencia

### ARC-119 · `aviso_correo_intento` (table) · **event_log**
Traza inmutable de cada intento de entrega de un aviso por correo y su resultado.
**Columnas:**
- `attempt_id` VARCHAR2(36) [PK, NOT NULL] — PK (uuid); fila append-only, sin update ni delete
- `notification_id` VARCHAR2(36) [NOT NULL, FK→aviso_correo] — Aviso al que pertenece el intento
- `attempt_number` NUMBER [NOT NULL] — >=1; correlativo y único por notification_id
- `attempted_at` TIMESTAMP [NOT NULL] — Sello de servidor; retención 2 años
- `result_code` VARCHAR2(30) [NOT NULL] — enum: SENT|TRANSIENT_ERROR|PERMANENT_ERROR|NO_RECIPIENTS|COMPOSE_ERROR|CONFIG_ERROR
- `smtp_response_code` VARCHAR2(3) — Código numérico devuelto por el servidor SMTP
- `error_code` VARCHAR2(50) — Informado cuando result_code distinto de SENT
- `error_message` VARCHAR2(500) — Máx. 500; nunca contiene credenciales SMTP
- `recipients_snapshot` CLOB — Destinatarios vigentes en el intento; solo nombre y correo corporativo
- `recipient_count` NUMBER [NOT NULL] — >=0; 0 exige result_code NO_RECIPIENTS
- `message_id` VARCHAR2(255) — Obligatorio cuando result_code = SENT

### ARC-120 · `resolucion_destinatario_log` (table) · **event_log**
Registro inmutable de cada resolución de destinatarios del directorio, individual o de colectivo.
**Columnas:**
- `resolution_id` NUMBER [PK, NOT NULL] — PK autogenerada; fila inmutable, sin borrado físico
- `request_type` VARCHAR2(20) [NOT NULL] — enum: INDIVIDUAL|COLECTIVO
- `requested_by_module` VARCHAR2(50) [NOT NULL] — Módulo consumidor de la resolución
- `subject_user_id` NUMBER [FK→usuario] — Informado solo cuando request_type = INDIVIDUAL
- `resolved_user_ids` VARCHAR2(4000) [NOT NULL] — Lista de identificadores; nunca correos en claro
- `recipient_count` NUMBER [NOT NULL] — >=0; coincide con la cardinalidad de resolved_user_ids
- `is_fallback_used` CHAR(1) [NOT NULL] — Y/N; Y solo si se usó el buzón de respaldo de facilities
- `outcome` VARCHAR2(30) [NOT NULL] — enum: OK|SIN_DESTINATARIOS|NO_ENCONTRADO|NO_NOTIFICABLE|ERROR_TECNICO; SIN_DESTINATARIOS exige recipient_count = 0
- `resolved_at` TIMESTAMP [NOT NULL] — Sello de servidor; retención 2 años
- `resolved_by_user_id` NUMBER [FK→usuario] — Informado solo cuando la consulta la lanza un administrador

## Requisitos que materializa esta tarea

### REQ-004 — Asignación de rol funcional del catálogo cerrado en el alta de usuario por el ADMINISTRADOR
El ADMINISTRADOR asigna un rol funcional del catálogo cerrado al dar de alta un usuario. Reglas: todo usuario creado queda con exactamente un rol de `cat_roles` ("El sistema debe distinguir dos roles: empleado y técnico de mantenimiento"); no se permite alta sin rol ni con dos roles; no hay autorregistro público, el alta siempre la ejecuta el ADMINISTRADOR. Flujo: admin abre el formulario de alta → selecciona rol en el desplegable → confirma → el sistema persiste la asignación y la traza. Datos: `user_id` (uuid, PK), `role_code` (varchar(32), obligatorio, ∈ `cat_roles`), `assigned_by_user_id` (uuid, obligatorio), `assigned_at` (timestamp, obligatorio, = now). Catálogos: `cat_roles` (valores cerrados: EMPLEADO, TECNICO_MANTENIMIENTO). Validaciones: `role_code` obligatorio y perteneciente al catálogo; el usuario no puede quedar con más de una fila de rol vigente. Errores: 400 "Debe seleccionar un rol válido: empleado o técnico de mantenimiento"; 403 "No tiene permisos para asignar roles"; 409 "El usuario ya tiene un rol asignado". CA: Given un ADMINISTRADOR dando de alta un usuario, when completa el alta, then el usuario queda registrado con exactamente un rol del catálogo. Given un valor de rol fuera del catálogo, when confirma, then el sistema rechaza la operación con 400 y no crea asignación. Seguridad: solo ADMINISTRADOR; alcance de datos: todos los usuarios de la organización; sin doble factor. Eventos de dominio: `RoleAssigned` [inferido]. Dependencias: requisito de alta de usuario de EPIC-001. Prioridad: Must [inferido].
**Reglas de negocio:**
1. Todo usuario creado queda con exactamente un rol del catálogo cerrado `cat_roles` (EMPLEADO o TECNICO_MANTENIMIENTO): no existe alta sin rol ni alta con dos roles
2. Un valor de rol ajeno a `cat_roles` no puede quedar persistido como asignación de un usuario
3. Toda alta de usuario tiene un ADMINISTRADOR como autor: no existe autorregistro público
4. Toda asignación de rol registra de forma obligatoria el usuario que la ejecuta (`assigned_by_user_id`) y su instante (`assigned_at`)
**Criterios de aceptación:**
1. AC-ROL-01: Dado un ADMINISTRADOR autenticado en el formulario de alta de usuario, cuando selecciona un `role_code` del catálogo cerrado `cat_roles` (`EMPLEADO`, `TECNICO_MANTENIMIENTO`) y confirma el alta, entonces el usuario queda persistido con exactamente una fila de rol vigente, con `assigned_by_user_id` y `assigned_at` informados, y una consulta a BD devuelve 1 y solo 1 rol vigente para ese usuario.
2. AC-ROL-02: Dado un ADMINISTRADOR en el alta de usuario, cuando envía la petición sin `role_code`, con un `role_code` fuera de `cat_roles`, o para un usuario que ya tiene rol asignado, entonces el sistema responde 400 «Debe seleccionar un rol válido: empleado o técnico de mantenimiento» en los dos primeros casos y 409 «El usuario ya tiene un rol asignado» en el tercero, y no se crea ninguna asignación de rol.
3. AC-ROL-06: Dado un usuario con rol `EMPLEADO` o `TECNICO_MANTENIMIENTO`, cuando invoca cualquiera de los endpoints de asignación de rol, cambio de rol, listado de usuarios, detalle de usuario o histórico de rol, entonces el sistema responde 403 con mensaje uniforme «No tiene permisos para realizar esta acción», no ejecuta la operación y no revela la existencia del recurso solicitado.
**Validaciones:**
1. `role_code` es obligatorio: no se acepta un alta de usuario sin rol seleccionado
2. `role_code` debe pertenecer al catálogo cerrado `cat_roles` (valores admitidos: EMPLEADO, TECNICO_MANTENIMIENTO); cualquier otro valor se rechaza con 400
3. `user_id` debe tener formato UUID válido
4. `assigned_by_user_id` es obligatorio y debe tener formato UUID válido
5. `assigned_at` es obligatorio y debe ser un timestamp válido informado por el sistema (= now), no aceptado desde la petición
**Escenarios de error:**
1. No se indica rol, o el rol indicado no pertenece al catálogo cerrado (empleado / técnico de mantenimiento)
2. El solicitante no está autorizado para asignar roles
3. El usuario ya tiene un rol vigente asignado
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Identificador del usuario creado (clave)
- `role_code` (enum, obligatorio) — Valores cerrados de `cat_roles`: EMPLEADO, TECNICO_MANTENIMIENTO; longitud máx. 32
- `assigned_by_user_id` (uuid, obligatorio) — Administrador que ejecuta la asignación
- `assigned_at` (datetime, obligatorio) — Se fija al instante de la operación (now)

### REQ-005 — Cambio del rol funcional de un usuario existente por el ADMINISTRADOR
El ADMINISTRADOR cambia el rol funcional de un usuario ya existente. Reglas: el cambio sustituye el rol vigente (siempre uno y solo uno); el cambio es efectivo de forma inmediata sobre los permisos (ver PERM-03); [gap: el RFP no indica si el cambio de rol debe conservar o reasignar las incidencias que el técnico tuviera autoasignadas]. Flujo: admin localiza al usuario en el listado → abre detalle → cambia el rol → confirma → el sistema cierra la asignación anterior (`valid_to`), crea la nueva y registra el evento en el histórico. Datos: `user_role_id` (uuid), `user_id`, `previous_role_code`, `new_role_code` (∈ `cat_roles`), `changed_by_user_id`, `changed_at` (timestamp). Validaciones: `new_role_code` ∈ `cat_roles` y distinto del vigente; usuario destino existente y no eliminado. Errores: 400 "Rol no válido"; 404 "Usuario no encontrado"; 409 "El usuario ya tiene ese rol"; 403 "No tiene permisos para modificar el rol de un usuario". CA: Given un usuario con rol empleado, when el ADMINISTRADOR lo cambia a técnico de mantenimiento, then el usuario queda con el nuevo rol y el listado de usuarios lo refleja. Given un usuario con rol EMPLEADO o TECNICO_MANTENIMIENTO, when intenta modificar el rol de cualquier usuario, then el sistema deniega con 403. Seguridad: solo ADMINISTRADOR; alcance: todos los usuarios; queda registrado quién ejecuta el cambio (dato personal tratado: nombre y correo corporativo). Eventos de dominio: `RoleChanged` [inferido]. Dependencias: ROL-01, PERM-03. Prioridad: Must [inferido].
**Reglas de negocio:**
1. El rol nuevo de un cambio de rol es siempre distinto del rol vigente del usuario destino
2. Un cambio de rol cierra la asignación anterior (`valid_to`) en el mismo instante en que abre la nueva, de modo que nunca coexisten dos asignaciones vigentes para un mismo usuario
3. Un usuario inexistente o eliminado no puede ser destino de un cambio de rol
4. Todo cambio de rol registra su autor (`changed_by_user_id`) y su instante (`changed_at`)
**Criterios de aceptación:**
1. AC-ROL-03: Dado un usuario existente con rol `EMPLEADO`, cuando el ADMINISTRADOR abre su detalle y lo cambia a `TECNICO_MANTENIMIENTO`, entonces la asignación anterior queda cerrada (`valid_to` informado), se crea la nueva asignación vigente, y el listado de usuarios recargado muestra el rol nuevo para ese usuario.
2. AC-ROL-06: Dado un usuario con rol `EMPLEADO` o `TECNICO_MANTENIMIENTO`, cuando invoca cualquiera de los endpoints de asignación de rol, cambio de rol, listado de usuarios, detalle de usuario o histórico de rol, entonces el sistema responde 403 con mensaje uniforme «No tiene permisos para realizar esta acción», no ejecuta la operación y no revela la existencia del recurso solicitado.
3. AC-PERM-05: Dado un usuario con sesión abierta cuyo rol acaba de ser cambiado por el ADMINISTRADOR, cuando realiza su siguiente petición a la API sin cerrar ni reabrir sesión, entonces los permisos se resuelven con el `role_code` vigente en base de datos: un empleado promovido a técnico obtiene 200 en el listado completo y un técnico degradado a empleado obtiene 403 en la misma petición que antes le devolvía 200.
**Validaciones:**
1. `new_role_code` es obligatorio y debe pertenecer al catálogo cerrado `cat_roles`
2. `new_role_code` debe ser distinto del `role_code` vigente del usuario destino (coherencia entre el valor entrante y el estado actual); si coincide se rechaza con 409
3. `user_id` del usuario destino es obligatorio, con formato UUID, y debe corresponder a un usuario existente y no eliminado; en caso contrario 404
**Escenarios de error:**
1. El nuevo rol indicado no pertenece al catálogo cerrado de roles
2. El solicitante no está autorizado para modificar el rol de un usuario
3. El usuario indicado no existe
4. El usuario ya tiene asignado ese mismo rol
5. El usuario está dado de baja y no admite cambio de rol
**Campos de datos:**
- `user_role_id` (uuid, obligatorio) — Identificador de la asignación de rol resultante
- `user_id` (uuid, obligatorio) — Debe existir y no estar eliminado
- `previous_role_code` (enum, opcional) — ∈ `cat_roles`
- `new_role_code` (enum, obligatorio) — ∈ `cat_roles` y distinto del vigente
- `changed_by_user_id` (uuid, obligatorio) — Administrador que ejecuta el cambio
- `changed_at` (datetime, obligatorio) — Momento del cambio de rol
- `valid_to` (datetime, opcional) — Se informa al sustituir el rol vigente

### REQ-008 — Consulta del histórico de asignaciones y cambios de rol de un usuario
El ADMINISTRADOR consulta el histórico de asignaciones y cambios de rol de un usuario. Reglas: cada asignación (ROL-01) y cada cambio (ROL-02) deja una entrada inmutable; las entradas no se editan ni se borran físicamente y se conservan 2 años ("Los datos de incidencias y usuarios se conservan durante 2 años"); orden cronológico descendente. Flujo: admin abre ficha de usuario → pestaña Histórico de rol → ve la secuencia rol anterior → rol nuevo con autor y fecha. Datos: `role_history_id` (uuid), `user_id`, `previous_role_code` (nullable en el alta), `new_role_code`, `changed_by_user_id`, `changed_at` (timestamp). Validaciones: rango de fechas del filtro coherente (`date_from` ≤ `date_to`). Errores: 404 "Usuario no encontrado"; 403 "Acceso no autorizado". CA: Given un usuario que pasó de empleado a técnico, when el admin consulta el histórico, then ve dos entradas (alta y cambio) con autor y fecha. Given cualquier usuario no administrador, when solicita el histórico de rol, then recibe 403. Seguridad: solo ADMINISTRADOR; alcance: todos los usuarios. [gap: el RFP exige historial explícito para la incidencia pero no concreta la retención/consulta del histórico de rol más allá de los 2 años generales]. Dependencias: ROL-01, ROL-02. Prioridad: Should [inferido].
**Reglas de negocio:**
1. Las entradas del histórico de rol son inmutables: no se editan ni se borran físicamente durante los 2 años de retención
2. La entrada de histórico correspondiente al alta es la única con `previous_role_code` vacío
3. Un filtro de histórico con `date_from` posterior a `date_to` no es válido
4. Cada asignación inicial y cada cambio de rol deja exactamente una entrada en el histórico del usuario
**Criterios de aceptación:**
1. AC-ROL-05: Dado un usuario que fue dado de alta como `EMPLEADO` y posteriormente cambiado a `TECNICO_MANTENIMIENTO`, cuando el ADMINISTRADOR consulta su histórico de rol, entonces obtiene exactamente dos entradas en orden cronológico descendente (cambio y alta) con `previous_role_code`, `new_role_code`, `changed_by_user_id` y `changed_at`; y un intento de modificar o borrar cualquier entrada vía API devuelve 403/405 y deja el histórico intacto.
2. AC-ROL-06: Dado un usuario con rol `EMPLEADO` o `TECNICO_MANTENIMIENTO`, cuando invoca cualquiera de los endpoints de asignación de rol, cambio de rol, listado de usuarios, detalle de usuario o histórico de rol, entonces el sistema responde 403 con mensaje uniforme «No tiene permisos para realizar esta acción», no ejecuta la operación y no revela la existencia del recurso solicitado.
**Validaciones:**
1. `user_id` es obligatorio, con formato UUID válido y correspondiente a un usuario existente
2. `date_from` y `date_to`, si se informan, deben ser fechas válidas y cumplir `date_from` ≤ `date_to`
**Escenarios de error:**
1. El rango de fechas del filtro no es coherente (fecha inicial posterior a la final)
2. El usuario solicitado no existe
3. El solicitante no está autorizado para consultar el histórico de rol
**Campos de datos:**
- `role_history_id` (uuid, obligatorio) — Entrada inmutable, sin borrado físico
- `user_id` (uuid, obligatorio) — Usuario al que pertenece el histórico
- `previous_role_code` (enum, opcional) — Vacío en la entrada de alta; ∈ `cat_roles`
- `new_role_code` (enum, obligatorio) — ∈ `cat_roles`
- `changed_by_user_id` (uuid, obligatorio) — Autor de la asignación o del cambio
- `changed_at` (datetime, obligatorio) — Orden cronológico descendente; retención 2 años
- `date_from` (date, opcional) — `date_from` ≤ `date_to`
- `date_to` (date, opcional) — `date_from` ≤ `date_to`

### REQ-012 — Todo usuario tiene en todo momento exactamente un rol vigente del catálogo cerrado
Todo usuario del sistema tiene, en todo momento, exactamente un rol vigente del catálogo cerrado `cat_roles` (empleado o técnico de mantenimiento); no existen usuarios sin rol ni con rol múltiple. Aplica a: ROL, PERM, incidencias, notificaciones.
**Reglas de negocio:**
1. Todo usuario del sistema tiene, en todo momento, exactamente un rol vigente del catálogo cerrado `cat_roles`: no existen usuarios sin rol ni con rol múltiple
**Criterios de aceptación:**
1. AC-ROL-01: Dado un ADMINISTRADOR autenticado en el formulario de alta de usuario, cuando selecciona un `role_code` del catálogo cerrado `cat_roles` (`EMPLEADO`, `TECNICO_MANTENIMIENTO`) y confirma el alta, entonces el usuario queda persistido con exactamente una fila de rol vigente, con `assigned_by_user_id` y `assigned_at` informados, y una consulta a BD devuelve 1 y solo 1 rol vigente para ese usuario.
**Escenarios de error:**
1. La operación dejaría al usuario sin rol vigente o con más de un rol simultáneo

### REQ-035 — Todo usuario debe tener exactamente un rol asignado del catálogo cat_roles
Todo usuario del sistema debe tener exactamente un rol asignado del catálogo `cat_roles`; sin rol no se resuelve ninguna autorización. Dependencia: ROL-01. auth_type: SESSION. data_scope: own_only.
**Reglas de negocio:**
1. Todo usuario del sistema tiene exactamente un rol asignado del catálogo cat_roles
2. Un usuario sin rol asignado no resuelve ninguna autorización
**Criterios de aceptación:**
1. AC-USR-09: **Dado** cualquier endpoint funcional del sistema, **cuando** se invoca sin sesión válida, **entonces** responde 401; **cuando** se invoca con la sesión de un usuario cuya cuenta está en estado `INACTIVO`, **entonces** responde 403 y no ejecuta la operación; **cuando** se invoca con un usuario sin rol asignado, **entonces** la autorización no se resuelve y la operación se deniega.
2. AC-ROL-01: **Dado** el acto de alta de un usuario, **cuando** el ADMINISTRADOR lo confirma, **entonces** el usuario queda con exactamente un `role_code` vigente del catálogo `cat_roles`, nunca cero ni más de uno, y no existe ninguna ruta (UI ni API) que permita persistir un usuario sin rol.
**Escenarios de error:**
1. El usuario no tiene un rol vigente asignado y no puede resolverse su autorización
**Campos de datos:**
- `role_code` (enum, obligatorio) — Exactamente un valor del catálogo cat_roles (EMPLEADO, TECNICO_MANTENIMIENTO, ADMINISTRADOR)

### REQ-043 — Asignación y cambio del rol de un usuario por el ADMINISTRADOR
El ADMINISTRADOR asigna el rol a un usuario en el alta y puede cambiarlo posteriormente. Reglas: (1) todo usuario tiene exactamente un rol vigente del catálogo `cat_roles` (PRE-03, RN-02); (2) el rol determina de forma exclusiva los permisos: EMPLEADO puede reportar y consultar solo sus propias incidencias; TECNICO-DE-MANTENIMIENTO puede ver, asignarse y resolver cualquier incidencia; (3) el rol se asigna en el mismo acto del alta (USR-01), nunca después de forma diferida; (4) el cambio de rol surte efecto en la siguiente petición del usuario afectado y se registra con actor y fecha (RN-04); (5) el ADMINISTRADOR no puede retirarse a sí mismo su propio rol de administración. Flujo: abre la ficha (USR-04) → selecciona el nuevo rol → confirma → registra el cambio, refresca los permisos efectivos e invalida la sesión vigente del usuario si la hubiera. Datos: `role_code` (FK `cat_roles`, obligatorio); `role_changed_at` (timestamp); `role_changed_by` (FK `users.id`). Catálogos: `cat_roles` con EMPLEADO y TECNICO_MANTENIMIENTO más ADMINISTRADOR [ambigüedad: el RFP enumera dos roles pero describe además un administrador]. Validaciones: el rol destino debe existir y ser distinto del vigente. Errores: 400 «Selecciona un rol válido»; 409 «El usuario ya tiene ese rol»; 422 «No puedes retirarte tu propio rol de administrador»; 403 sin permisos. Criterios de aceptación: Dado un usuario con rol EMPLEADO, cuando el ADMINISTRADOR le asigna TECNICO_MANTENIMIENTO, entonces pasa a poder acceder a los flujos propios de ese rol y deja de estar limitado a sus propias incidencias. Dado un intento de asignar un rol inexistente, cuando se confirma, entonces se rechaza con 400. Seguridad: solo ADMINISTRADOR; operación sensible auditada. Eventos: `UserRoleChanged`. Dependencias: USR-01, USR-04. Prioridad: Must. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. El rol de un usuario queda asignado en el mismo acto del alta: no existe usuario con asignación de rol diferida
2. El rol vigente determina de forma exclusiva el alcance de datos: EMPLEADO opera solo sobre sus propias incidencias y TECNICO_MANTENIMIENTO sobre cualquier incidencia
3. Un ADMINISTRADOR no puede retirarse a sí mismo su rol de administración
4. Un cambio de rol solo es válido si el rol destino existe en cat_roles y es distinto del vigente
5. Un usuario cuyo rol cambia no conserva sesión vigente con los permisos anteriores
**Criterios de aceptación:**
1. AC-ROL-01: **Dado** el acto de alta de un usuario, **cuando** el ADMINISTRADOR lo confirma, **entonces** el usuario queda con exactamente un `role_code` vigente del catálogo `cat_roles`, nunca cero ni más de uno, y no existe ninguna ruta (UI ni API) que permita persistir un usuario sin rol.
2. AC-ROL-02: **Dado** un usuario con rol EMPLEADO, **cuando** el ADMINISTRADOR le asigna TECNICO_MANTENIMIENTO desde su ficha, **entonces** el cambio se registra con `role_changed_at` y `role_changed_by`, su sesión vigente se invalida y en la siguiente petición sus permisos efectivos son los del nuevo rol (deja de estar limitado a sus propias incidencias).
3. AC-ROL-03: **Dado** una operación de cambio de rol, **cuando** se envía un rol inexistente en `cat_roles`, **entonces** responde 400; **cuando** se envía el mismo rol que ya tiene el usuario, **entonces** responde 409; **cuando** el ADMINISTRADOR intenta retirarse a sí mismo su rol de administración, **entonces** responde 422 y el rol permanece inalterado.
**Validaciones:**
1. `role_code` de destino es obligatorio y debe existir en el catálogo `cat_roles`
2. `role_code` de destino debe ser distinto del rol vigente del usuario
**Escenarios de error:**
1. El rol indicado no es un rol válido del catálogo
2. El usuario ya tiene asignado ese rol
3. No es posible retirarse el propio rol de administración
4. El usuario al que se quiere cambiar el rol no existe
5. El solicitante no tiene permisos para asignar o cambiar roles
**Campos de datos:**
- `role_code` (enum, obligatorio) — Debe existir en cat_roles y ser distinto del vigente
- `role_changed_at` (datetime, obligatorio) — Automático
- `role_changed_by` (reference, obligatorio) — Automático; no puede retirarse su propio rol de administración

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-071/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-071/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: arrancar la aplicación y ejecutar sus tests de integración.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.

**Tests de integración contra `oracle` — reglas de obligado cumplimiento:**
1. El motor del test tiene que ser **el mismo del proyecto**: `gvenzl/oracle-free:23-slim`, vía `testcontainers[oracle-free]`. NO uses otro motor (ni `PostgreSQLContainer`, ni H2, ni una BBDD embebida) aunque el test 'pase': verificarías contra un dialecto que no es el de producción, que es exactamente cómo se cuelan los defectos de esquema. Esta regla PREVALECE sobre cualquier plantilla o ejemplo de este brief —incluidas las «Plantillas canónicas» del handbook—: si alguna usa otro motor, la plantilla está mal; sigue esta regla y repórtalo como Warning.
2. Fija Testcontainers en **1.21.3 o superior**. El daemon de esta sesión exige API ≥1.40; las versiones anteriores de Testcontainers negocian v1.32 y el daemon las rechaza — el error que verías es `Could not find a valid Docker environment`, que no apunta a la causa. Añadir la dependencia en scope de test está autorizado: es convención del proyecto, no desviación del arquetipo.
3. El daemon ya está configurado en tu entorno (`DOCKER_HOST`, `DOCKER_API_VERSION`, `TESTCONTAINERS_HOST_OVERRIDE`): NO los toques ni montes el socket. Basta declarar el contenedor en el test.
4. **Si el test no llega a funcionar, NO lo desactives** —ni renombrando el fichero, ni borrándolo, ni comentándolo—: eso quita cobertura de forma invisible para el revisor y para el CI. Déjalo en el entregable, márcalo como que requiere Docker de la forma que el proyecto ya use para eso (etiqueta/anotación condicional) y repórtalo como health check **Warning** con el error EXACTO que te dio. Un test desactivado en silencio es peor que un test que falla.

### Si el entorno no está disponible
Comprueba `.mind/TSK-071/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.