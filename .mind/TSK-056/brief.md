# TSK-056 · incidencia_historico: traza append-only encadenada de alta, estado, asignación y reclasificación

- Componente dueño: `ARC-016`
- Arquetipo del repo: `database-relational` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/facilities/changelogs/0.0.1/ddl/13-incidencia-historico.xml`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
Crea `incidencia_historico` (history_id PK, incident_id FK NOT NULL a `incidencia`, entry_type NOT NULL check en ('CREACION','CAMBIO_ESTADO','ASIGNACION','RECLASIFICACION','COMENTARIO_RESOLUCION'), from_status FK, to_status FK NOT NULL, previous_room_id, new_room_id, previous_category_id, new_category_id, assigned_technician_id, previous_technician_id, assigned_technician_name, actor_user_id FK NOT NULL a `usuario`, actor_display_name varchar2(150) NOT NULL (snapshot que sobrevive a la desactivación, REQ-125/AC-HIST-REG-06), changed_at NOT NULL default SYSTIMESTAMP, comment varchar2(500), resolution_comment_ref, previous_entry_hash, entry_hash NOT NULL). Checks: `from_status` nulo si y solo si entry_type='CREACION'; en 'ASIGNACION' `from_status = to_status` (no altera el estado, REQ-126); `resolution_comment_ref` informado solo en la transición RESUELTA→CERRADA. Índices: (incident_id, changed_at, history_id) y (changed_at DESC, history_id DESC) para el registro de actividad reciente paginado; índice único funcional sobre incident_id para entry_type='CREACION' (exactamente un asiento de alta) y otro para la transición RESUELTA→CERRADA (cardinalidad 1:1 con el cierre, AC-HIS-06). Inmutabilidad: trigger `BEFORE UPDATE OR DELETE` que lanza ORA-20002 «El historial de cambios de estado es inmutable». Oráculos contra el Oracle 23ai real: test que recorre ABIERTA→EN_CURSO→RESUELTA→CERRADA y comprueba cadena sin huecos (`from_status` de cada entrada = `to_status` de la anterior, AC-HIST-REG-04), que el `status` de `incidencia` coincide con el `to_status` de la última entrada (invariante sobre el 100 % de las filas, AC-HIST-01) y que `entry_hash` encadena con `previous_entry_hash`; test que fuerza el fallo de inserción del asiento dentro de la transacción del alta y verifica rollback completo (ni incidencia ni asiento, AC-HIST-REG-02); test que ejecuta UPDATE y DELETE y recibe ORA-20002 con la fila idéntica campo a campo (AC-HIST-02). Nombres físicos iguales a los que consume la API (`changed_at`, no `changedAt`). `update`/`rollback-count` verdes.

## Oráculos de verificación (dod-oracles) — OBLIGATORIO

El DoD se evalúa por **comportamiento**, no porque exista un fichero o un string «implementado». Lo siguiente es **Blocker** si lo usas como entrega de producto (los dobles solo valen en tests):

- **Email / notificación:** cliente real o puerto inyectable (`aiosmtplib`, SES, SendGrid, …) + test que verifica que se invocó el envío. **`log.info` / `print` / «Simula el envío» ≠ email.**
- **Auth / rol (p. ej. ADMINISTRADOR):** dependency o middleware que devuelve 401/403 sin credencial/rol; tests con y sin permiso. **Un CRUD abierto no cumple «solo admin».**
- **Evento / AsyncAPI:** productor que publica al canal declarado; test que captura el publish. **Loguear el payload ≠ publicar el evento.**
- **Persistencia:** driver del stack del arquetipo (Motor/SQLAlchemy/…) contra el motor de prueba o Testcontainers. **`dict` / `db_*` in-memory en el módulo de producto ≠ base de datos.**
- **UI que consume API:** `HttpClient`/`fetch` hacia los paths del contrato. **`mocks.js` + `setTimeout` como único camino de producto ≠ integración.** (Mocks solo en unitarios.)

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

Tus zonas (`sources/facilities/changelogs/0.0.1/ddl/13-incidencia-historico.xml`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

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

### REQ-015 — Retención de 2 años e inmutabilidad de los registros de histórico
Los datos de usuarios, roles, incidencias y sus históricos se conservan 2 años ("Los datos de incidencias y usuarios se conservan durante 2 años"); los registros de histórico son inmutables y no se borran físicamente dentro de ese plazo. [gap: el RFP no indica qué ocurre al vencer el plazo de retención]. Aplica a: ROL, PERM, incidencias, notificaciones.
**Reglas de negocio:**
1. Los datos de usuarios, roles, incidencias y sus históricos se conservan durante 2 años
2. Un registro de histórico no se modifica ni se elimina físicamente dentro del plazo de retención de 2 años
**Criterios de aceptación:**
1. AC-ROL-05: Dado un usuario que fue dado de alta como `EMPLEADO` y posteriormente cambiado a `TECNICO_MANTENIMIENTO`, cuando el ADMINISTRADOR consulta su histórico de rol, entonces obtiene exactamente dos entradas en orden cronológico descendente (cambio y alta) con `previous_role_code`, `new_role_code`, `changed_by_user_id` y `changed_at`; y un intento de modificar o borrar cualquier entrada vía API devuelve 403/405 y deja el histórico intacto.
**Escenarios de error:**
1. Se intenta modificar o eliminar una entrada de histórico, que es inmutable durante el periodo de conservación

### REQ-026 — Mismo alcance por rol en el detalle y el historial de cambios de estado
El sistema aplica el mismo alcance por rol a la consulta del detalle y del historial de cambios de estado de una incidencia. Reglas: (1) acceden el reportante de la incidencia (reporter_user_id = session_user_id) o cualquier TECNICO_MANTENIMIENTO; (2) el historial se devuelve completo y en orden cronológico para quien está autorizado, pudiendo el reportante ver en todo momento el estado actual y el historial de cambios; (3) el alcance del historial se hereda del de su incidencia: no existe acceso al historial sin acceso al detalle. Flujo: usuario abre una incidencia → se comprueba alcance (PERM-03) → detalle + lista de transiciones. Datos del historial: history_id (uuid), incident_id (uuid), status_from (varchar, nulo en el alta), status_to (varchar, obligatorio), changed_by_user_id (uuid, obligatorio), changed_at (timestamp, obligatorio), resolution_comment (text, presente solo en la transición a CERRADA). Validaciones: el historial es de solo lectura para ambos roles; ninguna operación de la API permite editarlo ni borrarlo. Errores: incidencia ajena o inexistente → respuesta uniforme de PERM-03. Aceptación: EMPLEADO reportante ve estado actual y todas las transiciones con autor y fecha; EMPLEADO no reportante no recibe ninguna transición. Seguridad: EMPLEADO alcance OWN, TECNICO_MANTENIMIENTO alcance ALL; el historial identifica al autor del cambio (dato personal). Dependencias: PERM-03; historial escrito por MOD-002.
**Reglas de negocio:**
1. El alcance del historial de cambios de estado de una incidencia es idéntico al alcance de su detalle
2. El historial de cambios de estado es inmutable: ninguna operación de la API lo modifica ni lo elimina
3. Cada entrada del historial identifica el estado destino, el autor del cambio y su fecha
4. Un resolution_comment solo acompaña a la transición a CERRADA
**Criterios de aceptación:**
1. AC-G-05: Dado una incidencia que ha recorrido todas sus transiciones de estado, cuando el empleado reportante o cualquier técnico consultan su detalle, entonces se muestra el estado actual y el historial completo en orden cronológico con `status_from`, `status_to`, autor del cambio y fecha para cada transición, y ninguna operación de la API permite editar ni borrar entradas del historial.
2. AC-ALC-04: Dado una incidencia con varias transiciones registradas, cuando la consultan (a) su empleado reportante, (b) un técnico de mantenimiento y (c) un empleado no reportante, entonces (a) y (b) reciben el detalle y el historial completo en orden cronológico con autor y fecha de cada transición, y (c) recibe la respuesta uniforme de recurso inexistente sin ninguna transición; en ningún caso existe acceso al historial sin acceso al detalle.
**Validaciones:**
1. En cada entrada de historial, `status_to`, `changed_by_user_id` y `changed_at` son obligatorios
2. `status_from` solo puede ser nulo en la entrada correspondiente al alta de la incidencia
3. `resolution_comment` solo puede venir informado en la transición a `CERRADA`
4. El historial no admite peticiones de escritura: cualquier intento de edición o borrado se rechaza en el punto de entrada
**Escenarios de error:**
1. Consulta del detalle o del historial de una incidencia fuera del alcance del usuario o inexistente
**Campos de datos:**
- `history_id` (uuid, obligatorio) — Identificador de cada entrada del historial de cambios de estado
- `incident_id` (uuid, obligatorio) — El alcance del historial se hereda del de la incidencia
- `status_from` (enum, opcional) — Nulo en el alta; valores de `cat_estados_incidencia`
- `status_to` (enum, obligatorio) — Valores de `cat_estados_incidencia`
- `changed_by_user_id` (uuid, obligatorio) — Dato personal; solo lectura
- `changed_at` (datetime, obligatorio) — Historial devuelto en orden cronológico
- `resolution_comment` (string, opcional) — Presente solo en la transición a CERRADA

### REQ-101 — Reclasificación de sala o categoría por TECNICO_DE_MANTENIMIENTO con validación de catálogo y traza
La reclasificación aplica las mismas validaciones referenciales que el alta (CLAS-01) y solo es posible mientras la incidencia no esté en estado cerrada. Cada reclasificación genera un registro histórico inmutable con los valores anterior y nuevo (REQ-015). El EMPLEADO reportante no puede reclasificar. La reclasificación no altera el estado del ciclo de vida ni el autor original. Flujo: TECNICO_DE_MANTENIMIENTO abre el detalle de una incidencia, pulsa reclasificar, elige nueva sala y/o nueva categoría del catálogo, confirma, y se persiste el cambio y su traza. Datos: incident_classification_history con history_id (PK), incident_id (FK), previous_room_id, new_room_id, previous_category_id, new_category_id, changed_by (FK usuario), changed_at (timestamp), change_reason (varchar2(255), opcional). Validaciones: al menos uno de los dos valores debe cambiar; valores nuevos existentes y activos; incidencia no cerrada. Errores: 403 'No tiene permisos para realizar esta operación' a EMPLEADO (REQ-022, REQ-030); 409 'No se puede reclasificar una incidencia cerrada'; 422 con los mismos mensajes de catálogo de CLAS-01; 404 uniforme si la incidencia no es visible para el solicitante (REQ-031). Aceptación: un TECNICO_DE_MANTENIMIENTO que cambia la categoría de una incidencia abierta deja la incidencia con la nueva categoría y el historial registra valor anterior, nuevo, autor y fecha; un EMPLEADO que intenta reclasificar una incidencia propia recibe 403 y nada cambia. Seguridad: exclusivo de TECNICO_DE_MANTENIMIENTO sobre el conjunto completo de incidencias (REQ-029, REQ-030); autoría desde la sesión (REQ-064); sin doble factor. Dependencias: CLAS-01, CLAS-03, REQ-030, REQ-015. [inferido: el RFP no menciona explícitamente la corrección de una clasificación errónea; la épica sí contempla 'petición de alta o de actualización']. Prioridad: Should. auth_type: JWT. data_scope: all.
**Reglas de negocio:**
1. Solo el rol `TECNICO_DE_MANTENIMIENTO` puede reclasificar una incidencia; el empleado reportante no puede
2. Una incidencia en estado cerrada no admite reclasificación
3. Una reclasificación cambia al menos uno de los dos valores: sala o categoría
4. Cada reclasificación deja un registro histórico inmutable con el valor anterior, el nuevo, el autor y la fecha
5. Una reclasificación no modifica el estado del ciclo de vida ni el autor original de la incidencia
6. Los valores nuevos de una reclasificación pertenecen al catálogo y están activos
**Criterios de aceptación:**
1. AC-CLAS-02: **Dado** un `TECNICO_DE_MANTENIMIENTO` y una incidencia no cerrada, **cuando** reclasifica su sala y/o su categoría a valores activos del catálogo, **entonces** la incidencia queda con los nuevos valores y el historial registra valor anterior, valor nuevo, autor y fecha-hora; **dado** un `EMPLEADO`, **cuando** intenta reclasificar una incidencia propia, **entonces** recibe 403 y nada cambia; **dada** una incidencia `cerrada`, **cuando** se intenta reclasificar, **entonces** se rechaza con 409.
**Validaciones:**
1. La petición de reclasificación debe modificar al menos uno de los dos valores (sala o categoría); si ambos coinciden con los actuales, se rechaza
2. El nuevo `room_id` y/o el nuevo `category_id` deben existir en su catálogo y estar activos
3. La incidencia referenciada no puede estar en estado `cerrada` para admitir la petición de reclasificación
4. `change_reason` es opcional y, si se informa, no supera 255 caracteres
**Escenarios de error:**
1. El usuario no tiene permisos para reclasificar una incidencia
2. La incidencia indicada no existe o no está disponible para el solicitante
3. No se puede reclasificar una incidencia cerrada
4. Debe modificarse al menos la sala o la categoría
5. La categoría seleccionada no es válida
6. La sala seleccionada no existe o ya no está disponible
7. El motivo del cambio supera la longitud máxima permitida
**Campos de datos:**
- `history_id` (integer, obligatorio) — Clave primaria del historial de clasificación
- `incident_id` (integer, obligatorio) — Debe existir y no estar en estado cerrada
- `previous_room_id` (integer, opcional) — Se informa cuando cambia la sala
- `new_room_id` (integer, opcional) — Debe existir en catálogo y estar activa
- `previous_category_id` (integer, opcional) — Se informa cuando cambia la categoría
- `new_category_id` (integer, opcional) — Debe existir en el catálogo cerrado y estar activa; al menos uno de los dos valores debe cambiar
- `changed_by` (integer, obligatorio) — Tomado de la sesión (REQ-064)
- `changed_at` (datetime, obligatorio) — Registro inmutable (REQ-015)
- `change_reason` (string, opcional) — Máx. 255 caracteres

### REQ-116 — Entrada inmutable de historial para la transición «resuelta → cerrada»
El cierre registra en el historial de cambios de estado de la incidencia una entrada inmutable con la transición «resuelta → cerrada», su autor, su fecha y hora y la referencia al comentario de resolución. Reglas: la entrada se escribe en la MISMA transacción que el cambio de estado (CIE-01): no puede existir una incidencia cerrada sin su entrada de historial ni al revés; la entrada es inmutable: no se actualiza ni se borra físicamente (REQ-015, REQ-047), una corrección solo puede expresarse como una entrada nueva; un intento de cierre rechazado (400/403/409) NO genera entrada; el historial se conserva durante los 2 años de retención y es consultable en todo momento (criterio de aceptación 5 del RFP) [gap: el RFP declara retención de 2 años pero no indica qué ocurre con los datos al vencer el plazo]. Datos de la entrada: history_id (id), incident_id (id, obligatorio), from_status (resuelta), to_status (cerrada), changed_by_user_id (id del usuario de la sesión, obligatorio), changed_at (timestamp servidor, obligatorio), resolution_comment_ref (referencia al comentario del cierre, obligatoria solo en esta transición). Validaciones: from_status y to_status pertenecen a cat_estados_incidencia; changed_by_user_id corresponde a un usuario existente aunque esté desactivado (REQ-087); no se admite insertar una transición cuyo from_status no coincida con el estado real previo de la incidencia. Errores: 500 con reversión completa si falla la escritura del historial (el cierre no se consolida, mensaje «No se ha podido completar el cierre; inténtelo de nuevo»); 403 uniforme al consultar el historial fuera del alcance de rol. Criterios de aceptación: Dado un cierre correcto, cuando se consulta el historial de la incidencia, entonces la última entrada es «resuelta → cerrada» con el nombre del técnico y la fecha y hora del cierre. Dado un cierre rechazado por falta de comentario, cuando se consulta el historial, entonces no existe entrada nueva. Dado una entrada de historial existente, cuando se intenta modificarla o eliminarla, entonces la operación se rechaza. Seguridad: escribe TECNICO_DE_MANTENIMIENTO vía CIE-01; consulta con el alcance de REQ-026 (EMPLEADO solo su propia incidencia, TECNICO_DE_MANTENIMIENTO todas). Dependencias: CIE-01, REQ-026, REQ-048. Prioridad: Must.
**Reglas de negocio:**
1. Toda incidencia en estado «cerrada» tiene exactamente una entrada de historial con transición «resuelta → cerrada», y toda entrada de esa transición corresponde a una incidencia cerrada
2. Una entrada de historial es inmutable: no se actualiza ni se elimina; una corrección solo existe como una entrada nueva
3. Un intento de cierre rechazado no deja ninguna entrada en el historial de la incidencia
4. El estado de origen de una entrada de historial coincide con el estado real previo de la incidencia
5. La entrada de historial de la transición de cierre referencia obligatoriamente el comentario de resolución; las demás transiciones no lo referencian
6. Toda entrada de historial tiene informados autor y fecha y hora del cambio, tomados del usuario de la sesión y del servidor
7. El autor de una entrada de historial corresponde a un usuario existente, aunque esté desactivado
8. El historial de una incidencia es consultable durante los 2 años de retención
9. Un EMPLEADO consulta el historial únicamente de las incidencias que él reportó; el TECNICO_DE_MANTENIMIENTO, el de todas
**Criterios de aceptación:**
1. AC-CIE-05: Dado un fallo en la escritura de la entrada de historial durante el cierre, cuando la transacción se resuelve, entonces nada se consolida: la incidencia sigue en «resuelta», no existe comentario de cierre ni entrada de historial, y el usuario recibe 500 con «No se ha podido completar el cierre; inténtelo de nuevo».
2. AC-COM-02: Dado un TECNICO_DE_MANTENIMIENTO con el diálogo de cierre abierto, cuando pulsa confirmar dos veces seguidas antes de recibir respuesta, entonces se emite una sola petición de cierre y se crea una sola entrada de historial.
3. AC-HIS-01: Dado un cierre ejecutado con éxito, cuando se consulta el historial de la incidencia, entonces la última entrada es la transición from_status = 'resuelta' → to_status = 'cerrada' con changed_by_user_id del usuario de sesión, changed_at de servidor y resolution_comment_ref informado.
4. AC-HIS-02: Dado un intento de cierre rechazado con 400, 403 o 409, cuando se consulta el historial de la incidencia, entonces el número de entradas es idéntico al previo al intento y no existe ninguna entrada nueva.
5. AC-HIS-03: Dado una entrada de historial existente, cuando se intenta modificarla o eliminarla por cualquier vía expuesta por la aplicación, entonces la operación se rechaza y la entrada permanece idéntica en todos sus campos (from_status, to_status, changed_by_user_id, changed_at, resolution_comment_ref).
6. AC-HIS-04: Dado un usuario con rol EMPLEADO, cuando consulta el historial de una incidencia propia obtiene 200 con las transiciones completas, y cuando consulta el de una incidencia ajena recibe denegación uniforme; dado un TECNICO_DE_MANTENIMIENTO, cuando consulta el historial de cualquier incidencia, entonces obtiene 200.
7. AC-HIS-05: Dado una incidencia cerrada hace hasta 2 años (límite de la ventana de retención), cuando cualquier actor dentro de su alcance consulta su historial, entonces la entrada de cierre sigue presente y legible, incluyendo el caso de valores de catálogo (sala, categoría) desactivados con uso histórico.
8. AC-HIS-06: Dado el conjunto de incidencias en estado «cerrada» de la base de datos, cuando se ejecuta la verificación de consistencia, entonces toda incidencia cerrada tiene exactamente una entrada de historial resuelta → cerrada y toda entrada resuelta → cerrada corresponde a una incidencia en estado «cerrada» (cardinalidad 1:1, sin huérfanos en ninguna dirección).
**Validaciones:**
1. `incident_id` es obligatorio y debe referenciar una incidencia existente
2. `from_status` y `to_status` deben pertenecer a `cat_estados_incidencia`
3. `from_status` debe coincidir con el estado real previo de la incidencia; no se admite insertar una transición incoherente
4. `changed_by_user_id` es obligatorio y debe corresponder a un usuario existente, aunque esté desactivado
5. `changed_at` es obligatorio y lo informa el servidor; no se acepta del cliente
6. `resolution_comment_ref` es obligatoria cuando la transición registrada es `resuelta → cerrada`
**Escenarios de error:**
1. La incidencia cuyo historial se consulta no existe o no está dentro del alcance del usuario
2. El usuario no tiene permiso para consultar el historial de esta incidencia
3. Las entradas de historial son inmutables: no admiten modificación ni eliminación
4. La transición indicada no corresponde al estado real previo de la incidencia
5. No se ha podido completar el cierre; inténtelo de nuevo
**Campos de datos:**
- `history_id` (string, obligatorio) — Identificador de la entrada de historial
- `incident_id` (string, obligatorio) — Incidencia a la que pertenece la entrada de historial
- `from_status` (enum, obligatorio) — Valor de cat_estados_incidencia; en el cierre = resuelta; debe coincidir con el estado real previo
- `to_status` (enum, obligatorio) — Valor de cat_estados_incidencia; en el cierre = cerrada
- `changed_by_user_id` (string, obligatorio) — Debe corresponder a un usuario existente, aunque esté desactivado
- `changed_at` (datetime, obligatorio) — Informada por el servidor
- `resolution_comment_ref` (string, obligatorio) — Obligatoria solo en la transición resuelta → cerrada

### REQ-123 — Registro inmutable en el historial por cada transición aceptada, en la misma transacción
Cada transición de estado aceptada genera un registro inmutable en el historial de la incidencia, en la misma transacción que el cambio. Reglas: no existe cambio de estado sin registro —la inserción en el historial y la actualización de `incidencia.status` son atómicas; si el registro falla, se revierte todo y el estado no varía (AC-7 de la épica)—; los registros son append-only: sin UPDATE ni DELETE, ni desde la aplicación ni por baja lógica (REQ-015, REQ-047); el estado actual de la incidencia coincide siempre con el `to_status` del último registro del historial (invariante verificable); el alta de la incidencia genera el primer registro con `from_status` nulo y `to_status`=«abierta»; la atribución persiste aunque el usuario se desactive posteriormente (REQ-087); retención 2 años (REQ-015). Datos: `incident_status_history` (`history_id` PK, `incident_id` FK obligatorio, `from_status` varchar(20) nullable FK `cat_estados_incidencia`, `to_status` varchar(20) obligatorio FK, `changed_by_user_id` FK usuario obligatorio, `changed_at` timestamp obligatorio, `comment` varchar(500) nullable); índice por (`incident_id`, `changed_at`). Validaciones: `to_status` de catálogo; `from_status` = estado persistido antes del cambio; `changed_by_user_id` = usuario de la sesión, nunca un valor recibido del cliente (REQ-064); `changed_at` lo fija el servidor. Errores: fallo de inserción → rollback completo y 500 «No se ha podido completar el cambio de estado; inténtelo de nuevo»; intento de modificar o borrar un registro → 403 «El historial de cambios de estado es inmutable». AC: Given una transición aceptada, when finaliza la operación, then existe exactamente un registro nuevo con `from_status`, `to_status`, actor y fecha, y el estado de la incidencia coincide con ese `to_status`; Given un fallo al insertar en el historial, when se procesa la transición, then el estado de la incidencia permanece sin cambios; Given un registro existente, when se intenta actualizarlo, then la operación se deniega. Seguridad: la escritura solo la produce el servicio de transición (CVI-02) y el alta (REQ-090); ningún rol puede escribir directamente en el historial. Eventos de dominio: `IncidentStatusChanged`, emitido después del commit, disparador del aviso por correo gestionado por el módulo de notificaciones (REQ-079, REQ-082, REQ-089) [inferido]. Dependencias: CVI-02, REQ-090, REQ-015, REQ-048. Prioridad Must [inferido].
**Reglas de negocio:**
1. No existe cambio de estado de una incidencia sin exactamente un registro correspondiente en su historial
2. Los registros del historial de cambios de estado son append-only: no admiten modificación ni borrado, ni físico ni lógico
3. El estado actual de una incidencia coincide siempre con el `to_status` del último registro de su historial
4. El primer registro del historial de toda incidencia tiene estado origen nulo y estado destino «abierta»
5. El actor de un registro de historial es el usuario de la sesión, nunca un valor recibido del cliente
6. La fecha de un registro de historial la fija el servidor
7. La atribución de un registro de historial se conserva aunque el usuario actor sea desactivado posteriormente
8. Los registros del historial se conservan durante 2 años
9. Los únicos orígenes de escritura en el historial son el alta de la incidencia y el servicio de transición: ningún rol escribe directamente en él
**Criterios de aceptación:**
1. AC-HIST-02: Dado un fallo forzado en la inserción del registro de historial, cuando se procesa una transición, entonces la transacción se revierte completa, el estado de la incidencia permanece sin cambios, no queda entrada parcial y la API responde 500 con el mensaje genérico en español; y dado un registro de historial existente, cuando se intenta actualizarlo o borrarlo, entonces la operación se deniega con 403 «El historial de cambios de estado es inmutable».
2. AC-HIST-03: Dado una incidencia con alta y dos cambios de estado posteriores, cuando el empleado reportante consulta su historial —incluso con la incidencia ya cerrada—, entonces recibe tres entradas en orden cronológico ascendente con estado origen, estado destino, nombre del actor, fecha-hora y comentario cuando exista; y dado que el actor de una entrada fue desactivado, cuando se consulta el historial, entonces su nombre sigue mostrándose.
3. AC-HIST-01: Dado una transición de estado aceptada, cuando finaliza la operación, entonces existe exactamente una entrada nueva en incident_status_history con from_status, to_status, actor de la sesión y fecha-hora de servidor, y el estado persistido de la incidencia coincide con el to_status de la última entrada (invariante verificable sobre el 100 % de las incidencias del entorno de prueba).
**Validaciones:**
1. `incident_id` es obligatorio y debe referenciar una incidencia existente
2. `to_status` es obligatorio y debe ser un valor del catálogo `cat_estados_incidencia`
3. `from_status` debe coincidir con el estado persistido de la incidencia inmediatamente antes del cambio (es nulo solo en el registro de alta)
4. `changed_by_user_id` es obligatorio y se toma siempre del usuario de la sesión; se ignora/rechaza cualquier identificador de actor recibido del cliente
5. `changed_at` es obligatorio y lo fija el servidor; se ignora cualquier fecha enviada por el cliente
6. `comment` es opcional y no supera 500 caracteres
**Escenarios de error:**
1. El historial de cambios de estado es inmutable y no admite modificación ni borrado
2. No se ha podido completar el cambio de estado; inténtelo de nuevo
**Campos de datos:**
- `history_id` (string, obligatorio) — Clave primaria; append-only
- `incident_id` (string, obligatorio) — Referencia obligatoria; índice por (incident_id, changed_at)
- `from_status` (enum, opcional) — Nulo solo en el primer registro (alta); valor del catálogo
- `to_status` (enum, obligatorio) — Valor del catálogo; coincide con el estado actual de la incidencia
- `changed_by_user_id` (string, obligatorio) — Siempre el usuario de la sesión, nunca un valor del cliente
- `changed_at` (datetime, obligatorio) — La fija el servidor; registro inmutable; retención 2 años
- `comment` (string, opcional) — Máx. 500 caracteres

### REQ-124 — Consulta del historial cronológico de cambios de estado con actor y fecha
El usuario consulta el historial cronológico de cambios de estado de una incidencia en todo momento, con el actor y la fecha de cada cambio. Reglas: el historial está disponible «en todo momento», incluidas las incidencias resueltas y cerradas (cita RFP: «El empleado que reportó la incidencia debe poder ver en todo momento el estado actual y el historial de cambios de estado»); se presenta en orden cronológico ascendente por `changed_at`, incluyendo como primer hito el alta; el nombre y correo del actor se resuelven con los datos vigentes del directorio (REQ-079, REQ-080) y siguen visibles aunque el actor esté desactivado (REQ-087, REQ-088); sin paginación dada la volumetría declarada (máximo 50 incidencias/mes). Flujo: el usuario abre el detalle de su incidencia → sección «Historial» → lista de entradas «estado origen → estado destino · actor · fecha y hora» con el comentario cuando exista. Datos de salida: GET /api/incidencias/{incident_id}/historial → [{from_status, from_status_label, to_status, to_status_label, changed_by_name, changed_at, comment}]. Catálogos: etiquetas desde `cat_estados_incidencia`, incluidos valores desactivados con uso histórico. Validaciones: `incident_id` existente y dentro del alcance del rol. Errores: 404 uniforme, sin revelar la existencia del recurso, cuando el EMPLEADO pide el historial de una incidencia que no ha reportado (REQ-023, REQ-026, REQ-031); 401 sin sesión válida (REQ-051, REQ-058). AC: Given una incidencia con alta y dos cambios de estado, when el reportante consulta su historial, then recibe tres entradas en orden cronológico con actor y fecha; Given un EMPLEADO, when consulta el historial de una incidencia ajena, then recibe la denegación uniforme y ningún dato; Given un TECNICO_DE_MANTENIMIENTO, when consulta el historial de cualquier incidencia, then lo obtiene completo; Given una incidencia cuyo actor fue desactivado, when se consulta el historial, then el nombre del actor sigue mostrándose. Seguridad: alcance por rol idéntico al del detalle — EMPLEADO solo sus incidencias, TECNICO_DE_MANTENIMIENTO todas (REQ-026, REQ-029); sin doble factor. Dependencias: HIST-01, REQ-026, REQ-079. Nota de solape: el dominio clave «Historial de cambios de estado consultable» podría estar asignado también a otra épica; si aparece allí, consolidar en un único requisito. Prioridad Must [inferido].
**Reglas de negocio:**
1. El historial de una incidencia es consultable en cualquiera de sus estados, incluidas las resueltas y las cerradas
2. Las entradas del historial se presentan en orden cronológico ascendente, siendo el alta de la incidencia la primera entrada
3. El nombre del actor de cada entrada del historial sigue siendo visible aunque el usuario esté desactivado
4. Un empleado consulta el historial únicamente de las incidencias que ha reportado; el técnico de mantenimiento, el de todas
5. Las etiquetas de estado mostradas en el historial incluyen los valores de catálogo desactivados con uso histórico
**Criterios de aceptación:**
1. AC-HIST-03: Dado una incidencia con alta y dos cambios de estado posteriores, cuando el empleado reportante consulta su historial —incluso con la incidencia ya cerrada—, entonces recibe tres entradas en orden cronológico ascendente con estado origen, estado destino, nombre del actor, fecha-hora y comentario cuando exista; y dado que el actor de una entrada fue desactivado, cuando se consulta el historial, entonces su nombre sigue mostrándose.
2. AC-HIST-04: Dado un usuario con rol EMPLEADO, cuando solicita el historial de una incidencia que no ha reportado, entonces recibe la denegación uniforme 404 sin ningún dato de la incidencia; y dado un técnico de mantenimiento, cuando solicita el historial de cualquier incidencia, entonces lo obtiene completo.
**Validaciones:**
1. `incident_id` es obligatorio, debe corresponder a una incidencia existente y estar dentro del alcance del rol del solicitante; en otro caso se devuelve 404 uniforme
**Escenarios de error:**
1. No hay sesión válida para consultar el historial
2. La incidencia solicitada no existe o no está disponible para el usuario
**Campos de datos:**
- `from_status` (enum, opcional) — Nulo en la entrada de alta
- `from_status_label` (string, opcional) — Desde el catálogo, incluidos valores desactivados con uso histórico
- `to_status` (enum, obligatorio) — Valor del catálogo de estados
- `to_status_label` (string, obligatorio) — Desde el catálogo de estados
- `changed_by_name` (string, obligatorio) — Sigue visible aunque el usuario esté desactivado
- `changed_at` (datetime, obligatorio) — Orden cronológico ascendente; sin paginación
- `comment` (string, opcional) — Máx. 500 caracteres

### REQ-125 — Asiento inicial de creación en el historial, en la misma transacción que el alta
El sistema registra un asiento inicial de creación en el historial de la incidencia, en la misma transacción que su alta. Reglas: toda incidencia tiene al menos una entrada de historial desde el instante del alta (CA: una incidencia recién creada muestra al menos el asiento de creación en estado «abierta» con su autor y su momento); el asiento se graba con entry_type='CREACION', from_status=NULL y to_status='ABIERTA', valor del catálogo cerrado de estados (REQ-117); el actor es el usuario de la sesión, resuelto en backend e ignorando cualquier identidad del payload (REQ-064); se inserta en la misma transacción que el alta (REQ-090): si falla el asiento se revierte el alta completa, nunca existe incidencia sin historial; la entrada es inmutable, solo INSERT, sin UPDATE/DELETE desde la aplicación, y se conserva 2 años (REQ-015, REQ-047); las transiciones posteriores las asienta REQ-123, que garantiza que from_status de cada entrada nueva coincide con el to_status de la última, de modo que la cadena queda sin huecos. Flujo: empleado envía el alta → backend valida sala/categoría (REQ-100) → persiste incidencia → inserta asiento de creación → commit → 201 con incidence_id y history_id. Rama alternativa: error en cualquiera de los dos pasos → rollback y sin efectos laterales. Datos: history_id (number, PK), incidence_id (number, obligatorio, FK incidencia), entry_type (varchar, obligatorio, catálogo cat_tipos_entrada_historial), from_status (varchar, nulo solo en CREACION, FK cat_estados), to_status (varchar, obligatorio, FK cat_estados), actor_user_id (number, obligatorio, FK usuario), actor_display_name (varchar(150), obligatorio, snapshot del nombre para conservar la atribución si el usuario se desactiva — REQ-087), changed_at (timestamp, obligatorio, generado por el servidor en UTC; presentación en Europe/Madrid [inferido]). Catálogos: cat_estados (REQ-117), cat_tipos_entrada_historial (CREACION, CAMBIO_ESTADO, ASIGNACION) [gap: el RFP solo enumera los estados del ciclo de vida; no declara tipos de entrada de historial]. Validaciones: changed_at nunca aceptado del cliente; to_status debe existir en catálogo; incidence_id debe existir en la misma transacción. Errores: fallo de persistencia → HTTP 500 «No se ha podido registrar la incidencia. Inténtelo de nuevo.» con rollback total; intento de escribir un asiento con estado fuera de catálogo → HTTP 422 sin persistir. CA: Given un alta válida When se crea la incidencia Then su historial contiene exactamente una entrada CREACION con to_status='ABIERTA', autor el empleado reportante y fecha de alta. Given un fallo al insertar el asiento When se intenta confirmar Then no existe ni la incidencia ni el asiento. Given una entrada ya grabada When se intenta modificar desde la aplicación Then la operación no está disponible y la entrada permanece intacta. Seguridad: la ejecuta implícitamente quien da el alta — EMPLEADO (o TECNICO_DE_MANTENIMIENTO si reporta); alcance de datos: el asiento pertenece a la incidencia propia del reportante (REQ-029); sin doble factor. Dependencias: REQ-090, REQ-117, REQ-123, REQ-015, REQ-087. Eventos de dominio: ninguno propio — el aviso por correo al equipo de mantenimiento cuelga del alta (REQ-090). Prioridad: Must. Integración: —. Fase: —.
**Reglas de negocio:**
1. Toda incidencia tiene al menos una entrada de historial desde el instante mismo de su alta
2. Existe exactamente una entrada de tipo `CREACION` por incidencia
3. El asiento de creación de una incidencia tiene siempre `from_status` nulo y `to_status` igual a «abierta»
4. `from_status` solo es nulo en las entradas de tipo `CREACION`
5. Una incidencia y su asiento de creación son atómicos: no existe incidencia sin historial ni asiento de creación sin incidencia
6. Una entrada de historial ya grabada es inmutable: no admite modificación ni borrado desde la aplicación
7. Las entradas de historial se conservan durante 2 años
8. El `changed_at` de una entrada de historial es siempre el instante UTC del servidor y nunca un valor aportado por el cliente
9. El autor de una entrada de historial es el usuario de la sesión, con independencia de cualquier identidad presente en el payload
10. Los valores de `from_status` y `to_status` pertenecen siempre al catálogo cerrado de estados
11. El nombre del autor conservado en la entrada de historial permanece invariable aunque el usuario se desactive o cambie de nombre
12. En la cadena de historial de una incidencia, el `from_status` de cada entrada de cambio de estado coincide con el `to_status` de la entrada inmediatamente anterior
**Criterios de aceptación:**
1. AC-HIST-REG-01: Dado un alta de incidencia válida (sala y categoría del catálogo, descripción informada), cuando el empleado confirma el alta, entonces el historial de esa incidencia contiene exactamente una entrada de tipo `CREACION` con `from_status = NULL`, `to_status = 'ABIERTA'`, el usuario de la sesión como autor y la marca temporal generada por el servidor en UTC.
2. AC-HIST-REG-02: Dado un alta de incidencia en la que la inserción del asiento de historial falla (fallo forzado de persistencia), cuando se intenta confirmar la operación, entonces la transacción se revierte por completo: no existe ni la incidencia ni el asiento, y la API responde HTTP 500 con mensaje de reintento y sin efectos laterales.
3. AC-HIST-REG-03: Dado cualquier entrada de historial ya grabada (creación, cambio de estado o asignación), cuando se intenta modificarla o eliminarla desde la aplicación o desde la API por cualquier rol, entonces la operación no está disponible (no existe endpoint ni acción de UI), la entrada permanece intacta y se conserva durante 2 años desde su creación.
4. AC-HIST-REG-04: Dado una incidencia que ha recorrido el ciclo abierta → en curso → resuelta → cerrada, cuando se inspecciona su historial completo, entonces la cadena es continua sin huecos: el `from_status` de cada entrada de cambio de estado coincide con el `to_status` de la entrada inmediatamente anterior, la primera entrada es el asiento de `CREACION` y la última refleja el estado persistido actual de la incidencia.
5. AC-HIST-REG-06: Dado una entrada de historial cuyo autor es un usuario que posteriormente se desactiva, cuando se consulta el historial de esa incidencia, entonces el nombre del autor sigue mostrándose íntegro a partir del snapshot `actor_display_name` persistido en la entrada, sin dependencia del estado actual de la cuenta.
**Validaciones:**
1. `incidence_id` es obligatorio y debe referenciar una incidencia existente dentro de la misma transacción del alta
2. `entry_type` es obligatorio y su valor debe pertenecer al catálogo `cat_tipos_entrada_historial` (`CREACION`, `CAMBIO_ESTADO`, `ASIGNACION`)
3. `to_status` es obligatorio y debe existir en el catálogo cerrado `cat_estados`; un valor fuera de catálogo se rechaza sin persistir (HTTP 422)
4. `from_status` solo puede ser nulo cuando `entry_type = 'CREACION'`; en el resto de entradas es obligatorio y debe existir en `cat_estados`
5. `changed_at` nunca se acepta del cliente: cualquier valor recibido en el payload se ignora y la marca la genera el servidor en UTC
6. `actor_user_id` se resuelve en backend desde el usuario de la sesión, ignorando cualquier identidad presente en el payload
7. `actor_display_name` es obligatorio y no puede superar los 150 caracteres
**Escenarios de error:**
1. Sesión no válida o caducada al registrar el alta y su asiento inicial
2. Datos obligatorios del alta ausentes o con formato incorrecto
3. Se envía una marca temporal de la entrada de historial desde el cliente, no admitida
4. El estado destino del asiento inicial no pertenece al catálogo de estados permitidos
5. La sala o la categoría indicadas no corresponden a valores activos del catálogo
6. No se ha podido completar el registro de la incidencia y su asiento; la operación se deshace por completo
**Campos de datos:**
- `history_id` (integer, obligatorio) — Clave primaria generada por el sistema
- `incidence_id` (integer, obligatorio) — Debe existir en la misma transacción del alta
- `entry_type` (enum, obligatorio) — Valores: CREACION, CAMBIO_ESTADO, ASIGNACION (catálogo cat_tipos_entrada_historial); en este requisito siempre CREACION
- `from_status` (enum, opcional) — Nulo únicamente en entradas CREACION; si no, valor del catálogo cerrado de estados
- `to_status` (enum, obligatorio) — En el asiento de creación siempre ABIERTA; valor del catálogo cerrado de estados
- `actor_user_id` (integer, obligatorio) — Nunca aceptado desde el payload del cliente
- `actor_display_name` (string, obligatorio) — Longitud máxima 150 caracteres
- `changed_at` (datetime, obligatorio) — Generado por el servidor en UTC; nunca aceptado del cliente; presentación en Europe/Madrid

### REQ-126 — Entrada de historial trazable por cada asignación o reasignación de técnico
El sistema añade al historial de la incidencia una entrada trazable por cada asignación o reasignación de técnico. [ambigüedad] el RFP §2.1 exige literalmente «el historial de cambios de estado», mientras el CA de EPIC-015 pide «la secuencia completa… sin huecos» y el objetivo de negocio habla de «visibilidad… de la responsabilidad de cada actuación»; la asignación se registra como entrada de tipo propio que no altera el estado. Reglas: entry_type='ASIGNACION', from_status y to_status conservan el estado vigente sin cambiarlo — el paso a «en curso» sigue siendo transición aparte (REQ-119); se graba assigned_technician_id y, si la hubo, previous_technician_id; se inserta en la misma transacción que la autoasignación/reasignación, con rollback conjunto; entrada inmutable y retenida 2 años (REQ-015); una reasignación no borra la entrada anterior: se añade una nueva. Flujo: técnico pulsa «Asignármela» (o reasigna) → backend valida permiso y estado de la incidencia → actualiza la asignación → inserta entrada de historial → commit → respuesta con el historial actualizado. Datos: además de los de HIST-REG-01, assigned_technician_id (number, obligatorio en esta entrada, FK usuario), previous_technician_id (number, opcional), assigned_technician_name (varchar(150), snapshot). Validaciones: el técnico destino debe estar activo y con rol TECNICO_DE_MANTENIMIENTO (REQ-035, REQ-082); rechazar asignación a cuenta desactivada; rechazar si la incidencia está en estado terminal «cerrada» (REQ-112). Errores: HTTP 409 «La incidencia ya está asignada a otro técnico.»; HTTP 409 «La incidencia está cerrada y no admite cambios.»; HTTP 403 con denegación uniforme y sin revelar el recurso (REQ-031). CA: Given una incidencia sin asignar When un técnico se la asigna Then el historial incorpora una entrada ASIGNACION con su nombre, la fecha y sin cambio de estado. Given una reasignación When se confirma Then coexisten en el historial la entrada anterior y la nueva, en orden cronológico. Seguridad: ejecuta TECNICO_DE_MANTENIMIENTO (REQ-030); EMPLEADO solo lectura y únicamente en sus propias incidencias (REQ-026); ADMINISTRADOR sin alcance sobre incidencias (REQ-014); sin doble factor. Dependencias: HIST-REG-01, REQ-123, REQ-119, REQ-107. Prioridad Should [inferido]: el RFP no la declara y la capacidad excede el mínimo literal de «cambios de estado». Integración: —. Fase: —.
**Reglas de negocio:**
1. Una entrada de tipo `ASIGNACION` conserva el estado vigente de la incidencia: su `from_status` y su `to_status` son iguales
2. Cada asignación o reasignación de técnico añade una entrada nueva al historial y ninguna entrada de asignación previa desaparece
3. Una incidencia tiene como máximo un técnico asignado en un instante dado
4. El técnico destinatario de una asignación está activo y tiene rol `TECNICO_DE_MANTENIMIENTO`
5. Una incidencia en estado «cerrada» no admite asignación ni reasignación de técnico
6. La asignación de técnico y su entrada de historial son atómicas: o coexisten ambas o no existe ninguna
7. Solo el rol `TECNICO_DE_MANTENIMIENTO` origina entradas de asignación; empleado y administrador no
**Criterios de aceptación:**
1. AC-HIST-REG-03: Dado cualquier entrada de historial ya grabada (creación, cambio de estado o asignación), cuando se intenta modificarla o eliminarla desde la aplicación o desde la API por cualquier rol, entonces la operación no está disponible (no existe endpoint ni acción de UI), la entrada permanece intacta y se conserva durante 2 años desde su creación.
2. AC-HIST-REG-05: Dado una incidencia no cerrada, cuando un técnico de mantenimiento se la asigna y posteriormente se reasigna a otro técnico activo, entonces el historial incorpora una entrada `ASIGNACION` por cada operación, cada una con el técnico destino, el técnico anterior cuando existe y la fecha, sin alterar el estado de la incidencia (`from_status = to_status`), coexistiendo ambas entradas en orden cronológico.
3. AC-HIST-REG-06: Dado una entrada de historial cuyo autor es un usuario que posteriormente se desactiva, cuando se consulta el historial de esa incidencia, entonces el nombre del autor sigue mostrándose íntegro a partir del snapshot `actor_display_name` persistido en la entrada, sin dependencia del estado actual de la cuenta.
**Validaciones:**
1. `assigned_technician_id` es obligatorio en las entradas de tipo `ASIGNACION` y debe corresponder a un usuario existente
2. El usuario indicado en `assigned_technician_id` debe estar activo: se rechaza la asignación a una cuenta desactivada
3. El usuario indicado en `assigned_technician_id` debe tener el rol `TECNICO_DE_MANTENIMIENTO`
4. `assigned_technician_name` (snapshot) no puede superar los 150 caracteres
5. `from_status` y `to_status` de la entrada de asignación deben coincidir con el estado vigente de la incidencia (coherencia entre campos: la asignación no altera el estado)
**Escenarios de error:**
1. Sesión no válida o caducada al asignar o reasignar la incidencia
2. El usuario no tiene permiso para asignar incidencias
3. La incidencia indicada no existe o no está disponible para el usuario
4. El identificador del técnico destino tiene un formato inválido
5. El usuario destino no está activo o no tiene el rol de técnico de mantenimiento
6. La incidencia ya está asignada a otro técnico
7. La incidencia está cerrada y no admite cambios de asignación
8. No se ha podido completar la asignación ni su entrada de historial; no se aplica ningún cambio
**Campos de datos:**
- `entry_type` (enum, obligatorio) — Valor fijo ASIGNACION del catálogo cat_tipos_entrada_historial
- `assigned_technician_id` (integer, obligatorio) — Debe ser usuario activo con rol TECNICO_DE_MANTENIMIENTO
- `previous_technician_id` (integer, opcional) — Vacío en la primera asignación
- `assigned_technician_name` (string, obligatorio) — Longitud máxima 150 caracteres

### REQ-129 — Registro de actividad reciente de cambios de estado de todas las incidencias, filtrable, para el técnico
El técnico de mantenimiento consulta un registro de actividad reciente con los últimos cambios de estado de todas las incidencias, filtrable y enlazado al detalle. [inferido] no exigido literalmente por el RFP; deriva del valor declarado de EPIC-015 («visibilidad objetiva del avance», «detectar salas o categorías problemáticas») y del actor «equipo de facilities» citado en la épica. Reglas: agrega entradas de historial de todas las incidencias, orden descendente por changed_at (lo más reciente primero); filtros combinables por rango de fechas, sala, oficina, categoría, tipo de entrada y actor, alimentados con los catálogos ya disponibles (REQ-103) incluyendo valores inactivos con uso histórico; paginación estable de 25 filas con desempate por history_id para evitar duplicados/saltos entre páginas; cada fila enlaza al detalle de su incidencia, donde aplica el alcance de REQ-026; la vista es de solo lectura y no permite modificar ni ocultar entradas (REQ-015); no incorpora recuentos agregados: la agregación por oficina/sala/categoría ya existe en REQ-104, que se referencia como dependencia. Datos de filtro/respuesta: date_from (date, opcional, ≤ date_to), date_to (date, opcional, no futura), room_id (number, opcional, catálogo cat_salas), office_id (number, opcional, catálogo cat_oficinas), category_id (number, opcional, catálogo cat_categorias_incidencia), entry_type (varchar, opcional, cat_tipos_entrada_historial), actor_user_id (number, opcional), page (number, ≥1), page_size (number, fijo 25); por fila: incidence_id, incidence_title, room_name, category_name, from_status_label, to_status_label, actor_display_name, changed_at. Validaciones: rango de fechas coherente y máximo 12 meses [inferido]; identificadores de filtro existentes en catálogo, si no → 422 sin ejecutar la consulta. Errores: HTTP 422 «El rango de fechas no es válido.»; HTTP 403 denegación uniforme para EMPLEADO y ADMINISTRADOR; resultado vacío → «No hay actividad para los filtros seleccionados.» con acción de limpiar filtros. CA: Given un técnico de mantenimiento When abre el registro de actividad Then ve los últimos cambios de estado de todas las incidencias en orden descendente con sala, categoría, autor y fecha. Given filtros por sala y rango de fechas When se aplican Then el listado solo muestra entradas que los cumplen y la paginación se reinicia. Given un empleado When invoca la consulta Then recibe 403 uniforme sin datos. Seguridad: exclusivo de TECNICO_DE_MANTENIMIENTO (alcance completo de incidencias, REQ-029); EMPLEADO denegado (REQ-022, REQ-023); ADMINISTRADOR sin alcance de incidencias (REQ-014); resolución de permisos en backend desde el rol en BD (REQ-009, REQ-028); sesión válida obligatoria (REQ-051). Dependencias: REQ-124, REQ-103, REQ-104, HIST-REG-01, HIST-REG-02. Prioridad Could [inferido]: capacidad de explotación no pedida explícitamente en el RFP. Integración: —. Fase: —.
**Reglas de negocio:**
1. En el registro de actividad, `date_from` no es posterior a `date_to` y `date_to` no es una fecha futura
2. El rango de fechas del registro de actividad no excede 12 meses
3. Los filtros de sala, oficina y categoría admiten valores de catálogo inactivos siempre que tengan uso histórico
4. El registro de actividad es de solo lectura: no permite modificar ni ocultar entradas de historial
5. El registro de actividad reciente es accesible únicamente al rol `TECNICO_DE_MANTENIMIENTO`
6. El registro de actividad presenta las entradas en orden descendente de `changed_at`, con `history_id` como único desempate
7. Ninguna entrada de historial aparece duplicada ni omitida al recorrer las páginas del registro de actividad con los mismos filtros
8. El tamaño de página del registro de actividad es fijo de 25 filas
**Criterios de aceptación:**
1. AC-HIST-CONS-03: Dado un empleado autenticado y una incidencia que no reportó, cuando invoca la consulta de detalle/historial directamente por API con el identificador de esa incidencia, entonces recibe una denegación uniforme (misma respuesta que para una incidencia inexistente), cero entradas de historial en el cuerpo y ningún dato que revele la existencia del recurso; el mismo escenario para el rol `ADMINISTRADOR` sobre el registro de actividad devuelve igualmente denegación.
2. AC-HIST-CONS-07: Dado un técnico de mantenimiento, cuando abre el registro de actividad reciente y aplica filtros combinados por rango de fechas, sala, oficina, categoría, tipo de entrada y actor, entonces ve las entradas de todas las incidencias en orden descendente por fecha, paginadas de 25 en 25 con desempate estable por identificador (sin duplicados ni saltos entre páginas), cada fila enlaza al detalle de su incidencia, y un rango de fechas inválido o superior a 12 meses se rechaza con HTTP 422 sin ejecutar la consulta.
**Validaciones:**
1. `date_from` y `date_to` son opcionales y deben tener formato de fecha válido; si ambos se informan, `date_from` debe ser menor o igual que `date_to`
2. `date_to` no puede ser una fecha futura
3. El rango `date_from`–`date_to` no puede superar los 12 meses; en caso contrario se devuelve 422 sin ejecutar la consulta
4. `room_id`, si se informa, debe existir en el catálogo `cat_salas`
5. `office_id`, si se informa, debe existir en el catálogo `cat_oficinas`
6. `category_id`, si se informa, debe existir en el catálogo `cat_categorias_incidencia`
7. `entry_type`, si se informa, debe pertenecer al catálogo `cat_tipos_entrada_historial`
8. `actor_user_id`, si se informa, debe corresponder a un usuario existente
9. `page` debe ser un número entero mayor o igual que 1
10. `page_size` es fijo con valor 25; se rechaza cualquier otro valor recibido
**Escenarios de error:**
1. Sesión no válida o caducada al consultar el registro de actividad
2. El usuario no tiene permiso para consultar la actividad de todas las incidencias
3. Parámetros de paginación fuera de los valores admitidos
4. Formato de fecha de filtro incorrecto
5. El rango de fechas no es válido: la fecha inicial es posterior a la final, la fecha final es futura o el rango supera el máximo permitido
6. Alguno de los valores de filtro seleccionados no pertenece a los catálogos disponibles
7. El registro de actividad no está disponible temporalmente; puede reintentarse la consulta
**Campos de datos:**
- `date_from` (date, opcional) — Debe ser ≤ date_to; rango máximo 12 meses
- `date_to` (date, opcional) — No puede ser futura; debe ser ≥ date_from
- `room_id` (integer, opcional) — Debe existir en el catálogo de salas, incluidos valores inactivos con uso histórico
- `office_id` (integer, opcional) — Debe existir en el catálogo de oficinas
- `category_id` (integer, opcional) — Debe existir en el catálogo de categorías
- `entry_type` (enum, opcional) — Valores: CREACION, CAMBIO_ESTADO, ASIGNACION
- `actor_user_id` (integer, opcional) — Debe corresponder a un usuario existente
- `page` (integer, obligatorio) — Valor mínimo 1
- `page_size` (integer, obligatorio) — Valor fijo 25
- `incidence_id` (integer, obligatorio) — Enlaza al detalle de la incidencia
- `incidence_title` (string, obligatorio) — Título de la incidencia mostrado en la fila
- `room_name` (string, obligatorio) — Sala de la incidencia mostrada en la fila
- `category_name` (string, obligatorio) — Categoría de la incidencia mostrada en la fila
- `from_status_label` (string, opcional) — Vacía en entradas de creación
- `to_status_label` (string, obligatorio) — Etiqueta del estado de destino de la entrada
- `actor_display_name` (string, obligatorio) — Autor de la entrada listada
- `changed_at` (datetime, obligatorio) — Orden descendente, desempate por history_id

### REQ-151 — Traza cronológica consolidada de la incidencia con alta, asignación, reclasificaciones y comentario de resolución
El sistema ofrece una traza cronológica consolidada de la incidencia que, además de los cambios de estado, recoge el alta, la asignación de técnico, las reclasificaciones y el comentario de resolución. Reglas: la traza amplía el historial de cambios de estado ya existente (REQ-123, REQ-124) con los eventos de negocio que hoy no quedan en una vista única: alta de la incidencia, autoasignación/cambio de técnico, reclasificación de sala o categoría (REQ-101) e incorporación del comentario de resolución; cada entrada es append-only, se escribe en la misma transacción que la operación que la origina y nunca se modifica ni se borra; el orden de presentación es cronológico ascendente por occurred_at y, a igualdad, por history_entry_id; la traza está disponible durante toda la ventana de retención, también para incidencias cerradas; si un evento fue realizado por un usuario desactivado, la atribución se conserva (REQ-087). Flujo: el usuario abre el detalle, pestaña «Historial», obtiene la lista paginada de entradas con tipo de evento, actor, fecha y qué cambió, y puede filtrar por tipo de evento. Datos: history_entry_id (number, PK), incident_id (FK, obligatorio), event_type (varchar, valor de cat_tipos_evento_incidencia: alta, cambio_estado, asignacion, reclasificacion, comentario_resolucion), actor_user_id (FK usuarios, obligatorio), occurred_at (timestamp, obligatorio), value_before (varchar 200, nullable), value_after (varchar 200, nullable). Catálogos: cat_tipos_evento_incidencia, cat_estados_incidencia. Validaciones: event_type dentro de catálogo; cambio_estado exige value_before/value_after no nulos y coherentes con el grafo de transiciones (REQ-117); paginación page_size 1..100. Errores: 400 «Tipo de evento no válido»; 403 uniforme si el EMPLEADO consulta la traza de una incidencia ajena; 404 si la incidencia no existe o está fuera de retención. Criterios de aceptación: Given una incidencia que pasó por abierta → en curso → resuelta → cerrada con una reclasificación de categoría intermedia, When el EMPLEADO reportante consulta su historial, Then ve 6 entradas en orden cronológico (alta, asignación, 3 cambios de estado, reclasificación) con actor y fecha en cada una. Given una incidencia cerrada hace un año, When se consulta su traza, Then se devuelve íntegra. Given un intento de editar o borrar una entrada, When se ejecuta, Then la operación se rechaza y nada cambia. Seguridad: sesión válida (REQ-051); EMPLEADO alcance a sus propias incidencias, TECNICO_DE_MANTENIMIENTO a todas (REQ-026, REQ-029); solo lectura, sin doble factor. Dependencias: REQ-123, REQ-124, REQ-116, REQ-101, REQ-117, RET-01. Prioridad Must [inferido]. Integración: —. Fase: —.
**Reglas de negocio:**
1. Toda operación de alta, cambio de estado, asignación, reclasificación o comentario de resolución tiene una entrada de traza asociada
2. Las entradas de la traza son append-only: una vez escritas no se modifican ni se borran
3. La traza de una incidencia se presenta en orden cronológico ascendente por momento del evento y, a igualdad, por identificador de entrada
4. Cada entrada de la traza tiene actor y fecha informados, y la atribución se conserva aunque el usuario actor esté desactivado
5. El tipo de evento de una entrada de traza es siempre un valor de `cat_tipos_evento_incidencia`
6. Una entrada de tipo «cambio de estado» tiene valor anterior y valor posterior informados y coherentes con el grafo de transiciones declarado
7. La traza completa de una incidencia está disponible durante toda su ventana de retención, también cuando la incidencia está cerrada
**Criterios de aceptación:**
1. AC-HIST-01: **Dado** una incidencia que recorrió `abierta → en curso → resuelta → cerrada` con una reclasificación de categoría intermedia, **cuando** el EMPLEADO reportante consulta su historial, **entonces** obtiene exactamente 6 entradas (alta, asignación, 3 cambios de estado y reclasificación) en orden cronológico ascendente por `occurred_at`, cada una con `event_type`, actor y fecha.
2. AC-HIST-02: **Dado** cualquier entrada existente de la traza, **cuando** se intenta editarla o borrarla por API o por operación de negocio, **entonces** la operación se rechaza y el contenido de la entrada permanece idéntico (comportamiento append-only verificado byte a byte sobre `value_before`, `value_after`, `actor_user_id` y `occurred_at`).
3. AC-HIST-03: **Dado** una incidencia cerrada hace un año y dentro de la ventana de retención, **cuando** un usuario con alcance consulta su traza, **entonces** se devuelve íntegra y paginada (`page_size` 1..100), sin pérdida de entradas respecto a las registradas en el momento de las operaciones.
4. AC-HIST-04: **Dado** un EMPLEADO, **cuando** consulta la traza de una incidencia que no reportó, **entonces** recibe `403` uniforme; y **dado** un TECNICO_DE_MANTENIMIENTO, **cuando** consulta la traza de cualquier incidencia, **entonces** la obtiene completa; y **dado** una incidencia inexistente o fuera de retención, **cuando** se solicita su traza, **entonces** se devuelve `404`.
5. AC-HIST-05: **Dado** una entrada de historial cuyo actor fue posteriormente desactivado como usuario, **cuando** se consulta la traza, **entonces** la atribución (nombre y correo corporativo del actor en el momento del evento) sigue mostrándose y no aparece como anónima ni vacía.
**Validaciones:**
1. `event_type` debe pertenecer al catálogo `cat_tipos_evento_incidencia` (`alta`, `cambio_estado`, `asignacion`, `reclasificacion`, `comentario_resolucion`)
2. En las entradas de tipo `cambio_estado`, `value_before` y `value_after` son obligatorios (no nulos)
3. En las entradas de tipo `cambio_estado`, `value_before` y `value_after` deben ser coherentes con el grafo de transiciones declarado
4. `incident_id`, `actor_user_id` y `occurred_at` son obligatorios en cada entrada de la traza
5. El parámetro de paginación `page_size` debe ser un entero entre 1 y 100
**Escenarios de error:**
1. El tipo de evento indicado en el filtro no es válido
2. El tamaño de página solicitado está fuera del rango admitido (1 a 100)
3. No tiene permisos para consultar el historial de esta incidencia
4. La incidencia no existe o está fuera del periodo de retención
5. Las entradas del historial no admiten modificación ni borrado
6. El cambio de estado registrado no es coherente con las transiciones permitidas del ciclo de vida
**Campos de datos:**
- `history_entry_id` (integer, obligatorio) — Clave primaria; desempata el orden a igualdad de `occurred_at`
- `incident_id` (integer, obligatorio) — Incidencia a la que pertenece la entrada de historial
- `event_type` (enum, obligatorio) — Valores de `cat_tipos_evento_incidencia`: alta, cambio_estado, asignacion, reclasificacion, comentario_resolucion
- `actor_user_id` (integer, obligatorio) — Referencia a usuarios
- `occurred_at` (datetime, obligatorio) — Momento del evento; base del orden cronológico ascendente
- `value_before` (string, opcional) — Longitud máxima 200; obligatorio y coherente con el grafo de transiciones cuando `event_type = 'cambio_estado'`
- `value_after` (string, opcional) — Longitud máxima 200; obligatorio cuando `event_type = 'cambio_estado'`
- `page_size` (integer, opcional) — Rango 1..100

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-056/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-056/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: validar el modelo de datos que construye esta tarea contra el motor real.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.
El esquema de `TSK-044` ya está APLICADO en este servicio (changelogs Liquibase de su rama mergeada): asume las tablas creadas, no las vuelvas a crear ni las modifiques desde esta tarea.

Esta tarea materializa el MODELO DE DATOS: el motor se levanta VACÍO a propósito, para que valides tu propio changelog contra él. Cómo, exactamente:

```sh
./.mind/TSK-056/liquibase.sh <changelog-maestro>            # aplica (update)
./.mind/TSK-056/liquibase.sh <changelog-maestro> status
./.mind/TSK-056/liquibase.sh <changelog-maestro> rollback-count 99  # reversibilidad
```

Ese script lo genera la plataforma y corre Liquibase como contenedor contra ESTE motor. **NO descargues ni instales Liquibase por tu cuenta**: sus JAR acabarían commiteados en el repo del cliente, y apuntar al `local/*.properties` del arquetipo NO sirve (esa configuración mira a otra BBDD, no a la de tu sesión).

Comprueba las tres cosas antes de entregar: que el `update` termina limpio, que el rollback deshace, y que un segundo `update` es idempotente. Un changelog que nunca se ejecutó no está verificado — y la plataforma lo VUELVE a aplicar por su cuenta tras tu entrega, así que un rojo saldrá igual en el PR.

### Si el entorno no está disponible
Comprueba `.mind/TSK-056/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.