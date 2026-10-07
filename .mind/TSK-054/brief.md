# TSK-054 · Ciclo de vida, asignación, cierre y retención sobre incidencia

- Componente dueño: `ARC-016`
- Arquetipo del repo: `database-relational` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/facilities/changelogs/0.0.1/ddl/11-incidencia-ciclo-vida.xml`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
EXTIENDE la tabla `incidencia` de TSK-10 con `addColumn`; reutiliza sus columnas y NO redeclara `createTable` ni los snapshots. Añade: assigned_technician_id FK a `usuario` nullable, assigned_at, released_at, released_by FK, release_reason varchar2(500), in_progress_at, resolved_at, closed_at, closed_by FK, resolution_comment CLOB, status_changed_at NOT NULL, status_changed_by FK, retention_expires_at como columna virtual `created_at + INTERVAL '24' MONTH` de solo lectura. Checks verificables: coexistencia de los cuatro datos de cierre (status='CERRADA' ⟺ closed_at, closed_by y resolution_comment NOT NULL y no vacío tras TRIM, AC-CIE-01/AC-CIE-02); orden cronológico `created_at <= in_progress_at <= resolved_at <= closed_at` entre las marcas existentes y ninguna marca en el futuro (REQ-122); `release_reason` entre 10 y 500 caracteres cuando `released_at` está informado; trigger que impide sobrescribir `in_progress_at`/`resolved_at`/`assigned_at` ya informados (sellado una sola vez) y que incrementa `version` en cada UPDATE. Índice (room_id, category_id, closed_at DESC) para los cierres anteriores de la misma sala y categoría (REQ-113) e índice (assigned_technician_id, status) para el filtro de asignación (REQ-107). Oráculos contra el Oracle real: test de concurrencia con DOS conexiones simultáneas que ejecutan `UPDATE incidencia SET assigned_technician_id=:u WHERE incident_id=:id AND assigned_technician_id IS NULL` y comprueba 1 fila afectada en una y 0 en la otra, repetido 100 veces sin ninguna doble asignación (AC-ASG-02); test que cierra sin comentario y recibe violación de check; test que comprueba que una incidencia cerrada hace 30 días sigue en la tabla operativa y que `retention_expires_at` enviado por el cliente se ignora (AC-RET-01/AC-TRZ-03); test que libera por desactivación de técnico y verifica `release_reason='TECNICO_DESACTIVADO'` con las terminales intactas (AC-ASG-06). `update`/`rollback-count` verdes.

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

Tus zonas (`sources/facilities/changelogs/0.0.1/ddl/11-incidencia-ciclo-vida.xml`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

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

### REQ-107 — Filtro por estado de asignación y por técnico asignado
Reglas: la bandeja permite acotar por assignment_filter ∈ {TODAS, SIN_ASIGNAR, ASIGNADAS_A_MI, ASIGNADAS_A_OTRO}; SIN_ASIGNAR ≡ assigned_technician_id IS NULL; ASIGNADAS_A_MI resuelve contra la identidad del usuario de la sesión, nunca contra un identificador enviado por el cliente (REQ-064, REQ-018); alternativamente puede filtrarse por un assigned_technician_id concreto; combina en AND con los filtros de sala, categoría y estado de REQ-025; el selector de técnicos se alimenta del colectivo de mantenimiento activo (REQ-082) y excluye cuentas desactivadas (REQ-089), pero un técnico desactivado que figure como asignado histórico sigue mostrándose en las filas. Flujo: técnico abre la bandeja → selecciona «Sin asignar» para localizar trabajo no tomado → combina con estado ABIERTA → obtiene su cola de trabajo priorizable. Datos: assignment_filter (enum, default TODAS), assigned_technician_id (number, nullable; debe existir y tener rol TECNICO_DE_MANTENIMIENTO). Validaciones: técnico inexistente, inactivo o con otro rol → 400 «Técnico no válido»; assignment_filter y assigned_technician_id simultáneos con valores contradictorios → prevalece assigned_technician_id y se informa en la UI [ambigüedad: el RFP no regula la combinación]. Errores: 400 descrito; 401/403 como BAN-01. Aceptación: Dado un técnico, cuando filtra por «Sin asignar» y estado «abierta», entonces el resultado contiene solo incidencias abiertas sin assigned_technician_id; Dado el filtro «Asignadas a mí», cuando se ejecuta, entonces solo aparecen las incidencias cuyo técnico es el usuario de la sesión. Seguridad: exclusivo TECNICO_DE_MANTENIMIENTO; un EMPLEADO que invoque el endpoint recibe 403 uniforme sin filtrado parcial (REQ-023, REQ-031). Dependencias: REQ-025, REQ-082, REQ-089. [inferido: el RFP solo enumera filtros por sala, categoría y estado; el filtro por asignación deriva del objetivo «ver el listado de incidencias abiertas, asignarse una y cambiar su estado hasta cerrarla»]. Prioridad [inferido]. Prioridad: Must.
**Reglas de negocio:**
1. El filtro de asignación toma un único valor del conjunto cerrado {`TODAS`, `SIN_ASIGNAR`, `ASIGNADAS_A_MI`, `ASIGNADAS_A_OTRO`}
2. Una incidencia pertenece a `SIN_ASIGNAR` si y solo si su `assigned_technician_id` es nulo
3. El conjunto «Asignadas a mí» se define por la identidad del usuario de la sesión, y no por ningún identificador aportado por el cliente
4. Un `assigned_technician_id` es válido como filtro solo si corresponde a un usuario existente, activo y con rol TECNICO_DE_MANTENIMIENTO
5. Los filtros de asignación, sala, categoría y estado son acumulativos: el resultado contiene solo las incidencias que cumplen todas las condiciones a la vez
6. El selector de técnicos ofrece únicamente técnicos de mantenimiento activos, mientras que un técnico desactivado sigue siendo visible como asignado histórico en las filas ya existentes
7. Un filtro por técnico concreto y un filtro por estado de asignación no coexisten con valores contradictorios: el técnico concreto prevalece
**Criterios de aceptación:**
1. AC-BAN-04: Dado un técnico en la bandeja, cuando filtra por «Sin asignar» combinado con estado `ABIERTA`, entonces el resultado contiene solo incidencias abiertas con `assigned_technician_id` nulo; y cuando selecciona «Asignadas a mí», entonces solo aparecen las incidencias cuyo técnico es el usuario de la sesión, resuelto contra la identidad de sesión y no contra un identificador enviado por el cliente; y cuando indica un técnico inexistente, inactivo o con otro rol, entonces recibe 400 «Técnico no válido».
2. AC-BAN-08: Dado un usuario con rol `EMPLEADO` o `ADMINISTRADOR`, cuando invoca el endpoint de la bandeja completa, cualquiera de sus filtros o restaura una URL de bandeja compartida, entonces recibe un 403 uniforme sin resultados parciales ni pistas sobre la existencia de incidencias ajenas, y la autorización se reevalúa siempre en la API REST y nunca en la SPA (verificado manipulando la petición al margen del frontend).
3. AC-USR-03: Dado un técnico desactivado, cuando un técnico activo despliega el selector de «técnico asignado» en la bandeja, entonces la cuenta desactivada no aparece como opción seleccionable; y cuando ese técnico desactivado figura como asignado histórico de una incidencia, entonces su nombre sigue mostrándose en la fila correspondiente.
**Validaciones:**
1. `assignment_filter` debe pertenecer al enum `TODAS`, `SIN_ASIGNAR`, `ASIGNADAS_A_MI`, `ASIGNADAS_A_OTRO` (por defecto `TODAS`)
2. `assigned_technician_id` debe corresponder a un usuario existente, activo y con rol TECNICO_DE_MANTENIMIENTO; si no existe, está inactivo o tiene otro rol se devuelve 400 «Técnico no válido»
3. El identificador usado para `ASIGNADAS_A_MI` no se acepta del cliente: se resuelve contra la identidad de la sesión
4. Coherencia entre campos: si se envían `assignment_filter` y `assigned_technician_id` con valores contradictorios, prevalece `assigned_technician_id` y se informa al usuario
**Escenarios de error:**
1. El valor del filtro de asignación no pertenece a las opciones admitidas
2. El técnico indicado en el filtro no existe, está desactivado o no tiene rol de técnico de mantenimiento
3. La sesión del usuario ha caducado al aplicar el filtro por asignación
4. El usuario autenticado no tiene el rol de técnico de mantenimiento para filtrar por asignación
**Campos de datos:**
- `assignment_filter` (enum, opcional) — TODAS | SIN_ASIGNAR | ASIGNADAS_A_MI | ASIGNADAS_A_OTRO; default TODAS; ASIGNADAS_A_MI se resuelve con la identidad de la sesión
- `assigned_technician_id` (integer, opcional) — Debe existir, estar activo y tener rol TECNICO_DE_MANTENIMIENTO; prevalece sobre assignment_filter si hay contradicción

### REQ-111 — Cierre de incidencia resuelta con comentario de resolución obligatorio
El TECNICO_DE_MANTENIMIENTO cierra una incidencia en estado «resuelta» aportando un comentario de resolución obligatorio, quedando la incidencia en estado «cerrada». Reglas: solo se admite el cierre desde status = 'resuelta' (desde abierta o en_curso se rechaza); exige resolution_comment no vacío tras trim(); el cierre es atómico (estado, comentario, autor y fecha en la misma transacción); «cerrada» es terminal (CIE-02); la identidad del autor se toma SIEMPRE del usuario de la sesión, nunca del payload (REQ-064). Flujo: técnico abre el detalle de una incidencia resuelta → acción «Cerrar incidencia» → introduce el comentario (COM-01) → confirma → sistema valida estado + comentario + permisos → persiste → devuelve el detalle actualizado. Rama alternativa: otro técnico cerró la incidencia entre la carga y el envío → conflicto de concurrencia. Datos: incident_id (id, obligatorio), status (varchar2, valores de cat_estados_incidencia: abierta|en_curso|resuelta|cerrada), resolution_comment (clob/varchar2, obligatorio, no vacío tras trim, longitud mínima 1) [gap: longitud máxima permitida del comentario], closed_by_user_id (id usuario sesión, obligatorio), closed_at (timestamp, obligatorio, informado por el servidor), assigned_to_user_id (id, puede ser null) [ambigüedad: el RFP no aclara si puede cerrar cualquier TECNICO_DE_MANTENIMIENTO o solo el que se ha autoasignado la incidencia], version (number, concurrencia optimista). Catálogos: cat_estados_incidencia; cat_categorias_incidencia y cat_salas inalterados en el cierre (REQ-102). Validaciones: estado origen = resuelta; comentario presente y no blanco; incidencia existente y visible para el actor; version coincidente. Errores: 400 «Debe indicar un comentario de resolución para cerrar la incidencia»; 409 «La incidencia no está en estado resuelta y no puede cerrarse»; 409 «La incidencia ya fue cerrada por otro usuario»; 403 «No tiene permisos para realizar esta operación» (uniforme, REQ-031); 404 «Incidencia no encontrada». Criterios de aceptación: Dado una incidencia en «resuelta», cuando el técnico la cierra con comentario no vacío, entonces pasa a «cerrada» y se almacenan comentario, closed_by_user_id y closed_at. Dado una incidencia en «resuelta», cuando se intenta cerrar sin comentario o con comentario en blanco, entonces se rechaza con 400 y permanece en «resuelta». Dado una incidencia en «abierta», cuando se intenta cerrarla, entonces se rechaza con 409. Seguridad: ejecuta TECNICO_DE_MANTENIMIENTO; EMPLEADO recibe denegación uniforme (REQ-030, REQ-022, REQ-013, REQ-078); alcance de datos según REQ-029. Evento de dominio: emite IncidenciaCerrada (consumido por notificaciones; no se materializa aquí) [inferido]. Dependencias: REQ-090, REQ-030, REQ-048, COM-01, HIS-01. Prioridad: Must.
**Reglas de negocio:**
1. Una incidencia solo alcanza el estado «cerrada» desde el estado «resuelta»; desde «abierta» o «en_curso» el cierre no es válido
2. Toda incidencia en estado «cerrada» tiene un comentario de resolución no vacío tras eliminar espacios
3. Toda incidencia en estado «cerrada» tiene informados exactamente un autor de cierre y una fecha y hora de cierre
4. No existe una incidencia cerrada con alguno de sus datos de cierre (estado, comentario, autor, fecha) ausente: los cuatro coexisten o ninguno
5. El autor del cierre de una incidencia es el usuario de la sesión que ejecuta la operación, nunca un identificador aportado en la petición
6. La fecha y hora de cierre de una incidencia proviene del servidor, no del cliente
7. Una incidencia tiene como máximo un cierre: dos cierres sobre la misma incidencia son mutuamente excluyentes
8. El autor de un cierre es siempre un usuario con rol TECNICO_DE_MANTENIMIENTO
9. La sala y la categoría de una incidencia son las mismas antes y después de su cierre
10. Un cierre solo es válido si la versión de la incidencia enviada coincide con la versión persistida
**Criterios de aceptación:**
1. AC-CIE-07: Dado un usuario con rol EMPLEADO (reportante o no), cuando invoca directamente la API de cierre o el panel de cierres anteriores de cualquier incidencia, entonces recibe una denegación uniforme (403) que no revela la existencia ni el estado del recurso, y la incidencia no sufre ningún cambio.
2. AC-HIS-01: Dado un cierre ejecutado con éxito, cuando se consulta el historial de la incidencia, entonces la última entrada es la transición from_status = 'resuelta' → to_status = 'cerrada' con changed_by_user_id del usuario de sesión, changed_at de servidor y resolution_comment_ref informado.
3. AC-HIS-02: Dado un intento de cierre rechazado con 400, 403 o 409, cuando se consulta el historial de la incidencia, entonces el número de entradas es idéntico al previo al intento y no existe ninguna entrada nueva.
4. AC-HIS-06: Dado el conjunto de incidencias en estado «cerrada» de la base de datos, cuando se ejecuta la verificación de consistencia, entonces toda incidencia cerrada tiene exactamente una entrada de historial resuelta → cerrada y toda entrada resuelta → cerrada corresponde a una incidencia en estado «cerrada» (cardinalidad 1:1, sin huérfanos en ninguna dirección).
5. AC-CIE-01: Dado una incidencia en estado «resuelta» y un usuario TECNICO_DE_MANTENIMIENTO autenticado, cuando ejecuta el cierre aportando un comentario de resolución no vacío tras trim(), entonces la incidencia queda en estado «cerrada» y se persisten resolution_comment, closed_by_user_id (tomado de la sesión, nunca del payload) y closed_at (timestamp de servidor) en la misma transacción.
6. AC-CIE-02: Dado una incidencia en estado «resuelta», cuando se intenta cerrarla con comentario ausente, vacío o compuesto solo de espacios, entonces la respuesta es 400 con el mensaje «Debe indicar un comentario de resolución para cerrar la incidencia» y la incidencia permanece en «resuelta» sin ningún dato de cierre informado.
7. AC-CIE-03: Dado una incidencia en estado «abierta» o «en curso», cuando se intenta cerrarla, entonces la respuesta es 409 con el mensaje «La incidencia no está en estado resuelta y no puede cerrarse» y status no cambia.
8. AC-CIE-04: Dado dos técnicos que cargan simultáneamente el detalle de la misma incidencia «resuelta», cuando ambos confirman el cierre, entonces exactamente uno persiste el cierre y el segundo recibe 409 «La incidencia ya fue cerrada por otro usuario», sin sobrescribir resolution_comment, closed_by_user_id ni closed_at.
9. AC-CIE-05: Dado un fallo en la escritura de la entrada de historial durante el cierre, cuando la transacción se resuelve, entonces nada se consolida: la incidencia sigue en «resuelta», no existe comentario de cierre ni entrada de historial, y el usuario recibe 500 con «No se ha podido completar el cierre; inténtelo de nuevo».
**Validaciones:**
1. `incident_id` es obligatorio y debe corresponder a una incidencia existente y visible para el actor
2. `resolution_comment` es obligatorio: debe estar presente y no quedar vacío tras aplicar `trim()` (longitud mínima 1 carácter)
3. `status` de destino debe ser un valor del catálogo cerrado `cat_estados_incidencia` (`abierta|en_curso|resuelta|cerrada`)
4. El estado origen de la incidencia debe ser `resuelta`; se rechaza la petición si está en `abierta` o `en_curso`
5. `version` es obligatorio en el payload y debe coincidir con la versión persistida de la incidencia (concurrencia optimista)
6. `closed_by_user_id` se toma del usuario de la sesión: cualquier identidad de autor recibida en el payload se ignora y no se acepta como entrada
7. `closed_at` lo informa el servidor: no se admite una marca temporal de cierre procedente del cliente
**Escenarios de error:**
1. Falta el comentario de resolución o solo contiene espacios en blanco
2. Identificador de incidencia ausente o con formato no válido
3. Sesión no válida o expirada al confirmar el cierre
4. El usuario no tiene permiso para cerrar incidencias (denegación uniforme)
5. La incidencia indicada no existe o no está dentro del alcance del usuario
6. La incidencia no está en estado «resuelta» y no puede cerrarse
7. La incidencia ya fue cerrada por otro usuario mientras se editaba (conflicto de concurrencia)
**Campos de datos:**
- `incident_id` (string, obligatorio) — Debe existir y ser visible para el actor
- `status` (enum, obligatorio) — abierta | en_curso | resuelta | cerrada (cat_estados_incidencia); estado origen obligatorio = resuelta
- `resolution_comment` (string, obligatorio) — No vacío tras trim; longitud mínima 1; longitud máxima no definida (gap)
- `closed_by_user_id` (string, obligatorio) — Se toma siempre de la sesión, nunca del payload
- `closed_at` (datetime, obligatorio) — Informada por el servidor
- `assigned_to_user_id` (string, opcional) — Puede ser nulo; ambigüedad sobre si el cierre exige autoasignación
- `version` (integer, obligatorio) — Debe coincidir con la versión persistida; si no, conflicto 409

### REQ-113 — Consulta de cierres anteriores por sala y categoría desde el detalle
El técnico consulta, desde el detalle de la incidencia que va a cerrar, los cierres anteriores de la misma sala y categoría con su comentario de resolución. Reglas: se listan únicamente incidencias en estado «cerrada» con la misma room_id y category_id que la incidencia en curso, ordenadas por closed_at descendente; la consulta se limita a la ventana de retención vigente de 2 años (REQ-015); es una vista de solo lectura: no permite editar ni reabrir nada. Flujo: técnico abre el detalle → panel «Cierres anteriores en esta sala y categoría» → ve las N últimas entradas → puede abrir el detalle completo de cualquiera de ellas. Datos expuestos por entrada: incident_id, closed_at (timestamp), closed_by_user_name (varchar2), resolution_comment (texto, truncado en la lista con acceso al íntegro), category_id → cat_categorias_incidencia, room_id → cat_salas. Validaciones: sala y categoría deben existir en catálogo, incluidos valores desactivados con uso histórico (REQ-103). Errores: 403 uniforme si el actor no es TECNICO_DE_MANTENIMIENTO; lista vacía (no error) cuando no hay cierres previos, con mensaje «Sin cierres anteriores para esta sala y categoría». Criterios de aceptación: Dado dos incidencias cerradas de la sala S y categoría C, cuando el técnico abre una tercera incidencia de S y C, entonces ve ambas con su comentario de resolución y fecha de cierre. Dado un usuario con rol EMPLEADO, cuando solicita este panel, entonces se deniega de forma uniforme. Seguridad: exclusivo de TECNICO_DE_MANTENIMIENTO (alcance completo de incidencias, REQ-029); el EMPLEADO no accede a incidencias ajenas (REQ-023). Dependencias: CIE-01, REQ-102. [inferido: capacidad derivada del valor de negocio de EPIC-014 —registro histórico de causas y soluciones por sala y categoría para detectar incidencias recurrentes—; el RFP no la enuncia literalmente, de ahí la prioridad Could]. Prioridad: Could.
**Reglas de negocio:**
1. Un cierre anterior listado para una incidencia comparte con ella sala y categoría y está en estado «cerrada»
2. Los cierres anteriores de una sala y categoría se presentan ordenados por fecha de cierre descendente
3. El panel de cierres anteriores no expone incidencias cerradas fuera de la ventana de retención vigente de 2 años
4. Una sala o categoría desactivada en catálogo sigue siendo un valor válido para las incidencias que ya la usan
5. La ausencia de cierres previos para una sala y categoría produce una lista vacía, no una condición de error
6. El panel de cierres anteriores es accesible exclusivamente al rol TECNICO_DE_MANTENIMIENTO
**Criterios de aceptación:**
1. AC-CIE-07: Dado un usuario con rol EMPLEADO (reportante o no), cuando invoca directamente la API de cierre o el panel de cierres anteriores de cualquier incidencia, entonces recibe una denegación uniforme (403) que no revela la existencia ni el estado del recurso, y la incidencia no sufre ningún cambio.
2. AC-CIE-08: Dado dos incidencias ya cerradas de la sala S y categoría C dentro de la ventana de retención de 2 años, cuando un TECNICO_DE_MANTENIMIENTO abre el detalle de una tercera incidencia de la misma sala y categoría, entonces el panel «Cierres anteriores en esta sala y categoría» lista ambas ordenadas por closed_at descendente con su comentario de resolución, autor y fecha; y cuando no existen cierres previos, entonces se muestra «Sin cierres anteriores para esta sala y categoría» sin error.
3. AC-COM-08: Dado un comentario de resolución que contiene marcado (<script>, etiquetas HTML) o caracteres de control, cuando se persiste y se renderiza en el detalle o en el panel de cierres anteriores, entonces el texto se almacena tal cual, sin transformar y se renderiza escapado, sin que se ejecute ningún script en el navegador.
4. AC-HIS-05: Dado una incidencia cerrada hace hasta 2 años (límite de la ventana de retención), cuando cualquier actor dentro de su alcance consulta su historial, entonces la entrada de cierre sigue presente y legible, incluyendo el caso de valores de catálogo (sala, categoría) desactivados con uso histórico.
**Validaciones:**
1. `room_id` y `category_id` de la incidencia en curso deben existir en `cat_salas` y `cat_categorias_incidencia`, admitiendo valores desactivados con uso histórico
2. El filtro de estado de la consulta se restringe al valor `cerrada`; no se admiten otros estados como criterio
3. La ventana temporal de la consulta no puede exceder los 2 años de retención vigente
**Escenarios de error:**
1. El usuario no tiene permiso para consultar los cierres anteriores por sala y categoría
2. La incidencia de referencia no existe o no está dentro del alcance del usuario
3. Parámetros de consulta de sala o categoría con formato no válido
**Campos de datos:**
- `incident_id` (string, obligatorio) — Solo incidencias en estado «cerrada»
- `room_id` (string, obligatorio) — Debe existir en cat_salas, incluidos valores desactivados con uso histórico
- `category_id` (string, obligatorio) — Debe existir en cat_categorias_incidencia, incluidos desactivados con uso histórico
- `closed_at` (datetime, obligatorio) — Orden descendente; limitado a la ventana de retención de 2 años
- `closed_by_user_name` (string, obligatorio) — Nombre del técnico que ejecutó el cierre anterior
- `resolution_comment` (string, obligatorio) — Truncado en el listado, con acceso al texto íntegro

### REQ-118 — Operación única de cambio de estado que valida, aplica atómicamente y rechaza transiciones no válidas
El sistema ofrece una operación única de cambio de estado de una incidencia que valida la transición, la aplica de forma atómica y la rechaza con error explícito cuando no es válida. Reglas: toda transición se solicita por la misma operación POST /api/incidencias/{incident_id}/transiciones; el rol efectivo se resuelve en base de datos, ignorando lo que envíe el cliente (REQ-018); la transición se valida contra `cat_transiciones_incidencia` (CVI-01) antes de cualquier escritura; actualización de estado + inserción en historial (HIST-01) ocurren en una sola transacción: si falla el registro, el estado no varía (AC-7 de la épica); el rechazo no produce efectos laterales ni revela recursos fuera de alcance (REQ-031); concurrencia optimista: la petición envía la versión que el cliente leyó y se rechaza si el registro cambió. Flujo: técnico solicita transición → se resuelve identidad y rol de la sesión → se comprueba alcance y permiso (REQ-030) → se valida el par estado actual → destino → se aplican precondiciones de la transición (`requires_assignee`, `requires_comment`) → commit → se emite el evento y se delega el aviso. Rama alternativa: cualquier validación fallida corta el flujo y devuelve el error sin escribir. Datos: `incidencia.status` (varchar(20), obligatorio, FK `cat_estados_incidencia`), `incidencia.version` (number, obligatorio, incremental), `incidencia.status_changed_at` (timestamp, obligatorio), `incidencia.status_changed_by` (FK usuario, obligatorio); payload `to_status` (varchar(20), obligatorio, valor de catálogo), `expected_version` (number, obligatorio), `transition_comment` (varchar(500), opcional salvo `requires_comment`). Validaciones: `to_status` existe y está activo; `to_status` ≠ estado actual; par declarado; `expected_version` = `version` persistida. Errores: 403 «No tiene permisos para gestionar el ciclo de vida de la incidencia» (REQ-022, REQ-030); 404 uniforme si la incidencia no existe o queda fuera del alcance del rol (REQ-023, REQ-031); 422 «No se puede pasar de «abierta» a «resuelta»: la incidencia debe estar «en curso»»; 422 «La incidencia ya está en ese estado»; 422 «No se admite volver al estado anterior»; 409 «La incidencia ha cambiado mientras la consultaba; recargue el detalle»; 400 «Estado destino no válido». Mensajes en español (REQ-050). AC: Given una incidencia «abierta» asignada, when el técnico pide pasarla a «en curso», then responde 200, `status`=«en curso» y `version` incrementada; Given una incidencia «abierta», when se pide «resuelta», then responde 422 y el estado permanece «abierta»; Given una incidencia en cualquier estado, when se pide una transición hacia atrás no declarada, then responde 422 y el estado no varía; Given un usuario con rol EMPLEADO, when pide cualquier transición, then responde 403 y el estado no varía; Given dos peticiones simultáneas con la misma `expected_version`, when se procesan, then solo una aplica y la otra responde 409. Seguridad: ejecuta exclusivamente TECNICO_DE_MANTENIMIENTO; la decisión de autorización es vinculante en la API REST y nunca en la SPA (REQ-013, REQ-032); la atribución del cambio procede siempre del usuario de la sesión (REQ-064); no exige doble factor. Eventos de dominio: `IncidentStatusChanged` con {incident_id, from_status, to_status, actor_user_id, changed_at}, emitido tras commit [inferido]. Dependencias: CVI-01, HIST-01, REQ-030, REQ-018, REQ-089. Prioridad Must [inferido].
**Reglas de negocio:**
1. Una transición rechazada deja el estado de la incidencia y su historial exactamente como estaban
2. El rol con el que se evalúa una transición es siempre el persistido en base de datos para el usuario de la sesión, nunca el declarado por el cliente
3. Toda transición aceptada incrementa la versión de la incidencia
4. Entre dos solicitudes concurrentes de transición sobre la misma incidencia con idéntica versión esperada, como máximo una se aplica
5. El cambio de estado de una incidencia y su registro en el historial son atómicos: o persisten ambos o ninguno
6. Únicamente el rol técnico de mantenimiento ejecuta transiciones del ciclo de vida de una incidencia
7. Una incidencia fuera del alcance del rol del solicitante es indistinguible de una incidencia inexistente en la respuesta del sistema
8. Todos los mensajes de error de transición se expresan en español
**Criterios de aceptación:**
1. AC-CVI-01: Dado una incidencia en estado «abierta» asignada al técnico de la sesión, cuando dicho técnico ejecuta la secuencia «Iniciar atención» y después «Marcar como resuelta», entonces la incidencia queda en estado «resuelta», in_progress_at y resolved_at han quedado sellados con la fecha-hora de servidor de cada hito, version se ha incrementado en cada transición y el estado mostrado en listado y detalle coincide con el persistido.
2. AC-CVI-02: Dado una incidencia en estado «abierta», cuando un técnico solicita la transición a «resuelta» (salto de estado no declarado en el catálogo de transiciones) o cualquier retroceso del tipo «en curso» → «abierta», entonces la API responde 422 con el mensaje en español que explica la transición requerida, el estado persistido permanece sin cambios y no se genera ninguna entrada de historial.
3. AC-CVI-03: Dado dos peticiones de transición simultáneas sobre la misma incidencia enviadas con el mismo expected_version, cuando el backend las procesa, entonces exactamente una aplica el cambio con 200 y la otra responde 409 con invitación a recargar el detalle, quedando un único registro de historial para ese cambio.
4. AC-CVI-06: Dado un usuario con rol EMPLEADO consultando una incidencia propia, cuando carga el detalle, entonces available_transitions llega vacío, la SPA no muestra ninguna acción de cambio de estado y, si la petición de transición se emite directamente contra la API, esta responde 403 sin efectos laterales; y dado un técnico con una incidencia «abierta» asignada, cuando carga el detalle, entonces la única transición ofrecida es «Iniciar atención».
5. AC-HIST-01: Dado una transición de estado aceptada, cuando finaliza la operación, entonces existe exactamente una entrada nueva en incident_status_history con from_status, to_status, actor de la sesión y fecha-hora de servidor, y el estado persistido de la incidencia coincide con el to_status de la última entrada (invariante verificable sobre el 100 % de las incidencias del entorno de prueba).
**Validaciones:**
1. `to_status` es obligatorio, de máximo 20 caracteres, y debe corresponder a un valor existente y activo de `cat_estados_incidencia`; en caso contrario se responde 400 «Estado destino no válido»
2. `to_status` debe ser distinto del estado actual de la incidencia; en caso contrario se responde 422 «La incidencia ya está en ese estado»
3. `expected_version` es obligatorio, numérico, y debe coincidir con la `version` persistida de la incidencia; si no coincide se responde 409
4. `transition_comment` es opcional y no supera 500 caracteres, salvo que la transición tenga `requires_comment`=true, en cuyo caso es obligatorio
5. El par (estado actual, `to_status`) debe estar declarado en `cat_transiciones_incidencia` antes de cualquier escritura; cualquier par no declarado se rechaza con 422
**Escenarios de error:**
1. Estado destino no válido o versión esperada ausente o mal formada
2. No hay sesión válida para solicitar el cambio de estado
3. No tiene permisos para gestionar el ciclo de vida de la incidencia
4. La incidencia solicitada no existe o no está disponible para el usuario
5. La incidencia ha cambiado mientras la consultaba; recargue el detalle
6. No se puede pasar de «abierta» a «resuelta»: la incidencia debe estar «en curso»
7. La incidencia ya está en ese estado
8. No se admite volver al estado anterior
**Campos de datos:**
- `incident_id` (string, obligatorio) — Debe existir y estar dentro del alcance del rol
- `status` (enum, obligatorio) — abierta / en curso / resuelta / cerrada
- `version` (integer, obligatorio) — Incremental; se incrementa en cada transición aceptada
- `status_changed_at` (datetime, obligatorio) — La fija el servidor; no puede ser futura
- `status_changed_by` (string, obligatorio) — Referencia a usuario; procede siempre de la sesión
- `to_status` (enum, obligatorio) — Valor activo del catálogo; distinto del estado actual; par declarado
- `expected_version` (integer, obligatorio) — Debe coincidir con version persistida; si no, conflicto
- `transition_comment` (string, opcional) — Máx. 500 caracteres; obligatorio si requires_comment

### REQ-119 — El técnico de mantenimiento pasa una incidencia «abierta» a «en curso»
Un técnico de mantenimiento pasa una incidencia «abierta» a «en curso» para dejar constancia de que ha empezado a atenderla. Reglas: la incidencia debe estar en `abierta`; debe tener técnico asignado (`assignee_user_id` no nulo), precondición declarada en `cat_transiciones_incidencia.requires_assignee` —la asignación en sí la aporta la épica de asignación (dependencia, no se redefine aquí)—; solo el técnico asignado puede iniciar la atención [ambigüedad][inferido] (el RFP dice que «un técnico de mantenimiento puede asignarse una incidencia, cambiar su estado…» pero no aclara si un técnico distinto del asignado puede operar sobre ella); al aceptarse se sella `in_progress_at`. Flujo: el técnico abre el detalle de la incidencia → pulsa «Iniciar atención» → confirma → el sistema aplica la transición vía CVI-02 → el nuevo estado queda reflejado en el detalle y en el listado. Datos: `incidencia.in_progress_at` (timestamp, nullable, se fija una sola vez), `incidencia.assignee_user_id` (FK usuario, obligatorio para esta transición). Validaciones: estado origen = abierta; `assignee_user_id` no nulo y usuario activo; `assignee_user_id` = usuario de la sesión. Errores: 422 «La incidencia debe tener un técnico asignado antes de pasarla a «en curso»»; 422 «Solo el técnico asignado puede iniciar la atención de esta incidencia»; 422 «La incidencia no está en estado «abierta»»; 403 para EMPLEADO. AC: Given una incidencia «abierta» asignada al técnico de la sesión, when la pasa a «en curso», then el sistema acepta la transición, sella `in_progress_at` y el nuevo estado aparece en el listado y en el detalle; Given una incidencia «abierta» sin técnico asignado, when se intenta pasar a «en curso», then se rechaza con 422 y el estado permanece «abierta». Seguridad: ejecuta TECNICO_DE_MANTENIMIENTO sobre el conjunto completo de incidencias (REQ-029); EMPLEADO no ve ni ejecuta la acción (REQ-022); el reportante la observa (solo lectura, REQ-024). Efecto: el empleado reportante recibe aviso por correo del cambio de estado (cita RFP: «Cuando una incidencia cambia de estado, el empleado que la reportó debe recibir una notificación por correo electrónico»), resuelto por el módulo de notificaciones (REQ-079, REQ-089); no se materializa aquí como requisito. Eventos de dominio: `IncidentStatusChanged` (`to_status`=«en curso»). Dependencias: CVI-02, CVI-01, REQ-030. Prioridad Must [inferido].
**Reglas de negocio:**
1. Una incidencia sin técnico asignado no puede alcanzar el estado «en curso»
2. El único estado origen admitido para alcanzar «en curso» es «abierta»
3. El técnico que inicia la atención de una incidencia es el técnico asignado a esa incidencia
4. La marca `in_progress_at` de una incidencia se fija una sola vez y no vuelve a alterarse
5. El técnico asignado a una incidencia es un usuario activo
**Criterios de aceptación:**
1. AC-CVI-01: Dado una incidencia en estado «abierta» asignada al técnico de la sesión, cuando dicho técnico ejecuta la secuencia «Iniciar atención» y después «Marcar como resuelta», entonces la incidencia queda en estado «resuelta», in_progress_at y resolved_at han quedado sellados con la fecha-hora de servidor de cada hito, version se ha incrementado en cada transición y el estado mostrado en listado y detalle coincide con el persistido.
2. AC-CVI-04: Dado una incidencia en estado «abierta» sin técnico asignado, cuando un técnico de mantenimiento abre el detalle e intenta iniciar la atención, entonces la acción «Iniciar atención» se muestra deshabilitada con el motivo devuelto por el backend en blocked_reason, y si la petición se fuerza contra la API esta responde 422 «La incidencia debe tener un técnico asignado antes de pasarla a en curso» sin alterar el estado.
3. AC-CVI-05: Dado una incidencia en estado «en curso» asignada a un técnico A, cuando un técnico B distinto del asignado intenta iniciarla o resolverla, entonces la API responde 422 «Solo el técnico asignado puede…» y el estado y el responsable permanecen sin cambios. [ambigüedad del RFP: no aclara si un técnico distinto del asignado puede operar; criterio derivado de la regla inferida en REQ-119/REQ-120 — requiere confirmación del cliente]
**Validaciones:**
1. El estado actual de la incidencia debe ser «abierta» para admitir la petición de inicio de atención
2. `assignee_user_id` de la incidencia debe ser no nulo y corresponder a un usuario activo
3. `assignee_user_id` de la incidencia debe coincidir con el usuario de la sesión que solicita la transición
**Escenarios de error:**
1. No tiene permisos para iniciar la atención de la incidencia
2. La incidencia debe tener un técnico asignado antes de pasarla a «en curso»
3. Solo el técnico asignado puede iniciar la atención de esta incidencia
4. La incidencia no está en estado «abierta»
**Campos de datos:**
- `in_progress_at` (datetime, opcional) — Se sella una sola vez al pasar a «en curso»; no se sobrescribe
- `assignee_user_id` (string, obligatorio) — No nulo, usuario activo y coincidente con el usuario de la sesión

### REQ-120 — El técnico responsable marca una incidencia «en curso» como «resuelta»
El técnico responsable marca una incidencia «en curso» como «resuelta» cuando ha completado la reparación. Reglas: la incidencia debe estar en `en curso`; solo el técnico asignado (`assignee_user_id` = usuario de la sesión) puede resolverla [inferido], coherente con el AC de la épica «el técnico responsable la marca como resuelta»; al aceptarse se sella `resolved_at` y se conserva `assignee_user_id` como responsable de la resolución; el comentario de resolución exigido por el RFP se sitúa en el cierre, no en la resolución (cita RFP: «añadir un comentario de resolución al cerrarla»), por lo que aquí `transition_comment` es opcional [inferido]; «resuelta» no es terminal: el cierre posterior lo opera la épica de cierre y queda fuera de este alcance. Flujo: el técnico responsable abre el detalle de la incidencia en curso → pulsa «Marcar como resuelta» → opcionalmente escribe una nota de avance → confirma → transición aplicada vía CVI-02 → la incidencia queda pendiente de cierre. Datos: `incidencia.resolved_at` (timestamp, nullable, se fija una sola vez), `transition_comment` (varchar(500), opcional, texto libre sin HTML). Validaciones: estado origen = en curso; responsable = usuario de la sesión; longitud del comentario ≤ 500. Errores: 422 «La incidencia debe estar «en curso» para poder resolverse»; 422 «Solo el técnico asignado puede resolver esta incidencia»; 400 «El comentario supera los 500 caracteres»; 403 para EMPLEADO. AC: Given una incidencia «en curso» asignada al técnico de la sesión, when la marca como «resuelta», then el sistema acepta la transición, registra el nuevo estado y sella `resolved_at`; Given una incidencia «resuelta», when se intenta marcarla de nuevo como «resuelta», then se rechaza con 422 y el estado no varía. Seguridad: ejecuta TECNICO_DE_MANTENIMIENTO responsable; EMPLEADO solo observa el resultado en su incidencia (REQ-024, REQ-026). Efecto: aviso por correo al reportante (módulo de notificaciones, REQ-079). Eventos de dominio: `IncidentStatusChanged` (`to_status`=«resuelta»). Dependencias: CVI-02, CVI-03. Prioridad Must [inferido].
**Reglas de negocio:**
1. El único estado origen admitido para alcanzar «resuelta» es «en curso»
2. El técnico que marca una incidencia como resuelta es el técnico asignado a esa incidencia
3. La marca `resolved_at` de una incidencia se fija una sola vez y no vuelve a alterarse
4. El comentario asociado a una transición no supera los 500 caracteres
5. El comentario de resolución es exigible en el cierre de la incidencia, no en su paso a «resuelta»
**Criterios de aceptación:**
1. AC-CVI-01: Dado una incidencia en estado «abierta» asignada al técnico de la sesión, cuando dicho técnico ejecuta la secuencia «Iniciar atención» y después «Marcar como resuelta», entonces la incidencia queda en estado «resuelta», in_progress_at y resolved_at han quedado sellados con la fecha-hora de servidor de cada hito, version se ha incrementado en cada transición y el estado mostrado en listado y detalle coincide con el persistido.
2. AC-CVI-05: Dado una incidencia en estado «en curso» asignada a un técnico A, cuando un técnico B distinto del asignado intenta iniciarla o resolverla, entonces la API responde 422 «Solo el técnico asignado puede…» y el estado y el responsable permanecen sin cambios. [ambigüedad del RFP: no aclara si un técnico distinto del asignado puede operar; criterio derivado de la regla inferida en REQ-119/REQ-120 — requiere confirmación del cliente]
**Validaciones:**
1. El estado actual de la incidencia debe ser «en curso» para admitir la petición de resolución
2. El `assignee_user_id` (técnico responsable) debe coincidir con el usuario de la sesión que solicita la resolución
3. La longitud de `transition_comment` no supera 500 caracteres; si se excede se responde 400
4. `transition_comment` es texto libre sin marcado HTML (se rechaza o se sanea el contenido con etiquetas)
**Escenarios de error:**
1. El comentario supera los 500 caracteres permitidos
2. No tiene permisos para resolver la incidencia
3. La incidencia debe estar «en curso» para poder resolverse
4. Solo el técnico asignado puede resolver esta incidencia
**Campos de datos:**
- `resolved_at` (datetime, opcional) — Se sella una sola vez al pasar a «resuelta»; no se sobrescribe
- `transition_comment` (string, opcional) — Máx. 500 caracteres; texto libre sin HTML

### REQ-122 — Marcas temporales del ciclo de vida y tiempo en el estado actual
El sistema registra y expone las marcas temporales del ciclo de vida y el tiempo que la incidencia lleva en su estado actual. Reglas: cada hito del ciclo de vida sella su marca temporal la primera vez que se alcanza y no se sobrescribe; el tiempo en estado se calcula como diferencia entre la fecha actual y `status_changed_at`, no se persiste; el dato sirve para que el equipo de mantenimiento priorice («saber qué está pendiente, qué está en curso y quién lo atiende»); el EMPLEADO ve estas marcas solo en sus propias incidencias. Datos: `incidencia.opened_at` (timestamp, obligatorio, = fecha de alta), `in_progress_at` (timestamp, nullable), `resolved_at` (timestamp, nullable), `closed_at` (timestamp, nullable, lo sella la épica de cierre), `status_changed_at` (timestamp, obligatorio), derivado `days_in_current_status` (number entero ≥ 0). Validaciones: coherencia cronológica `opened_at` ≤ `in_progress_at` ≤ `resolved_at` ≤ `closed_at`; ninguna marca en el futuro. Presentación: las marcas del hito y `days_in_current_status` se muestran en el detalle de la incidencia y como columna ordenable del listado del técnico (dependencia REQ-025, que ya define ese listado y sus filtros). [gap: el RFP no aporta plazos objetivo, SLA ni umbrales de alerta por estado, por lo que no se define semáforo ni escalado]. Errores: incoherencia cronológica detectada → 500 con rollback de la transición y traza interna; no se expone al usuario más que «No se ha podido completar el cambio de estado». AC: Given una incidencia que pasa a «en curso» el día D, when se consulta al día D+3, then `days_in_current_status` = 3 y `in_progress_at` conserva la fecha D; Given una incidencia que vuelve a cambiar de estado, when se consulta, then `status_changed_at` refleja el último cambio y las marcas de hitos anteriores no se alteran. Seguridad: TECNICO_DE_MANTENIMIENTO sobre todas las incidencias, EMPLEADO solo sobre las propias (REQ-024, REQ-029). Dependencias: CVI-02, CVI-03, CVI-04, REQ-025. Prioridad Should [inferido].
**Reglas de negocio:**
1. Las marcas temporales de hito del ciclo de vida son inmutables una vez selladas
2. Para toda incidencia se cumple `opened_at` ≤ `in_progress_at` ≤ `resolved_at` ≤ `closed_at` entre las marcas existentes
3. Ninguna marca temporal del ciclo de vida es posterior a la fecha actual
4. El tiempo en el estado actual es un valor derivado de `status_changed_at` y nunca se persiste
5. Un empleado accede a las marcas temporales únicamente de las incidencias que ha reportado
**Criterios de aceptación:**
1. AC-CVI-07: Dado una incidencia que pasó a «en curso» el día D, cuando se consulta el día D+3, entonces days_in_current_status es 3, in_progress_at conserva la fecha D sin sobrescritura y el listado del técnico permite ordenar por esa columna.
**Validaciones:**
1. Las marcas temporales del ciclo de vida deben guardar coherencia cronológica: `opened_at` ≤ `in_progress_at` ≤ `resolved_at` ≤ `closed_at`
2. Ninguna marca temporal del ciclo de vida (`opened_at`, `in_progress_at`, `resolved_at`, `closed_at`, `status_changed_at`) puede ser una fecha futura
3. `days_in_current_status` es un número entero mayor o igual que 0
**Escenarios de error:**
1. La incidencia solicitada no existe o no está disponible para el usuario
2. No se ha podido completar el cambio de estado; inténtelo de nuevo
**Campos de datos:**
- `opened_at` (datetime, obligatorio) — Coincide con la fecha de alta; no futura
- `in_progress_at` (datetime, opcional) — opened_at ≤ in_progress_at; no futura; no se sobrescribe
- `resolved_at` (datetime, opcional) — in_progress_at ≤ resolved_at; no futura
- `closed_at` (datetime, opcional) — resolved_at ≤ closed_at; no futura
- `status_changed_at` (datetime, obligatorio) — Fecha del último cambio de estado, base del cálculo de permanencia
- `days_in_current_status` (integer, obligatorio) — Derivado, no persistido; entero ≥ 0

### REQ-147 — Mantener incidencias cerradas en el conjunto operativo consultable durante la ventana de retención
El sistema mantiene las incidencias cerradas dentro del conjunto operativo consultable (listado, detalle e historial) durante toda la ventana de retención. Reglas: el cierre no archiva ni mueve la incidencia (la fila permanece en la tabla operativa con status='cerrada' y sigue recuperable mientras esté dentro de la retención de 2 años); ningún filtro por defecto excluye los estados terminales, de modo que si la consulta no fija status el resultado incluye todos los estados de cat_estados_incidencia y filtrar solo por sala devuelve vigentes y cerradas; no hay borrado físico ni anonimización automática (REQ-047); el alcance de datos no cambia por estar cerrada: EMPLEADO solo las que reportó, TECNICO_DE_MANTENIMIENTO todas (REQ-024, REQ-025, REQ-029). Flujo: el usuario abre la bandeja o su listado, aplica filtros (sala, oficina, categoría, estado, rango de fechas), el sistema resuelve el conjunto sin excluir cerradas, abre el detalle y accede al historial; rama alternativa: incidencia fuera de la ventana de retención no se devuelve (ver RET-02). Datos: incident_id (number, PK), status (varchar, valor de cat_estados_incidencia, obligatorio), room_id/category_id (FK a cat_salas/cat_categorias_incidencia), reported_by (FK usuarios, obligatorio), assigned_to (FK usuarios, nullable), created_at (timestamp, obligatorio), closed_at (timestamp, nullable, solo si status='cerrada'), resolution_comment (clob, obligatorio si status='cerrada'). Catálogos: cat_estados_incidencia, cat_categorias_incidencia, cat_salas, cat_oficinas. Validaciones: status recibido debe pertenecer al catálogo (incluidos valores desactivados con uso histórico, REQ-103); date_from <= date_to; paginación page_size 1..100. Errores: 400 «El filtro de estado no es válido»; 403 uniforme «No tiene permisos para consultar esta incidencia» sin revelar existencia (REQ-023, REQ-031); 404 «La incidencia no existe o ya no está disponible». Criterios de aceptación: Given una incidencia cerrada hace 30 días, When un TECNICO_DE_MANTENIMIENTO filtra la bandeja por status='cerrada', Then aparece en los resultados con sala, categoría, fecha de cierre y técnico asignado. Given el EMPLEADO que la reportó, When abre el detalle de su incidencia cerrada, Then obtiene descripción, clasificación, comentario de resolución e historial completos. Given una consulta filtrada únicamente por sala, When se ejecuta, Then el resultado incluye tanto incidencias vigentes como cerradas dentro de la retención. Given un EMPLEADO, When solicita el detalle de una incidencia cerrada de otro, Then recibe 403 uniforme. Seguridad: requiere sesión válida (REQ-051); roles EMPLEADO (alcance: solo reported_by = usuario de sesión) y TECNICO_DE_MANTENIMIENTO (alcance: todas); ADMINISTRADOR no accede al contenido de incidencias salvo lo indicado en RET-02; sin doble factor (operación de solo lectura, sin datos de categoría especial). Dependencias: REQ-112, REQ-115, REQ-124, REQ-047, REQ-024, REQ-025. Prioridad Must [inferido] — el RFP no declara MoSCoW. Integración: —. Fase: —.
**Reglas de negocio:**
1. Una incidencia cerrada permanece en el conjunto operativo consultable (listado, detalle e historial) mientras esté dentro de su ventana de retención
2. Una consulta que no fija el filtro de estado devuelve incidencias de todos los estados del catálogo, incluidos los terminales
3. Ninguna incidencia se elimina físicamente ni se anonimiza de forma automática dentro de la ventana de retención
4. Una incidencia con estado «cerrada» tiene fecha de cierre y comentario de resolución informados
5. El alcance de consulta de un EMPLEADO se limita a las incidencias que él reportó, con independencia del estado de la incidencia
6. Un TECNICO_DE_MANTENIMIENTO tiene alcance de consulta sobre todas las incidencias dentro de la retención
7. El estado de una incidencia es siempre un valor de `cat_estados_incidencia`, incluidos los valores desactivados con uso histórico
8. La respuesta ante una incidencia fuera del alcance del usuario es indistinguible de la respuesta ante una incidencia inexistente
**Criterios de aceptación:**
1. AC-RET-01: **Dado** una incidencia cerrada hace 30 días dentro de la ventana de retención, **cuando** un TECNICO_DE_MANTENIMIENTO filtra la bandeja por `status='cerrada'`, **entonces** la incidencia aparece en el resultado con sala, categoría, fecha de cierre y técnico asignado, y el registro sigue en la tabla operativa (no archivado ni movido).
2. AC-RET-02: **Dado** una consulta de listado que no fija el parámetro `status`, **cuando** se ejecuta filtrando únicamente por sala, **entonces** el resultado incluye incidencias en todos los estados del catálogo, vigentes y cerradas, sin que ningún filtro por defecto excluya los estados terminales.
3. AC-RET-03: **Dado** una incidencia cerrada reportada por otro empleado, **cuando** un EMPLEADO no reportante solicita su detalle, su listado o la descarga de su adjunto, **entonces** el sistema responde `403` con mensaje uniforme que no revela la existencia de la incidencia; y **dado** el EMPLEADO reportante, **cuando** abre el detalle, **entonces** obtiene descripción, clasificación, comentario de resolución e historial completos.
4. AC-HIST-03: **Dado** una incidencia cerrada hace un año y dentro de la ventana de retención, **cuando** un usuario con alcance consulta su traza, **entonces** se devuelve íntegra y paginada (`page_size` 1..100), sin pérdida de entradas respecto a las registradas en el momento de las operaciones.
**Validaciones:**
1. El valor de `status` recibido como filtro debe pertenecer al catálogo `cat_estados_incidencia`, admitiendo también los valores desactivados con uso histórico
2. El rango de fechas del filtro debe cumplir `date_from <= date_to`
3. El parámetro de paginación `page_size` debe ser un entero entre 1 y 100
**Escenarios de error:**
1. El valor de estado indicado en el filtro no pertenece al catálogo de estados admitidos
2. El rango de fechas del filtro es incorrecto: la fecha inicial es posterior a la final
3. El tamaño de página solicitado está fuera del rango admitido (1 a 100)
4. No tiene permisos para consultar esta incidencia
5. La incidencia no existe o ya no está disponible
**Campos de datos:**
- `incident_id` (integer, obligatorio) — Clave primaria
- `status` (enum, obligatorio) — Valor de `cat_estados_incidencia` (abierta, en curso, resuelta, cerrada), incluidos valores desactivados con uso histórico
- `room_id` (integer, obligatorio) — Referencia a `cat_salas`
- `category_id` (integer, obligatorio) — Referencia a `cat_categorias_incidencia`
- `reported_by` (integer, obligatorio) — Referencia a usuarios
- `assigned_to` (integer, opcional) — Puede estar vacío si nadie se ha autoasignado
- `created_at` (datetime, obligatorio) — Fecha y hora de alta de la incidencia
- `closed_at` (datetime, opcional) — Solo se informa cuando `status = 'cerrada'`
- `resolution_comment` (string, opcional) — Obligatorio cuando `status = 'cerrada'`; texto largo
- `date_from` (date, opcional) — `date_from <= date_to`
- `date_to` (date, opcional) — `date_from <= date_to`
- `page_size` (integer, opcional) — Rango 1..100

### REQ-148 — Calcular y exponer el vencimiento de retención y consulta administrativa de próximas a vencer, sin purga automática
El sistema calcula y expone el vencimiento de retención de cada incidencia y ofrece al administrador la consulta de las próximas a vencer, sin purgar de forma automática. Reglas: cada incidencia tiene retention_expires_at derivado = fecha de inicio del cómputo + 24 meses; [ambigüedad] el RFP dice «se conservan durante 2 años» pero no indica si el cómputo arranca en el alta o en el cierre — se implementa desde created_at y se marca para confirmación; el vencimiento no dispara ningún borrado, anonimización ni archivado: hasta que el cliente defina la política, la incidencia vencida se marca vencida y se sigue conservando; el estado de retención es derivado, nunca editable a mano; la consulta agregada de vencimientos es una vista administrativa, no expone descripción ni comentario de resolución. Flujo: el ADMINISTRADOR abre la vista de retención, filtra por ventana (expiring_within_days) u oficina, obtiene recuento por mes de vencimiento y listado con incident_id, sala, fecha de alta y fecha de vencimiento, y puede abrir el detalle si su rol lo permite. Datos: retention_expires_at (date, derivado, no nulo), retention_status (varchar derivado: vigente | proxima_a_vencer | vencida; proxima_a_vencer cuando faltan ≤ 30 días [inferido]), parámetros expiring_within_days (integer, 1..365), office_id (FK cat_oficinas, opcional). Validaciones: expiring_within_days dentro de rango; no se admite fijar retention_expires_at por API. Errores: 400 «El número de días debe estar entre 1 y 365»; 403 uniforme si el rol no es ADMINISTRADOR (REQ-078). Criterios de aceptación: Given una incidencia creada hace 23 meses, When el ADMINISTRADOR consulta las próximas a vencer en 60 días, Then aparece con retention_status='proxima_a_vencer' y su retention_expires_at. Given una incidencia cuyo retention_expires_at ya pasó, When se consulta el sistema, Then sigue existiendo y se marca vencida, sin haberse borrado ni alterado. Given un TECNICO_DE_MANTENIMIENTO, When invoca la vista de retención, Then recibe 403 uniforme. Seguridad: vista restringida a ADMINISTRADOR [inferido]; el indicador retention_status sí es visible en el detalle para TECNICO_DE_MANTENIMIENTO; sin doble factor. [gap: política de tratamiento de los datos al vencer los 2 años (purga, anonimización o archivado) y hito exacto de inicio del cómputo de retención — el RFP declara el plazo pero no qué ocurre al vencer]. Dependencias: RET-01, REQ-047, REQ-015. Prioridad Must [inferido]. Integración: —. Fase: —.
**Reglas de negocio:**
1. La fecha de vencimiento de retención de una incidencia es exactamente 24 meses posterior a su fecha de alta
2. El vencimiento de la retención no provoca borrado, anonimización ni archivado: la incidencia vencida sigue conservada y marcada como «vencida»
3. El estado de retención de una incidencia es un valor derivado y no admite asignación manual por ninguna vía
4. Una incidencia está «próxima a vencer» cuando faltan 30 días o menos para su fecha de vencimiento de retención
5. La vista de retención y de próximas a vencer es accesible únicamente al rol ADMINISTRADOR
6. La vista agregada de retención no expone la descripción ni el comentario de resolución de ninguna incidencia
7. La ventana de consulta de vencimientos está comprendida entre 1 y 365 días
**Criterios de aceptación:**
1. AC-RET-04: **Dado** una incidencia creada hace 23 meses, **cuando** el ADMINISTRADOR consulta la vista de retención con `expiring_within_days = 60`, **entonces** la incidencia aparece con `retention_status = 'proxima_a_vencer'` y su `retention_expires_at` calculado como `created_at + 24 meses`, sin exponer descripción ni comentario de resolución `[ambigüedad: el hito de inicio del cómputo (alta vs. cierre) está pendiente de confirmación con el cliente]`.
2. AC-RET-05: **Dado** una incidencia cuyo `retention_expires_at` ya ha pasado, **cuando** se consulta el sistema y se inspecciona la base de datos, **entonces** la incidencia sigue existiendo íntegra, se marca `retention_status = 'vencida'` y no se ha ejecutado ningún borrado, anonimización ni archivado automático.
3. AC-RET-06: **Dado** un usuario con rol TECNICO_DE_MANTENIMIENTO o EMPLEADO, **cuando** invoca la vista administrativa de retención y próximas a vencer, **entonces** recibe `403` uniforme; y **cuando** intenta fijar `retention_expires_at` por API, **entonces** la operación se rechaza por tratarse de un campo derivado no editable.
**Validaciones:**
1. El parámetro `expiring_within_days` debe ser un entero comprendido entre 1 y 365
2. El campo `retention_expires_at` es derivado: la petición se rechaza si intenta fijarlo o modificarlo por API
3. El parámetro opcional `office_id` debe corresponder a una oficina existente en `cat_oficinas`
**Escenarios de error:**
1. El número de días de la ventana de vencimiento debe estar entre 1 y 365
2. No tiene permisos para acceder a la vista de retención
3. La fecha de vencimiento de retención es un dato derivado y no puede establecerse manualmente
**Campos de datos:**
- `retention_expires_at` (date, obligatorio) — Derivado = `created_at` + 24 meses; no editable por API
- `retention_status` (enum, obligatorio) — Valores: vigente, proxima_a_vencer, vencida; `proxima_a_vencer` cuando faltan ≤ 30 días; derivado
- `expiring_within_days` (integer, opcional) — Rango 1..365
- `office_id` (integer, opcional) — Referencia a `cat_oficinas`

### REQ-153 — Autoasignación exclusiva de una incidencia sin responsable por un técnico de mantenimiento
Un técnico de mantenimiento se autoasigna una incidencia sin responsable y queda registrado como técnico responsable de forma exclusiva. Reglas: solo son autoasignables las incidencias con status_code no terminal (ABIERTA, EN_CURSO) de cat_estados_incidencia y con assigned_technician_id NULL; una incidencia tiene como máximo un técnico responsable en todo momento; el técnico solo puede asignarse a sí mismo, tomando el identificador del responsable del usuario de la sesión y nunca del cuerpo de la petición (REQ-064, REQ-018); la toma es atómica mediante actualización condicional (UPDATE … WHERE incident_id = :id AND assigned_technician_id IS NULL) resuelta por filas afectadas, de modo que ante dos técnicos concurrentes solo uno gana y el otro recibe conflicto; la asignación no modifica status_code. Flujo: el técnico abre la bandeja completa (REQ-025, REQ-105), filtra por «sin asignar» (REQ-107), abre fila o detalle, ejecuta «Asignármela», confirma y la vista se refresca mostrándole como responsable; rama alternativa: otro técnico la tomó entre el pintado y el clic → conflicto informativo y refresco de la fila. Datos: incident_id (number, obligatorio, existente), assigned_technician_id (number, FK usuario, desde la sesión), assigned_at (timestamp, obligatorio al asignar, instante del servidor), assignment_status (derivado: SIN_ASIGNAR / ASIGNADA), updated_by (number), updated_at (timestamp). Catálogos: cat_estados_incidencia, cat_roles. Validaciones: incidencia existente; estado no terminal; assigned_technician_id NULL; actor con rol TECNICO_DE_MANTENIMIENTO y cuenta activa. Errores: 404 «La incidencia solicitada no existe.»; 409 «Esta incidencia ya está asignada a {nombre del técnico responsable}.»; 409 «No se puede asignar una incidencia resuelta o cerrada.» (REQ-112); 403 denegación uniforme «No tienes permiso para realizar esta acción.» (REQ-031, REQ-078). Criterios de aceptación: Given una incidencia «abierta» sin asignar y un TECNICO_DE_MANTENIMIENTO autenticado, When ejecuta la autoasignación, Then queda con él como responsable y con assigned_at informado; Given una incidencia ya asignada a otro técnico, When un segundo técnico intenta autoasignársela, Then 409, se informa de quién es el responsable actual y la asignación original permanece intacta; Given dos técnicos simultáneos sobre la misma incidencia, When ambas peticiones llegan, Then exactamente una persiste y la otra recibe 409; Given un usuario con rol EMPLEADO, When invoca el endpoint, Then recibe 403 y la incidencia permanece sin asignar. Seguridad: ejecuta exclusivamente TECNICO_DE_MANTENIMIENTO (REQ-030, REQ-022); alcance de datos: el técnico opera sobre el conjunto completo de incidencias (REQ-029); EMPLEADO y ADMINISTRADOR denegados; no aplica doble factor. Eventos de dominio: ninguno; el asiento de trazabilidad se escribe en la misma transacción mediante REQ-126. Dependencias: REQ-126, REQ-105, REQ-107, REQ-121, REQ-030, REQ-064. Marcas: [inferido] prioridad Must; [ambigüedad] no se aclara si autoasignarse implica transición automática a «en curso» (REQ-119), se asume independencia; [gap: no se contempla que un despachador o el ADMINISTRADOR asigne a un tercero]. Integración: —. Prioridad: Must. Fase: —.
**Reglas de negocio:**
1. Una incidencia tiene como máximo un técnico responsable en todo momento
2. Solo son autoasignables las incidencias cuyo estado es no terminal (`ABIERTA` o `EN_CURSO`) y que no tienen técnico responsable
3. El técnico responsable resultante de una autoasignación es siempre el usuario autenticado de la sesión, nunca un tercero indicado en la petición
4. Ante dos autoasignaciones concurrentes sobre la misma incidencia, exactamente una prevalece y la otra queda rechazada por conflicto
5. Toda incidencia con técnico responsable tiene informado el instante de asignación (`assigned_at`)
6. El estado (`status_code`) de una incidencia es independiente de su titularidad: asignar un responsable no altera el estado
7. Solo un usuario con rol `TECNICO_DE_MANTENIMIENTO` y cuenta activa puede figurar como técnico responsable de una incidencia
**Criterios de aceptación:**
1. AC-ASG-01: **Dado** una incidencia en estado no terminal (`ABIERTA` o `EN_CURSO`) sin `assigned_technician_id`, **cuando** un usuario con rol `TECNICO_DE_MANTENIMIENTO` activo ejecuta «Asignármela», **entonces** la incidencia queda con él como único técnico responsable, `assigned_at` se informa con el instante del servidor, `status_code` no cambia y el `assignment_status` derivado pasa de `SIN_ASIGNAR` a `ASIGNADA`.
2. AC-ASG-02: **Dado** una misma incidencia sin responsable y dos técnicos que lanzan la autoasignación simultáneamente, **cuando** ambas peticiones alcanzan el backend, **entonces** exactamente una persiste (verificado por filas afectadas del `UPDATE … WHERE assigned_technician_id IS NULL`) y la otra recibe 409 indicando el nombre del técnico responsable actual, sin que la asignación ganadora se altere; se ejecutan 100 pares concurrentes sin ninguna doble asignación.
3. AC-ASG-03: **Dado** una incidencia ya asignada, o una incidencia en estado terminal (`RESUELTA`/`CERRADA`), o un actor con rol `EMPLEADO` o `ADMINISTRADOR`, **cuando** se invoca el endpoint de autoasignación, **entonces** la petición se rechaza con 409 («ya está asignada» / «no se puede asignar una incidencia resuelta o cerrada») o 403 uniforme según el caso, y el `assigned_technician_id` almacenado permanece inalterado.
4. AC-RESP-01: **Dado** una incidencia asignada al técnico A, **cuando** cualquier usuario con acceso a ella abre su detalle, **entonces** el bloque de responsabilidad muestra el nombre del técnico responsable y la fecha de asignación en formato `dd/mm/aaaa hh:mm` y en español; y **dado** una incidencia sin responsable, **cuando** un técnico abre el detalle, **entonces** ve el estado «Pendiente de asignar» junto a la acción de autoasignarse.
**Validaciones:**
1. `incident_id` es obligatorio en la ruta y debe ser un entero positivo
2. `incident_id` debe corresponder a una incidencia existente en el sistema (si no, 404)
3. El cuerpo de la petición no puede aportar `assigned_technician_id`: si llega, se ignora y el identificador se toma siempre del usuario de la sesión
4. El `status_code` de la incidencia debe pertenecer a los estados no terminales de `cat_estados_incidencia` (`ABIERTA`, `EN_CURSO`)
5. El `assigned_technician_id` de la incidencia debe estar a NULL en el momento de la toma (comprobado de forma condicional en la propia actualización)
6. El actor debe tener rol `TECNICO_DE_MANTENIMIENTO` vigente y cuenta activa
**Escenarios de error:**
1. El identificador de la incidencia indicado no tiene un formato válido
2. No hay una sesión válida para realizar la operación
3. No tienes permiso para realizar esta acción
4. La incidencia solicitada no existe
5. Esta incidencia ya está asignada a otro técnico responsable
6. No se puede asignar una incidencia resuelta o cerrada
**Campos de datos:**
- `incident_id` (integer, obligatorio) — Debe existir; se resuelve antes de la actualización condicional
- `status_code` (enum, obligatorio) — Valores de cat_estados_incidencia; solo no terminales (ABIERTA, EN_CURSO)
- `assigned_technician_id` (integer, obligatorio) — FK usuario; debe ser NULL antes de la toma; se toma del usuario de sesión, nunca del cuerpo de la petición
- `assigned_at` (datetime, obligatorio) — Fijado por el servidor; obligatorio al asignar
- `assignment_status` (enum, opcional) — Derivado: SIN_ASIGNAR / ASIGNADA
- `updated_by` (integer, opcional) — FK usuario
- `updated_at` (datetime, opcional) — Instante de la última modificación de la incidencia

### REQ-154 — Liberación de la incidencia por su técnico responsable con motivo obligatorio
El técnico responsable libera la incidencia que tiene asignada, devolviéndola al conjunto de incidencias tomables. Reglas: solo el assigned_technician_id vigente puede liberar; solo se libera si el status_code es no terminal (ABIERTA, EN_CURSO) — una incidencia RESUELTA o CERRADA conserva su responsable de forma permanente (REQ-112, REQ-087); la liberación exige motivo; tras liberar, assigned_technician_id vuelve a NULL y la incidencia es tomable de nuevo por cualquier técnico vía ASG-01; la liberación no retrocede el estado: una incidencia liberada en «en curso» se presenta como «en curso, sin responsable». Flujo: el técnico abre el detalle de una incidencia de la que es responsable → acción «Liberar» → captura de motivo → confirmación → la incidencia queda sin responsable y la acción «Asignármela» vuelve a estar disponible para el resto. Datos: released_at (timestamp), released_by (number, FK usuario), release_reason (varchar, obligatorio, 10–500 caracteres, texto libre en español). Validaciones: actor = responsable vigente; estado no terminal; motivo presente y dentro de longitud. Errores: 403 «Solo el técnico responsable puede liberar esta incidencia.»; 409 «No se puede liberar una incidencia resuelta o cerrada.»; 409 «La incidencia no tiene técnico asignado.»; 422 «Indica el motivo de la liberación (mínimo 10 caracteres).». Criterios de aceptación: Given una incidencia «en curso» asignada al técnico autenticado, When la libera con motivo, Then queda sin responsable, se registra released_at/release_reason y otro técnico puede autoasignársela; Given una incidencia asignada a otro técnico, When un técnico distinto intenta liberarla, Then 403 y la asignación permanece; Given una incidencia cerrada, When su antiguo responsable intenta liberarla, Then 409 y el responsable histórico se conserva. Seguridad: TECNICO_DE_MANTENIMIENTO responsable; EMPLEADO y ADMINISTRADOR denegados (REQ-030). Eventos de dominio: ninguno; el movimiento se registra en el historial mediante REQ-126. Dependencias: ASG-01, REQ-126, REQ-112, REQ-117. Marcas: [inferido] la capacidad se deduce de que REQ-126 ya contempla «asignación o reasignación de técnico», pero el RFP §2.1 solo enuncia la toma; [gap: el RFP no define la política de liberación/reasignación]; [ambigüedad] si una incidencia «en curso» liberada debe volver a «abierta» — el grafo de REQ-117 no contempla retrocesos. Integración: —. Prioridad: Should. Fase: —.
**Reglas de negocio:**
1. Solo el técnico responsable vigente de una incidencia puede liberarla
2. Una incidencia en estado `RESUELTA` o `CERRADA` conserva su técnico responsable de forma permanente
3. Toda liberación tiene asociado un motivo en texto libre de entre 10 y 500 caracteres
4. Una incidencia liberada queda sin técnico responsable y vuelve a ser autoasignable por cualquier técnico
5. La liberación no retrocede el estado: una incidencia liberada en `EN_CURSO` permanece en `EN_CURSO` sin responsable
**Criterios de aceptación:**
1. AC-ASG-04: **Dado** una incidencia en estado no terminal asignada al técnico autenticado, **cuando** éste la libera aportando un motivo de entre 10 y 500 caracteres, **entonces** `assigned_technician_id` vuelve a NULL, se registran `released_at`, `released_by` y `release_reason`, el `status_code` no retrocede y cualquier otro técnico puede autoasignársela acto seguido; **cuando** el motivo falta o es menor de 10 caracteres, la operación se rechaza con 422 y la asignación se conserva.
2. AC-RESP-04: **Dado** una incidencia que ha pasado por asignación y liberación y cuyo técnico fue posteriormente desactivado, **cuando** un técnico consulta su detalle y su línea temporal, **entonces** el bloque de responsabilidad y el historial son coherentes entre sí (cada asignación y liberación mostrada tiene su asiento correspondiente), el nombre mostrado es el vigente del directorio y, si la incidencia está cerrada, se sigue mostrando el nombre histórico del técnico desactivado.
**Validaciones:**
1. `incident_id` es obligatorio, entero positivo y debe corresponder a una incidencia existente
2. `release_reason` es obligatorio y no puede quedar vacío tras normalizar espacios
3. `release_reason` debe tener entre 10 y 500 caracteres (422 si tiene menos de 10)
4. La incidencia debe tener `assigned_technician_id` informado (no NULL) para poder liberarse
5. El actor de la sesión debe coincidir con el `assigned_technician_id` vigente de la incidencia
6. El `status_code` de la incidencia debe ser no terminal (`ABIERTA`, `EN_CURSO`)
**Escenarios de error:**
1. El identificador de la incidencia indicado no tiene un formato válido
2. No hay una sesión válida para realizar la operación
3. Solo el técnico responsable puede liberar esta incidencia
4. La incidencia solicitada no existe
5. No se puede liberar una incidencia resuelta o cerrada
6. La incidencia no tiene técnico asignado
7. Falta el motivo de la liberación o no alcanza la longitud mínima exigida
**Campos de datos:**
- `incident_id` (integer, obligatorio) — Debe existir y tener responsable vigente
- `status_code` (enum, obligatorio) — Solo no terminales (ABIERTA, EN_CURSO)
- `assigned_technician_id` (integer, obligatorio) — FK usuario; debe coincidir con el usuario de la sesión
- `release_reason` (string, obligatorio) — Texto libre en español, 10–500 caracteres
- `released_at` (datetime, obligatorio) — Fijado por el servidor
- `released_by` (integer, obligatorio) — FK usuario

### REQ-156 — Liberación automática de incidencias no terminales al desactivar un técnico
Las incidencias no terminales de un técnico desactivado quedan liberadas y visibles como pendientes de tomar. Reglas: al desactivar una cuenta con rol TECNICO_DE_MANTENIMIENTO (REQ-042, REQ-086), todas sus incidencias con status_code no terminal quedan liberadas en la misma transacción, con release_reason = TECNICO_DESACTIVADO; las incidencias RESUELTA y CERRADA conservan la atribución al técnico desactivado, que sigue siendo consultable (REQ-087, REQ-088); ningún registro se borra físicamente (REQ-047); la comprobación de impacto previa a la desactivación (REQ-084) debe mostrar al ADMINISTRADOR el número de incidencias no terminales asignadas que se liberarán. Flujo: ADMINISTRADOR inicia la desactivación → pantalla de impacto con recuento de incidencias afectadas → confirma con motivo → el sistema libera las no terminales, registra la liberación en el historial de cada una (REQ-126) y revoca las sesiones del técnico (REQ-086). Datos: assigned_technician_id → NULL, released_at (timestamp), released_by (number, id del ADMINISTRADOR), release_reason (varchar, valor fijo TECNICO_DESACTIVADO). Validaciones: el actor es ADMINISTRADOR; la cuenta objetivo está activa y tiene rol técnico; la liberación es idempotente si se reintenta. Errores: 403 uniforme si el actor no es ADMINISTRADOR (REQ-003); 409 «El usuario ya está desactivado.»; 500 controlado con rollback completo si falla la liberación. Criterios de aceptación: Given un técnico con 3 incidencias «en curso» y 5 «cerradas», When el ADMINISTRADOR lo desactiva, Then las 3 no terminales quedan sin responsable con motivo TECNICO_DESACTIVADO y las 5 cerradas mantienen su atribución; Given esa desactivación, When otro técnico consulta la bandeja filtrando «sin asignar», Then las 3 incidencias liberadas aparecen y puede autoasignárselas; Given la pantalla de impacto previa, When el ADMINISTRADOR va a desactivar, Then ve el recuento exacto de incidencias que se liberarán. Seguridad: ejecuta ADMINISTRADOR (REQ-003); el efecto sobre incidencias es automático del sistema y queda auditado con actor y fecha (REQ-048). Eventos de dominio: ninguno. Dependencias: REQ-042, REQ-084, REQ-086, REQ-087, REQ-126, ASG-02. Marcas: [inferido] el RFP no describe el escenario, pero sin él quedarían incidencias huérfanas; [gap: el RFP no indica qué ocurre con las incidencias en curso de un técnico dado de baja]. Integración: —. Prioridad: Should. Fase: —.
**Reglas de negocio:**
1. Un técnico con cuenta desactivada no es responsable de ninguna incidencia en estado no terminal
2. Las incidencias en estado `RESUELTA` o `CERRADA` conservan la atribución a su técnico responsable aunque la cuenta de este esté desactivada
3. Toda incidencia liberada por desactivación de su técnico tiene `release_reason` con el valor fijo `TECNICO_DESACTIVADO`
4. La desactivación de un técnico y la liberación de sus incidencias no terminales son atómicas: o se producen ambas o ninguna
5. Ningún registro de incidencia ni de usuario se elimina físicamente como consecuencia de una desactivación
6. Una cuenta ya desactivada no admite una nueva desactivación
**Criterios de aceptación:**
1. AC-ASG-06: **Dado** un técnico con incidencias en estado no terminal y otras ya cerradas, **cuando** el `ADMINISTRADOR` desactiva su cuenta, **entonces** la pantalla de impacto previa muestra el recuento exacto de incidencias que se liberarán, tras confirmar todas las no terminales quedan sin responsable con `release_reason = TECNICO_DESACTIVADO` en la misma transacción, las terminales conservan la atribución al técnico desactivado, cada liberación consta en el historial y un fallo parcial provoca rollback completo (ni usuario desactivado ni incidencias liberadas a medias).
2. AC-RESP-04: **Dado** una incidencia que ha pasado por asignación y liberación y cuyo técnico fue posteriormente desactivado, **cuando** un técnico consulta su detalle y su línea temporal, **entonces** el bloque de responsabilidad y el historial son coherentes entre sí (cada asignación y liberación mostrada tiene su asiento correspondiente), el nombre mostrado es el vigente del directorio y, si la incidencia está cerrada, se sigue mostrando el nombre histórico del técnico desactivado.
**Validaciones:**
1. El identificador de usuario objetivo es obligatorio y debe corresponder a una cuenta existente
2. La cuenta objetivo debe estar activa en el momento de la petición (409 si ya está desactivada)
3. La cuenta objetivo debe tener rol `TECNICO_DE_MANTENIMIENTO` para que aplique la liberación de incidencias
4. El motivo de la desactivación es obligatorio en la confirmación del `ADMINISTRADOR`
5. El actor debe tener rol `ADMINISTRADOR`
**Escenarios de error:**
1. No hay una sesión válida para realizar la operación
2. No tienes permiso para realizar esta acción
3. El usuario indicado no existe
4. El usuario ya está desactivado
5. La liberación de las incidencias del técnico no ha podido completarse; no se ha aplicado ningún cambio
**Campos de datos:**
- `assigned_technician_id` (integer, obligatorio) — FK usuario; pasa a NULL solo en incidencias no terminales; se conserva en RESUELTA y CERRADA
- `status_code` (enum, obligatorio) — Solo no terminales (ABIERTA, EN_CURSO)
- `release_reason` (string, obligatorio) — Valor fijo TECNICO_DESACTIVADO
- `released_at` (datetime, obligatorio) — Fijado por el servidor, mismo instante para toda la transacción
- `released_by` (integer, obligatorio) — FK usuario con rol ADMINISTRADOR
- `pending_release_count` (integer, opcional) — Derivado; entero mayor o igual que 0

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-054/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-054/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: validar el modelo de datos que construye esta tarea contra el motor real.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.
El esquema de `TSK-044` ya está APLICADO en este servicio (changelogs Liquibase de su rama mergeada): asume las tablas creadas, no las vuelvas a crear ni las modifiques desde esta tarea.

Esta tarea materializa el MODELO DE DATOS: el motor se levanta VACÍO a propósito, para que valides tu propio changelog contra él. Cómo, exactamente:

```sh
./.mind/TSK-054/liquibase.sh <changelog-maestro>            # aplica (update)
./.mind/TSK-054/liquibase.sh <changelog-maestro> status
./.mind/TSK-054/liquibase.sh <changelog-maestro> rollback-count 99  # reversibilidad
```

Ese script lo genera la plataforma y corre Liquibase como contenedor contra ESTE motor. **NO descargues ni instales Liquibase por tu cuenta**: sus JAR acabarían commiteados en el repo del cliente, y apuntar al `local/*.properties` del arquetipo NO sirve (esa configuración mira a otra BBDD, no a la de tu sesión).

Comprueba las tres cosas antes de entregar: que el `update` termina limpio, que el rollback deshace, y que un segundo `update` es idempotente. Un changelog que nunca se ejecutó no está verificado — y la plataforma lo VUELVE a aplicar por su cuenta tras tu entrega, así que un rojo saldrá igual en el PR.

### Si el entorno no está disponible
Comprueba `.mind/TSK-054/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.