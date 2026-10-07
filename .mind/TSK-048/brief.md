# TSK-048 · Tabla usuario: identidad, rol vigente, estado de cuenta, auditoría y retención

- Componente dueño: `ARC-016`
- Arquetipo del repo: `database-relational` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/facilities/changelogs/0.0.1/ddl/05-usuario.xml`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
Crea la tabla `usuario` con los nombres físicos exactos: user_id PK, full_name (check longitud 2..120), corporate_email varchar2(150) NOT NULL, role_code FK NOT NULL a `cat_rol` (exactamente un rol vigente por usuario: columna única, no tabla de asignaciones múltiples — REQ-012/REQ-035), status FK/check en ('ACTIVO','INACTIVO') default 'ACTIVO', is_active derivado de status, role_updated_at, role_changed_by, created_at NOT NULL default SYSTIMESTAMP, created_by FK NOT NULL, updated_at, updated_by, last_login_at, deactivated_at, deactivated_by FK, deactivation_reason_code FK a `cat_motivo_desactivacion`, deactivation_note, retention_until. Restricciones verificables: índice único funcional sobre `LOWER(TRIM(corporate_email))` (unicidad global insensible a mayúsculas y espacios, REQ-045); check `deactivated_by <> user_id` (nadie se desactiva a sí mismo, AC-USU-03); check `created_by <> user_id`; check de coexistencia de status='INACTIVO' con deactivated_at/deactivated_by/deactivation_reason_code informados y de status='ACTIVO' con esos tres nulos; `retention_until` como columna virtual `deactivated_at + INTERVAL '24' MONTH`, de solo lectura y siempre posterior a deactivated_at (REQ-088/AC-TRZ-03). Índices: (role_code, status), (status, created_at DESC). Oráculo de persistencia contra el Oracle 23ai de `local/docker-compose.yml` (motor real, no dict en memoria): test que inserta 'A@mapfre.com' y ' a@mapfre.com ' y obtiene ORA-00001; test que intenta UPDATE poniendo role_code fuera de `cat_rol` y falla por FK; test que verifica que `retention_until` enviado por el cliente se ignora (columna virtual) y que una fila desactivada hace 23 meses sigue siendo consultable. Persistencia y nombres físicos iguales a los del changelog (`full_name`, no `name`). `update`/`rollback-count` verdes; fichero recogido por el `includeAll` de TSK-01.

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

Tus zonas (`sources/facilities/changelogs/0.0.1/ddl/05-usuario.xml`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

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

### REQ-001 — Autenticación obligatoria con credenciales propias, sin acceso anónimo ni SSO
Toda operación del sistema exige un usuario autenticado con credenciales propias de la aplicación ("usuario y contraseña propios de la aplicación, con roles almacenados en base de datos"); no existe acceso anónimo ni SSO. Dependencia: requisito de autenticación (dominio de autenticación, otra épica).
**Reglas de negocio:**
1. Ninguna operación del sistema es accesible sin un usuario autenticado con credenciales propias de la aplicación: no existe acceso anónimo ni autenticación federada (SSO)
2. Los roles de un usuario residen en la base de datos de la aplicación y no en un proveedor de identidad externo
**Criterios de aceptación:**
1. AC-PERM-07: Dado una petición sin credenciales, con credenciales inválidas o con sesión expirada, cuando alcanza cualquier endpoint de la API, entonces recibe 401 «Sesión no válida o expirada» y no se devuelve ningún dato de negocio; no existe ningún endpoint funcional accesible de forma anónima.
**Escenarios de error:**
1. Petición recibida sin credenciales de aplicación válidas o con sesión expirada

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

### REQ-006 — Consulta del listado de usuarios con su rol vigente, con búsqueda y filtro por rol
El ADMINISTRADOR consulta el listado de usuarios con su rol vigente, con búsqueda y filtro por rol. Reglas: el listado muestra el rol vigente de cada usuario y es la vista sobre la que se verifica el resultado de ROL-01/ROL-02; paginación por defecto 25 (volumetría máxima: 50 usuarios concurrentes, plantilla pequeña). Flujo: admin entra a Usuarios → aplica filtro por `role_code` y/o texto libre sobre nombre/correo → ordena por nombre o por `assigned_at` → navega al detalle. Datos expuestos: `user_id`, `full_name` (varchar, obligatorio), `corporate_email` (varchar, obligatorio, formato email), `role_code`, `assigned_at`. Validaciones: `role_code` del filtro ∈ `cat_roles`; `page_size` ≤ 100. Errores: 400 "Filtro de rol no válido"; 403 si el solicitante no es ADMINISTRADOR. CA: Given usuarios con ambos roles, when el admin filtra por técnico de mantenimiento, then solo aparecen usuarios con ese rol vigente. Given un rol recién cambiado, when el admin recarga el listado, then aparece el rol nuevo. Seguridad: solo ADMINISTRADOR; alcance: todos los usuarios; no se muestran credenciales. Dependencias: ROL-01. Prioridad: Must [inferido].
**Reglas de negocio:**
1. El rol mostrado para cada usuario en el listado es su rol vigente, no uno histórico
2. El tamaño de página del listado de usuarios es como máximo 100, y 25 por defecto
3. Un filtro por rol solo admite valores pertenecientes a `cat_roles`
4. Las credenciales de los usuarios no forman parte de los datos expuestos en el listado
**Criterios de aceptación:**
1. AC-ROL-03: Dado un usuario existente con rol `EMPLEADO`, cuando el ADMINISTRADOR abre su detalle y lo cambia a `TECNICO_MANTENIMIENTO`, entonces la asignación anterior queda cerrada (`valid_to` informado), se crea la nueva asignación vigente, y el listado de usuarios recargado muestra el rol nuevo para ese usuario.
2. AC-ROL-06: Dado un usuario con rol `EMPLEADO` o `TECNICO_MANTENIMIENTO`, cuando invoca cualquiera de los endpoints de asignación de rol, cambio de rol, listado de usuarios, detalle de usuario o histórico de rol, entonces el sistema responde 403 con mensaje uniforme «No tiene permisos para realizar esta acción», no ejecuta la operación y no revela la existencia del recurso solicitado.
3. AC-ROL-07: Dado un conjunto de usuarios con ambos roles del catálogo, cuando el ADMINISTRADOR filtra el listado por `role_code = TECNICO_MANTENIMIENTO` o busca por texto libre sobre nombre o correo, entonces el resultado contiene exclusivamente los usuarios que cumplen el filtro, paginado a 25 por defecto; y un `role_code` de filtro fuera de `cat_roles` devuelve 400 «Filtro de rol no válido».
**Validaciones:**
1. El parámetro de filtro `role_code`, si se informa, debe pertenecer a `cat_roles`; un valor fuera del catálogo se rechaza con 400
2. `page_size` debe ser un entero positivo menor o igual a 100 (valor por defecto 25)
3. El campo de ordenación debe ser uno de los admitidos (`full_name` o `assigned_at`) y el sentido de orden un valor válido (asc/desc)
4. El texto libre de búsqueda sobre nombre/correo debe respetar la longitud máxima admitida del campo de búsqueda
**Escenarios de error:**
1. El filtro de rol indicado no corresponde a un rol válido
2. El tamaño de página solicitado supera el máximo permitido
3. El solicitante no está autorizado para consultar el listado de usuarios
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Identificador del usuario listado
- `full_name` (string, obligatorio) — Dato personal tratado
- `corporate_email` (email, obligatorio) — Formato email; dato personal tratado
- `role_code` (enum, obligatorio) — ∈ `cat_roles`
- `assigned_at` (datetime, opcional) — Fecha de asignación del rol vigente; criterio de ordenación
- `role_filter` (enum, opcional) — ∈ `cat_roles`; error 400 si no pertenece al catálogo
- `search_text` (string, opcional) — Texto libre de búsqueda sobre nombre y correo
- `page_size` (integer, opcional) — Por defecto 25; máximo 100

### REQ-009 — Resolución y aplicación en backend de los permisos efectivos del rol vigente en cada petición
La API REST resuelve y aplica en cada petición los permisos efectivos del usuario a partir de su rol vigente. Reglas: la autorización se evalúa siempre en backend, con independencia de lo que muestre la SPA; EMPLEADO puede crear incidencias y consultar solo las propias; TECNICO_MANTENIMIENTO puede ver el listado completo, autoasignarse, cambiar estado y cerrar cualquier incidencia; solo ADMINISTRADOR gestiona usuarios y roles. Cualquier operación no contemplada para el rol se deniega por defecto (deny by default). Flujo: petición autenticada → se recupera `role_code` vigente de BD → se evalúa la operación contra la matriz de permisos (§5) → se ejecuta o se deniega. Datos: `user_id`, `role_code`, `resource`, `action`, `data_scope` (enum: OWN / ALL). Validaciones: usuario activo con rol vigente (PRE-02); `role_code` ∈ `cat_roles`. Errores: 401 "Sesión no válida o expirada"; 403 "No tiene permisos para realizar esta acción" (mensaje uniforme, sin revelar existencia del recurso). CA: Given un usuario con rol empleado, when solicita el listado completo de incidencias, then recibe 403 y solo puede obtener las propias. Given un usuario con rol técnico, when solicita cualquier incidencia, then accede correctamente. Given un empleado o técnico, when invoca el endpoint de cambio de rol, then recibe 403. Seguridad: núcleo de autorización del sistema; alcance de datos por rol como arriba; sin doble factor. Dependencias: ROL-01, PRE-01. Prioridad: Must [inferido].
**Reglas de negocio:**
1. El resultado de toda decisión de autorización es el que dicta la API REST, con independencia de lo que muestre la SPA
2. Toda operación no contemplada explícitamente para el rol vigente del solicitante queda denegada (deny by default)
3. El alcance de datos de un usuario con rol EMPLEADO sobre incidencias es OWN; el de TECNICO_MANTENIMIENTO es ALL
4. El rol usado para resolver permisos es el rol vigente en base de datos en el momento de la petición
5. El mensaje de denegación es uniforme y no revela la existencia ni el estado del recurso solicitado
**Criterios de aceptación:**
1. AC-PERM-01: Dado un usuario autenticado con rol `EMPLEADO`, cuando solicita el listado completo de incidencias o el detalle de una incidencia que no reportó, entonces recibe 403 «No tiene permisos para realizar esta acción»; y cuando solicita el listado de sus incidencias, entonces recibe 200 con un conjunto de resultados donde el 100 % de los registros tiene `reported_by_user_id` igual a su propio identificador.
2. AC-PERM-02: Dado un usuario autenticado con rol `TECNICO_MANTENIMIENTO`, cuando consulta el listado completo con filtros por sala, categoría y estado, se autoasigna una incidencia ajena y cambia su estado, entonces todas las operaciones devuelven 2xx y quedan persistidas con su identificador como autor.
3. AC-PERM-03: Dado un atacante autenticado con rol `EMPLEADO` que invoca directamente los endpoints de la API omitiendo la SPA (curl/Postman), cuando recorre la totalidad del catálogo de endpoints documentado y no documentado, entonces toda operación no contemplada para su rol se deniega por defecto con 403 y no se detecta ninguna operación autorizada únicamente por ocultamiento en el frontend.
**Validaciones:**
1. La petición debe portar un identificador de sesión/credencial válido y no expirado; en caso contrario 401
2. El `role_code` vigente recuperado para el usuario debe pertenecer a `cat_roles`; un valor no reconocido invalida la resolución de permisos
3. La petición debe identificar de forma completa `resource` y `action` evaluables contra la matriz de permisos; una operación no contemplada se deniega por defecto
**Escenarios de error:**
1. Sesión no válida o expirada
2. La operación solicitada no está permitida para el rol vigente del usuario
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Debe estar activo y con rol vigente
- `role_code` (enum, obligatorio) — ∈ `cat_roles`; nunca se toma de un token no revalidado
- `resource` (string, obligatorio) — Recurso solicitado que se evalúa contra la matriz de permisos
- `action` (string, obligatorio) — Deny by default si no está contemplada para el rol
- `data_scope` (enum, obligatorio) — Valores: OWN, ALL

### REQ-011 — Resolución de permisos con el rol nuevo en la siguiente petición tras un cambio de rol
Tras un cambio de rol, los permisos efectivos del usuario se resuelven con el rol nuevo en su siguiente petición. Reglas: el cambio de rol (ROL-02) invalida las capacidades cacheadas de la sesión activa del usuario afectado; la resolución de permisos usa el rol vigente en BD, nunca un rol embebido y no revalidado en el token; no se requiere que el usuario vuelva a iniciar sesión, pero sí que su siguiente petición se evalúe con el rol nuevo. Flujo: ADMINISTRADOR cambia el rol → el sistema marca la sesión del usuario como "permisos obsoletos" → en la siguiente petición se recargan rol y capacidades → la SPA refresca menú y rutas. Datos: `session_id`, `user_id`, `role_code`, `permissions_refreshed_at` (timestamp). Validaciones: la sesión debe seguir siendo válida; si el rol vigente no existe, se deniega (PRE-02). Errores: 403 "Sus permisos han cambiado; la acción solicitada ya no está autorizada"; 401 si la sesión dejó de ser válida. CA: Given un usuario con sesión abierta cuyo rol acaba de cambiar, when realiza su siguiente petición, then sus permisos se resuelven según el rol nuevo y no según el anterior. Given un empleado promovido a técnico con sesión abierta, when refresca el listado, then pasa a ver el listado completo sin cerrar sesión. Seguridad: evita ventanas de privilegio residual tras una degradación de rol. Eventos de dominio: consume `RoleChanged` [inferido]. Dependencias: ROL-02, PERM-01. Prioridad: Must [inferido].
**Reglas de negocio:**
1. Un cambio de rol invalida las capacidades cacheadas de las sesiones activas del usuario afectado
2. La primera petición posterior a un cambio de rol se evalúa con el rol nuevo y nunca con el anterior
3. Un cambio de rol no invalida la sesión del usuario afectado: no se exige un nuevo inicio de sesión
**Criterios de aceptación:**
1. AC-PERM-05: Dado un usuario con sesión abierta cuyo rol acaba de ser cambiado por el ADMINISTRADOR, cuando realiza su siguiente petición a la API sin cerrar ni reabrir sesión, entonces los permisos se resuelven con el `role_code` vigente en base de datos: un empleado promovido a técnico obtiene 200 en el listado completo y un técnico degradado a empleado obtiene 403 en la misma petición que antes le devolvía 200.
**Validaciones:**
1. `session_id` debe corresponder a una sesión existente y todavía válida; si dejó de serlo, 401
2. El `role_code` vigente en base de datos debe existir al recargar las capacidades; si no existe, la petición se deniega
3. `permissions_refreshed_at` debe ser un timestamp válido generado por el sistema en el momento de la recarga
**Escenarios de error:**
1. Los permisos del usuario han cambiado y la acción solicitada ya no está autorizada
2. La sesión ha dejado de ser válida al reevaluar los permisos
**Campos de datos:**
- `session_id` (uuid, obligatorio) — La sesión debe seguir siendo válida (401 si no)
- `user_id` (uuid, obligatorio) — Usuario cuyo rol ha cambiado
- `role_code` (enum, obligatorio) — ∈ `cat_roles`; si no hay rol vigente se deniega
- `permissions_refreshed_at` (datetime, obligatorio) — Momento de la última recarga de rol y capacidades

### REQ-014 — El alcance de datos depende del rol: EMPLEADO propias, TECNICO todas, ADMIN usuarios
El alcance de datos depende del rol: EMPLEADO accede únicamente a las incidencias que él reportó; TECNICO_MANTENIMIENTO accede a todas las incidencias; ADMINISTRADOR accede a la gestión de usuarios y roles, no a la operación de incidencias. [ambigüedad: el RFP no declara si el administrador puede además operar incidencias]. Aplica a: ROL, PERM, incidencias.
**Reglas de negocio:**
1. Un usuario con rol EMPLEADO accede únicamente a las incidencias que él reportó
2. Un usuario con rol TECNICO_MANTENIMIENTO accede a todas las incidencias de la organización
3. El perfil ADMINISTRADOR accede a la gestión de usuarios y roles, no a la operación de incidencias
**Criterios de aceptación:**
1. AC-USR-02: Dado el conjunto de operaciones del sistema, cuando se ejecuta la matriz completa rol×operación (`EMPLEADO`, `TECNICO_DE_MANTENIMIENTO`, `ADMINISTRADOR`), entonces cada celda coincide con la matriz acordada —el empleado solo reporta y consulta lo propio, el técnico ve/asigna/resuelve cualquier incidencia, el administrador gestiona usuarios y no tiene alcance sobre incidencias— y la autorización se evalúa en la API REST en el 100 % de los casos, nunca solo en la SPA.
**Escenarios de error:**
1. Se solicita un recurso fuera del alcance de datos permitido al rol del solicitante

### REQ-017 — Usuario existente, activo y con rol asignado
El usuario de la sesión debe existir en base de datos, estar activo (is_active = true) y tener exactamente un role_code asignado; sin rol asignado ninguna operación funcional es autorizable. Dependencia: Alta de usuarios y asignación de rol por ADMINISTRADOR (MOD-001).
**Reglas de negocio:**
1. Un usuario del sistema tiene exactamente un role_code asignado
2. Un usuario inactivo (is_active = false) no es autorizable para ninguna operación funcional
**Criterios de aceptación:**
1. AC-ROL-02: Dado un usuario cuya fila tiene `is_active = false`, o sin `role_code` asignado, o con un `role_code` que no pertenece a `cat_roles`, cuando invoca cualquier endpoint funcional, entonces el sistema responde `403` con el mensaje «Tu usuario no tiene permisos para realizar esta acción», no ejecuta ningún efecto y no degrada a un permiso más amplio (comportamiento fail-closed verificado en los tres casos).
**Validaciones:**
1. El `session_user_id` de la petición debe corresponder a un usuario existente en base de datos
2. El usuario de la sesión debe tener `is_active = true`; un usuario inactivo no supera la comprobación de entrada
3. El usuario de la sesión debe tener exactamente un `role_code` asignado (no nulo, no múltiple)
**Escenarios de error:**
1. Usuario de la sesión desactivado; ninguna operación funcional es autorizable
2. Usuario de la sesión sin rol asignado; no se puede autorizar la operación
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Debe existir en la tabla de usuarios
- `is_active` (boolean, obligatorio) — Solo `true` autoriza operaciones funcionales
- `role_code` (enum, obligatorio) — Exactamente un valor de `cat_roles`: EMPLEADO | TECNICO_MANTENIMIENTO

### REQ-018 — Resolución del rol efectivo desde base de datos, ignorando el rol enviado por el cliente
El sistema resuelve el rol efectivo de cada petición leyéndolo de la base de datos para el usuario de la sesión, ignorando cualquier rol enviado por el cliente. Reglas: (1) en cada petición autenticada el backend obtiene role_code desde la tabla de usuarios a partir del session_user_id; (2) si la petición trae rol, alcance o identificador de usuario en body, query o cabecera, se descarta silenciosamente y nunca participa en la decisión; (3) si el usuario no tiene rol o está inactivo se deniega (PRE-02). Flujo: petición → validación de sesión → lectura de role_code → construcción del contexto de autorización (session_user_id, role_code) → evaluación del permiso → ejecución. Datos: session_user_id (uuid, obligatorio), role_code (varchar, obligatorio, valor de cat_roles), is_active (boolean, obligatorio). Catálogos: cat_roles con EMPLEADO y TECNICO_MANTENIMIENTO. Validaciones: role_code debe pertenecer a cat_roles; valor desconocido deniega en vez de degradar a permisos amplios (fail-closed). Errores: sin sesión → 401 «Debes iniciar sesión para continuar»; rol ausente/desconocido/usuario inactivo → 403 «Tu usuario no tiene permisos para realizar esta acción». Aceptación: un EMPLEADO que envía role=TECNICO_MANTENIMIENTO recibe el alcance de EMPLEADO; un usuario con is_active = false recibe 403 sin efectos. Seguridad: la decisión reside en el backend Python (API REST), nunca en la SPA Angular. Dependencias: PRE-01, PRE-02.
**Reglas de negocio:**
1. El rol efectivo de una petición es únicamente el role_code almacenado en base de datos para el usuario de la sesión
2. Un rol, alcance o identificador de usuario suministrado por el cliente no participa en ninguna decisión de autorización
3. Un role_code que no pertenece a cat_roles no concede ningún permiso (fail-closed)
4. Los roles funcionales del catálogo cat_roles son exclusivamente EMPLEADO y TECNICO_MANTENIMIENTO
**Criterios de aceptación:**
1. AC-G-04: Dado el conjunto completo de operaciones funcionales del sistema (`INCIDENT_CREATE`, `INCIDENT_LIST_OWN`, `INCIDENT_LIST_ALL`, `INCIDENT_VIEW`, `INCIDENT_HISTORY_VIEW`, `INCIDENT_ASSIGN_SELF`, `INCIDENT_STATUS_CHANGE`, `INCIDENT_CLOSE_WITH_COMMENT`, `USER_MANAGE`), cuando se invoca cada operación con una sesión de rol `EMPLEADO` y con una sesión de rol `TECNICO_MANTENIMIENTO`, entonces el resultado (autorizado / `403` / alcance `OWN` o `ALL`) coincide al 100 % con la matriz rol × operación acordada, sin ninguna desviación y sin efecto lateral en las denegaciones.
2. AC-ROL-01: Dado un usuario con `role_code = EMPLEADO` en base de datos, cuando envía una petición al listado de incidencias incluyendo `role=TECNICO_MANTENIMIENTO` (o `data_scope=ALL`, o un `session_user_id` ajeno) en body, query o cabecera, entonces el sistema resuelve el rol leyéndolo de base de datos, descarta el valor recibido del cliente y devuelve exclusivamente las incidencias propias del usuario de la sesión.
3. AC-ROL-02: Dado un usuario cuya fila tiene `is_active = false`, o sin `role_code` asignado, o con un `role_code` que no pertenece a `cat_roles`, cuando invoca cualquier endpoint funcional, entonces el sistema responde `403` con el mensaje «Tu usuario no tiene permisos para realizar esta acción», no ejecuta ningún efecto y no degrada a un permiso más amplio (comportamiento fail-closed verificado en los tres casos).
**Validaciones:**
1. `session_user_id` es obligatorio y debe tener formato uuid
2. `role_code` es obligatorio y debe pertenecer al catálogo `cat_roles` (`EMPLEADO`, `TECNICO_MANTENIMIENTO`); un valor desconocido no se acepta (fail-closed)
3. Los campos `rol`, `alcance` o identificador de usuario recibidos en body, query o cabecera se descartan silenciosamente y no se admiten como entrada de la decisión
**Escenarios de error:**
1. Solicitud sin sesión válida al resolver el contexto de autorización
2. Rol del usuario de la sesión ausente o no reconocido; se deniega por defecto
3. Usuario de la sesión inactivo al evaluar la operación
**Campos de datos:**
- `session_user_id` (uuid, obligatorio) — Se obtiene de la sesión; nunca de body/query/cabecera
- `role_code` (enum, obligatorio) — Valor de `cat_roles`: EMPLEADO | TECNICO_MANTENIMIENTO; valor desconocido → deniega (fail-closed)
- `is_active` (boolean, obligatorio) — `false` → 403 sin efecto

### REQ-019 — Aplicación inmediata del cambio de rol en peticiones posteriores
Cuando el administrador cambia el rol de un usuario, el nuevo rol pasa a aplicarse a sus peticiones posteriores sin necesidad de intervención manual sobre la sesión. Reglas: (1) el permiso efectivo se evalúa siempre contra el role_code vigente en base de datos en el momento de la petición, no contra un rol congelado al iniciar sesión; (2) degradar el rol de TECNICO_MANTENIMIENTO a EMPLEADO revoca de inmediato el acceso al listado completo y a las acciones de gestión; (3) desactivar un usuario (is_active = false) invalida el efecto de su sesión en la siguiente petición. Flujo: administrador actualiza rol → siguiente petición → se relee role_code → se recalcula el alcance. Datos: role_code, role_updated_at (timestamp, obligatorio en el cambio), is_active. Validaciones: el cambio de rol no puede dejar al usuario sin role_code. Errores: 403 «Tu rol ha cambiado; vuelve a iniciar sesión». Gap: el RFP no indica si la sesión activa debe cerrarse al cambiar el rol; se implementa recálculo por petición. Aceptación: un técnico con sesión abierta degradado a EMPLEADO recibe 403 en el listado completo y conserva el listado propio. Seguridad: el cambio de rol solo lo ejecuta ADMINISTRADOR (MOD-001). Dependencias: ROL-01; alta/edición de usuarios en MOD-001.
**Reglas de negocio:**
1. El permiso efectivo de una petición se corresponde con el role_code vigente en base de datos en el instante de esa petición, no con el vigente al iniciar la sesión
2. Un usuario no puede quedar sin role_code tras un cambio de rol
3. Tras la degradación de TECNICO_MANTENIMIENTO a EMPLEADO, el acceso al listado completo y a las acciones de gestión deja de estar disponible desde la siguiente petición
**Criterios de aceptación:**
1. AC-ROL-03: Dado un usuario con rol `TECNICO_MANTENIMIENTO` y sesión abierta, cuando el administrador cambia su `role_code` a `EMPLEADO` y el usuario lanza su siguiente petición, entonces la petición a `INCIDENT_LIST_ALL` devuelve `403` sin necesidad de intervención manual sobre la sesión, mientras que el listado propio sigue accesible con alcance `OWN`.
**Validaciones:**
1. En una operación de cambio de rol, el `role_code` resultante es obligatorio: no se admite un cambio que deje al usuario sin rol
2. `role_updated_at` es obligatorio (timestamp) en toda operación de cambio de rol
**Escenarios de error:**
1. El rol vigente del usuario ya no permite la operación solicitada tras un cambio de rol
2. El usuario ha sido desactivado y su siguiente petición deja de autorizarse
**Campos de datos:**
- `role_code` (enum, obligatorio) — Valor de `cat_roles`; el cambio no puede dejar al usuario sin rol
- `role_updated_at` (datetime, obligatorio) — Obligatorio al registrar el cambio de rol
- `is_active` (boolean, obligatorio) — `false` → siguiente petición denegada

### REQ-028 — Permisos y alcance derivados solo del rol almacenado en base de datos
El alcance y los permisos de cualquier petición se derivan exclusivamente del rol almacenado en base de datos para el usuario de la sesión; ningún dato suministrado por el cliente altera la decisión. Aplica a: ROL, PERM, ALC y toda operación de MOD-002.
**Reglas de negocio:**
1. El alcance y los permisos de cualquier petición se derivan exclusivamente del rol almacenado en base de datos para el usuario de la sesión
**Criterios de aceptación:**
1. AC-ROL-01: Dado un usuario con `role_code = EMPLEADO` en base de datos, cuando envía una petición al listado de incidencias incluyendo `role=TECNICO_MANTENIMIENTO` (o `data_scope=ALL`, o un `session_user_id` ajeno) en body, query o cabecera, entonces el sistema resuelve el rol leyéndolo de base de datos, descarta el valor recibido del cliente y devuelve exclusivamente las incidencias propias del usuario de la sesión.
2. AC-ALC-02: Dado un usuario con rol `EMPLEADO`, cuando solicita el listado propio enviando `reporter_user_id` de otro usuario, entonces el parámetro se ignora, la respuesta contiene únicamente sus propias incidencias y el sistema no devuelve error (para no revelar la existencia de otros usuarios).
**Escenarios de error:**
1. El rol enviado por el cliente se ignora y el rol vigente del usuario no autoriza la operación

### REQ-034 — La cuenta autenticada debe estar en estado ACTIVO
La cuenta del usuario autenticado debe estar en estado `ACTIVO`; una cuenta desactivada no puede iniciar sesión ni ejecutar ninguna operación. Dependencia: USR-06. auth_type: SESSION. data_scope: own_only.
**Reglas de negocio:**
1. Una cuenta en estado INACTIVO no puede iniciar sesión ni ejecutar ninguna operación
**Criterios de aceptación:**
1. AC-USR-08: **Dado** un usuario `ACTIVO` con incidencias reportadas, **cuando** el ADMINISTRADOR lo desactiva y confirma el aviso de pérdida de acceso, **entonces** su `status` pasa a `INACTIVO`, se informan `deactivated_at`/`deactivated_by`, su sesión activa se invalida, deja de poder autenticarse y sus incidencias siguen consultables mostrando su nombre; **cuando** el ADMINISTRADOR intenta desactivar su propia cuenta, **entonces** el sistema lo impide con 422; **cuando** se reactiva un usuario `INACTIVO`, **entonces** recupera el acceso con su rol anterior.
2. AC-USR-09: **Dado** cualquier endpoint funcional del sistema, **cuando** se invoca sin sesión válida, **entonces** responde 401; **cuando** se invoca con la sesión de un usuario cuya cuenta está en estado `INACTIVO`, **entonces** responde 403 y no ejecuta la operación; **cuando** se invoca con un usuario sin rol asignado, **entonces** la autorización no se resuelve y la operación se deniega.
**Escenarios de error:**
1. La cuenta del usuario está desactivada y no puede operar
**Campos de datos:**
- `status` (enum, obligatorio) — ACTIVO | INACTIVO; solo ACTIVO puede iniciar sesión y operar

### REQ-037 — Alta de usuario por el ADMINISTRADOR con nombre, correo corporativo y rol
El ADMINISTRADOR da de alta un usuario indicando nombre, correo corporativo y rol. Reglas: (1) solo el ADMINISTRADOR puede crear usuarios; (2) `full_name`, `corporate_email` y `role_code` son obligatorios en el mismo acto de alta — no se admite un usuario sin rol (PRE-03); (3) `corporate_email` es único en todo el sistema, comparado en minúsculas y sin espacios (RN-01); (4) el usuario se crea en estado `ACTIVO`; (5) no existe vía por la que un usuario se cree a sí mismo (ROL-02). Flujo: abre el formulario de alta → introduce nombre, correo y selecciona rol de `cat_roles` → confirma → el sistema valida formato, obligatoriedad y unicidad → persiste el usuario → genera la credencial inicial (USR-02) → aparece en el listado (USR-03). Rama alternativa: si la validación falla, el formulario conserva los datos y señala el campo erróneo. Datos: `full_name` (varchar(120), obligatorio, 2–120 caracteres, normaliza espacios); `corporate_email` (varchar(150), obligatorio, formato email válido, único, en minúsculas) [gap: no se indica si debe validarse un dominio corporativo concreto]; `role_code` (FK `cat_roles`, obligatorio); `status` (FK `cat_user_status`, inicial `ACTIVO`) [inferido]; `created_at` (timestamp, automático); `created_by` (FK `users.id`, automático). Catálogos: `cat_roles` (EMPLEADO, TECNICO_MANTENIMIENTO, ADMINISTRADOR [ambigüedad]); `cat_user_status` (ACTIVO, INACTIVO) [inferido]. Validaciones: nombre no vacío; email con formato válido y ≤150; rol perteneciente a `cat_roles`. Errores: 400 «Indica el nombre del usuario» / «Indica el correo corporativo» / «Selecciona un rol»; 409 «Ya existe un usuario con ese correo corporativo»; 403 «No tienes permisos para dar de alta usuarios». Criterios de aceptación: Dado un ADMINISTRADOR autenticado, cuando da de alta un usuario con nombre, correo y rol válidos, entonces el usuario queda registrado y aparece en el listado con esos tres datos. Dado un alta sin nombre o sin correo, cuando se confirma, entonces se rechaza indicando el dato obligatorio que falta y no crea nada. Dado un correo ya registrado, cuando se intenta un segundo alta, entonces devuelve 409 y no crea el usuario. Seguridad: solo ADMINISTRADOR; EMPLEADO y TECNICO-DE-MANTENIMIENTO reciben 403; sin doble factor; datos personales limitados a nombre y correo (RN-06). Eventos: `UserCreated`. Dependencias: ROL-01, USR-02. Prioridad: Must. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Solo un usuario con rol ADMINISTRADOR puede crear usuarios
2. Un usuario no puede existir sin nombre completo, correo corporativo y rol
3. Todo usuario recién creado queda en estado ACTIVO
4. Ningún usuario es creador de sí mismo: el actor del alta es siempre distinto del usuario creado
5. El nombre completo de un usuario tiene entre 2 y 120 caracteres
6. El correo corporativo de un usuario se almacena siempre en minúsculas y con longitud máxima de 150 caracteres
7. El rol de un usuario pertenece siempre al catálogo cerrado cat_roles
**Criterios de aceptación:**
1. AC-USR-01: **Dado** un ADMINISTRADOR autenticado, **cuando** da de alta un usuario con `full_name`, `corporate_email` y `role_code` válidos, **entonces** el usuario queda persistido en estado `ACTIVO`, con `created_at` y `created_by` informados automáticamente, y aparece en el listado de usuarios con nombre, correo y rol.
2. AC-USR-02: **Dado** un formulario de alta de usuario, **cuando** se confirma sin nombre, sin correo o sin rol, **entonces** el sistema responde 400 con el mensaje del campo concreto que falta, conserva los datos introducidos y no crea ningún registro; y **cuando** se confirma con un `corporate_email` ya existente (comparado en minúsculas y sin espacios), **entonces** responde 409 «Ya existe un usuario con ese correo corporativo» y no crea nada.
3. AC-ROL-01: **Dado** el acto de alta de un usuario, **cuando** el ADMINISTRADOR lo confirma, **entonces** el usuario queda con exactamente un `role_code` vigente del catálogo `cat_roles`, nunca cero ni más de uno, y no existe ninguna ruta (UI ni API) que permita persistir un usuario sin rol.
**Validaciones:**
1. `full_name` es obligatorio y no puede estar vacío ni contener solo espacios
2. `full_name` debe tener entre 2 y 120 caracteres, normalizando los espacios redundantes antes de validar
3. `corporate_email` es obligatorio y no puede estar vacío
4. `corporate_email` debe cumplir un formato de email válido y no superar 150 caracteres
5. `corporate_email` se normaliza a minúsculas y sin espacios antes de comprobar que no exista ya otro usuario con ese mismo valor
6. `role_code` es obligatorio en el mismo acto de alta y debe corresponder a un valor existente del catálogo `cat_roles`
**Escenarios de error:**
1. Falta el nombre del usuario
2. Falta el correo corporativo
3. El correo corporativo no tiene un formato válido o supera la longitud admitida
4. El nombre no respeta la longitud admitida
5. No se ha seleccionado ningún rol para el usuario
6. El rol indicado no pertenece al catálogo de roles disponibles
7. Ya existe un usuario con ese correo corporativo
8. El solicitante no tiene permisos para dar de alta usuarios
9. Petición de alta sin sesión iniciada
**Campos de datos:**
- `full_name` (string, obligatorio) — 2–120 caracteres; no vacío; se normalizan espacios
- `corporate_email` (email, obligatorio) — Formato de email válido; máx. 150; único en el sistema; se almacena en minúsculas
- `role_code` (enum, obligatorio) — Debe existir en cat_roles (EMPLEADO, TECNICO_MANTENIMIENTO, ADMINISTRADOR)
- `status` (enum, obligatorio) — Valor inicial ACTIVO; dominio ACTIVO | INACTIVO
- `created_at` (datetime, obligatorio) — Automático, no editable
- `created_by` (reference, obligatorio) — Referencia al usuario autenticado; automático

### REQ-039 — Listado de usuarios con búsqueda y filtros para el ADMINISTRADOR
El ADMINISTRADOR consulta el listado de usuarios dados de alta con búsqueda y filtros. Reglas: (1) el listado muestra todos los usuarios de la organización, activos e inactivos; (2) por defecto solo los `ACTIVO`, con conmutador para incluir inactivos; (3) cada fila muestra nombre, correo corporativo, rol y estado. Flujo: entra en «Usuarios» → carga la primera página ordenada por `created_at` descendente → puede buscar por texto y filtrar por rol y estado → puede abrir el detalle (USR-04). Datos de consulta: `q` (texto libre, opcional, busca en `full_name` y `corporate_email`, insensible a mayúsculas y acentos); `role_code` (opcional, FK `cat_roles`); `status` (opcional, FK `cat_user_status`); `page` (int, ≥1), `page_size` (int, por defecto 25, máx 100). Validaciones: los valores de filtro deben existir en su catálogo; paginación con límites. Errores: 403 «No tienes permisos para consultar los usuarios»; 400 «Filtro no válido»; sin resultados → estado vacío «Ningún usuario coincide con los filtros aplicados» (no es error). Criterios de aceptación: Dado un ADMINISTRADOR autenticado, cuando consulta el listado, entonces obtiene todos los usuarios con nombre, correo corporativo y rol asignado. Dado un filtro por rol TECNICO_MANTENIMIENTO, cuando se aplica, entonces solo se devuelven usuarios con ese rol. Dado un usuario con rol EMPLEADO, cuando invoca el listado, entonces recibe 403. Seguridad: solo ADMINISTRADOR; acceso a datos personales restringido a este rol. Dependencias: USR-01. Prioridad: Must. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Solo un usuario con rol ADMINISTRADOR puede consultar el censo de usuarios
2. Un usuario INACTIVO solo aparece en el listado si se activa explícitamente el conmutador de inclusión de inactivos
3. El tamaño de página del listado de usuarios no supera los 100 registros
4. Un valor de filtro por rol o por estado solo es válido si existe en su catálogo
**Criterios de aceptación:**
1. AC-USR-01: **Dado** un ADMINISTRADOR autenticado, **cuando** da de alta un usuario con `full_name`, `corporate_email` y `role_code` válidos, **entonces** el usuario queda persistido en estado `ACTIVO`, con `created_at` y `created_by` informados automáticamente, y aparece en el listado de usuarios con nombre, correo y rol.
2. AC-USR-05: **Dado** un ADMINISTRADOR en la pantalla «Usuarios», **cuando** abre el listado sin filtros, **entonces** obtiene solo usuarios `ACTIVO` ordenados por `created_at` descendente con paginación por defecto de 25 (máx 100); **cuando** aplica el filtro `role_code = TECNICO_MANTENIMIENTO`, **entonces** solo se devuelven usuarios con ese rol; **cuando** ningún usuario coincide, **entonces** se muestra el estado vacío «Ningún usuario coincide con los filtros aplicados» y no un error.
**Validaciones:**
1. `role_code` recibido como filtro, si se informa, debe existir en el catálogo `cat_roles`
2. `status` recibido como filtro, si se informa, debe existir en el catálogo `cat_user_status`
3. `page` debe ser un entero mayor o igual que 1
4. `page_size` debe ser un entero positivo con valor por defecto 25 y máximo 100
5. `q` es un texto libre opcional; la búsqueda se aplica de forma insensible a mayúsculas y acentos
**Escenarios de error:**
1. El solicitante no tiene permisos para consultar el censo de usuarios
2. Filtro de rol o de estado no reconocido
3. Parámetros de paginación fuera de los límites admitidos
**Campos de datos:**
- `q` (string, opcional) — Insensible a mayúsculas y acentos
- `role_code` (enum, opcional) — Debe existir en cat_roles
- `status` (enum, opcional) — Debe existir en cat_user_status; por defecto solo ACTIVO
- `page` (integer, opcional) — ≥ 1
- `page_size` (integer, opcional) — Por defecto 25; máximo 100

### REQ-040 — Ficha de detalle de usuario con datos, rol y trazabilidad
El ADMINISTRADOR consulta la ficha de detalle de un usuario con sus datos, su rol y su trazabilidad. Reglas: (1) la ficha muestra datos de identidad, rol vigente, estado y metadatos de auditoría; (2) muestra el registro de cambios de rol y de estado, con actor y fecha (RN-04); (3) no muestra nunca la contraseña ni su hash. Flujo: desde el listado (USR-03) abre un usuario → ve la ficha → desde ella puede editar (USR-05), cambiar rol (ROL-01) o desactivar/reactivar (USR-06). Datos mostrados: `full_name`, `corporate_email`, `role_code`, `status`, `created_at`, `created_by`, `updated_at`, `updated_by`, `last_login_at`; colección `user_audit_entries` (`changed_at`, `changed_by`, `field_changed`, `old_value`, `new_value`). Errores: 404 «El usuario no existe»; 403 «No tienes permisos para consultar este usuario». Criterios de aceptación: Dado un ADMINISTRADOR, cuando abre un usuario existente, entonces ve su nombre, correo, rol, estado y el histórico de cambios con quién y cuándo los hizo. Dado un identificador inexistente, cuando se consulta, entonces responde 404 sin revelar información. Seguridad: solo ADMINISTRADOR. Dependencias: USR-01, ROL-01. Prioridad: Should. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. La ficha de un usuario nunca expone su contraseña ni el hash de esta
2. Todo cambio de rol o de estado de un usuario es consultable en su ficha con actor y fecha
**Criterios de aceptación:**
1. AC-USR-06: **Dado** un ADMINISTRADOR, **cuando** abre la ficha de detalle de un usuario existente, **entonces** ve nombre, correo, rol vigente, estado, `created_at`/`created_by`, `updated_at`/`updated_by`, `last_login_at` y la colección de entradas de auditoría con actor y fecha, sin que aparezca en ningún punto la contraseña ni su hash; **cuando** el identificador no existe, **entonces** responde 404 sin revelar información del recurso.
**Escenarios de error:**
1. El usuario solicitado no existe
2. El solicitante no tiene permisos para consultar la ficha de un usuario
**Campos de datos:**
- `full_name` (string, obligatorio) — Solo lectura en esta vista
- `corporate_email` (email, obligatorio) — Correo corporativo mostrado en la ficha
- `role_code` (enum, obligatorio) — Valor de cat_roles
- `status` (enum, obligatorio) — ACTIVO | INACTIVO
- `created_at` (datetime, obligatorio) — Fecha de alta del usuario
- `created_by` (reference, obligatorio) — Autor del alta
- `updated_at` (datetime, opcional) — Nulo si nunca se modificó
- `updated_by` (reference, opcional) — Nulo si nunca se modificó
- `last_login_at` (datetime, opcional) — Nulo si nunca ha accedido
- `changed_at` (datetime, obligatorio) — Colección user_audit_entries
- `changed_by` (reference, obligatorio) — Colección user_audit_entries
- `field_changed` (string, obligatorio) — Colección user_audit_entries
- `old_value` (string, opcional) — Colección user_audit_entries; nunca contiene contraseña ni hash
- `new_value` (string, opcional) — Colección user_audit_entries; nunca contiene contraseña ni hash

### REQ-041 — Modificación de los datos identificativos de un usuario existente
El ADMINISTRADOR modifica los datos identificativos de un usuario existente. Reglas: (1) son editables `full_name` y `corporate_email`; (2) el cambio de correo mantiene la unicidad global (RN-01) y pasa a ser el destino de las notificaciones futuras; (3) el cambio de rol no se hace aquí sino en ROL-01; (4) toda modificación registra actor y fecha (RN-04); (5) no se modifica un usuario `INACTIVO` sin reactivarlo antes. Flujo: abre la ficha (USR-04) → edita nombre y/o correo → confirma → validación → persistencia → confirmación en pantalla. Datos: `full_name` (mismas constraints que USR-01), `corporate_email` (mismas constraints, único), `updated_at`, `updated_by`. Validaciones: al menos un campo modificado; formato de email; unicidad excluyendo al propio usuario. Errores: 409 «Ya existe un usuario con ese correo corporativo»; 400 «El nombre no puede estar vacío»; 409 «El usuario está desactivado»; 403 sin permisos; 404 usuario inexistente. Criterios de aceptación: Dado un ADMINISTRADOR, cuando cambia el correo de un usuario por uno no utilizado, entonces se guarda y las notificaciones posteriores se envían al nuevo correo. Dado un correo ya usado por otro usuario, cuando se guarda, entonces rechaza con 409 y no altera el registro. Seguridad: solo ADMINISTRADOR; alcance: cualquier usuario. Eventos: `UserUpdated`. Dependencias: USR-01, USR-04. Prioridad: Should. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Los únicos datos identificativos modificables de un usuario son su nombre completo y su correo corporativo
2. El rol de un usuario no es modificable por la vía de edición de datos identificativos
3. Un usuario en estado INACTIVO no admite modificación de sus datos mientras no sea reactivado
4. El correo corporativo vigente de un usuario es el único destino de sus notificaciones posteriores
**Criterios de aceptación:**
1. AC-USR-07: **Dado** un usuario `ACTIVO`, **cuando** el ADMINISTRADOR modifica su nombre y/o su correo por uno no utilizado, **entonces** el cambio se persiste, queda registrado con actor y fecha, y las notificaciones posteriores se envían al nuevo correo; **cuando** el correo introducido ya pertenece a otro usuario, **entonces** responde 409 y no altera el registro; **cuando** el usuario está `INACTIVO`, **entonces** responde 409 y exige reactivarlo antes de editarlo.
**Validaciones:**
1. La petición de modificación debe incluir al menos un campo modificado respecto al valor actual
2. `full_name`, si se informa, no puede estar vacío y mantiene el rango de 2 a 120 caracteres
3. `corporate_email`, si se informa, debe cumplir formato de email válido y no superar 150 caracteres
4. `corporate_email` no puede coincidir con el de otro usuario, excluyendo de la comprobación al propio usuario editado
**Escenarios de error:**
1. El nombre no puede quedar vacío
2. El correo corporativo no tiene un formato válido
3. No se ha modificado ningún dato respecto a los valores actuales
4. Ya existe otro usuario con ese correo corporativo
5. El usuario está desactivado y debe reactivarse antes de modificarlo
6. El usuario a modificar no existe
7. El solicitante no tiene permisos para modificar usuarios
**Campos de datos:**
- `full_name` (string, opcional) — 2–120 caracteres; no puede quedar vacío si se edita
- `corporate_email` (email, opcional) — Formato válido; máx. 150; único excluyendo al propio usuario
- `updated_at` (datetime, obligatorio) — Automático
- `updated_by` (reference, obligatorio) — Automático

### REQ-042 — Desactivación y reactivación de usuario con baja lógica
El ADMINISTRADOR desactiva o reactiva un usuario, sin borrado físico del registro. Reglas: (1) la baja es lógica: el registro se conserva para mantener la trazabilidad de quién reportó o resolvió cada incidencia y la retención de 2 años (RN-03); (2) un usuario `INACTIVO` no puede autenticarse (PRE-02) ni recibir asignaciones nuevas; (3) las incidencias ya reportadas o asignadas permanecen intactas y siguen mostrando su nombre; (4) el ADMINISTRADOR no puede desactivarse a sí mismo; (5) la reactivación devuelve al usuario su rol previo, que puede cambiarse después con ROL-01. Flujo: abre la ficha → pulsa «Desactivar» → el sistema pide confirmación indicando el efecto (pierde el acceso) → confirma → `status` pasa a `INACTIVO`, se registran `deactivated_at` y `deactivated_by` y se invalida su sesión activa. Reactivar sigue el camino inverso. Datos: `status` (ACTIVO/INACTIVO), `deactivated_at` (timestamp, nullable), `deactivated_by` (FK `users.id`, nullable). Validaciones: el usuario debe existir y estar en el estado contrario al solicitado. Errores: 409 «El usuario ya está desactivado»; 422 «No puedes desactivar tu propia cuenta»; 403 sin permisos; 404 usuario inexistente. Criterios de aceptación: Dado un usuario `ACTIVO` con incidencias reportadas, cuando el ADMINISTRADOR lo desactiva, entonces deja de poder iniciar sesión y sus incidencias siguen consultables con su nombre. Dado el propio ADMINISTRADOR, cuando intenta desactivarse, entonces el sistema lo impide con un mensaje explícito. Dado un usuario `INACTIVO`, cuando se reactiva, entonces recupera el acceso con su rol anterior. Seguridad: solo ADMINISTRADOR; operación sensible auditada (RN-04). Eventos: `UserDeactivated`, `UserReactivated`. Dependencias: USR-01, USR-04. [gap: retención de 2 años sin indicar qué ocurre al vencer el plazo]. Prioridad: Should. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Ningún registro de usuario se elimina físicamente: la baja de un usuario es siempre lógica
2. Un ADMINISTRADOR no puede desactivar su propia cuenta
3. Un usuario INACTIVO no puede recibir asignaciones nuevas
4. Las incidencias reportadas o asignadas a un usuario desactivado permanecen intactas y siguen mostrando su nombre
5. Un usuario desactivado no conserva ninguna sesión válida
6. Un usuario reactivado recupera el rol que tenía vigente antes de su desactivación
7. Una desactivación solo es válida sobre un usuario ACTIVO y una reactivación solo sobre un usuario INACTIVO
**Criterios de aceptación:**
1. AC-USR-08: **Dado** un usuario `ACTIVO` con incidencias reportadas, **cuando** el ADMINISTRADOR lo desactiva y confirma el aviso de pérdida de acceso, **entonces** su `status` pasa a `INACTIVO`, se informan `deactivated_at`/`deactivated_by`, su sesión activa se invalida, deja de poder autenticarse y sus incidencias siguen consultables mostrando su nombre; **cuando** el ADMINISTRADOR intenta desactivar su propia cuenta, **entonces** el sistema lo impide con 422; **cuando** se reactiva un usuario `INACTIVO`, **entonces** recupera el acceso con su rol anterior.
**Validaciones:**
1. El usuario objetivo debe existir y encontrarse en el estado contrario al solicitado (solo se desactiva un `ACTIVO` y solo se reactiva un `INACTIVO`)
**Escenarios de error:**
1. El usuario ya se encuentra en el estado solicitado
2. No es posible desactivar la propia cuenta
3. El usuario indicado no existe
4. El solicitante no tiene permisos para desactivar o reactivar usuarios
**Campos de datos:**
- `status` (enum, obligatorio) — ACTIVO | INACTIVO; debe partir del estado contrario al solicitado
- `deactivated_at` (datetime, opcional) — Nulo mientras la cuenta esté ACTIVO
- `deactivated_by` (reference, opcional) — Nulo mientras la cuenta esté ACTIVO; no puede ser el propio usuario

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

### REQ-045 — Unicidad global del correo corporativo como identificador de persona
El correo corporativo identifica unívocamente a una persona en todo el sistema: no pueden coexistir dos usuarios con el mismo `corporate_email` (comparación en minúsculas, sin espacios). Aplica a: USR (alta y edición), notificaciones por correo, autenticación. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. No pueden coexistir dos usuarios con el mismo correo corporativo, comparado en minúsculas y sin espacios
**Criterios de aceptación:**
1. AC-USR-02: **Dado** un formulario de alta de usuario, **cuando** se confirma sin nombre, sin correo o sin rol, **entonces** el sistema responde 400 con el mensaje del campo concreto que falta, conserva los datos introducidos y no crea ningún registro; y **cuando** se confirma con un `corporate_email` ya existente (comparado en minúsculas y sin espacios), **entonces** responde 409 «Ya existe un usuario con ese correo corporativo» y no crea nada.
2. AC-USR-07: **Dado** un usuario `ACTIVO`, **cuando** el ADMINISTRADOR modifica su nombre y/o su correo por uno no utilizado, **entonces** el cambio se persiste, queda registrado con actor y fecha, y las notificaciones posteriores se envían al nuevo correo; **cuando** el correo introducido ya pertenece a otro usuario, **entonces** responde 409 y no altera el registro; **cuando** el usuario está `INACTIVO`, **entonces** responde 409 y exige reactivarlo antes de editarlo.
**Escenarios de error:**
1. El correo corporativo indicado ya identifica a otra persona del sistema
**Campos de datos:**
- `corporate_email` (email, obligatorio) — Único global; comparación en minúsculas y sin espacios

### REQ-047 — Sin borrado físico: bajas lógicas y retención de datos 2 años
Ningún registro de usuario o de incidencia se elimina físicamente: las bajas son lógicas y los datos se conservan 2 años [gap: el RFP no indica qué ocurre con los datos al vencer el plazo de retención]. Aplica a: USR, incidencias, historial de estados. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Ningún registro de usuario o de incidencia se elimina físicamente
2. Los datos de usuarios e incidencias se conservan durante al menos 2 años desde su creación
**Criterios de aceptación:**
1. AC-USR-08: **Dado** un usuario `ACTIVO` con incidencias reportadas, **cuando** el ADMINISTRADOR lo desactiva y confirma el aviso de pérdida de acceso, **entonces** su `status` pasa a `INACTIVO`, se informan `deactivated_at`/`deactivated_by`, su sesión activa se invalida, deja de poder autenticarse y sus incidencias siguen consultables mostrando su nombre; **cuando** el ADMINISTRADOR intenta desactivar su propia cuenta, **entonces** el sistema lo impide con 422; **cuando** se reactiva un usuario `INACTIVO`, **entonces** recupera el acceso con su rol anterior.
**Escenarios de error:**
1. La eliminación definitiva de un registro no está permitida; solo procede la baja lógica
**Campos de datos:**
- `status` (enum, obligatorio) — ACTIVO | INACTIVO; datos conservados 2 años

### REQ-048 — Trazabilidad de actor y fecha en toda creación o modificación
Toda operación que crea o modifica un usuario o una incidencia registra quién la ejecutó y cuándo, de forma consultable en todo momento (trazabilidad sustitutiva del buzón compartido). Aplica a: USR, ROL, incidencias, historial de cambios de estado. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Toda creación o modificación de un usuario o de una incidencia tiene asociado exactamente un actor identificado y una marca temporal, consultables en todo momento
**Criterios de aceptación:**
1. AC-USR-06: **Dado** un ADMINISTRADOR, **cuando** abre la ficha de detalle de un usuario existente, **entonces** ve nombre, correo, rol vigente, estado, `created_at`/`created_by`, `updated_at`/`updated_by`, `last_login_at` y la colección de entradas de auditoría con actor y fecha, sin que aparezca en ningún punto la contraseña ni su hash; **cuando** el identificador no existe, **entonces** responde 404 sin revelar información del recurso.
2. AC-ROL-02: **Dado** un usuario con rol EMPLEADO, **cuando** el ADMINISTRADOR le asigna TECNICO_MANTENIMIENTO desde su ficha, **entonces** el cambio se registra con `role_changed_at` y `role_changed_by`, su sesión vigente se invalida y en la siguiente petición sus permisos efectivos son los del nuevo rol (deja de estar limitado a sus propias incidencias).
3. AC-ROL-06: **Dado** un intento de operación denegado por falta de permisos, **cuando** se revisa la traza `access_denied_log`, **entonces** existe una entrada con `attempted_at`, `user_id`, `operation` y `outcome` para el 100% de los intentos denegados de una muestra auditada de 20 intentos.
**Campos de datos:**
- `changed_by` (reference, obligatorio) — Consultable en todo momento
- `changed_at` (datetime, obligatorio) — Consultable en todo momento

### REQ-049 — Datos personales limitados a nombre y correo corporativo
Los datos personales tratados se limitan a nombre y correo corporativo; no se capturan ni almacenan datos de categoría especial. Aplica a: USR, incidencias, notificaciones. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Los únicos datos personales almacenados de una persona son su nombre y su correo corporativo
2. No existe en el sistema ningún dato personal de categoría especial
**Escenarios de error:**
1. Se han aportado datos personales no admitidos por el alcance del tratamiento
**Campos de datos:**
- `full_name` (string, obligatorio) — Sin datos de categoría especial
- `corporate_email` (email, obligatorio) — Sin datos de categoría especial

### REQ-052 — Acceso solo para usuarios dados de alta por ADMINISTRADOR y activos
Solo pueden acceder usuarios previamente dados de alta por un ADMINISTRADOR y con `is_active = true`; no existe autorregistro público ("no se requiere autorregistro público"). Dependencia: módulo de gestión de usuarios y roles (alta de usuario y asignación de rol).
**Reglas de negocio:**
1. Un usuario existe en el sistema únicamente si un ADMINISTRADOR lo ha dado de alta; no hay creación de usuarios por autorregistro.
2. Solo un usuario con `is_active = true` tiene acceso a la aplicación.
**Criterios de aceptación:**
1. AC-AUT-01: Dado un usuario dado de alta por un ADMINISTRADOR con `is_active = true`, cuando introduce su `username` (correo corporativo) y su contraseña correctos en la pantalla de acceso, entonces `POST /api/auth/login` responde 200, se emite una sesión y la SPA lo redirige a la vista inicial correspondiente a su rol.
**Escenarios de error:**
1. Se intenta acceder con un usuario que no ha sido dado de alta o cuya cuenta no está activa: se deniega con un mensaje genérico
2. Se intenta crear una cuenta propia sin ser administrador: el alta de usuarios no está disponible para el solicitante
**Campos de datos:**
- `is_active` (boolean, obligatorio) — Solo accede el usuario con is_active = true; no existe autorregistro público

### REQ-060 — Autorización por rol de la sesión y acotación del alcance de datos
El sistema aplica en cada petición autenticada el rol de la sesión para autorizar o denegar la operación y para acotar el alcance de datos accesible. Reglas: EMPLEADO solo puede crear incidencias y consultar aquellas cuyo `reported_by_user_id` coincide con el suyo; TECNICO-DE-MANTENIMIENTO puede consultar cualquier incidencia, autoasignársela, cambiar su estado y registrar el comentario de resolución; ADMINISTRADOR gestiona usuarios y roles; un rol no hereda los permisos de otro salvo declaración explícita [ambigüedad: el RFP enumera dos roles pero el ADMINISTRADOR aparece como tercer actor sin permisos funcionales detallados sobre incidencias]; la autorización se resuelve en el servidor, sobre `role_code` de la sesión (SES-01), nunca sobre parámetros de la petición; el filtrado por propietario se aplica en la consulta a base de datos, no en la respuesta ya construida. Flujo: petición con sesión válida (SES-03) → resolución de `role_code` → comprobación del permiso requerido por la operación → si procede, aplicación del filtro de alcance de datos → ejecución. Datos: `role_code` (string, obligatorio, de `cat_roles`), `user_id` (uuid, propietario efectivo de la petición), `required_permission` (string, declarado por cada operación). Validaciones: toda operación declara su permiso requerido; una operación sin declaración se deniega por defecto. Errores: 403 "No tienes permisos para realizar esta acción" cuando hay sesión válida pero rol insuficiente; 404 uniforme cuando el recurso existe pero está fuera del alcance de datos del rol. Criterios: Given un EMPLEADO con sesión When solicita el listado completo de incidencias Then 403; Given un EMPLEADO When consulta una incidencia de otro empleado Then no accede a ella; Given un TECNICO-DE-MANTENIMIENTO When consulta cualquier incidencia Then accede correctamente; Given una operación sin permiso declarado When se invoca Then se deniega. Seguridad: materializa el criterio de aceptación 4 del RFP; toda denegación queda registrada (TRZ-01). Dependencias: SES-01, SES-04, módulo de gestión de usuarios y roles, MOD-002 gestión de incidencias. Prioridad: Must.
**Reglas de negocio:**
1. Un EMPLEADO solo tiene acceso a las incidencias cuyo `reported_by_user_id` coincide con su propio identificador.
2. Un TECNICO-DE-MANTENIMIENTO tiene acceso a cualquier incidencia, con independencia de quién la haya reportado.
3. Un rol no dispone de los permisos de otro rol salvo declaración explícita.
4. Una operación sin permiso requerido declarado está denegada para todos los roles.
5. Un recurso existente pero fuera del alcance de datos del rol es indistinguible, desde la respuesta, de un recurso inexistente.
6. La autorización se resuelve sobre el `role_code` de la sesión y nunca sobre parámetros de la petición.
**Criterios de aceptación:**
1. AC-PERM-01: Dado un EMPLEADO con sesión válida, cuando solicita el listado completo de incidencias o cualquier operación de gestión (autoasignación, cambio de estado, comentario de resolución), entonces el sistema responde 403 y la denegación queda registrada en la auditoría.
2. AC-PERM-02: Dado un EMPLEADO con sesión válida y una incidencia cuyo `reported_by_user_id` es de otro empleado, cuando solicita su detalle por identificador directo, entonces el sistema responde 404 uniforme sin revelar su existencia, y el filtrado por propietario se aplica en la consulta a base de datos (no sobre la respuesta ya construida).
3. AC-PERM-03: Dado un TECNICO-DE-MANTENIMIENTO con sesión válida, cuando consulta cualquier incidencia, se la autoasigna, cambia su estado y registra el comentario de resolución, entonces todas las operaciones se ejecutan correctamente sobre incidencias de cualquier empleado y de cualquiera de las tres oficinas.
4. AC-PERM-04: Dada una operación de la API que no declara su `required_permission`, cuando se invoca con una sesión válida de cualquier rol, entonces el sistema la deniega por defecto con 403; y cuando la petición incluye un rol o permiso en su cuerpo o parámetros, entonces el sistema lo ignora y autoriza exclusivamente con el `role_code` de la sesión.
5. AC-TRZ-03: Dado un ADMINISTRADOR con sesión válida, cuando consulta la auditoría de accesos filtrando por usuario y por rango de fechas, entonces obtiene los eventos correspondientes a ese filtro; y dado un EMPLEADO o un TECNICO-DE-MANTENIMIENTO, cuando intenta la misma consulta, entonces obtiene 403.
**Validaciones:**
1. Toda operación debe declarar su `required_permission`; si la declaración falta, la invocación se deniega por defecto
2. El `role_code` evaluado se toma de la sesión; se ignora cualquier rol o permiso enviado como parámetro de la petición
3. El `user_id` usado como propietario efectivo para acotar el alcance de datos procede de la sesión, no del cuerpo ni de la query de la petición
**Escenarios de error:**
1. Hay sesión válida pero el rol no dispone del permiso requerido por la operación
2. El recurso solicitado queda fuera del alcance de datos del rol: se responde de forma uniforme sin revelar su existencia
3. La operación invocada no declara el permiso que requiere y se deniega por defecto
**Campos de datos:**
- `role_code` (enum, obligatorio) — Procede de cat_roles y de la sesión, nunca de parámetros de la petición
- `user_id` (uuid, obligatorio) — El filtrado por propietario se aplica en la consulta a base de datos
- `required_permission` (string, obligatorio) — Toda operación debe declararlo; sin declaración se deniega por defecto

### REQ-066 — Usuario existente dado de alta por ADMINISTRADOR con rol y estado activo
Toda operación del servicio exige que el usuario exista previamente en el sistema, dado de alta por un ADMINISTRADOR, con rol asignado (role_code ∈ cat_roles) y estado activo. El RFP excluye el autorregistro público: «no se requiere autorregistro público». Dependencia: alta de usuario y asignación de rol (épica de gestión de usuarios, fuera de este alcance). auth_type: SESSION. data_scope: own_only.
**Reglas de negocio:**
1. Todo usuario del sistema ha sido creado por un ADMINISTRADOR: no existe ningún usuario originado por autorregistro público
2. Todo usuario tiene exactamente un `role_code` perteneciente al catálogo `cat_roles`
3. Sólo los usuarios en estado activo pueden ejecutar operaciones del servicio
**Criterios de aceptación:**
1. AC-XFN-02: Dado el sistema en producción, cuando se buscan vías de creación de cuenta, entonces no existe ningún endpoint ni pantalla de autorregistro público y toda cuenta operativa tiene un `role_code` de `cat_roles` y estado activo asignados por un ADMINISTRADOR.
**Validaciones:**
1. `role_code` recibido en el alta debe ser un valor existente del catálogo `cat_roles` (EMPLEADO, TECNICO_DE_MANTENIMIENTO, ADMINISTRADOR)
**Escenarios de error:**
1. La cuenta indicada no está dada de alta o no se encuentra activa; la operación se deniega sin revelar si existe
2. La cuenta no tiene un rol válido asignado y no puede operar en el sistema
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Sin autorregistro público: el alta la realiza siempre un ADMINISTRADOR
- `role_code` (enum, obligatorio) — Valores de `cat_roles`: EMPLEADO, TECNICO_DE_MANTENIMIENTO, ADMINISTRADOR
- `is_active` (boolean, obligatorio) — Sólo cuentas activas pueden operar

### REQ-079 — Resolución de destinatario individual (nombre y correo corporativo vigente) por identificador de usuario
El sistema resuelve el destinatario de un aviso (nombre y correo corporativo vigente) a partir del identificador de un usuario. Reglas: devuelve siempre el correo almacenado en el registro del usuario en ese instante, nunca copias ni listas de distribución paralelas; la resolución es determinista porque el correo corporativo es identificador único global (dep. REQ-045); si el usuario existe pero no puede recibir avisos (cuenta no activa, correo ausente o inválido) la consulta no falla: devuelve is_notifiable=false y el motivo para que MOD-002 no envíe y quede traza; la respuesta solo contiene nombre y correo corporativo (dep. REQ-049). Flujo: el módulo consumidor invoca la resolución con user_id → el directorio lee el usuario y su rol vigente → si no existe, error de no resolución → si existe, compone el destinatario y calcula is_notifiable → devuelve el destinatario y registra la resolución (DIR-03). Datos: user_id (number, obligatorio, >0), display_name (string, ≤120, obligatorio), recipient_email (string, ≤254, RFC 5322, normalizado a minúsculas y sin espacios), role_code (FK cat_roles), user_status (FK cat_estados_usuario), is_notifiable (boolean), not_notifiable_reason (FK cat_motivos_no_notificable, null si is_notifiable=true), resolved_at (timestamp). Catálogos: cat_roles (EMPLEADO, TECNICO_DE_MANTENIMIENTO, ADMINISTRADOR — dep. REQ-035), cat_estados_usuario (ACTIVO/INACTIVO — dep. REQ-042), cat_motivos_no_notificable (CUENTA_INACTIVA, CORREO_AUSENTE, CORREO_INVALIDO). [gap: lista de dominios de correo corporativo admitidos por Officeo Facilities S.L.]. Validaciones: user_id entero positivo; recipient_email válido RFC 5322 y ≤254 caracteres; se rechaza cualquier dirección que no supere la validación marcando CORREO_INVALIDO en lugar de intentar el envío. Errores: 404 DESTINATARIO_NO_ENCONTRADO; 422 IDENTIFICADOR_INVALIDO; 403 ACCESO_NO_AUTORIZADO (mensaje uniforme, sin revelar si el usuario existe, dep. REQ-031/REQ-078); 503 DIRECTORIO_NO_DISPONIBLE si la base de datos no responde — el consumidor reintenta, nunca envía con datos supuestos. Criterios de aceptación: dado un usuario activo con correo vigente, al resolver por user_id se devuelve ese correo con is_notifiable=true; dado un user_id inexistente, se responde 404 y no se genera ningún aviso; dado un usuario desactivado, se devuelve is_notifiable=false con motivo CUENTA_INACTIVA. Seguridad: consulta reservada a los módulos backend (autorización servicio-a-servicio) y al rol ADMINISTRADOR; EMPLEADO y TECNICO_DE_MANTENIMIENTO no pueden consultar el correo de terceros ni enumerar el directorio (dep. REQ-014, REQ-028, REQ-078); autorización vinculante en la API REST, nunca en la SPA (dep. REQ-013/REQ-032); los correos no se escriben en logs ni en mensajes de error (solo user_id). Sin doble factor. Eventos de dominio: ninguno propio; integración con MOD-002 por consulta síncrona. Dependencias: REQ-037, REQ-041, REQ-042, REQ-045, REQ-049. [ambigüedad] El RFP no indica si un usuario desactivado debe seguir recibiendo avisos de incidencias reportadas antes de la baja; se modela como no notificable a la espera de confirmación. Prioridad: Must. auth_type: JWT. data_scope: all.
**Reglas de negocio:**
1. El correo corporativo devuelto por el directorio es siempre el almacenado en el registro del usuario en el instante de la consulta; ninguna copia guardada por un módulo consumidor ni lista de distribución paralela es fuente válida
2. El correo corporativo es identificador único global de persona, por lo que la resolución de un mismo user_id devuelve siempre el mismo destinatario mientras el registro no cambie
3. Un usuario con cuenta inactiva, sin correo corporativo o con correo de formato inválido no es notificable
4. Un destinatario no notificable tiene exactamente un motivo del catálogo de motivos de no notificable; un destinatario notificable no tiene motivo asociado
5. Un identificador de usuario inexistente no produce ningún destinatario ni origina aviso alguno
6. La respuesta de resolución individual no contiene más datos personales que el nombre y el correo corporativo del destinatario
7. Los roles EMPLEADO y TECNICO_DE_MANTENIMIENTO no tienen acceso al correo corporativo de terceros ni a la enumeración del directorio
8. Ningún correo corporativo figura en registros de log ni en mensajes de error del directorio
**Criterios de aceptación:**
1. AC-G-05: Dado un usuario con rol empleado y otro con rol técnico de mantenimiento, cuando cada uno invoca directamente los endpoints de la API REST (sin pasar por la SPA) correspondientes a incidencias de terceros, a la gestión de usuarios y a la consulta del directorio de destinatarios, entonces el empleado solo obtiene 200 sobre sus propias incidencias, el técnico solo sobre incidencias (no sobre gestión de usuarios ni directorio) y cualquier otro intento devuelve 403 ACCESO_NO_AUTORIZADO con mensaje uniforme que no revela existencia de recursos; la ocultación en la SPA no se acepta como evidencia.
2. AC-G-03: Dado una incidencia reportada por un empleado con correo corporativo vigente y cuenta activa, cuando un técnico cambia el estado de esa incidencia, entonces el sistema resuelve el destinatario contra el directorio en el instante del envío (nunca contra una copia almacenada) y el empleado reportante recibe un correo en esa dirección en ≤ 5 min desde la transición ([inferido] — el RFP no fija umbral de entrega), quedando registro de la resolución con outcome=OK.
3. AC-DIR-01: Dado un usuario con user_status=ACTIVO y recipient_email presente y válido RFC 5322, cuando un módulo backend autorizado resuelve el destinatario por su user_id, entonces la respuesta contiene display_name y recipient_email normalizado a minúsculas y sin espacios, con is_notifiable=true, not_notifiable_reason=null y ningún otro dato personal del usuario.
4. AC-DIR-02: Dado un user_id inexistente o con formato no entero positivo, cuando se invoca la resolución individual, entonces el sistema responde 404 DESTINATARIO_NO_ENCONTRADO o 422 IDENTIFICADOR_INVALIDO según el caso, no se genera ningún aviso y queda fila de registro con outcome=NO_ENCONTRADO.
5. AC-DIR-03: Dado un usuario existente pero desactivado, sin correo, o con correo que no supera la validación RFC 5322, cuando se resuelve por user_id, entonces la consulta responde 200 (no error) con is_notifiable=false y not_notifiable_reason ∈ {CUENTA_INACTIVA, CORREO_AUSENTE, CORREO_INVALIDO}, y el módulo consumidor no ejecuta ningún envío SMTP.
6. AC-DIR-04: Dado un usuario cuyo correo, rol o estado ha sido modificado y confirmado por el ADMINISTRADOR, cuando se resuelve el directorio inmediatamente después (sin acción manual adicional y sin esperar más de cache_ttl_seconds ≤ 60), entonces se devuelve el valor nuevo; y dado un fallo en la invalidación de caché, cuando llega la siguiente resolución, entonces se degrada a lectura directa a base de datos y en ningún caso se sirve un valor anterior.
7. AC-COL-04: Dado un técnico de mantenimiento activo cuyo correo está ausente o no supera la validación RFC 5322, cuando se resuelve el colectivo, entonces queda fuera de recipients, aparece en excluded_recipients con su not_notifiable_reason y el resto de destinatarios recibe el aviso con normalidad.
8. AC-ADM-01: Dado un usuario desactivado con correo vigente y otro activo con correo inválido, cuando el ADMINISTRADOR abre la vista de verificación del directorio, entonces ambos aparecen en el bloque «Destinatarios no notificables» (presentado en primer lugar) con su motivo del catálogo (CUENTA_INACTIVA, CORREO_INVALIDO), y los filtros por rol y por notificabilidad y la búsqueda por nombre/correo (≤ 100 caracteres) devuelven resultados coherentes.
**Validaciones:**
1. `user_id` es obligatorio y debe ser un número entero positivo (>0); en caso contrario se rechaza con `422 IDENTIFICADOR_INVALIDO`
2. `recipient_email` debe cumplir el formato RFC 5322 y no superar 254 caracteres
3. `recipient_email` debe normalizarse a minúsculas y sin espacios antes de aceptarse
4. `display_name` es obligatorio y no puede superar 120 caracteres
5. `role_code` debe corresponder a un valor existente del catálogo `cat_roles` (EMPLEADO, TECNICO_DE_MANTENIMIENTO, ADMINISTRADOR)
6. `user_status` debe corresponder a un valor existente del catálogo `cat_estados_usuario` (ACTIVO / INACTIVO)
7. `not_notifiable_reason` debe ser un valor del catálogo `cat_motivos_no_notificable` y debe ser null cuando `is_notifiable=true` (coherencia entre campos)
**Escenarios de error:**
1. El identificador de usuario indicado no tiene un formato válido (debe ser un número entero positivo)
2. La solicitud no aporta credenciales válidas o la sesión ha caducado
3. El solicitante no tiene permiso para resolver destinatarios del directorio (respuesta uniforme, sin revelar si el usuario consultado existe)
4. No existe ningún usuario con el identificador indicado
5. El directorio de destinatarios no está disponible en este momento; la consulta debe reintentarse y no puede enviarse ningún aviso con datos supuestos
**Campos de datos:**
- `user_id` (integer, obligatorio) — Entero positivo (>0)
- `display_name` (string, obligatorio) — Longitud máxima 120
- `recipient_email` (string, obligatorio) — Formato RFC 5322; máx. 254; normalizado a minúsculas y sin espacios
- `role_code` (enum, obligatorio) — EMPLEADO, TECNICO_DE_MANTENIMIENTO, ADMINISTRADOR (cat_roles)
- `user_status` (enum, obligatorio) — ACTIVO, INACTIVO (cat_estados_usuario)
- `is_notifiable` (boolean, obligatorio) — true/false
- `not_notifiable_reason` (enum, opcional) — CUENTA_INACTIVA, CORREO_AUSENTE, CORREO_INVALIDO; vacío si is_notifiable=true
- `resolved_at` (datetime, obligatorio) — Instante en que se resolvió el destinatario

### REQ-080 — Reflejo inmediato de cambios de correo, rol o estado en las resoluciones posteriores del directorio
El directorio refleja de forma inmediata los cambios de correo, rol o estado de un usuario en las resoluciones posteriores. Reglas: la resolución se ejecuta en el instante del envío contra la tabla de usuarios, única fuente de verdad; queda prohibido persistir listas de distribución o copias del correo en otros módulos (causa actual de avisos a direcciones obsoletas); si por rendimiento se emplea caché en proceso, su vigencia máxima es cache_ttl_seconds ≤ 60 y se invalida explícitamente al confirmarse un alta, una modificación de datos, un cambio de rol o una desactivación/reactivación; un fallo de invalidación degrada a lectura directa a base de datos, nunca a servir un dato antiguo. Flujo: el ADMINISTRADOR confirma un alta/modificación/cambio de rol/baja (REQ-037, REQ-041, REQ-042, REQ-043) → la transacción confirmada dispara la invalidación de la entrada del directorio afectada → la siguiente resolución (DIR-01 o COL-01) devuelve ya el dato nuevo, sin acción manual adicional. Datos: user_id (number), directory_cache_key (string), cache_ttl_seconds (number, [inferido] 60), invalidated_at (timestamp), invalidation_source (ALTA, MODIFICACION, CAMBIO_ROL, DESACTIVACION, REACTIVACION), fallback_to_db (boolean). Validaciones: toda invalidación referencia un user_id existente; ninguna resolución puede servir una entrada con antigüedad superior a cache_ttl_seconds. Errores: 503 DIRECTORIO_NO_DISPONIBLE si ni la caché ni la base de datos responden; el fallo de invalidación no se propaga al ADMINISTRADOR (su operación ya está confirmada) pero se registra como incidencia técnica en el registro de DIR-03. Criterios de aceptación: dado un correo corregido por el ADMINISTRADOR, al consultar el directorio inmediatamente después se devuelve el correo nuevo sin acción adicional; dado un usuario cuyo rol pasa a TECNICO_DE_MANTENIMIENTO, al resolver el colectivo está incluido, y si pasa a EMPLEADO queda excluido; dado un usuario desactivado, ya no aparece en el colectivo. Seguridad: las operaciones que disparan la invalidación son exclusivas del ADMINISTRADOR (dep. REQ-003, REQ-044); el mecanismo de invalidación no expone ningún endpoint accesible a EMPLEADO ni a TECNICO_DE_MANTENIMIENTO. Eventos de dominio: ninguno propio; se reacciona a la confirmación transaccional de las operaciones de gestión de usuarios. Dependencias: DIR-01, COL-01, REQ-041, REQ-042, REQ-043, REQ-019. Prioridad: Must. auth_type: JWT. data_scope: all.
**Reglas de negocio:**
1. Ninguna resolución del directorio sirve un dato con antigüedad superior a 60 segundos
2. Ningún módulo distinto del directorio conserva copias persistentes del correo corporativo ni listas de distribución de destinatarios
3. Tras la confirmación de un alta, modificación de datos, cambio de rol, desactivación o reactivación, la siguiente resolución del directorio devuelve ya el dato nuevo sin acción manual adicional
4. Un fallo de invalidación de caché nunca deriva en servir un dato antiguo: la resolución se resuelve contra la base de datos
5. Las operaciones que provocan la invalidación de una entrada del directorio son exclusivas del rol ADMINISTRADOR
**Criterios de aceptación:**
1. AC-G-03: Dado una incidencia reportada por un empleado con correo corporativo vigente y cuenta activa, cuando un técnico cambia el estado de esa incidencia, entonces el sistema resuelve el destinatario contra el directorio en el instante del envío (nunca contra una copia almacenada) y el empleado reportante recibe un correo en esa dirección en ≤ 5 min desde la transición ([inferido] — el RFP no fija umbral de entrega), quedando registro de la resolución con outcome=OK.
2. AC-DIR-04: Dado un usuario cuyo correo, rol o estado ha sido modificado y confirmado por el ADMINISTRADOR, cuando se resuelve el directorio inmediatamente después (sin acción manual adicional y sin esperar más de cache_ttl_seconds ≤ 60), entonces se devuelve el valor nuevo; y dado un fallo en la invalidación de caché, cuando llega la siguiente resolución, entonces se degrada a lectura directa a base de datos y en ningún caso se sirve un valor anterior.
3. AC-COL-02: Dado un usuario cuyo rol vigente cambia a TECNICO_DE_MANTENIMIENTO, cuando se resuelve el colectivo tras la confirmación del cambio, entonces ese usuario figura en recipients; y dado que su rol pasa a EMPLEADO o su cuenta se desactiva, cuando se vuelve a resolver, entonces ya no figura — sin mantenimiento manual de lista alguna.
4. AC-ADM-03: Dado un destinatario marcado como no notificable por correo erróneo, cuando el ADMINISTRADOR navega al detalle del usuario desde la vista, corrige el correo y vuelve, entonces el destinatario figura ya como notificable sin ninguna acción adicional ni recarga manual de caché, y la vista permanece de solo lectura (no permite editar correo ni rol en línea).
**Validaciones:**
1. Toda solicitud de invalidación debe referenciar un `user_id` existente en el directorio
2. `cache_ttl_seconds` debe ser un número entero positivo y no superior a 60
3. `invalidation_source` debe ser uno de los valores admitidos: ALTA, MODIFICACION, CAMBIO_ROL, DESACTIVACION, REACTIVACION
**Escenarios de error:**
1. La solicitud de refresco del directorio referencia un usuario que no existe
2. El solicitante no tiene permiso para forzar la actualización de una entrada del directorio
3. El directorio de destinatarios no está disponible en este momento y no puede devolverse un dato vigente
4. No se pudo refrescar la entrada del directorio tras confirmarse el cambio; la operación del administrador queda confirmada y la consulta se atiende con lectura directa
**Campos de datos:**
- `user_id` (integer, obligatorio) — Debe existir en el padrón
- `directory_cache_key` (string, obligatorio) — Clave de la entrada cacheada del directorio
- `cache_ttl_seconds` (integer, opcional) — ≤ 60 segundos (valor inferido: 60)
- `invalidated_at` (datetime, obligatorio) — Instante de la invalidación de la entrada
- `invalidation_source` (enum, obligatorio) — ALTA, MODIFICACION, CAMBIO_ROL, DESACTIVACION, REACTIVACION
- `fallback_to_db` (boolean, obligatorio) — true/false

### REQ-082 — Resolución del colectivo «equipo de mantenimiento» con técnicos activos y notificables
El sistema resuelve el colectivo «equipo de mantenimiento» como el conjunto de destinatarios con rol técnico de mantenimiento activos y notificables. Reglas: la pertenencia se deriva exclusivamente del rol vigente en base de datos (dep. REQ-028, REQ-046), sin lista de miembros mantenida a mano; el colectivo lo forman los usuarios con role_code = TECNICO_DE_MANTENIMIENTO y user_status = ACTIVO y is_notifiable = true, «y de ninguno más» (criterio de aceptación de EPIC-006); los destinatarios se deduplican por recipient_email (la unicidad REQ-045 lo garantiza; la deduplicación es defensiva); si el conjunto queda vacío no es un error: se marca outcome=SIN_DESTINATARIOS y, si está configurado, se devuelve el buzón de respaldo del equipo de facilities para que el alta de incidencia no quede sin aviso; el orden de devolución es alfabético por display_name y sin paginación (volumen máximo 50 usuarios). Flujo: el módulo consumidor solicita el colectivo → el directorio consulta los usuarios por rol y estado → filtra no notificables (motivo por cada exclusión) → deduplica y ordena → si queda vacío aplica el buzón de respaldo → devuelve la lista y registra la resolución (DIR-03). Datos: group_code (valor fijo EQUIPO_MANTENIMIENTO), recipients (lista de {display_name, recipient_email, user_id}), recipient_count (number, ≥0), excluded_recipients (lista de {user_id, not_notifiable_reason}), is_fallback_used (boolean), facilities_fallback_email (string, ≤254, RFC 5322, opcional), resolved_at (timestamp). Catálogos: cat_roles, cat_estados_usuario, cat_motivos_no_notificable. [gap: dirección del buzón de respaldo del equipo de facilities]. Validaciones: todos los correos devueltos superan la validación de formato de DIR-01; ningún correo aparece dos veces; ningún usuario con user_status distinto de ACTIVO figura en recipients. Errores: 200 con recipient_count=0 e is_fallback_used=false cuando no hay técnicos activos ni buzón configurado —nunca 404—; 403 ACCESO_NO_AUTORIZADO uniforme para roles no autorizados; 503 DIRECTORIO_NO_DISPONIBLE ante caída de la base de datos. Criterios de aceptación: dado un padrón con tres técnicos activos y uno desactivado, se devuelven exactamente los tres activos y ninguno más; dado un usuario cuyo rol cambia a TECNICO_DE_MANTENIMIENTO, tras el cambio está incluido; dado que no existe ningún técnico activo y hay buzón de respaldo configurado, se devuelve el buzón con is_fallback_used=true; dado un técnico con correo inválido, queda fuera de recipients y aparece en excluded_recipients con su motivo. Seguridad: consulta reservada a módulos backend y al rol ADMINISTRADOR; EMPLEADO y TECNICO_DE_MANTENIMIENTO no pueden enumerar el colectivo ni sus correos (dep. REQ-014, REQ-078); autorización resuelta en la API REST (dep. REQ-013); correos fuera de logs. Eventos de dominio: ninguno propio; consumo síncrono desde MOD-002 «Notificaciones por correo de incidencias». Dependencias: DIR-01, REQ-035, REQ-042, REQ-043, REQ-046. Prioridad: Must. auth_type: JWT. data_scope: all.
**Reglas de negocio:**
1. El colectivo equipo de mantenimiento lo componen exactamente los usuarios con rol TECNICO_DE_MANTENIMIENTO, estado ACTIVO y notificables, y ninguno más
2. La pertenencia al colectivo equipo de mantenimiento se deriva del rol vigente en base de datos; no existe ninguna lista de miembros mantenida manualmente
3. Ningún correo corporativo aparece dos veces en los destinatarios devueltos de un colectivo
4. Un colectivo sin destinatarios no es un error: la resolución concluye con resultado SIN_DESTINATARIOS
5. El buzón de respaldo del equipo de facilities solo se utiliza cuando el conjunto de técnicos activos y notificables está vacío
6. Todo usuario con rol TECNICO_DE_MANTENIMIENTO excluido de los destinatarios figura en la lista de excluidos con su motivo de no notificable
7. Los destinatarios del colectivo se devuelven ordenados alfabéticamente por nombre y sin paginación, con un máximo de 50 usuarios
**Criterios de aceptación:**
1. AC-BAN-04: Dado un técnico en la bandeja, cuando filtra por «Sin asignar» combinado con estado `ABIERTA`, entonces el resultado contiene solo incidencias abiertas con `assigned_technician_id` nulo; y cuando selecciona «Asignadas a mí», entonces solo aparecen las incidencias cuyo técnico es el usuario de la sesión, resuelto contra la identidad de sesión y no contra un identificador enviado por el cliente; y cuando indica un técnico inexistente, inactivo o con otro rol, entonces recibe 400 «Técnico no válido».
2. AC-USR-01: Dado un administrador autenticado, cuando da de alta un usuario con nombre, correo corporativo y rol, entonces el usuario queda creado y puede autenticarse con sus credenciales; y cuando un usuario no administrador intenta la misma operación, entonces recibe 403; y cuando se busca una vía de autorregistro público, entonces no existe ninguna expuesta.
**Validaciones:**
1. `group_code` solo admite el valor fijo `EQUIPO_MANTENIMIENTO`
2. `facilities_fallback_email`, cuando se aporta, debe cumplir el formato RFC 5322 y no superar 254 caracteres
3. Todos los correos devueltos en `recipients` deben superar la misma validación de formato aplicada en REQ-079
4. `recipient_count` debe ser un número entero ≥0 y coincidir con el número de elementos de `recipients`
**Escenarios de error:**
1. El código de colectivo solicitado no está reconocido
2. La solicitud no aporta credenciales válidas o la sesión ha caducado
3. El solicitante no tiene permiso para enumerar el colectivo ni sus direcciones de correo (respuesta uniforme)
4. La dirección del buzón de respaldo configurada no supera la validación de formato y no puede utilizarse como destinatario
5. El directorio de destinatarios no está disponible en este momento
**Campos de datos:**
- `group_code` (enum, obligatorio) — Valor fijo EQUIPO_MANTENIMIENTO
- `recipients` (array, obligatorio) — Elementos {user_id, display_name, recipient_email}; deduplicados por recipient_email; orden alfabético por display_name; sin paginación (máx. 50)
- `recipient_count` (integer, obligatorio) — ≥0
- `excluded_recipients` (array, opcional) — Elementos {user_id, not_notifiable_reason}
- `is_fallback_used` (boolean, obligatorio) — true/false
- `facilities_fallback_email` (email, opcional) — Formato RFC 5322; máx. 254
- `resolved_at` (datetime, obligatorio) — Instante de resolución del colectivo

### REQ-084 — Comprobación de impacto y registro de motivo previos a la desactivación de un usuario
Extiende REQ-042 (baja lógica). Solo ADMINISTRADOR puede iniciar la desactivación (REQ-003). Antes de confirmar, el sistema resuelve y muestra el panel de impacto: nº de incidencias reportadas por el usuario, nº de incidencias asignadas en estado ABIERTA/EN_CURSO y si figura como destinatario del colectivo equipo de mantenimiento (datos de MOD-002). Si existe ≥1 incidencia asignada en EN_CURSO, la confirmación es explícita y esas incidencias quedan sin asignar conservando su historial [gap: el RFP no define si deben reasignarse, liberarse o mantenerse asignadas a un usuario inactivo]. El motivo es obligatorio; un ADMINISTRADOR no puede desactivar su propia cuenta; operación idempotente: desactivar un usuario ya inactivo no tiene efecto y devuelve error. Flujo: listado de usuarios (REQ-039) → ficha (REQ-040) → acción Desactivar → panel de impacto → selección de motivo → confirmación → usuario INACTIVO. Datos: user_id (uuid, obligatorio), status (enum cat_estados_cuenta: ACTIVO|INACTIVO, obligatorio), deactivation_reason_code (varchar(30), obligatorio, FK cat_motivos_desactivacion) [gap: el RFP no aporta el catálogo de motivos], deactivation_note (varchar(500), opcional), deactivated_at (timestamp, automático), deactivated_by (uuid, automático desde la sesión, REQ-064). Validaciones: usuario existe; status = ACTIVO; deactivation_reason_code ∈ cat_motivos_desactivacion; deactivated_by ≠ user_id. Errores: 404 «El usuario indicado no existe»; 409 «El usuario ya está desactivado»; 409 «No puedes desactivar tu propia cuenta»; 422 «Indica el motivo de la desactivación»; 403 uniforme «No tienes permiso para realizar esta acción» (REQ-078). Criterios de aceptación: Given un ADMINISTRADOR autenticado y un usuario ACTIVO, when lo desactiva con motivo válido, then el usuario queda INACTIVO y se registran deactivated_at, deactivated_by y deactivation_reason_code. Given un técnico con 2 incidencias en EN_CURSO, when el administrador abre el panel de impacto, then el sistema lista esas 2 incidencias antes de permitir confirmar. Given un ADMINISTRADOR, when intenta desactivarse a sí mismo, then se rechaza con 409. Seguridad: exclusivo ADMINISTRADOR; alcance de datos: todos los usuarios (REQ-014); sin doble factor [inferido]. Evento de dominio: UserDeactivated [inferido]. Dependencias: REQ-042, REQ-040, REQ-048; MOD-002. Prioridad: Must [inferido]. auth_type: JWT. data_scope: all.
**Reglas de negocio:**
1. Una cuenta de usuario tiene en todo momento exactamente un estado, `ACTIVO` o `INACTIVO`, y ambos son mutuamente excluyentes
2. Solo una cuenta con rol `ADMINISTRADOR` es origen válido de una desactivación de usuario
3. Un administrador no puede figurar a la vez como `deactivated_by` y como `user_id` de la misma desactivación
4. Una cuenta ya `INACTIVO` no admite una segunda desactivación
5. Toda desactivación tiene asociado un motivo perteneciente al catálogo `cat_motivos_desactivacion`
6. Una incidencia en estado `EN_CURSO` asignada a un usuario que se desactiva queda sin asignar, conservando íntegro su historial
7. Ninguna desactivación queda confirmada sin que se haya expuesto previamente el impacto asociado al usuario (incidencias reportadas, incidencias asignadas abiertas o en curso y pertenencia al colectivo de destinatarios)
**Criterios de aceptación:**
1. AC-G-06: Dado una persona que deja de participar en el proceso y tiene incidencias reportadas y/o resueltas, cuando el administrador desactiva su cuenta, entonces (a) su acceso queda cortado en la siguiente petición, (b) ninguna incidencia, entrada de historial o comentario pierde la atribución a su nombre, y (c) sus datos siguen consultables por el administrador hasta deactivated_at + 2 años. (Verificable por: Test automatizado)
2. AC-USU-01: Dado un ADMINISTRADOR autenticado y un técnico ACTIVO con 3 incidencias reportadas y 2 incidencias asignadas en estado EN_CURSO, cuando pulsa Desactivar sobre su ficha, entonces el sistema muestra el panel de impacto con los conteos exactos (3 reportadas, 2 asignadas EN_CURSO) y la indicación de que figura como destinatario del colectivo equipo de mantenimiento, antes de permitir confirmar; y cuando confirma explícitamente, entonces esas 2 incidencias quedan sin asignar conservando su historial completo [gap RFP: no define si deben reasignarse a otro técnico — asunción a validar con cliente]. (Verificable por: UAT manual)
3. AC-USU-02: Dado un ADMINISTRADOR desactivando un usuario ACTIVO, cuando confirma la operación con un deactivation_reason_code perteneciente a cat_motivos_desactivacion, entonces el usuario queda INACTIVO y se persisten deactivated_at, deactivated_by (resuelto desde la sesión, nunca del cliente) y el motivo; y cuando intenta confirmar sin motivo, entonces la operación se rechaza con 422 «Indica el motivo de la desactivación» y el usuario permanece ACTIVO. (Verificable por: Test automatizado)
4. AC-USU-03: Dado un ADMINISTRADOR autenticado, cuando intenta desactivar su propia cuenta, entonces recibe 409 «No puedes desactivar tu propia cuenta»; y dado un usuario ya INACTIVO, cuando se invoca de nuevo la desactivación, entonces recibe 409 «El usuario ya está desactivado» sin producir ningún efecto lateral (idempotencia: ni nueva entrada de histórico, ni nueva revocación, ni recálculo de retention_until). (Verificable por: Test automatizado)
5. AC-USU-05: Dado un usuario con rol EMPLEADO o TECNICO_MANTENIMIENTO autenticado, cuando invoca directamente los endpoints de desactivación, de consulta de histórico de estado de cuenta o de ficha de baja, entonces recibe 403 con el mensaje uniforme de REQ-078, sin revelar si el usuario objetivo existe ni su estado. (Verificable por: Pentest)
**Validaciones:**
1. `user_id` es obligatorio y debe tener formato UUID válido
2. El `user_id` indicado debe corresponder a un usuario existente en el sistema
3. `deactivation_reason_code` es obligatorio (no se acepta la desactivación sin motivo)
4. `deactivation_reason_code` debe ser un código de longitud máxima 30 y pertenecer al catálogo `cat_motivos_desactivacion`
5. `deactivation_note` es opcional y no puede superar los 500 caracteres
6. `status` es obligatorio y debe ser un valor del enumerado `cat_estados_cuenta` (`ACTIVO`|`INACTIVO`)
7. `deactivated_by` debe ser distinto de `user_id` (coherencia entre el actor de la sesión y el usuario objetivo)
8. `deactivated_at` y `deactivated_by` se resuelven en servidor (desde la sesión/reloj del backend) y se ignoran si el cliente los envía
**Escenarios de error:**
1. El identificador de usuario indicado no tiene un formato válido
2. El motivo de desactivación indicado no pertenece al catálogo de motivos admitidos
3. No se ha iniciado sesión o la sesión ya no es válida
4. No tienes permiso para realizar esta acción
5. El usuario indicado no existe
6. El usuario ya está desactivado
7. No puedes desactivar tu propia cuenta
8. Falta indicar el motivo de la desactivación
9. No ha sido posible obtener el impacto de la desactivación en este momento; vuelve a intentarlo
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Debe existir; distinto de deactivated_by
- `status` (enum, obligatorio) — Valores: ACTIVO | INACTIVO (cat_estados_cuenta); solo se desactiva si status = ACTIVO
- `deactivation_reason_code` (string, obligatorio) — Máx. 30 caracteres; valor del catálogo cat_motivos_desactivacion
- `deactivation_note` (string, opcional) — Máx. 500 caracteres
- `deactivated_at` (datetime, obligatorio) — Asignado automáticamente por el sistema
- `deactivated_by` (uuid, obligatorio) — Resuelto desde la sesión; nunca del cliente; ≠ user_id
- `reported_incidents_count` (integer, obligatorio) — Valor calculado (MOD-002); ≥ 0; solo lectura
- `open_assigned_incidents_count` (integer, obligatorio) — Valor calculado (MOD-002); ≥ 0; si ≥ 1 en EN_CURSO exige confirmación explícita
- `is_maintenance_recipient` (boolean, obligatorio) — Valor calculado; solo lectura

### REQ-088 — Conservación y consulta de los datos del usuario desactivado durante la ventana de retención
Reglas: al desactivar se calcula y persiste retention_until = deactivated_at + 2 años, materializando «Los datos de incidencias y usuarios se conservan durante 2 años»; dentro de esa ventana los datos identificativos permanecen almacenados y consultables por el ADMINISTRADOR, sin excepción; no se admite purga, anonimización ni borrado físico antes de retention_until (REQ-047); una reactivación (REQ-042) limpia retention_until y deactivated_at del registro vigente, pero no borra las entradas del histórico de estado (USU-02); [gap: el RFP declara la retención de 2 años pero no indica qué ocurre al vencer el plazo — purga, anonimización o conservación indefinida; no se implementa ningún proceso automático de expiración hasta que el cliente lo defina]. Datos: deactivated_at (timestamp), retention_until (date, calculado, no editable), deactivation_reason_code, full_name, corporate_email, status. Flujo: ficha de usuario (REQ-040) de un usuario INACTIVO → bloque Baja con fecha de desactivación, motivo, administrador que la ejecutó y fecha de fin de retención. Validaciones: retention_until siempre posterior a deactivated_at; campo de solo lectura en la API (se ignora si el cliente lo envía). Errores: 404 «El usuario indicado no existe»; 403 uniforme si el solicitante no es ADMINISTRADOR. Criterios de aceptación: Given un usuario desactivado hace 6 meses, when el ADMINISTRADOR consulta su ficha, then ve sus datos identificativos completos junto a la fecha de desactivación, el motivo y retention_until. Given un usuario desactivado, when se consulta el sistema antes de cumplirse los 2 años, then sus datos siguen almacenados y consultables. Given un usuario reactivado, when se consulta su ficha, then no muestra fecha de fin de retención pero sí conserva la entrada de baja en su histórico de estado. Seguridad: consulta exclusiva de ADMINISTRADOR (REQ-003, REQ-014); el dato personal tratado se limita a nombre y correo corporativo (REQ-049). Dependencias: USU-01, USU-02, REQ-040, REQ-047. Prioridad: Must [inferido]. auth_type: JWT. data_scope: all.
**Reglas de negocio:**
1. El `retention_until` de una cuenta desactivada equivale a `deactivated_at` más 2 años y es siempre posterior a `deactivated_at`
2. Un usuario desactivado no admite purga, anonimización ni borrado físico antes de su `retention_until`
3. `retention_until` es un valor calculado y de solo lectura: cualquier valor aportado por el cliente es irrelevante
4. Una cuenta `ACTIVO` no tiene `deactivated_at` ni `retention_until` informados
5. Una reactivación limpia `deactivated_at` y `retention_until` del registro vigente pero no altera ninguna entrada del histórico de estado
6. Los datos de baja y retención de un usuario son consultables únicamente por el rol `ADMINISTRADOR`
**Criterios de aceptación:**
1. AC-G-06: Dado una persona que deja de participar en el proceso y tiene incidencias reportadas y/o resueltas, cuando el administrador desactiva su cuenta, entonces (a) su acceso queda cortado en la siguiente petición, (b) ninguna incidencia, entrada de historial o comentario pierde la atribución a su nombre, y (c) sus datos siguen consultables por el administrador hasta deactivated_at + 2 años. (Verificable por: Test automatizado)
2. AC-USU-05: Dado un usuario con rol EMPLEADO o TECNICO_MANTENIMIENTO autenticado, cuando invoca directamente los endpoints de desactivación, de consulta de histórico de estado de cuenta o de ficha de baja, entonces recibe 403 con el mensaje uniforme de REQ-078, sin revelar si el usuario objetivo existe ni su estado. (Verificable por: Pentest)
3. AC-TRZ-02: Dado un usuario INACTIVO dentro de la ventana de retención, cuando se intenta eliminarlo físicamente por API o por script, entonces no existe operación de borrado expuesta (respuesta 405/403) y el registro permanece en base de datos; y cuando se inspecciona el modelo de datos, entonces ninguna FK a usuario (reported_by, assigned_to, changed_by) admite quedar a NULL como consecuencia de una desactivación. (Verificable por: Audit externo)
4. AC-TRZ-03: Dado una desactivación ejecutada en la fecha D, cuando se consulta el registro del usuario, entonces retention_until = D + 2 años está persistido, es de solo lectura (un valor enviado por el cliente se ignora) y siempre posterior a deactivated_at; y cuando transcurren 6 meses, entonces sus datos identificativos siguen almacenados y consultables sin purga ni anonimización previa [gap RFP: no define qué ocurre al vencer el plazo — no se implementa proceso de expiración automática]. (Verificable por: Test automatizado)
5. AC-TRZ-04: Dado un usuario desactivado hace 6 meses, cuando el ADMINISTRADOR abre su ficha, entonces ve el bloque Baja con fecha de desactivación, motivo, administrador que la ejecutó y fecha de fin de retención; y dado un usuario reactivado, cuando consulta su ficha, entonces no se muestra fecha de fin de retención pero la entrada de baja sí permanece en su histórico de estado. (Verificable por: UAT manual)
**Validaciones:**
1. `retention_until` debe ser una fecha posterior a `deactivated_at`
2. `retention_until` es un campo de solo lectura: se ignora si el cliente lo envía en la petición
3. El `user_id` de consulta de la ficha es obligatorio, con formato UUID válido, y debe corresponder a un usuario existente
**Escenarios de error:**
1. No se ha iniciado sesión o la sesión ya no es válida
2. No tienes permiso para realizar esta acción
3. El usuario indicado no existe
**Campos de datos:**
- `deactivated_at` (datetime, obligatorio) — Se limpia al reactivar el usuario; base del cálculo de retention_until
- `retention_until` (date, obligatorio) — Calculado: deactivated_at + 2 años; solo lectura (se ignora si lo envía el cliente); siempre posterior a deactivated_at
- `deactivation_reason_code` (string, obligatorio) — Máx. 30 caracteres; catálogo cat_motivos_desactivacion
- `full_name` (string, obligatorio) — No se admite purga ni anonimización antes de retention_until
- `corporate_email` (email, obligatorio) — No se admite purga ni anonimización antes de retention_until
- `status` (enum, obligatorio) — Valores: ACTIVO | INACTIVO

### REQ-089 — Resolución de destinatarios de aviso por correo excluyendo las cuentas desactivadas
Cubre la resolución del destinatario (capacidad funcional), no la composición ni el envío del correo. Reglas: el colectivo equipo de mantenimiento se resuelve como el conjunto de usuarios con role_code = TECNICO_MANTENIMIENTO y status = ACTIVO; un usuario INACTIVO nunca figura entre los destinatarios; la resolución se hace en el momento del envío, no se cachea, para que una desactivación tenga efecto inmediato en el siguiente aviso; el destinatario del aviso de cambio de estado es el EMPLEADO reportante; si su cuenta está INACTIVA, el aviso no se le envía y se deja constancia en el registro de envío del motivo RECIPIENT_INACTIVE — la incidencia sigue su ciclo con normalidad y el cambio de estado no se bloquea; si la resolución del colectivo devuelve una lista vacía, no se envía nada, se registra en auditoría y el hecho es visible para el ADMINISTRADOR [gap: el RFP no define destinatario alternativo cuando no hay ningún técnico de mantenimiento activo]; [ambigüedad] el RFP no aclara si el colectivo «equipo de mantenimiento» equivale exactamente al conjunto de usuarios con rol técnico de mantenimiento o es una lista de distribución independiente; se asume la primera interpretación [gap: composición del colectivo]. Datos: user_id, corporate_email (varchar(254), obligatorio, único — REQ-045), full_name, role_code (FK cat_roles), status (FK cat_estados_cuenta). Flujo: se produce un hecho notificable (alta de incidencia / cambio de estado, MOD-002) → se invoca la resolución de destinatarios → se devuelve la lista de correos elegibles → si está vacía, se registra el motivo. Validaciones: todo destinatario devuelto tiene corporate_email con formato válido y status = ACTIVO; la lista se devuelve sin duplicados. Errores: la indisponibilidad del directorio no puede impedir la operación de negocio subyacente: se registra el fallo y la incidencia continúa su ciclo (modo degradado). Criterios de aceptación: Given un técnico desactivado, when se consulta el directorio de destinatarios del colectivo equipo de mantenimiento, then ese usuario no figura entre los destinatarios. Given dos técnicos activos y uno inactivo, when se crea una incidencia nueva, then la lista de destinatarios contiene exactamente los 2 correos de los técnicos activos. Given un empleado reportante desactivado, when su incidencia cambia de estado, then no se le envía aviso, el cambio de estado se completa y queda constancia del motivo. Seguridad: la resolución es un servicio interno del backend, no expuesto como endpoint de consulta a EMPLEADO ni a TECNICO_MANTENIMIENTO; solo el ADMINISTRADOR puede consultar el resultado con fines de diagnóstico. Integración: servidor SMTP estándar (consumidor de la lista resuelta; el envío no es alcance de este requisito). Dependencias: USU-01, REQ-042, REQ-045; MOD-002. Prioridad: Must [inferido]. auth_type: API_KEY (servicio interno/SMTP). data_scope: all.
**Reglas de negocio:**
1. El colectivo equipo de mantenimiento equivale exactamente al conjunto de usuarios con `role_code = TECNICO_MANTENIMIENTO` y `status = ACTIVO`
2. Un usuario `INACTIVO` nunca figura entre los destinatarios de un aviso por correo
3. La lista de destinatarios resuelta refleja el estado de las cuentas en el instante del envío, sin valores cacheados de resoluciones anteriores
4. Una lista de destinatarios resuelta no contiene direcciones duplicadas y todas sus direcciones tienen formato de correo válido
5. El correo corporativo es único en el conjunto de usuarios
6. Un cambio de estado de incidencia se completa aunque su reportante esté inactivo o el directorio de destinatarios no esté disponible
7. Todo aviso no entregado por destinatario inactivo deja constancia con el motivo `RECIPIENT_INACTIVE`
8. Una resolución de destinatarios con resultado vacío no origina ningún envío y queda registrada en auditoría
9. El resultado de la resolución de destinatarios solo es consultable por el rol `ADMINISTRADOR`
**Criterios de aceptación:**
1. AC-USR-03: Dado un técnico desactivado, cuando un técnico activo despliega el selector de «técnico asignado» en la bandeja, entonces la cuenta desactivada no aparece como opción seleccionable; y cuando ese técnico desactivado figura como asignado histórico de una incidencia, entonces su nombre sigue mostrándose en la fila correspondiente.
**Validaciones:**
1. `corporate_email` es obligatorio, con formato de correo válido y longitud máxima de 254 caracteres
2. `corporate_email` debe ser único en el directorio (no se admiten duplicados en la lista de destinatarios resuelta)
3. `role_code` debe pertenecer al catálogo `cat_roles` y `status` al catálogo `cat_estados_cuenta`
**Escenarios de error:**
1. No tienes permiso para consultar la resolución de destinatarios
2. El colectivo de destinatarios indicado no existe
3. No hay ningún destinatario activo elegible: no se envía aviso y queda constancia del motivo
4. El destinatario del aviso tiene la cuenta desactivada: no se le envía aviso y queda constancia del motivo
5. El directorio de destinatarios no está disponible en este momento; la operación de negocio continúa en modo degradado
6. El servicio de salida de correo no está disponible o no responde a tiempo; la operación de negocio continúa en modo degradado
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Lista devuelta sin duplicados
- `corporate_email` (email, obligatorio) — Máx. 254 caracteres; único; formato de correo válido
- `full_name` (string, obligatorio) — Nombre del destinatario resuelto
- `role_code` (enum, obligatorio) — Catálogo cat_roles; el colectivo equipo de mantenimiento = TECNICO_MANTENIMIENTO
- `status` (enum, obligatorio) — Solo se devuelven usuarios con status = ACTIVO; resolución sin caché
- `non_delivery_reason` (enum, opcional) — Valor RECIPIENT_INACTIVE cuando el reportante está inactivo; se registra sin bloquear el cambio de estado

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-048/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-048/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: validar el modelo de datos que construye esta tarea contra el motor real.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.
El esquema de `TSK-044` ya está APLICADO en este servicio (changelogs Liquibase de su rama mergeada): asume las tablas creadas, no las vuelvas a crear ni las modifiques desde esta tarea.

Esta tarea materializa el MODELO DE DATOS: el motor se levanta VACÍO a propósito, para que valides tu propio changelog contra él. Cómo, exactamente:

```sh
./.mind/TSK-048/liquibase.sh <changelog-maestro>            # aplica (update)
./.mind/TSK-048/liquibase.sh <changelog-maestro> status
./.mind/TSK-048/liquibase.sh <changelog-maestro> rollback-count 99  # reversibilidad
```

Ese script lo genera la plataforma y corre Liquibase como contenedor contra ESTE motor. **NO descargues ni instales Liquibase por tu cuenta**: sus JAR acabarían commiteados en el repo del cliente, y apuntar al `local/*.properties` del arquetipo NO sirve (esa configuración mira a otra BBDD, no a la de tu sesión).

Comprueba las tres cosas antes de entregar: que el `update` termina limpio, que el rollback deshace, y que un segundo `update` es idempotente. Un changelog que nunca se ejecutó no está verificado — y la plataforma lo VUELVE a aplicar por su cuenta tras tu entrega, así que un rojo saldrá igual en el PR.

### Si el entorno no está disponible
Comprueba `.mind/TSK-048/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.