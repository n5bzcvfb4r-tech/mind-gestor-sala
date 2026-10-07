# TSK-057 · aviso_correo: outbox transaccional de avisos con idempotencia, estados y supresión

- Componente dueño: `ARC-016`
- Arquetipo del repo: `database-relational` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/facilities/changelogs/0.0.1/ddl/14-aviso-correo.xml`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
Crea `aviso_correo` (dispatch_id PK, notification_type NOT NULL check en ('NEW_INCIDENT_ALERT','STATUS_CHANGE_ALERT','CREDENTIAL_ISSUED','PASSWORD_RESET'), incident_id FK a `incidencia`, history_entry_id FK a `incidencia_historico`, recipient_user_id FK a `usuario`, recipient_email varchar2(254) (snapshot del encolado), notification_key varchar2(100) NOT NULL UNIQUE, status NOT NULL check en ('PENDIENTE','ENVIANDO','ENVIADO','FALLIDO','DESCARTADO','SUPRIMIDO') default 'PENDIENTE', subject varchar2(255), body_text CLOB, body_html CLOB (snapshot congelado que no se recompone en reenvíos, REQ-138/REQ-142), previous_status_code, new_status_code, attempt_count NUMBER default 0 check >= 0, max_attempts, next_attempt_at, locked_by, locked_at, last_error_code, last_error_message varchar2(500), suppression_reason_code check en ('USUARIO_DESACTIVADO','SIN_CORREO','DESTINATARIO_NO_RESOLUBLE','NO_RECIPIENTS','COMPOSICION_INCOMPLETA'), suppressed_at, sent_at, created_at NOT NULL). Índices únicos: (incident_id, notification_type) para NEW_INCIDENT_ALERT —exactamente un aviso de alta por incidencia, AC-AVI-01/AC-AVI-02— y (incident_id, history_entry_id) para STATUS_CHANGE_ALERT. Índice (status, next_attempt_at, created_at) para el consumo FIFO. Checks: `attempt_count <= max_attempts`; `suppression_reason_code` informado si y solo si status='SUPRIMIDO'; trigger de transición que rechaza pasar de 'ENVIADO' a cualquier otro estado (terminal, REQ-142). Oráculos de persistencia contra el Oracle real: test que inserta dos veces la misma `notification_key` y recibe ORA-00001 (idempotencia del reintento y del doble submit); test de concurrencia con dos conexiones ejecutando `SELECT ... FOR UPDATE SKIP LOCKED` que obtienen filas distintas y ninguna duplicada; test que intenta `UPDATE status='FALLIDO'` sobre una fila 'ENVIADO' y es rechazado; test que confirma el patrón outbox: el rollback del alta de incidencia no deja ninguna fila en `aviso_correo` (AC-AVI-05). `update`/`rollback-count` verdes.

## Oráculos de verificación (dod-oracles) — OBLIGATORIO

El DoD se evalúa por **comportamiento**, no porque exista un fichero o un string «implementado». Lo siguiente es **Blocker** si lo usas como entrega de producto (los dobles solo valen en tests):

- **Email / notificación:** cliente real o puerto inyectable (`aiosmtplib`, SES, SendGrid, …) + test que verifica que se invocó el envío. **`log.info` / `print` / «Simula el envío» ≠ email.**
- **Auth / rol (p. ej. ADMINISTRADOR):** dependency o middleware que devuelve 401/403 sin credencial/rol; tests con y sin permiso. **Un CRUD abierto no cumple «solo admin».**
- **Evento / AsyncAPI:** productor que publica al canal declarado; test que captura el publish. **Loguear el payload ≠ publicar el evento.**
- **Persistencia:** driver del stack del arquetipo (Motor/SQLAlchemy/…) contra el motor de prueba o Testcontainers. **`dict` / `db_*` in-memory en el módulo de producto ≠ base de datos.**

Si el entorno de prueba no levanta el servicio necesario: escribe el código de producto real + tests, declara Warning `entorno-de-prueba`, y **NO** sustituyas el DoD con un fake en el código entregado.

## El esquema tiene que soportar el modelo (ddl-match) — OBLIGATORIO

Un changelog que se EJECUTA puede estar mal: Hibernate no encontrará la columna, o la CHECK rechazará todo lo que la aplicación escriba.

1. El DDL se **deriva** de las entidades del repo (tabla, nombre de columna con la estrategia de Hibernate — `dueDate` → `due_date` —, tipo y nulabilidad). No se escribe en paralelo a ellas.
2. Las CHECK de enumerados usan lo que se PERSISTE. Con `@Enumerated(EnumType.STRING)` eso es `Enum.name()` (`LOW`/`MEDIUM`), **no** la etiqueta de presentación (`'Baja'`/`'Media'`).
3. Ningún índice de árbol B sobre una columna `@Lob`/CLOB: Oracle lo rechaza. Búsqueda por texto en CLOB = Oracle Text (`CONTAINS`).
4. Toda FK apunta a una tabla que ALGÚN changelog de este repo crea.
5. Un solo changelog maestro, en la ruta que la configuración declara. Un `.xml` que nadie incluye no crea nada.

## Dependencias pineadas (dependency-pins) — OBLIGATORIO

Un manifiesto sin versiones (o incompleto frente a los imports) NO es entrega válida.

1. **Python:** en `requirements.txt` / deps de `pyproject.toml` cada paquete lleva pin (`==` preferido, o rango acotado `>=x,<y`). **PROHIBIDO** listar solo el nombre (`fastapi`, `uvicorn`, `pydantic`).
2. **Completitud:** toda librería de terceros que importes (`sqlalchemy`, `pydantic`, `motor`, …) debe figurar en el manifiesto.
3. **API ↔ major:** el código debe ser compatible con el major pineado. Si usas `__modify_schema__` / `@validator` / `from_orm` (Pydantic v1), pinea `pydantic>=1.10,<2` (o equivalente). Si pegas Pydantic 2, usa APIs v2 (`field_validator`, `model_validate`). NUNCA código v1 + install latest.
4. **Node:** versiones en `package.json` + `package-lock.json` cuando toques deps; no dejes dependencias sin versión.
5. Actualiza el manifiesto del host en ESTA tarea (misma excepción de zona que el composition root).

## Extiende la zona, no la reimplementes (zone-extend) — OBLIGATORIO

Tus zonas (`sources/facilities/changelogs/0.0.1/ddl/14-aviso-correo.xml`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

1. **Lista y lee** los ficheros bajo la zona (`ls` / abre `service.py`, `router.py`, …).
2. **EDIT/EXTIENDE** clases, funciones y exports existentes. **PROHIBIDO** una segunda `class`/`def`/export con el **mismo nombre** en el mismo fichero (en Python gana la última y el resto es basura).
3. Si esta tarea es «API pública» / «consulta» sobre el mismo dominio que un CRUD previo, **reutiliza** servicios y modelos; añade solo el router/handlers públicos (montados en el composition root).
4. Un módulo = un dueño semántico de cada símbolo top-level. Si hace falta otro tipo, **nómbralo distinto** o factoriza — no pegues un duplicado al EOF.

El runtime puede anexar la lista real de ficheros presentes en la zona al arrancar la sesión.

## Contrato de salida del arquetipo

> El repo se genera desde el arquetipo `database-relational` (ArqRef MAPFRE), y de él heredas la HERRAMIENTA y la FORMA de los ficheros: imita el esqueleto/ejemplos de abajo y NO improvises otra herramienta (p.ej. si el arquetipo usa Liquibase, NO uses Flyway). Las RUTAS, en cambio, las manda este repo —ver «Raíz del proyecto»— porque el modelo de datos ya vive aquí y no en un repo de BBDD dedicado.

**Raíz del proyecto**: el modelo de datos de este repo YA existe y vive en `sources/facilities/` — su changelog maestro es `sources/facilities/master.xml`, persistido por una tarea anterior. EXTIENDE ese maestro (añade tus changelogs y su `<include>`) y NO montes aquí el layout `sources/`, `local/`, `apps/`, `libs/`, `src/` del arquetipo: un segundo maestro parte el esquema en dos y la plataforma aplica uno solo, así que tu entrega quedaría fuera del entorno de quien la consuma. Del arquetipo heredas la HERRAMIENTA y la forma de los changelogs (los ejemplos de abajo), no la raíz.

### Estructura del proyecto (del arquetipo)
```
├── .github
|  └── workflows
|      ├── merge-commit.yml
|      └── pull-request.yml
├── local
|  ├── docker-compose.yml
|  ├── DockerfileLiquibase
|  ├── liquibase-local.sh
|  ├── mar2_database1.properties
├── sources
|  └── mar2_database1
|      ├── changelogs
|      |   ├── 0.0.1
|      |   │   ├── dcl
|      |   │   │   └── grant-employees.xml
|      |   │   ├── ddl
|      |   │   │   └── table-employees.xml
|      |   │   ├── dml
|      |   │   │   └── data-employees.xml
|      |    └── changelog-0.0.1.xml
|      └── master.xml
├── CHANGELOG.md
├── CONTRIBUTING.md
└── project-manifest.json
└── README.md

```
- `.github/workflows/merge-commit.yml`: Workflow de GitHub para el proceso de integración continua (CI), genera y publica el artefacto de la base de datos relacional.
- `.github/workflows/pull-request.yml`: Workflow de GitHub para el proceso de integración continua (Build, Test, Sonar) cuando se crea o actualiza un Pull Request.
- `README.md`: Este archivo.
- `local/docker-compose.yaml`: Archivo docker-compose para pruebas locales, crea un contenedor de base de datos.
- `local/DockerfileLiquibase`: Dockerfile para el `cli` de Liquibase.
- `local/liquibase-local.sh`: Script Bash para invocar usar el `cli` de Liquibase usando un contenedor Docker.
- `local/mar2_database1.properties`: Archivo de propiedades de Liquibase para pruebas locales.
- `sources/mar2_database1/master.xml`: Archivo maestro de Liquibase para la base de datos mar2_database1.
- `sources/mar2_database1/changelogs/changelog-0.0.1.xml`: Ejemplo de archivo changelog de Liquibase para la release 0.0.1. Este archivo incluye los cambios de definición de la base de datos (DDL), manipulación de datos (DML) y control de acceso (DCL) de la base de datos mar2_database1.
- `sources/mar2_database1/changelogs/0.0.1/ddl/table-employees.xml`: Ejemplo de archivo changelog de Liquibase para crear una tabla. La carpeta `ddl` contiene los cambios de definición de la base de datos mar2_database1.
- `sources/mar2_database1/changelogs/0.0.1/dml/data-employees.xml`: Ejemplo de archivo changelog de Liquibase para insertar datos. La carpeta `dml` contiene los cambios de manipulación de datos de mar2_database1.
- `sources/mar2_database1/changelogs/0.0.1/dcl/dcl-0.0.1-0.xml`: Ejemplo de archivo changelog de Liquibase para crear permisos. La carpeta `dcl` contiene los cambios de control de acceso a la base de datos mar2_database1.
- `CHANGELOG.md`: Registro de cambios realizados en el repositorio.
- `CONTRIBUTING.md`: Información necesaria para contribuir al repositorio.
- `project-manifest.json`: Descriptor del proyecto, contiene la versión y la configuración de las bases de datos.

### Esqueleto y ejemplos (imítalos exactamente)
#### `sources/mar2_database1/master.xml`
```xml
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog" xmlns:ext="http://www.liquibase.org/xml/ns/dbchangelog-ext" xmlns:pro="http://www.liquibase.org/xml/ns/pro" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog-ext http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-ext.xsd http://www.liquibase.org/xml/ns/pro http://www.liquibase.org/xml/ns/pro/liquibase-pro-latest.xsd http://www.liquibase.org/xml/ns/dbchangelog http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd">
   
   <include file="/changelogs/0.0.0/init-schema.xml" relativeToChangelogFile="true"/>
   <include file="/changelogs/0.0.1/changelog-0.0.1.xml" relativeToChangelogFile="true"/>   

</databaseChangeLog>

```
#### `local/mar2_database1.properties`
```properties
changeLogFile=sources/mar2_database1/master.xml

#for docker use
liquibase.command.url=jdbc:oracle:thin:@//oracle-mar2_database1:1521/mar2_database1
#for local use
#liquibase.command.url=jdbc:oracle:thin:@//localhost:1521/mar2_database1
liquibase.command.username=liquibase


```
#### `sources/mar2_database1/changelogs/0.0.0/init-schema.xml`
```xml
<databaseChangeLog 
    xmlns="http://www.liquibase.org/xml/ns/dbchangelog" 
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd">
    
    <!-- CREAR ESQUEMA PARA BUILD AND TEST -->
    <changeSet id="create-schema-and-grants" author="PGO">
        <preConditions onFail="MARK_RAN">
            <sqlCheck expectedResult="0">
                SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = 'mar2_database1';
            </sqlCheck>
        </preConditions>
        <sql>
            CREATE SCHEMA mar2_database1;
            CREATE USER pgogopic_mar2_database1_appuser WITH PASSWORD 'entornodev';
            GRANT USAGE ON SCHEMA mar2_database1 TO pgogopic_mar2_database1_appuser;
        </sql>
        <rollback>
            <!-- SE DEJA VACIO HASTA QUE MEJOREN EL ARQUETIPO -->
        </rollback>
    </changeSet>
</databaseChangeLog>
```
#### `sources/mar2_database1/changelogs/0.0.1/changelog-0.0.1.xml`
```xml
<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd">
   
     <!-- DDL changesets -->
     <include file="/ddl/table-employees.xml" relativeToChangelogFile="true"/>
     <!-- DML changesets -->
     <include file="/dml/data-employees.xml" relativeToChangelogFile="true"/>
     <!-- DCL changesets -->
     <include file="/dcl/grants-employees.xml" relativeToChangelogFile="true"/>

</databaseChangeLog>
```
#### `sources/mar2_database1/changelogs/0.0.1/dcl/grants-employees.xml`
```xml
<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd">
   
  
    <!-- <changeSet id="dcl-0.0.1-0" author="user">
        <sql>
            GRANT SELECT, INSERT, UPDATE, DELETE ON employees TO your_user_name;
        </sql>
    </changeSet> -->

</databaseChangeLog>
```
#### `sources/mar2_database1/changelogs/0.0.1/ddl/table-employees.xml`
```xml
<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd">
   
    <changeSet id="ddl-0.0.1-0" author="user">
        <createTable tableName="employees">
            <column name="id" type="INT">
                <constraints primaryKey="true" nullable="false"/>
            </column>
            <column name="first_name" type="VARCHAR(255)">
                <constraints nullable="false"/>
            </column>
            <column name="last_name" type="VARCHAR(255)">
                <constraints nullable="false"/>
            </column>
            <column name="email" type="VARCHAR(255)">
                <constraints nullable="false" unique="true"/>
            </column>
            <column name="hire_date" type="DATE">
                <constraints nullable="false"/>
            </column>
        </createTable>
        <rollback>
            <dropTable tableName="employees"/>
        </rollback>
    </changeSet>   
</databaseChangeLog>
```
#### `sources/mar2_database1/changelogs/0.0.1/dml/data-employees.xml`
```xml
<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="http://www.liquibase.org/xml/ns/dbchangelog http://www.liquibase.org/xml/ns/dbchangelog/dbchangelog-latest.xsd">
   
       <changeSet id="dml-0.0.1-0" author="user">
        <insert tableName="employees">
            <column name="id" value="1"/>
            <column name="first_name" value="John"/>
            <column name="last_name" value="Doe"/>
            <column name="email" value="john.doe@example.com"/>
            <column name="hire_date" valueDate="2023-01-15"/>
        </insert>
        <insert tableName="employees">
            <column name="id" value="2"/>
            <column name="first_name" value="Jane"/>
            <column name="last_name" value="Smith"/>
            <column name="email" value="jane.smith@example.com"/>
            <column name="hire_date" valueDate="2023-02-20"/>
        </insert>
        <insert tableName="employees">
            <column name="id" value="3"/>
            <column name="first_name" value="Alice"/>
            <column name="last_name" value="Johnson"/>
            <column name="email" value="alice.johnson@example.com"/>
            <column name="hire_date" valueDate="2023-03-10"/>
        </insert>
        <insert tableName="employees">
            <column name="id" value="4"/>
            <column name="first_name" value="Bob"/>
            <column name="last_name" value="Brown"/>
            <column name="email" value="bob.brown@example.com"/>
            <column name="hire_date" valueDate="2023-04-05"/>
        </insert>
        <rollback>
            <delete tableName="employees">
                <where>id IN (1, 2, 3, 4)</where>
            </delete>
        </rollback>
    </changeSet>  
</databaseChangeLog>
```

## Guía del programador del proyecto (convenciones — T.7, aprobada)

**Precedencia (handbook-filter):** si esta guía choca con el **contrato ArqRef** o el **DoD de ESTA tarea**, ganan ArqRef y el DoD. Solo se incluyen convenciones del stack de esta TSK; se omiten slices de otros lenguajes/frameworks (p. ej. Angular HttpClient en una TSK React, FastAPI en un SPA).

### Librerías del handbook (pines — dependency-pins)
Usa estas coordenadas/versiones en el manifiesto del host; no improvises latest sin pin.
- **Alembic** `1.14.x o superior` [bbdd] · `alembic`
- **argon2-cffi (Argon2id)** `23.1.x o superior` [backend] · `argon2-cffi`
- **APScheduler** `3.11.x o superior` [backend] · `APScheduler`
- **Pillow** `11.x o superior` [backend] · `pillow`
- **aiosmtpd** `1.4.6 o superior` [tests] · `aiosmtpd`
- **freezegun** `1.5.x o superior` [tests] · `freezegun`
- **Ruff (linter + formatter)** `0.8.x o superior` [backend] · `ruff`
- **mypy** `1.14.x o superior` [backend] · `mypy`
- **Prettier** `3.4.x` [frontend] · `prettier`
- **GitHub Actions** `runners ubuntu-24.04` [infra]
- **pre-commit** `4.0.x o superior` [infra] · `pre-commit`

### Convenciones
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
- Declarar la PK con autoincrement=True, con un tipo SERIAL, o con AUTOINCREMENT en el DDL. → usa: sa.Column('<entity>_id', sa.Integer(), sa.Identity(always=True), primary_key=True), que Oracle traduce a NUMBER GENERATED ALWAYS AS IDENTITY. AUTOINCREMENT es sintaxis de SQLite y SERIAL de PostgreSQL: ninguna existe en Oracle y el DDL falla en el despliegue, no en el test unitario.
- Codificar las transiciones de estado o la matriz de permisos como cadenas de if/elif en el servicio (if estado == 'ABIERTA' and nuevo == 'EN_CURSO': ...). → usa: Delegar en TransicionRuleService y PermisoRuleService, que leen cat_transicion_incidencia y permiso_rol_operacion con deny-by-default y devuelven la regla aplicada para trazarla. REQ-021 y REQ-117 exigen fuente unica de verdad y 'ninguna comprobacion de rol codificada dispersa en los endpoints', verificable por revision de codigo (AC-PERM-01).
- Comprobar el rol o el alcance dentro del cuerpo del endpoint (if actor.role_code != 'TECNICO_MANTENIMIENTO': raise 403) en lugar de declararlo en la dependencia require(operation_code). → usa: Declarar siempre Depends(require(Op.X)) en la firma del endpoint. Un endpoint sin require(...) y no listado como publico hace fallar el test de contrato de rutas. Esto materializa el 'protegido por defecto' de AC-SES-04 y evita el endpoint nuevo que nadie recordo proteger.
- Loggear el cuerpo completo de la peticion, el correo corporativo del usuario, la contrasenia, la credencial temporal o la cookie de sesion. → usa: Registrar identificadores (session_user_id, incidencia_id, operation_code, outcome) y confiar el enmascarado al processor central de structlog, que elimina password*, temporary_password, password_hash, password_salt, session_token, Cookie, Authorization y corporate_email. REQ-063, REQ-076 y REQ-079 lo prohiben de forma explicita y es auditable.
- Emitir un JWT autocontenido con el rol embebido y considerar la sesion cerrada solo en el navegador. → usa: Sesion opaca server-side en tabla sesion_usuario, cookie HttpOnly+Secure+SameSite=Strict, y relectura del rol vigente en BBDD en cada peticion. REQ-057, REQ-070 y REQ-086 exigen revocacion inmediata en servidor (logout, cambio de contrasenia, desactivacion) y REQ-019 que el cambio de rol surta efecto en la siguiente peticion: un JWT con rol embebido no puede cumplirlo.
- Enviar el correo SMTP dentro de la transaccion del alta o de la transicion de estado, o encolar el aviso en una lista en memoria del proceso. → usa: Patron outbox: la solicitud de aviso se INSERTA en la misma transaccion que el alta o la transicion; el despachador ARC-014 la toma despues en FIFO con bloqueo y la entrega. Un SMTP caido debe dejar la solicitud PENDIENTE y devolver 2xx al usuario (AC-AVI-05), nunca revertir la operacion de negocio ni perder el aviso al reiniciar el contenedor.
- Hacer commit() o rollback() dentro de un repositorio, o guardar la incidencia y su asiento de historico en transacciones separadas. → usa: El unico with uow(db) esta en el metodo del servicio y abarca la escritura de negocio, su asiento de historico y su fila de outbox. AC-CIE-05 exige que un fallo al escribir el historico deje la incidencia en 'resuelta' sin comentario ni asiento; con commits parciales quedan historicos huerfanos imposibles de conciliar (AC-TRZ-03).

### Matriz de compatibilidad de tipos
- `datetime (naive, UTC)` en `oracle`: **OK** — TIPO CANONICO del proyecto para toda marca temporal. mapped_column(DateTime(timezone=False)) -> TIMESTAMP(6). Se sella con utc_now() en el servidor y se formatea a Europe/Madrid solo en presentacion.
- `datetime (aware, con tzinfo)` en `oracle`: **PROHIBIDO** — Oracle lo soporta como TIMESTAMP WITH TIME ZONE, pero su uso esta PROHIBIDO en este proyecto: mezclar naive y aware en el mismo modelo provoca TypeError al restar fechas (calculo de days_in_current_status, time_to_resolution) y desfases en los filtros de rango inclusivo.
- `int (identificador de PK)` en `oracle`: **OK** — mapped_column(Integer, Identity(always=True), primary_key=True) -> NUMBER GENERATED ALWAYS AS IDENTITY. Es la unica forma admitida de generar PKs; no se usan secuencias manuales ni triggers.
- `str corto/medio (<= 4000)` en `oracle`: **OK** — mapped_column(String(n)) -> VARCHAR2(n CHAR). Declarar SIEMPRE la longitud explicita en caracteres, no en bytes (el parametro de sesion NLS_LENGTH_SEMANTICS no debe darse por supuesto). Ej.: description String(500), reference_code String(20), corporate_email String(254).
- `str largo (texto ilimitado) CLOB` en `oracle`: **OK** — mapped_column(CLOB) para resolution_comment y para el cuerpo del correo (body_text/body_html). No se puede indexar ni usar en DISTINCT/GROUP BY directamente; si hace falta buscar en el, usar Oracle Text o una columna VARCHAR2 derivada.
- `bytes BLOB` en `oracle`: **PROHIBIDO** — Tecnicamente soportado por Oracle, pero PROHIBIDO en este proyecto por ADR-005: la foto adjunta se guarda en el volumen persistente del contenedor (ARC-015) con clave opaca, y en Oracle solo viven los metadatos y el checksum SHA-256. Persistir binarios como BLOB cargaria la base y encareceria la copia.
- `cualquier tipo` en `h2`: **PROHIBIDO** — H2 NO es un motor de este proyecto y no puede usarse como sustituto de Oracle en los tests: no reproduce IDENTITY always, OFFSET..FETCH, SYS_EXTRACT_UTC ni la semantica de VARCHAR2(n CHAR). Los tests de integracion usan Testcontainers con Oracle 23ai Free.
- `cualquier tipo` en `sqlite`: **PROHIBIDO** — SQLite NO es un motor de este proyecto. Se declara explicitamente para bloquear el atajo de 'un sqlite en memoria para los tests': su tipado dinamico, su AUTOINCREMENT y su datetime('now') no tienen equivalencia con Oracle y ocultarian errores de DDL hasta el despliegue.

## Modelo de datos a implementar (diseñado en arquitectura — T.5)

> Modelo FÍSICO ya diseñado (21 entidades). TRANSCRÍBELO tal cual a los changelogs del arquetipo — NO inventes tablas ni columnas, NO re-diseñes. **schema-names:** el DoD debe cubrir **todas** las entidades del inventario; las TSK de API posteriores usarán **los mismos nombres**.

**Inventario T.5 (obligatorio en migraciones):** `cat_rol`, `cat_operacion`, `permiso_rol_operacion`, `cat_oficina`, `cat_sala`, `cat_categoria_incidencia`, `cat_estado_incidencia`, `cat_transicion_incidencia`, `cat_motivo_desactivacion`, `configuracion_smtp`, `usuario`, `usuario_password_historico`, `sesion_usuario`, `incidencia`, `incidencia_adjunto`, `aviso_correo`, `usuario_historico`, `auditoria_acceso`, `incidencia_historico`, `aviso_correo_intento`, `resolucion_destinatario_log`

**Semillas OBLIGATORIAS (`data_kind = catalog`): `cat_rol`, `cat_operacion`, `permiso_rol_operacion`, `cat_oficina`, `cat_sala`, `cat_categoria_incidencia`, `cat_estado_incidencia`, `cat_transicion_incidencia`, `cat_motivo_desactivacion`, `configuracion_smtp`.** Una tabla de catálogo vacía deja inoperativos los endpoints que la leen (tres en el ciclo anterior). Además del DDL, entrega un changelog **`dml`** por cada una con sus valores de referencia (derivados de los requisitos y de las notas de `constraint_note`: enumerados, estados, tipos), idempotente (`INSERT` con precondición o `MERGE`) y aplicado por el changelog maestro. El DoD las nombra una a una; el DoD que dice «catálogos poblados» sin nombrarlos no vale.

- **Motor de datos del proyecto: Oracle no fijada en MAR2** (capa `db` del stack, aprobada en T.2/T.3). TODO el DDL y el DML que escribas debe ser válido EN ESE MOTOR y en su dialecto. Nada de tipos ni sintaxis de otros motores: si un tipo o construcción no existe en Oracle, usa su equivalente nativo. Y no te fíes de tu criterio: APLICA el changelog contra el motor real del entorno de prueba antes de entregar (ver «Entorno de prueba de esta sesión»).

### ARC-100 · `cat_rol` (table) · **catalog**
Catálogo cerrado de roles funcionales del sistema.
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
**Columnas:**
- `role_code` VARCHAR2(32) [PK, NOT NULL] — PK; valores cerrados: EMPLEADO, TECNICO_MANTENIMIENTO, ADMINISTRADOR
- `role_name` VARCHAR2(60) [NOT NULL] — Etiqueta legible en español; única
- `role_description` VARCHAR2(200) — Resumen de permisos del rol mostrado en la ficha de usuario
- `display_order` NUMBER [NOT NULL] — Único; orden de presentación en desplegables
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y; baja lógica, sin borrado físico

### ARC-101 · `cat_operacion` (table) · **catalog**
Catálogo de operaciones funcionales autorizables de la API.
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
**Columnas:**
- `operation_code` VARCHAR2(50) [PK, NOT NULL] — PK; enum: INCIDENT_CREATE|INCIDENT_LIST_OWN|INCIDENT_LIST_ALL|INCIDENT_VIEW|INCIDENT_HISTORY_VIEW|INCIDENT_ASSIGN_SELF|INCIDENT_STATUS_CHANGE|INCIDENT_CLOSE_WITH_COMMENT|USER_MANAGE
- `operation_name` VARCHAR2(100) [NOT NULL] — Etiqueta legible en español
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y

### ARC-102 · `permiso_rol_operacion` (table) · **catalog**
Matriz única rol x operación con el alcance de datos aplicable; fuente de verdad de la autorización.
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
**Columnas:**
- `permiso_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `role_code` VARCHAR2(32) [NOT NULL, FK→cat_rol] — Único junto a operation_code (una sola entrada por par)
- `operation_code` VARCHAR2(50) [NOT NULL, FK→cat_operacion] — Único junto a role_code
- `data_scope` VARCHAR2(10) [NOT NULL] — enum: OWN|ALL; ausencia de fila = denegado (deny by default)

### ARC-103 · `cat_oficina` (table) · **catalog**
Catálogo de las oficinas de la organización a las que pertenecen las salas.
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
**Columnas:**
- `office_id` NUMBER [PK, NOT NULL] — PK autogenerada
- `office_code` VARCHAR2(10) [NOT NULL] — Único
- `office_name` VARCHAR2(80) [NOT NULL] — Máx. 80 caracteres
- `city` VARCHAR2(60) — Opcional; máx. 60 caracteres
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y; no desactivable con salas activas; siempre >=1 oficina activa

### ARC-104 · `cat_sala` (table) · **catalog**
Catálogo de salas de reuniones sobre las que se reportan incidencias.
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
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
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
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
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
**Columnas:**
- `status_code` VARCHAR2(20) [PK, NOT NULL] — PK; enum: ABIERTA|EN_CURSO|RESUELTA|CERRADA
- `status_name` VARCHAR2(40) [NOT NULL] — Etiqueta de presentación en español
- `sort_order` NUMBER [NOT NULL] — Único; secuencia del ciclo de vida usada para ordenar
- `is_terminal` CHAR(1) [NOT NULL] — Y/N; Y solo para CERRADA
- `is_active` CHAR(1) [NOT NULL] — Y/N; no desactivable si hay incidencias vivas en ese estado

### ARC-107 · `cat_transicion_incidencia` (table) · **catalog**
Grafo de transiciones permitidas entre estados de incidencia y sus precondiciones.
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
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
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
**Columnas:**
- `reason_code` VARCHAR2(30) [PK, NOT NULL] — PK; valor obligatorio en toda desactivación
- `reason_name` VARCHAR2(120) [NOT NULL] — Etiqueta en español mostrada en el desplegable de baja
- `is_active` CHAR(1) [NOT NULL] — Y/N, por defecto Y

### ARC-109 · `configuracion_smtp` (table) · **catalog**
Parámetros del servidor de correo y del remitente usados para los avisos.
_Semillas: changelog `dml` con sus valores de referencia (obligatorio)._
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

### REQ-130 — Disparo automático y único del aviso al equipo de mantenimiento al crear una incidencia
El sistema dispara automáticamente, y una sola vez, el aviso al equipo de mantenimiento cuando se crea una incidencia nueva. Reglas: el aviso se dispara exclusivamente al completarse con éxito el alta de una incidencia; exactamente un aviso por incidencia mediante restricción única (incident_id, notification_type) que hace idempotente cualquier reintento; ningún cambio de estado posterior (en curso/resuelta/cerrada) vuelve a disparar este aviso; si el alta falla o se revierte, no queda solicitud de aviso alguna. Flujo: el EMPLEADO confirma el alta (REQ-090) → se persiste la incidencia → en la misma transacción se inserta la solicitud de aviso en PENDING (AVI-03) → commit → el procesador asíncrono resuelve destinatarios (REQ-082, REQ-089) → compone (AVI-02) → entrega (SMTP-01). Datos: dispatch_id (uuid, PK), incident_id (number, FK, obligatorio), notification_type (varchar2, catálogo, valor NEW_INCIDENT_ALERT), status (varchar2, catálogo: PENDING/SENDING/SENT/FAILED), created_at (timestamp, informado por el sistema). Catálogos: cat_notification_types, cat_dispatch_statuses [gap: el RFP no enumera tipos de aviso ni estados de envío]. Validaciones: incidencia existente y persistida; notification_type y status de catálogo; unicidad (incident_id, notification_type). Errores: si falla la inserción de la solicitud, falla toda la transacción de alta y el empleado ve 'No se ha podido registrar la incidencia. Inténtelo de nuevo.' (HTTP 500); un segundo intento de registrar el mismo aviso se ignora de forma idempotente. CA: Given una incidencia creada con éxito, When concluye el alta, Then existe exactamente una solicitud NEW_INCIDENT_ALERT en PENDING para ese incident_id; Given una incidencia ya avisada, When pasa a 'en curso', Then no se crea ninguna nueva solicitud; Given N incidencias creadas, Then se ha enviado exactamente un correo de alta por incidencia sin duplicados. Seguridad: el disparo es del sistema, no de un actor; lo origina el EMPLEADO autenticado que reporta (REQ-033, REQ-090); ningún rol puede disparar el aviso manualmente salvo el reenvío controlado de SMTP-04 reservado a ADMINISTRADOR; sin doble factor. Eventos: consume IncidentCreated y emite MaintenanceAlertDispatched [inferido]. Dependencias: REQ-090, REQ-082 + REQ-089, AVI-03, SMTP-01. Prioridad Must.
**Reglas de negocio:**
1. Una solicitud de aviso de alta existe únicamente para incidencias cuya transacción de alta ha concluido con éxito; si el alta falla o se revierte, no queda ninguna solicitud asociada
2. Los cambios de estado posteriores de una incidencia (en curso, resuelta, cerrada) no originan ninguna solicitud `NEW_INCIDENT_ALERT` adicional
3. El aviso de alta se origina siempre por el sistema a partir de la creación de la incidencia; ningún rol distinto de `ADMINISTRADOR` (vía reenvío controlado) puede originarlo manualmente
4. Existe exactamente una solicitud de aviso de tipo `NEW_INCIDENT_ALERT` por incidencia: la pareja `(incident_id, notification_type)` es única
**Criterios de aceptación:**
1. AC-AVI-01: **Dado** un empleado autenticado que completa el alta de una incidencia, **cuando** la transacción de alta hace commit, **entonces** existe **exactamente una** solicitud de aviso `NEW_INCIDENT_ALERT` en estado `PENDING` para ese `incident_id`, creada en la misma transacción del alta.
2. AC-AVI-02: **Dado** una incidencia cuyo aviso de alta ya se ha registrado, **cuando** la incidencia pasa a `en curso`, `resuelta` o `cerrada`, **entonces** no se crea ninguna solicitud `NEW_INCIDENT_ALERT` adicional y el equipo de mantenimiento no recibe un segundo correo de alta.
3. AC-AVI-05: **Dado** el servidor SMTP caído o inaccesible, **cuando** un empleado crea una incidencia, **entonces** el alta responde `2xx`, la incidencia aparece en su listado y en la bandeja del técnico, y la solicitud de aviso queda en `PENDING` o `FAILED` con el error trazado, sin que el empleado reciba error alguno.
4. AC-AVI-06: **Dado** solicitudes bloqueadas en estado `SENDING` por una caída del worker, **cuando** el servicio se reanuda dentro de la ventana de horario laboral, **entonces** esas solicitudes se reprocesan y las ya entregadas (con `message_id` registrado) **no** generan un segundo correo.
5. AC-SMTP-09: **Dado** un aviso en estado `FAILED`, **cuando** el `ADMINISTRADOR` pulsa "Reenviar" y el servidor SMTP responde correctamente, **entonces** la misma solicitud pasa a `SENT` (sin crear una segunda solicitud), con destinatarios vueltos a resolver en ese momento y un nuevo intento trazado con el actor y la fecha del reenvío.
**Validaciones:**
1. `incident_id` es obligatorio y debe referenciar una incidencia existente y ya persistida
2. `notification_type` es obligatorio y debe pertenecer al catálogo `cat_notification_types` (valor `NEW_INCIDENT_ALERT`)
3. `status` es obligatorio y debe pertenecer al catálogo `cat_dispatch_statuses` (`PENDING`/`SENDING`/`SENT`/`FAILED`)
4. La pareja `(incident_id, notification_type)` debe ser única: se rechaza (de forma idempotente) el alta de una segunda solicitud para la misma incidencia y tipo de aviso
**Escenarios de error:**
1. La incidencia indicada para el aviso no existe o aún no está confirmada
2. No se ha podido registrar la solicitud de aviso junto con el alta y la operación no se completa
**Campos de datos:**
- `dispatch_id` (uuid, obligatorio) — Clave primaria; formato UUID
- `incident_id` (integer, obligatorio) — Referencia a una incidencia ya persistida
- `notification_type` (enum, obligatorio) — Valor de catálogo; en esta épica `NEW_INCIDENT_ALERT`
- `status` (enum, obligatorio) — PENDING / SENDING / SENT / FAILED
- `created_at` (datetime, obligatorio) — Informado por el sistema, no editable
- `unique_key (incident_id + notification_type)` (string, obligatorio) — Restricción única (incident_id, notification_type)

### REQ-131 — Composición del contenido del correo de aviso con los datos clave de la incidencia
El sistema compone el contenido del correo de aviso con los datos clave de la incidencia. Reglas: el correo debe contener como mínimo sala afectada, categoría y descripción breve, más el identificador de la incidencia y el empleado reportante; la descripción se trunca a 500 caracteres con elipsis si excede; la foto adjunta no se envía por correo, se indica su existencia y se enlaza al detalle en la SPA; las etiquetas de sala, oficina y categoría se resuelven a su valor vigente de catálogo aunque esté desactivado (REQ-102, REQ-103); todo el contenido en español (REQ-050). Flujo: tomada la solicitud PENDING → se leen los datos de la incidencia y sus catálogos → se renderiza asunto y cuerpo → se entrega a SMTP-01. Datos: subject (varchar2 200, obligatorio, incluye incident_id y nombre de sala), body_text (clob, obligatorio), body_html (clob, opcional), incident_id (number), room_name, office_name, category_name, description_excerpt (varchar2 500), reporter_name, reporter_email, created_at (timestamp), has_photo (boolean), incident_detail_url. Catálogos: cat_salas, cat_oficinas, cat_categorias_incidencia (REQ-093, REQ-095, REQ-097, REQ-099). Validaciones: ningún campo obligatorio vacío; si no se resuelve la etiqueta de catálogo se usa el código y se traza; subject ≤ 200 caracteres. Errores: si la composición falla por datos inconsistentes, la solicitud pasa a FAILED con error_code = COMPOSE_ERROR, se registra el intento (SMTP-03) y la incidencia no se ve afectada; el empleado no recibe ningún error. CA: Given una incidencia con sala, categoría y descripción, Then el asunto contiene el incident_id y el nombre de la sala y el cuerpo contiene sala, oficina, categoría, descripción y reportante en español; Given una incidencia con foto adjunta, Then el mensaje no lleva la imagen adjunta pero sí el enlace al detalle; Given una descripción de 900 caracteres, Then el cuerpo muestra 500 caracteres y elipsis. Seguridad: el correo solo expone datos de la incidencia y nombre y correo corporativo del reportante (REQ-049); destinatarios únicamente TECNICO_DE_MANTENIMIENTO activos (REQ-082, REQ-089). Integración: Servidor SMTP estándar. Dependencias: AVI-01, REQ-090, REQ-097. [gap: plantilla, asunto corporativo y firma/imagen de marca]. [gap: URL base pública de la SPA para incident_detail_url]. Prioridad Must.
**Reglas de negocio:**
1. El correo de aviso contiene siempre la sala afectada, la categoría, la descripción breve, el identificador de la incidencia y el empleado reportante
2. La descripción de la incidencia incluida en el correo nunca excede 500 caracteres, con elipsis cuando la original es más larga
3. El asunto del correo no excede 200 caracteres e incluye el identificador de la incidencia y el nombre de la sala
4. La foto adjunta a una incidencia nunca viaja como adjunto del correo; en su lugar figura el enlace al detalle en la SPA
5. Las etiquetas de sala, oficina y categoría mostradas en el correo son las vigentes en catálogo aunque el elemento esté desactivado; a falta de etiqueta resoluble figura el código
6. Todo el contenido del correo está en español
7. Los únicos datos personales presentes en el correo son el nombre y el correo corporativo del empleado reportante
**Criterios de aceptación:**
1. AC-AVI-03: **Dado** una incidencia con sala, oficina, categoría, descripción y reportante, **cuando** el sistema compone el correo de aviso, **entonces** el asunto contiene el `incident_id` y el nombre de la sala (≤ 200 caracteres) y el cuerpo contiene sala, oficina, categoría, descripción y nombre y correo del reportante, íntegramente en español.
2. AC-AVI-04: **Dado** una incidencia con foto adjunta y descripción de 900 caracteres, **cuando** se compone el correo, **entonces** el mensaje **no** incluye la imagen como adjunto pero sí el enlace al detalle en la SPA, y la descripción se muestra truncada a 500 caracteres con elipsis.
**Validaciones:**
1. Ningún campo obligatorio del mensaje puede estar vacío: `subject`, `body_text`, `incident_id`, `room_name`, `category_name`, `reporter_name` y `reporter_email`
2. `subject` no puede exceder 200 caracteres
3. `description_excerpt` no puede exceder 500 caracteres (la descripción original se trunca con elipsis si es más larga)
4. `reporter_email` debe cumplir formato de correo electrónico válido
5. Si la etiqueta de un catálogo (sala, oficina, categoría) no se resuelve, se acepta el código como valor y se deja traza, en lugar de emitir el campo vacío
**Escenarios de error:**
1. Faltan datos obligatorios de la incidencia necesarios para componer el aviso
2. El asunto generado supera la longitud máxima admitida
3. No está disponible la dirección base para construir el enlace al detalle de la incidencia
**Campos de datos:**
- `subject` (string, obligatorio) — Máx. 200 caracteres; incluye incident_id y nombre de sala
- `body_text` (text, obligatorio) — Contenido en español
- `body_html` (text, opcional) — Opcional; misma información que body_text
- `incident_id` (integer, obligatorio) — Identificador de la incidencia mostrado en el correo
- `room_name` (string, obligatorio) — Etiqueta vigente de catálogo, aunque esté desactivada
- `office_name` (string, obligatorio) — Etiqueta vigente de catálogo
- `category_name` (string, obligatorio) — Catálogo cerrado: mobiliario / climatización / audiovisual / limpieza / otros
- `description_excerpt` (string, obligatorio) — Truncada a 500 caracteres con elipsis si excede
- `reporter_name` (string, obligatorio) — Dato personal limitado a nombre corporativo
- `reporter_email` (email, obligatorio) — Formato de correo válido
- `created_at` (datetime, obligatorio) — Fecha y hora de alta de la incidencia
- `has_photo` (boolean, obligatorio) — La imagen no se adjunta al correo; solo se señala su existencia
- `incident_detail_url` (string, obligatorio) — URL absoluta; requiere URL base pública de la SPA (gap)

### REQ-132 — Registro transaccional de la solicitud de aviso y procesamiento asíncrono sin bloquear el alta
El sistema registra la solicitud de aviso en la transacción del alta y la procesa de forma asíncrona, sin bloquear ni condicionar el alta. Reglas: el alta responde al empleado sin esperar al envío del correo, para no comprometer el objetivo de reportar en menos de 1 minuto (REQ-092); la solicitud se persiste dentro de la misma transacción del alta (patrón outbox) de modo que no se pierde ningún aviso si el proceso cae tras el commit; un fallo de correo nunca revierte el alta, la incidencia permanece creada y consultable en la bandeja; el procesador toma solicitudes PENDING en orden FIFO con bloqueo (SELECT … FOR UPDATE SKIP LOCKED) y las marca SENDING; una solicitud atascada en SENDING más allá de la ventana de recuperación vuelve a PENDING sin generar correo duplicado (idempotencia de AVI-01 junto con el message_id de SMTP-03); la disponibilidad es solo en horario laboral y las solicitudes pendientes fuera de esa ventana se procesan al reanudar el servicio. Flujo: commit del alta → solicitud PENDING → worker la toma y marca SENDING → compone (AVI-02) → entrega (SMTP-01) → SENT o retorno a PENDING/FAILED (SMTP-02). Datos: status (varchar2, catálogo), locked_by (varchar2), locked_at (timestamp), attempt_count (number, default 0), next_attempt_at (timestamp). Validaciones: transiciones permitidas PENDING→SENDING→SENT|PENDING|FAILED; no se admite reactivar una solicitud en SENT. Errores: worker no disponible → solicitudes quedan PENDING y se envían al reanudar sin pérdida; caída en SENDING → recuperación automática tras la ventana con traza. CA: Given el servidor SMTP caído, When un empleado crea una incidencia, Then el alta responde con éxito, la incidencia aparece en la bandeja y la solicitud queda PENDING/FAILED con el error trazado; Given solicitudes en SENDING al reiniciar, Then se reprocesan sin entregar un segundo correo. Seguridad: proceso interno del sistema sin actor de negocio; el contenido de la cola solo es consultable por ADMINISTRADOR (SMTP-04), el resto de roles no tiene acceso (REQ-078). Dependencias: AVI-01, SMTP-01, SMTP-02. [gap: ventana de recuperación de solicitudes atascadas y franja concreta del horario laboral]. Prioridad Must [inferido].
**Reglas de negocio:**
1. Una solicitud de aviso existe si y solo si la transacción de alta de su incidencia ha hecho commit (la solicitud se persiste en esa misma transacción)
2. Un fallo en el aviso por correo nunca revierte ni oculta la incidencia: esta permanece creada y consultable en la bandeja
3. Las únicas transiciones admitidas de una solicitud son `PENDING→SENDING` y `SENDING→SENT`, `SENDING→PENDING` o `SENDING→FAILED`; una solicitud en `SENT` es terminal y no se reactiva
4. Una solicitud en `SENDING` está tomada por un único worker a la vez, identificado en `locked_by`
5. Una solicitud que permanece en `SENDING` más allá de la ventana de recuperación vuelve a `PENDING` sin producir un segundo correo
6. Fuera del horario laboral no hay procesamiento de solicitudes: las pendientes se conservan y se procesan al reanudar el servicio, sin pérdida
7. La respuesta del alta al empleado es independiente del resultado del envío: el alta no espera al correo
**Criterios de aceptación:**
1. AC-AVI-01: **Dado** un empleado autenticado que completa el alta de una incidencia, **cuando** la transacción de alta hace commit, **entonces** existe **exactamente una** solicitud de aviso `NEW_INCIDENT_ALERT` en estado `PENDING` para ese `incident_id`, creada en la misma transacción del alta.
2. AC-AVI-05: **Dado** el servidor SMTP caído o inaccesible, **cuando** un empleado crea una incidencia, **entonces** el alta responde `2xx`, la incidencia aparece en su listado y en la bandeja del técnico, y la solicitud de aviso queda en `PENDING` o `FAILED` con el error trazado, sin que el empleado reciba error alguno.
3. AC-AVI-06: **Dado** solicitudes bloqueadas en estado `SENDING` por una caída del worker, **cuando** el servicio se reanuda dentro de la ventana de horario laboral, **entonces** esas solicitudes se reprocesan y las ya entregadas (con `message_id` registrado) **no** generan un segundo correo.
4. AC-AVI-07: **Dado** el escenario de carga de referencia con 50 usuarios concurrentes, **cuando** se registran altas de incidencia, **entonces** el tiempo de respuesta del endpoint de alta es **p95 < 2 s** y es **independiente** de la latencia del servidor SMTP (diferencia p95 < 200 ms entre SMTP disponible y SMTP con timeout forzado).
**Validaciones:**
1. La transición de `status` solicitada debe pertenecer al conjunto permitido `PENDING→SENDING→SENT|PENDING|FAILED`
2. No se admite una solicitud de reactivación sobre un envío cuyo `status` es `SENT`
3. `attempt_count` debe ser un número entero no negativo (valor por defecto 0)
**Escenarios de error:**
1. La transición de estado solicitada sobre el aviso no está permitida
2. Se intenta reactivar un aviso que ya consta como entregado
3. El procesamiento de avisos está fuera de la ventana de servicio y la solicitud queda pendiente
**Campos de datos:**
- `status` (enum, obligatorio) — Transiciones permitidas PENDING→SENDING→SENT/PENDING/FAILED
- `locked_by` (string, opcional) — Informado solo mientras la solicitud está en SENDING
- `locked_at` (datetime, opcional) — Base para la ventana de recuperación de solicitudes atascadas
- `attempt_count` (integer, obligatorio) — Valor por defecto 0; entero no negativo
- `next_attempt_at` (datetime, opcional) — Vacío mientras no hay reintento programado

### REQ-134 — Reintento controlado de envíos fallidos transitorios y cierre como fallo definitivo
El sistema reintenta de forma controlada los envíos fallidos por causa transitoria y los cierra como fallo definitivo al agotar los intentos. Reglas: se reintenta solo ante fallo transitorio (timeout, conexión rehusada, error temporal del servidor), un rechazo permanente no se reintenta; máximo de intentos con espera creciente entre ellos [gap: el RFP no define política de reintentos; propuesta a validar: 3 intentos a 1, 5 y 15 minutos]; agotados los intentos la solicitud queda FAILED definitivo y visible en el panel de supervisión (SMTP-04); un reintento nunca produce un segundo correo de un envío ya entregado, la solicitud en SENT con message_id no se reprocesa (idempotencia de AVI-01); con disponibilidad solo en horario laboral, los reintentos programados fuera de esa ventana se ejecutan al reanudar el servicio respetando el orden FIFO. Flujo: fallo transitorio en SMTP-01 → incremento de attempt_count → cálculo de next_attempt_at → solicitud vuelve a PENDING → el worker la retoma cuando next_attempt_at <= now → si attempt_count = max_attempts y vuelve a fallar → FAILED. Datos: attempt_count (number, obligatorio), max_attempts (number, configuración), next_attempt_at (timestamp), last_error_code, last_error_message (varchar2 500), last_attempt_at (timestamp). Validaciones: attempt_count <= max_attempts; no se programa reintento sobre solicitudes en SENT; next_attempt_at siempre futura. Errores: error no clasificable → se trata como transitorio en el primer intento y como permanente a partir del segundo, dejando traza del código devuelto por el servidor. CA: Given que el SMTP devuelve timeout, Then attempt_count se incrementa, se programa next_attempt_at y la solicitud vuelve a PENDING; Given attempt_count = max_attempts, When el envío vuelve a fallar, Then la solicitud queda FAILED y no se reintenta automáticamente; Given una dirección inexistente (rechazo permanente), Then no hay reintento y el motivo queda trazado. Seguridad: proceso interno sin actor; solo el ADMINISTRADOR puede forzar un reenvío posterior (SMTP-04). Integración: Servidor SMTP estándar. Dependencias: SMTP-01, SMTP-03, AVI-03. Prioridad Should [inferido].
**Reglas de negocio:**
1. Solo los fallos transitorios (timeout, conexión rehusada, error temporal del servidor) dan lugar a reintento; un rechazo permanente no se reintenta
2. El número de intentos de una solicitud nunca supera `max_attempts`
3. Una solicitud en `SENT` no tiene reintentos programados ni se reprocesa
4. La fecha `next_attempt_at` de una solicitud es siempre posterior al instante en que se programa
5. Agotado el máximo de intentos, la solicitud queda `FAILED` definitiva y no vuelve a reintentarse de forma automática
6. Un error no clasificable se considera transitorio en el primer intento y permanente a partir del segundo
7. Los reintentos programados fuera del horario laboral se ejecutan al reanudar el servicio respetando el orden FIFO
**Criterios de aceptación:**
1. AC-SMTP-04: **Dado** que el servidor SMTP devuelve un error transitorio (timeout o conexión rehusada), **cuando** el procesador intenta el envío, **entonces** `attempt_count` se incrementa, se programa `next_attempt_at` y la solicitud vuelve a `PENDING`; y **dado** `attempt_count = max_attempts` (propuesta: 3 intentos a 1, 5 y 15 min, `[gap: política de reintentos no definida en el RFP]`), **cuando** el envío vuelve a fallar, **entonces** la solicitud queda `FAILED` sin nuevos reintentos automáticos.
2. AC-SMTP-05: **Dado** un rechazo permanente del servidor (dirección inexistente o buzón inválido), **cuando** el servidor lo rechaza, **entonces** no se programa ningún reintento y el motivo queda registrado con su `error_code` en el registro de intentos.
**Validaciones:**
1. `attempt_count` no puede superar `max_attempts`
2. `next_attempt_at` debe ser una fecha-hora futura respecto al momento de programación del reintento
3. No se admite programar un reintento sobre una solicitud cuyo `status` es `SENT`
4. `last_error_message` no puede exceder 500 caracteres
**Escenarios de error:**
1. Se intenta programar un reintento sobre un aviso ya entregado
2. La fecha del próximo intento no es posterior al momento actual
3. Se supera el número máximo de intentos y el aviso queda como fallo definitivo
**Campos de datos:**
- `attempt_count` (integer, obligatorio) — Debe cumplir attempt_count <= max_attempts
- `max_attempts` (integer, obligatorio) — Parámetro de configuración; propuesta 3 intentos (gap)
- `next_attempt_at` (datetime, opcional) — Siempre una fecha futura; no se programa sobre solicitudes SENT
- `last_error_code` (string, opcional) — Ej. TRANSIENT_ERROR, AUTH_ERROR, NO_RECIPIENTS
- `last_error_message` (string, opcional) — Máx. 500 caracteres
- `last_attempt_at` (datetime, opcional) — Fecha y hora del último intento realizado

### REQ-136 — Supervisión del estado de los avisos de alta y reenvío manual de los fallidos por ADMINISTRADOR
El ADMINISTRADOR supervisa el estado de los avisos de alta y reenvía manualmente los que han fallado. Reglas: la vista es exclusiva del ADMINISTRADOR; el reenvío manual solo se permite sobre solicitudes en FAILED; el reenvío crea un nuevo intento sobre la misma solicitud, nunca una segunda solicitud, preservando la regla de un único aviso por incidencia (AVI-01); el reenvío vuelve a resolver los destinatarios vigentes en ese momento (REQ-082, REQ-089) y no reutiliza el snapshot antiguo; cada reenvío deja traza del actor y la fecha (REQ-048, SMTP-03); esta vista es de estado de envíos y no duplica la vista de verificación de destinatarios del directorio (REQ-083), a la que enlaza. Flujo: el ADMINISTRADOR abre el panel → filtra → consulta el detalle de una solicitud con sus intentos → pulsa 'Reenviar' sobre una FAILED → confirmación → se encola un nuevo intento → resultado visible en el listado. Datos: filtros status (catálogo), date_from (date), date_to (date), incident_id (number, opcional); fila del listado: incident_id, room_name, category_name, status, attempt_count, last_error_message, sent_at, recipient_count; paginación page (number) y page_size (number, máx 100). Validaciones: date_from <= date_to; rango máximo consultable acotado a la retención de 2 años; incident_id existente; status de catálogo. Errores: reenvío sobre solicitud SENT → HTTP 409 'El aviso ya se entregó correctamente.'; reenvío sobre solicitud PENDING/SENDING → HTTP 409 'El aviso está pendiente de envío.'; acceso sin rol ADMINISTRADOR → HTTP 403 uniforme sin revelar el recurso (REQ-031, REQ-078); rango de fechas inválido → HTTP 400 'La fecha de inicio debe ser anterior a la fecha de fin.'. CA: Given un aviso en FAILED, When el ADMINISTRADOR pulsa reenviar y el SMTP responde correctamente, Then la solicitud pasa a SENT y se registra un intento con el actor del reenvío; Given un usuario con rol TECNICO_DE_MANTENIMIENTO, When accede al panel, Then recibe 403 y no ve dato alguno; Given un filtro por estado FAILED y rango de fechas, Then solo se listan los avisos fallidos de ese rango, paginados. Seguridad: rol ADMINISTRADOR; alcance de datos: todos los avisos del sistema; toda decisión de autorización se resuelve en la API REST, nunca en la SPA (REQ-013, REQ-032); sin doble factor. Integración: Servidor SMTP estándar. Dependencias: SMTP-01, SMTP-02, SMTP-03, REQ-003, REQ-083. Prioridad Could [inferido].
**Reglas de negocio:**
1. El panel de supervisión de avisos es accesible únicamente al rol `ADMINISTRADOR`
2. Solo las solicitudes en estado `FAILED` admiten reenvío manual; las que están en `SENT`, `PENDING` o `SENDING` no son reenviables
3. Un reenvío manual produce un nuevo intento sobre la solicitud existente y nunca una segunda solicitud para la misma incidencia
4. Un reenvío manual usa los destinatarios vigentes en el momento del reenvío, no el snapshot de destinatarios de intentos anteriores
5. Todo reenvío manual queda trazado con el actor que lo ejecuta y su fecha
6. En toda consulta del panel, `date_from` es anterior o igual a `date_to` y el rango consultable no excede la retención de 2 años
7. El tamaño de página del listado de avisos no supera 100 filas
**Criterios de aceptación:**
1. AC-SMTP-03: **Dado** que no existe ningún técnico de mantenimiento activo, **cuando** se procesa el aviso de una nueva incidencia, **entonces** no se envía ningún correo, la solicitud queda `FAILED` con motivo `NO_RECIPIENTS` y el caso es visible para el administrador.
2. AC-SMTP-09: **Dado** un aviso en estado `FAILED`, **cuando** el `ADMINISTRADOR` pulsa "Reenviar" y el servidor SMTP responde correctamente, **entonces** la misma solicitud pasa a `SENT` (sin crear una segunda solicitud), con destinatarios vueltos a resolver en ese momento y un nuevo intento trazado con el actor y la fecha del reenvío.
3. AC-SMTP-10: **Dado** un usuario con rol `EMPLEADO` o `TECNICO_DE_MANTENIMIENTO`, **cuando** accede al panel de supervisión de avisos o intenta un reenvío manual, **entonces** la API REST responde `403` uniforme sin revelar dato alguno del recurso; y **dado** un reenvío sobre una solicitud `SENT` o `PENDING`, **cuando** se solicita, **entonces** responde `409` con el mensaje correspondiente.
**Validaciones:**
1. `date_from` debe ser anterior o igual a `date_to`; en caso contrario se rechaza la consulta con HTTP 400
2. El rango de fechas consultable no puede exceder la retención de 2 años
3. El filtro `status`, si se informa, debe pertenecer al catálogo de estados de envío
4. El filtro `incident_id`, si se informa, debe ser numérico y corresponder a una incidencia existente
5. `page` debe ser un entero positivo y `page_size` un entero positivo con máximo 100
**Escenarios de error:**
1. Acceso al panel de supervisión de avisos sin sesión válida
2. Acceso al panel de supervisión de avisos sin el rol de administrador
3. El aviso o la incidencia indicados en el filtro o en el reenvío no existen
4. La fecha de inicio del filtro es posterior a la fecha de fin
5. El estado indicado en el filtro no pertenece al catálogo admitido
6. El tamaño de página solicitado supera el máximo permitido
7. El rango de fechas consultado excede el periodo de conservación admitido
8. Se solicita reenviar un aviso que ya se entregó correctamente
9. Se solicita reenviar un aviso que aún está pendiente o en curso de envío
10. El servidor de correo no está disponible al ejecutar el reenvío manual
**Campos de datos:**
- `status` (enum, opcional) — Valor de catálogo PENDING / SENDING / SENT / FAILED
- `date_from` (date, opcional) — date_from <= date_to; rango acotado a la retención de 2 años
- `date_to` (date, opcional) — date_to >= date_from
- `incident_id` (integer, opcional) — Debe corresponder a una incidencia existente
- `room_name` (string, opcional) — Etiqueta de catálogo
- `category_name` (string, opcional) — Etiqueta de catálogo
- `attempt_count` (integer, opcional) — Intentos realizados, mostrados en el listado
- `last_error_message` (string, opcional) — Máx. 500 caracteres
- `sent_at` (datetime, opcional) — Vacío si el aviso no se ha entregado
- `recipient_count` (integer, opcional) — Entero no negativo
- `page` (integer, opcional) — Entero >= 1
- `page_size` (integer, opcional) — Máximo 100
- `resend_action_actor` (string, opcional) — Se traza junto con la fecha del reenvío; solo sobre solicitudes FAILED

### REQ-137 — Generación de un aviso por cada cambio de estado aceptado, sin duplicados
El sistema genera un aviso para el empleado reportante por cada cambio de estado aceptado de su incidencia, exactamente uno y sin duplicados. Reglas: se genera un aviso por cada entrada de historial de transición confirmada (dep. REQ-123): N cambios → N avisos; solo transiciones aceptadas y persistidas (post-commit), una transición rechazada por el grafo (REQ-118) no genera aviso; destinatario único: el empleado reportante (reported_by_user_id), nunca el técnico que ejecuta el cambio; aplica a las tres transiciones del grafo abierta→en curso, en curso→resuelta, resuelta→cerrada (cat_estados_incidencia, REQ-117), y si el grafo se ampliase el disparo aplica a toda transición nueva sin cambio de lógica; la generación es posterior y ajena a la transacción del cambio de estado (ver SMTP-01). Flujo: técnico confirma transición → se persiste la incidencia y su entrada de historial → se resuelve el destinatario (REQ-079) → se crea notification_request en estado PENDIENTE → SMTP-01 entrega. Datos: notification_id (uuid, PK), notification_key (varchar, único, derivado de incident_id+history_entry_id), incident_id (number, oblig., FK), history_entry_id (number, oblig., FK), recipient_user_id (number, oblig.), recipient_email (varchar 254, snapshot del momento del encolado), previous_status_code y new_status_code (cat_estados_incidencia, oblig.), changed_at (timestamp, oblig.), changed_by_user_id (number, oblig.), status_code (cat_estados_envio), created_at (timestamp). Validaciones: unicidad de notification_key (idempotencia ante reintentos de la API o doble submit); recipient_email con formato RFC 5322 y no nulo salvo supresión (AVI-04). Errores: el reportante no resoluble o sin correo → no se envía, se registra motivo en TRZ-01 y la transición permanece registrada; el técnico nunca ve un error bloqueante por el canal de correo. CA: Given incidencia abierta con reportante activo, When el técnico la pasa a «en curso», Then existe exactamente 1 notification_request PENDIENTE con el history_entry_id de esa transición. Given 3 cambios de estado sobre la misma incidencia, When concluyen, Then se han generado y entregado exactamente 3 avisos al reportante. Given una transición no permitida por el grafo, When se intenta, Then no se genera ningún aviso. Seguridad: no es una operación invocable por usuario, es un efecto del sistema; la transición origen solo la ejecuta TECNICO_DE_MANTENIMIENTO (REQ-030, REQ-119, REQ-120); ningún rol puede alterar el destinatario del aviso. Eventos: consume IncidentStatusChanged [inferido: debe coincidir con el evento que declare el módulo de ciclo de vida de incidencias]. Dependencias: REQ-117, REQ-118, REQ-123, REQ-079, AVI-04, SMTP-01. Prioridad: Must.
**Reglas de negocio:**
1. La clave de idempotencia del aviso es única por combinación de incidencia y entrada de historial
2. Un cambio de estado persistido permanece registrado aunque su aviso no llegue a generarse ni entregarse
3. Cada transición de estado aceptada y persistida de una incidencia tiene exactamente un aviso asociado
4. El destinatario de un aviso es siempre el empleado reportante de la incidencia, nunca el técnico que ejecuta el cambio
5. Una transición no permitida por el grafo de estados no tiene ningún aviso asociado
**Criterios de aceptación:**
1. AC-G-03: Dado una incidencia con un empleado reportante activo y con correo corporativo válido, cuando se produce cualquier cambio de estado aceptado de esa incidencia, entonces el reportante recibe exactamente un correo por cambio, sin duplicados, con identificador de incidencia, sala, estado anterior, estado nuevo y momento del cambio, verificado sobre un buzón de prueba en 3/3 transiciones de una misma incidencia.
2. AC-AVI-01: Dado una incidencia «abierta» cuyo reportante está activo y es notificable, cuando un técnico la transiciona a «en curso», entonces existe exactamente un `notification_request` en estado `PENDIENTE` vinculado al `history_entry_id` de esa transición, y su destinatario es el `reported_by_user_id`, nunca el técnico ejecutor.
3. AC-AVI-02: Dado la misma incidencia sometida a 3 cambios de estado consecutivos y a un doble submit de la API sobre uno de ellos, cuando concluyen las transiciones, entonces se han generado exactamente 3 avisos (uno por `notification_key` único) y 0 duplicados.
4. AC-AVI-03: Dado una transición no permitida por el grafo de estados, cuando se intenta ejecutar, entonces la transición se rechaza y no se genera ningún `notification_request`.
**Validaciones:**
1. `notification_key` debe ser único (idempotencia ante reintentos de la API o doble submit): no se admite un segundo aviso con la misma combinación `incident_id` + `history_entry_id`
2. `recipient_email` debe cumplir formato de correo RFC 5322 y no puede ser nulo salvo cuando el aviso se registra como suprimido
3. `recipient_email` no puede exceder 254 caracteres
4. `incident_id`, `history_entry_id`, `recipient_user_id`, `changed_at` y `changed_by_user_id` son obligatorios y no pueden ser nulos
5. `previous_status_code` y `new_status_code` son obligatorios y deben pertenecer al catálogo cerrado `cat_estados_incidencia`
**Escenarios de error:**
1. No se puede determinar al empleado reportante de la incidencia, por lo que el aviso no se genera y queda registrado el motivo
2. El empleado reportante no tiene una dirección de correo con formato válido
3. Ya existe un aviso generado para esa misma transición de estado, por lo que no se genera otro
**Campos de datos:**
- `notification_id` (uuid, obligatorio) — PK, generado por el sistema
- `notification_key` (string, obligatorio) — Único; derivado de incident_id + history_entry_id
- `incident_id` (integer, obligatorio) — FK a incidencia
- `history_entry_id` (integer, obligatorio) — FK a historial; 1 aviso por entrada
- `recipient_user_id` (integer, obligatorio) — Siempre reported_by_user_id; no modificable
- `recipient_email` (email, opcional) — Formato RFC 5322; máx. 254; nulo solo si supresión
- `previous_status_code` (enum, obligatorio) — Valores de cat_estados_incidencia
- `new_status_code` (enum, obligatorio) — Valores de cat_estados_incidencia
- `changed_at` (datetime, obligatorio) — Momento en que se produjo el cambio de estado
- `changed_by_user_id` (integer, obligatorio) — Nunca es el destinatario del aviso
- `status_code` (enum, obligatorio) — Valores de cat_estados_envio; inicial PENDIENTE
- `created_at` (datetime, obligatorio) — Momento de creación del aviso

### REQ-138 — Composición del contenido mínimo del aviso para entender el cambio sin entrar en la app
El sistema compone el contenido del aviso con la información mínima para entender el cambio sin entrar en la aplicación. Reglas: todo aviso contiene, como mínimo, identificador de la incidencia, sala afectada (y su oficina), estado anterior, estado nuevo y momento del cambio — criterio de aceptación literal de la épica; el asunto incluye identificador y estado nuevo para ser reconocible en bandeja; contenido íntegramente en español (REQ-050), sin variantes de idioma; el contenido se congela como snapshot en el momento de la composición, de modo que un reenvío posterior (TRZ-03) no recalcula el estado actual. Datos: subject (varchar 255, oblig.), body_text (clob, oblig.), body_html (clob, opcional), room_name + office_name (denormalizados del catálogo, REQ-097), category_name (cat_categorias_incidencia, REQ-095), previous_status_label/new_status_label (etiquetas de cat_estados_incidencia), changed_at formateado en zona horaria peninsular. Validaciones: ningún marcador de plantilla sin resolver puede salir ({{…}} ⇒ composición inválida); subject ≤ 255 caracteres; no se adjunta la foto de la incidencia (solo se referencia su existencia). Errores: si falta un dato obligatorio para componer → no se envía, se traza COMPOSICION_INCOMPLETA en TRZ-01 y no se reintenta hasta corrección. CA: Given una transición a «en curso», When se recibe el correo, Then contiene identificador, sala, estado anterior, estado nuevo y fecha/hora del cambio, en español. Given una plantilla con un dato no resoluble, When se compone, Then el aviso queda en estado descartado con motivo trazado y no se envía texto con marcadores. Seguridad: el cuerpo no expone datos de otras incidencias ni de otros usuarios; solo se incluye nombre y correo corporativo (REQ-049); si se incluye enlace al detalle, este exige sesión válida (REQ-051) y respeta el alcance del empleado (REQ-023). [gap: plantilla/copy exacto de los correos, dirección remitente corporativa y firma]. Integración: Servidor SMTP. Dependencias: AVI-01, REQ-094, REQ-097. Prioridad: Must.
**Reglas de negocio:**
1. Todo aviso contiene identificador de incidencia, sala afectada con su oficina, estado anterior, estado nuevo y momento del cambio
2. El asunto de un aviso incluye el identificador de la incidencia y el estado nuevo, y no supera 255 caracteres
3. El contenido de un aviso está íntegramente en español, sin variantes de idioma
4. Un aviso con marcadores de plantilla sin resolver es inválido y no se entrega
5. El contenido de un aviso es inmutable una vez compuesto: ningún envío posterior lo recalcula
6. El cuerpo de un aviso no contiene datos de incidencias ni de usuarios ajenos al reportante y a su incidencia
**Criterios de aceptación:**
1. AC-G-03: Dado una incidencia con un empleado reportante activo y con correo corporativo válido, cuando se produce cualquier cambio de estado aceptado de esa incidencia, entonces el reportante recibe exactamente un correo por cambio, sin duplicados, con identificador de incidencia, sala, estado anterior, estado nuevo y momento del cambio, verificado sobre un buzón de prueba en 3/3 transiciones de una misma incidencia.
2. AC-G-08: Dado cualquier pantalla de la SPA, mensaje de error de la API o correo emitido por el sistema, cuando se inspeccionan en el recorrido completo de aceptación, entonces el 100 % de los textos visibles al usuario están en español y no existe ningún selector de idioma ni literal sin traducir.
3. AC-AVI-04: Dado una transición a «en curso» ya entregada, cuando se inspecciona el correo recibido en el buzón de prueba, entonces contiene identificador de incidencia, sala y oficina, estado anterior, estado nuevo y fecha/hora del cambio, íntegramente en español, y el asunto incluye identificador y estado nuevo.
4. AC-AVI-05: Dado un aviso cuyo dato obligatorio de composición no es resoluble, cuando se compone el mensaje, entonces no se envía ningún texto con marcadores `{{…}}` sin resolver, el aviso queda descartado con motivo `COMPOSICION_INCOMPLETA` trazado y no se reintenta hasta corrección.
**Validaciones:**
1. El contenido compuesto no puede contener ningún marcador de plantilla sin resolver (patrón `{{…}}`): si queda alguno, la composición es inválida
2. `subject` es obligatorio y no puede exceder 255 caracteres
3. `body_text` es obligatorio y no puede quedar vacío
4. Deben estar presentes todos los datos mínimos para componer (identificador de incidencia, sala, oficina, estado anterior, estado nuevo y momento del cambio); si falta alguno la composición se rechaza como `COMPOSICION_INCOMPLETA`
**Escenarios de error:**
1. Falta información obligatoria de la incidencia para componer el contenido del aviso
2. El contenido compuesto conserva marcadores sin resolver, por lo que el aviso se descarta antes de enviarse
3. El asunto del aviso supera la longitud máxima admitida
**Campos de datos:**
- `subject` (string, obligatorio) — Longitud máx. 255 caracteres
- `body_text` (string, obligatorio) — Español; conserva saltos de línea; sin marcadores sin resolver
- `body_html` (string, opcional) — Español; sin marcadores de plantilla sin resolver
- `room_name` (string, obligatorio) — Denormalizado del catálogo de salas
- `office_name` (string, obligatorio) — Denormalizado del catálogo de salas
- `category_name` (string, opcional) — Valor de cat_categorias_incidencia
- `previous_status_label` (string, obligatorio) — Etiqueta en español de cat_estados_incidencia
- `new_status_label` (string, obligatorio) — Etiqueta en español de cat_estados_incidencia
- `changed_at_formatted` (string, obligatorio) — Zona horaria peninsular; formato español

### REQ-139 — Aviso de cierre con el comentario de resolución del técnico
El aviso de cierre incluye el comentario de resolución aportado por el técnico. Reglas: en la transición resuelta→cerrada el correo incluye el resolution_comment íntegro, sin truncar, junto con el autor y la fecha del cierre; en el resto de transiciones ese bloque no aparece; el comentario es obligatorio en el cierre (REQ-111), por lo que en condiciones normales siempre existe. Datos: resolution_comment (clob, oblig. en cierre), closed_by_user_id (number), closed_at (timestamp). Validaciones: escapado de HTML y caracteres de control del comentario antes de insertarlo en body_html (prevención de inyección en el cuerpo del correo); conservación de saltos de línea en body_text. Errores: si excepcionalmente llegase un cierre sin comentario, el aviso se envía igualmente sin el bloque de resolución y se traza COMENTARIO_AUSENTE en TRZ-01 (el aviso nunca se pierde por este motivo). CA: Given una incidencia cerrada con comentario «Proyector sustituido», When el reportante recibe el correo de cierre, Then el mensaje contiene ese texto completo, el nombre del técnico que cerró y la fecha de cierre. Given una transición a «resuelta», When se recibe el correo, Then no incluye bloque de comentario de resolución. Seguridad: el comentario viaja únicamente al reportante de esa incidencia; ningún otro destinatario. Integración: Servidor SMTP. Dependencias: AVI-01, AVI-02, REQ-111, REQ-115. Prioridad: Must.
**Reglas de negocio:**
1. Solo los avisos de la transición «resuelta→cerrada» incluyen el bloque de comentario de resolución
2. El comentario de resolución viaja íntegro y sin truncar, acompañado del autor y la fecha del cierre
3. La ausencia excepcional de comentario de resolución no impide la entrega del aviso de cierre
**Criterios de aceptación:**
1. AC-AVI-06: Dado una incidencia cerrada con comentario de resolución «Proyector sustituido», cuando el reportante recibe el correo de cierre, entonces el mensaje contiene ese texto completo y sin truncar, el nombre del técnico que cerró y la fecha de cierre, con el comentario escapado de HTML.
2. AC-AVI-07: Dado una transición a «resuelta» (no a «cerrada»), cuando el reportante recibe el correo, entonces el mensaje no incluye el bloque de comentario de resolución.
**Validaciones:**
1. El `resolution_comment` debe escaparse en HTML y depurarse de caracteres de control antes de insertarse en `body_html`
2. El `resolution_comment` no puede truncarse: debe insertarse íntegro conservando los saltos de línea en `body_text`
3. En una transición `resuelta→cerrada` se comprueba la presencia de `resolution_comment`, `closed_by_user_id` y `closed_at`; si el comentario no viene, el bloque se omite y se traza `COMENTARIO_AUSENTE`
**Escenarios de error:**
1. El cierre no aporta comentario de resolución: el aviso se envía sin ese bloque y se deja constancia del motivo
**Campos de datos:**
- `resolution_comment` (string, obligatorio) — Solo en transición resuelta→cerrada; íntegro, sin truncar; HTML escapado
- `closed_by_user_id` (integer, obligatorio) — Solo en aviso de cierre
- `closed_at` (datetime, obligatorio) — Solo en aviso de cierre

### REQ-140 — Supresión del aviso con motivo cuando el reportante no es notificable
El sistema suprime el aviso, dejando constancia del motivo, cuando el reportante no es un destinatario notificable. Reglas: antes de encolar se resuelve el destinatario contra el directorio (dep. REQ-079) aplicando la exclusión de cuentas desactivadas (dep. REQ-089); si el reportante no es notificable no se envía correo: el notification_request se registra en estado SUPRIMIDO con su motivo y el cambio de estado de la incidencia permanece intacto; nunca se sustituye el destinatario por otro buzón (p. ej. el de facilities) ni se difunde el aviso a terceros; la incidencia mantiene la atribución al usuario desactivado (REQ-087), por lo que la supresión no altera datos de la incidencia; si el usuario se reactiva (REQ-042), los avisos ya suprimidos no se envían retroactivamente de forma automática (ver TRZ-03). Datos: suppression_reason_code (cat_motivos_no_envio: USUARIO_DESACTIVADO, SIN_CORREO, DESTINATARIO_NO_RESOLUBLE), suppressed_at (timestamp). Validaciones: la decisión se toma siempre con el dato vigente del directorio en el instante del cambio de estado (REQ-080), no con una copia cacheada. Errores: sin error visible para el técnico que ejecutó la transición; la supresión es consultable por ADMINISTRADOR en TRZ-02. CA: Given un reportante desactivado, When un técnico cambia el estado de su incidencia, Then la transición se registra, no se envía correo y existe un registro de supresión con motivo USUARIO_DESACTIVADO. Given un reportante activo sin correo corporativo válido, When se dispara el aviso, Then se suprime con motivo SIN_CORREO. Seguridad: la consulta al directorio no expone datos del usuario desactivado al técnico; el motivo de supresión solo lo ve ADMINISTRADOR (REQ-078). [gap: destino de escalado deseado cuando el reportante ya no es notificable]. Dependencias: REQ-079, REQ-080, REQ-089, REQ-087, TRZ-01. Prioridad: Must.
**Reglas de negocio:**
1. Un reportante no notificable (cuenta desactivada, sin correo o no resoluble) produce un aviso en estado SUPRIMIDO con motivo del catálogo cerrado y sin correo enviado
2. El destinatario de un aviso no es sustituible por otro buzón ni ampliable a terceros
3. La notificabilidad del reportante se determina con el dato vigente del directorio en el instante del cambio de estado, no con una copia cacheada
4. La supresión de un aviso no altera el cambio de estado registrado ni la atribución de la incidencia al usuario desactivado
5. La reactivación posterior de un usuario no provoca el envío retroactivo automático de sus avisos suprimidos
**Criterios de aceptación:**
1. AC-AVI-08: Dado un reportante desactivado o sin correo corporativo válido, cuando un técnico cambia el estado de su incidencia, entonces la transición queda registrada y confirmada, no se envía ningún correo, el destinatario no se sustituye por ningún otro buzón, y existe un registro de supresión con motivo `USUARIO_DESACTIVADO` o `SIN_CORREO` consultable solo por `ADMINISTRADOR`.
2. AC-TRZ-02: Dado un aviso suprimido por destinatario no notificable, cuando se consulta su traza, entonces consta el `suppression_reason_code` y no consta ningún intento de envío.
**Validaciones:**
1. El correo del reportante debe existir y tener formato válido para considerarlo notificable; en caso contrario se registra motivo `SIN_CORREO`
2. `suppression_reason_code` debe pertenecer al catálogo cerrado `cat_motivos_no_envio` (`USUARIO_DESACTIVADO`, `SIN_CORREO`, `DESTINATARIO_NO_RESOLUBLE`)
3. `suppressed_at` es obligatorio cuando el aviso se registra en estado `SUPRIMIDO`
4. La notificabilidad del destinatario se comprueba contra el dato vigente del directorio en el instante del cambio de estado, no contra una copia cacheada
**Escenarios de error:**
1. El empleado reportante tiene la cuenta desactivada y no puede recibir avisos
2. El empleado reportante no dispone de correo corporativo vigente
3. El destinatario no se puede resolver en el directorio de usuarios en el instante del cambio de estado
**Campos de datos:**
- `suppression_reason_code` (enum, obligatorio) — USUARIO_DESACTIVADO, SIN_CORREO, DESTINATARIO_NO_RESOLUBLE
- `suppressed_at` (datetime, obligatorio) — Momento en que se suprimió el aviso

### REQ-142 — Ciclo de vida de la entrega con reintentos automáticos ante fallos temporales
El sistema gestiona el ciclo de vida de la entrega del aviso con reintentos automáticos ante fallos temporales. Reglas: catálogo cerrado cat_estados_envio = PENDIENTE → ENVIANDO → ENVIADO | FALLIDO | DESCARTADO | SUPRIMIDO; un fallo temporal programa un reintento automático con espera creciente hasta agotar el número máximo de intentos, agotados el aviso pasa a DESCARTADO y queda trazado; los errores permanentes no se reintentan; el reintento reutiliza el contenido ya compuesto (snapshot de AVI-02), no lo recompone; los avisos generados fuera del horario laboral se entregan igualmente sin retención, ya que el sistema solo garantiza disponibilidad en horario laboral pero no restringe la ventana de envío [inferido]. Datos: status_code (cat_estados_envio, oblig.), attempt_count (number ≥0, oblig.), next_attempt_at (timestamp, nulo si no procede reintento), last_error_code (varchar), last_error_message (varchar 500). Validaciones: attempt_count nunca supera el máximo configurado; transición de estados de envío validada (no se puede pasar de ENVIADO a FALLIDO). Errores: ninguno visible al empleado ni al técnico; el resultado final es consultable por ADMINISTRADOR (TRZ-02). CA: Given un aviso que falla con error temporal, When transcurre la espera programada, Then se reintenta automáticamente y el intento queda registrado. Given un aviso que agota los intentos, When se evalúa, Then queda en DESCARTADO, no se vuelve a intentar solo y es visible en la consulta administrativa. Seguridad: solo ADMINISTRADOR consulta o fuerza el ciclo de entrega (REQ-078); el consumidor opera con identidad de sistema. [gap: número máximo de reintentos, intervalo/backoff entre intentos y ventana máxima de reintento]. Integración: Servidor SMTP. Dependencias: SMTP-01, TRZ-01. Prioridad: Must.
**Reglas de negocio:**
1. El estado de envío de un aviso pertenece siempre al catálogo cerrado PENDIENTE, ENVIANDO, ENVIADO, FALLIDO, DESCARTADO, SUPRIMIDO
2. ENVIADO es un estado final: un aviso enviado no pasa a FALLIDO ni a DESCARTADO
3. El número de intentos de un aviso nunca supera el máximo configurado
4. Un aviso que agota sus intentos queda en estado DESCARTADO y no se reintenta de forma automática
5. Un reintento reutiliza el contenido ya compuesto del aviso y no lo recompone
6. La hora de generación de un aviso no restringe su ventana de envío: fuera del horario laboral se entrega igualmente
**Criterios de aceptación:**
1. AC-SMTP-02: Dado una cola con N avisos pendientes acumulados durante la caída y M avisos ya `ENVIADO`, cuando el servidor SMTP se restablece y el consumidor reanuda, entonces se entregan exactamente los N pendientes en orden de creación por incidencia y se generan 0 reenvíos de los M ya entregados.
2. AC-SMTP-03: Dado un aviso cuyo envío falla con respuesta SMTP 4xx (temporal), cuando transcurre la espera programada, entonces se reintenta automáticamente, cada intento queda registrado con su `attempt_number`, y al agotarse el número máximo configurado el aviso queda en `DESCARTADO` sin nuevos intentos automáticos.
3. AC-SMTP-04: Dado un aviso cuyo envío falla con respuesta SMTP 5xx permanente (p. ej. 550 buzón inexistente), cuando el consumidor procesa el resultado, entonces el aviso pasa a `DESCARTADO` con `attempt_count = 1`, sin ningún reintento, y con `smtp_response_code` y motivo trazados.
**Validaciones:**
1. `status_code` es obligatorio y debe pertenecer al catálogo cerrado `cat_estados_envio` (`PENDIENTE`, `ENVIANDO`, `ENVIADO`, `FALLIDO`, `DESCARTADO`, `SUPRIMIDO`)
2. `attempt_count` es obligatorio, entero ≥ 0 y no puede superar el número máximo de intentos configurado
3. `next_attempt_at` debe ser nulo cuando no procede reintento y posterior a `attempted_at` del último intento cuando sí procede
4. `last_error_message` no puede exceder 500 caracteres
5. La transición de estado de envío solicitada debe ser una de las permitidas por el grafo de `cat_estados_envio` (p. ej. se rechaza `ENVIADO` → `FALLIDO`)
**Escenarios de error:**
1. El aviso agota el número máximo de intentos de entrega y queda descartado
2. Se intenta un cambio de estado de envío no permitido para el aviso
**Campos de datos:**
- `status_code` (enum, obligatorio) — PENDIENTE, ENVIANDO, ENVIADO, FALLIDO, DESCARTADO, SUPRIMIDO
- `attempt_count` (integer, obligatorio) — ≥ 0; nunca supera el máximo configurado
- `next_attempt_at` (datetime, opcional) — Nulo si no procede reintento
- `last_error_code` (string, opcional) — Código del último error de entrega
- `last_error_message` (string, opcional) — Longitud máx. 500 caracteres

### REQ-145 — Consulta administrativa del listado de avisos emitidos con filtros
El ADMINISTRADOR consulta el listado de avisos emitidos con filtros por estado, fecha, incidencia y destinatario. Reglas: el listado muestra por fila identificador de incidencia, destinatario (nombre y correo), asunto, estado de envío, número de intentos, fecha del último intento y último error o motivo de supresión; ordenación por defecto descendente por fecha de creación del aviso, con paginación estable (coherente con REQ-106); los filtros son combinables y el resultado vacío es informativo, no un error. Flujo: administrador abre la vista → aplica filtros → obtiene página de resultados → abre el detalle de un aviso con su traza de intentos (TRZ-01). Datos/filtros: status_code (cat_estados_envio), date_from/date_to (date, date_from ≤ date_to), incident_id (number), recipient_user_id (number), page (number ≥1), page_size (number, máx 100). Validaciones: rango de fechas coherente; page_size acotado; filtros desconocidos ignorados sin romper la consulta. Errores: rango inválido → 400 «La fecha de inicio no puede ser posterior a la fecha de fin»; rol no autorizado → 403 uniforme (REQ-078); incidencia inexistente en el filtro → resultado vacío, nunca 500. CA: Given 5 avisos descartados en el último mes, When el ADMINISTRADOR filtra por estado DESCARTADO y ese rango, Then obtiene esas 5 filas con su último error visible. Given un EMPLEADO, When accede a la vista de avisos, Then recibe 403 y la SPA no muestra la opción de menú (REQ-010, REQ-013). Seguridad: alcance total de datos solo para ADMINISTRADOR; EMPLEADO y TECNICO_DE_MANTENIMIENTO no tienen acceso; la decisión de autorización es vinculante en la API (REQ-013, REQ-032). Dependencias: TRZ-01, REQ-078. Prioridad: Should [inferido].
**Reglas de negocio:**
1. El listado de avisos emitidos solo es accesible para ADMINISTRADOR
2. En un filtro por rango de fechas, la fecha de inicio no es posterior a la fecha de fin
3. El tamaño de página del listado de avisos no supera 100
4. Un conjunto de resultados vacío es un resultado válido de la consulta, no un error
**Criterios de aceptación:**
1. AC-TRZ-05: Dado 5 avisos en estado `DESCARTADO` en el último mes, cuando el `ADMINISTRADOR` filtra por estado `DESCARTADO` y ese rango de fechas, entonces obtiene esas 5 filas con incidencia, destinatario, asunto, número de intentos, fecha del último intento y último error visible, con paginación estable y orden descendente por fecha de creación.
2. AC-TRZ-06: Dado un filtro con `date_from` posterior a `date_to` o una incidencia inexistente, cuando el `ADMINISTRADOR` ejecuta la consulta, entonces obtiene `400` con mensaje en español en el primer caso y un resultado vacío informativo (nunca `500`) en el segundo.
3. AC-TRZ-07: Dado un usuario con rol `EMPLEADO`, cuando accede a la vista o al endpoint de avisos emitidos, entonces recibe `403` uniforme y la SPA no muestra la opción de menú correspondiente.
**Validaciones:**
1. `date_from` debe ser menor o igual que `date_to`; en caso contrario se rechaza con 400
2. `page` debe ser un entero mayor o igual que 1
3. `page_size` debe ser un entero acotado, con máximo 100
4. `status_code` del filtro debe pertenecer al catálogo `cat_estados_envio`
5. `incident_id` y `recipient_user_id` del filtro deben ser valores numéricos
6. Los filtros desconocidos se ignoran sin provocar error en la consulta
**Escenarios de error:**
1. La fecha de inicio del filtro es posterior a la fecha de fin
2. El tamaño de página solicitado supera el máximo permitido o el número de página no es válido
3. La sesión no es válida o ha caducado al consultar el listado de avisos
4. El usuario no tiene perfil de administrador para acceder al listado de avisos
5. El aviso cuyo detalle se solicita no existe
**Campos de datos:**
- `status_code` (enum, opcional) — Valores de cat_estados_envio
- `date_from` (date, opcional) — date_from ≤ date_to
- `date_to` (date, opcional) — date_from ≤ date_to
- `incident_id` (integer, opcional) — Incidencia inexistente ⇒ resultado vacío
- `recipient_user_id` (integer, opcional) — Filtro por destinatario del aviso
- `page` (integer, opcional) — Valor ≥ 1
- `page_size` (integer, opcional) — Máximo 100

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-057/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-057/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: validar el modelo de datos que construye esta tarea contra el motor real.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.
El esquema de `TSK-044` ya está APLICADO en este servicio (changelogs Liquibase de su rama mergeada): asume las tablas creadas, no las vuelvas a crear ni las modifiques desde esta tarea.

Esta tarea materializa el MODELO DE DATOS: el motor se levanta VACÍO a propósito, para que valides tu propio changelog contra él. Cómo, exactamente:

```sh
./.mind/TSK-057/liquibase.sh <changelog-maestro>            # aplica (update)
./.mind/TSK-057/liquibase.sh <changelog-maestro> status
./.mind/TSK-057/liquibase.sh <changelog-maestro> rollback-count 99  # reversibilidad
```

Ese script lo genera la plataforma y corre Liquibase como contenedor contra ESTE motor. **NO descargues ni instales Liquibase por tu cuenta**: sus JAR acabarían commiteados en el repo del cliente, y apuntar al `local/*.properties` del arquetipo NO sirve (esa configuración mira a otra BBDD, no a la de tu sesión).

Comprueba las tres cosas antes de entregar: que el `update` termina limpio, que el rollback deshace, y que un segundo `update` es idempotente. Un changelog que nunca se ejecutó no está verificado — y la plataforma lo VUELVE a aplicar por su cuenta tras tu entrega, así que un rojo saldrá igual en el PR.

### Si el entorno no está disponible
Comprueba `.mind/TSK-057/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.