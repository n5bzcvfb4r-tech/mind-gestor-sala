# TSK-049 · Credenciales y bloqueo de cuenta sobre usuario, e historial de contraseñas

- Componente dueño: `ARC-016`
- Arquetipo del repo: `database-relational` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/facilities/changelogs/0.0.1/ddl/06-usuario-credenciales.xml`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
EXTIENDE la tabla `usuario` creada en TSK-05 mediante `addColumn` en changesets propios; NO redeclara `createTable usuario` ni reescribe sus columnas. Añade: username varchar2(100) NOT NULL (check longitud 3..100) con índice único funcional sobre `LOWER(username)`, password_hash NOT NULL, password_salt, password_algorithm, password_updated_at, must_change_password default 1, credential_issued_at, failed_password_attempts NUMBER default 0 check >= 0, last_failed_attempt_at, locked_until. Crea `usuario_password_historico` (history_id PK, user_id FK NOT NULL a `usuario`, password_hash NOT NULL, created_at NOT NULL) con índice (user_id, created_at DESC) y purga que conserva como máximo las 3 últimas por usuario (REQ-069). Oráculos de persistencia contra el Oracle real: la inspección de `USER_TAB_COLUMNS` de `usuario` y `usuario_password_historico` NO devuelve ninguna columna de contraseña en claro (solo hash y sal, REQ-076/AC-G-08); test que inserta cuatro contraseñas sucesivas para un usuario y comprueba que el histórico deja exactamente 3 filas; test que verifica que `failed_password_attempts` es por cuenta (no por sesión) y que un `locked_until` futuro convive con `is_active` sin alterarlo (AC-AUT-04); test que comprueba que dos usuarios con la misma contraseña presentan `password_hash` distintos (sal por usuario). Los nombres físicos coinciden con los usados por la API (`password_hash`, no `passwordHash`). `update`/`rollback-count` verdes.

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

Tus zonas (`sources/facilities/changelogs/0.0.1/ddl/06-usuario-credenciales.xml`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

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

### REQ-038 — Generación y entrega de credencial inicial al nuevo usuario
El sistema genera y entrega al nuevo usuario una credencial inicial de acceso al crearse su cuenta. Reglas: (1) todo usuario creado en USR-01 debe poder autenticarse sin intervención adicional del ADMINISTRADOR más allá del alta; (2) la contraseña nunca se almacena en claro, solo su hash; (3) la credencial inicial es de un solo uso y obliga a cambiarla en el primer acceso; (4) el ADMINISTRADOR puede reemitir la credencial de un usuario `ACTIVO` si no ha accedido o la ha perdido. Flujo: tras persistir el usuario → genera contraseña temporal aleatoria → la marca como pendiente de cambio → la comunica al correo corporativo mediante servidor SMTP estándar → en el primer login fuerza el cambio. Datos: `password_hash` (varchar, obligatorio, hash con salt); `must_change_password` (boolean, inicial `true`); `credential_issued_at` (timestamp); `last_login_at` (timestamp, nullable). Validaciones: la nueva contraseña debe cumplir la política de contraseñas [gap: no se define longitud mínima, complejidad ni caducidad]. Errores: 403 «No tienes permisos para reemitir credenciales»; 409 «El usuario está desactivado» al reemitir sobre cuenta `INACTIVO`; 502 «No se ha podido enviar el correo con la credencial; reinténtalo» si SMTP no responde — el usuario queda creado y la reemisión es reintentable. Criterios de aceptación: Dado un usuario recién creado, cuando accede por primera vez con la credencial recibida, entonces se le obliga a establecer una contraseña nueva antes de operar. Dado un ADMINISTRADOR, cuando reemite la credencial de un usuario activo, entonces la credencial anterior deja de ser válida. Seguridad: solo ADMINISTRADOR puede emitir o reemitir credenciales; nunca visualiza la contraseña en claro. Eventos: `UserCredentialIssued`. Dependencias: USR-01. Integración: Servidor SMTP estándar. Prioridad: Must. auth_type: API_KEY. data_scope: all.
**Reglas de negocio:**
1. La contraseña de un usuario nunca existe almacenada en claro: solo su hash con salt
2. La credencial inicial de un usuario es de un solo uso y pierde validez tras el primer acceso
3. Un usuario con contraseña pendiente de cambio no puede ejecutar ninguna operación funcional hasta establecer una contraseña nueva
4. Solo un usuario en estado ACTIVO admite reemisión de credencial
5. La emisión de una credencial nueva deja sin validez la credencial anterior del mismo usuario
6. Un usuario creado permanece creado aunque falle el envío del correo con su credencial inicial
7. La contraseña en claro de un usuario no es visible para ningún otro usuario, incluido el ADMINISTRADOR
**Criterios de aceptación:**
1. AC-USR-03: **Dado** un usuario recién creado que ha recibido su credencial inicial, **cuando** accede por primera vez con ella, **entonces** el sistema le exige establecer una contraseña nueva antes de permitirle ejecutar ninguna operación funcional, y la credencial inicial deja de ser válida tras ese primer uso (`must_change_password` pasa a `false`). La contraseña nunca se almacena en claro: la verificación en base de datos muestra únicamente `password_hash` con salt.
2. AC-USR-04: **Dado** un usuario en estado `ACTIVO` que ha perdido su credencial, **cuando** el ADMINISTRADOR la reemite, **entonces** la credencial anterior queda invalidada y la nueva se envía al correo corporativo; y **cuando** el servidor SMTP no responde durante un alta, **entonces** el sistema devuelve 502, el usuario permanece creado y la reemisión es reintentable sin duplicar el registro.
**Validaciones:**
1. La nueva contraseña elegida por el usuario en el primer acceso debe cumplir la política de contraseñas definida (longitud mínima, complejidad) [gap: el RFP no define la política]
2. El usuario sobre el que se solicita la reemisión de credencial debe existir y encontrarse en estado `ACTIVO`
**Escenarios de error:**
1. El solicitante no tiene permisos para emitir o reemitir credenciales
2. El usuario destinatario no existe
3. No se puede reemitir la credencial de un usuario desactivado
4. La nueva contraseña elegida no cumple la política de contraseñas
5. No se ha podido enviar el correo con la credencial; la operación es reintentable
6. El servicio de correo no responde dentro del tiempo previsto
**Campos de datos:**
- `password_hash` (string, obligatorio) — Algoritmo de hash con salt; nunca se guarda ni se muestra en claro
- `must_change_password` (boolean, obligatorio) — Valor inicial true; pasa a false tras el cambio
- `credential_issued_at` (datetime, obligatorio) — La emisión nueva invalida la credencial anterior
- `last_login_at` (datetime, opcional) — Nulo mientras el usuario no haya accedido

### REQ-053 — Inicio de sesión con usuario y contraseña propios de la aplicación
El usuario inicia sesión en la aplicación introduciendo su usuario y su contraseña propios de la aplicación. Reglas: solo inicia sesión un usuario existente y con `is_active = true`; credenciales incorrectas, usuario inexistente o usuario inactivo devuelven el mismo mensaje genérico, sin revelar cuál de los dos datos es erróneo (criterio EPIC-003); la contraseña se verifica siempre contra `password_hash` (ver AUT-02), nunca por comparación en claro; un inicio de sesión correcto reinicia `failed_login_attempts` a 0. Flujo: pantalla de acceso → el usuario introduce `username` y `password` → `POST /api/auth/login` → si es válido se emite la sesión (SES-01) y la SPA redirige a la vista inicial correspondiente a su rol (PERM-01); si no, vuelve al formulario con el error genérico. Datos: `username` (string, obligatorio, 3–100, único, comparación case-insensitive, corresponde al correo corporativo del empleado), `password` (string, obligatorio, nunca persistido ni registrado en logs), `is_active` (boolean, obligatorio), `last_login_at` (timestamp, se actualiza solo en el éxito). Validaciones: obligatoriedad en front y back; `trim` de `username`; sin normalización destructiva de `password`. Errores: 400 "Introduce usuario y contraseña"; 401 "Usuario o contraseña incorrectos" (idéntico para credencial errónea, usuario inexistente y usuario inactivo); 423 "Cuenta bloqueada temporalmente" si aplica AUT-03; 500 "No ha sido posible iniciar sesión, inténtelo de nuevo". Criterios: Given usuario activo When introduce credenciales correctas Then 200 y sesión iniciada; Given contraseña incorrecta When intenta acceder Then 401 con mensaje idéntico al de usuario inexistente; Given usuario con `is_active = false` When introduce credenciales correctas Then 401 y no se emite sesión. Seguridad: único endpoint público junto a AUT-03; aplicable a EMPLEADO, TECNICO-DE-MANTENIMIENTO y ADMINISTRADOR por igual; sin doble factor. Eventos: `UserLoggedIn` (consumido por TRZ-01). Dependencias: PRE-02, AUT-02. [gap: política de contraseñas — longitud mínima, complejidad, caducidad y reutilización no están definidas en el RFP]. Prioridad: Must.
**Reglas de negocio:**
1. `username` es único en el sistema, con unicidad evaluada sin distinguir mayúsculas de minúsculas.
2. `username` tiene entre 3 y 100 caracteres y se corresponde con el correo corporativo del empleado.
3. El mensaje devuelto ante un acceso denegado es idéntico para credencial incorrecta, usuario inexistente y usuario inactivo.
4. Tras un inicio de sesión correcto, `failed_login_attempts` de esa cuenta vale 0.
5. `last_login_at` solo cambia como consecuencia de un inicio de sesión correcto.
6. La contraseña en claro no queda persistida ni registrada en logs en ningún punto del flujo de acceso.
**Criterios de aceptación:**
1. AC-AUT-01: Dado un usuario dado de alta por un ADMINISTRADOR con `is_active = true`, cuando introduce su `username` (correo corporativo) y su contraseña correctos en la pantalla de acceso, entonces `POST /api/auth/login` responde 200, se emite una sesión y la SPA lo redirige a la vista inicial correspondiente a su rol.
2. AC-AUT-02: Dado un intento de acceso con contraseña incorrecta, con usuario inexistente o con usuario `is_active = false`, cuando se envía el formulario de acceso, entonces los tres casos devuelven 401 con el mensaje idéntico "Usuario o contraseña incorrectos", no se emite sesión y la respuesta no permite distinguir cuál de los tres casos se ha producido (mismo cuerpo, mismo código y diferencia de latencia < 50 ms).
**Validaciones:**
1. `username` es obligatorio y se valida tanto en el front como en el back antes de invocar el login
2. `password` es obligatorio y se valida tanto en el front como en el back antes de invocar el login
3. `username` debe tener entre 3 y 100 caracteres
4. Al `username` se le aplica `trim` antes de compararlo y la comparación es case-insensitive
5. Sobre `password` no se aplica ninguna normalización destructiva (ni `trim`, ni cambio de mayúsculas/minúsculas)
6. `is_active` es obligatorio y debe ser un booleano en el registro de usuario evaluado
**Escenarios de error:**
1. Falta el usuario o la contraseña en la solicitud de acceso
2. Usuario o contraseña incorrectos (mensaje idéntico para credencial errónea, usuario inexistente o cuenta inactiva)
3. La cuenta está bloqueada temporalmente y no se admiten nuevos intentos de acceso
4. No ha sido posible completar el inicio de sesión; se solicita reintentar más tarde
**Campos de datos:**
- `username` (string, obligatorio) — Longitud 3-100, único, comparación case-insensitive, trim previo
- `password` (string, obligatorio) — Nunca persistida ni registrada en logs; sin normalización destructiva
- `is_active` (boolean, obligatorio) — Si es false se responde 401 con el mismo mensaje genérico y no se emite sesión
- `last_login_at` (datetime, opcional) — Se actualiza únicamente cuando la autenticación es correcta

### REQ-054 — Custodia y verificación de credenciales sin contraseña en texto claro
El sistema custodia y verifica las credenciales de forma que la contraseña nunca se almacena ni se expone en texto claro. Reglas: la contraseña se persiste exclusivamente como `password_hash` con algoritmo de hash con sal y coste configurable [inferido: el RFP solo exige que "la contraseña no figura en texto claro"]; el hash no se devuelve en ninguna respuesta de la API ni se escribe en logs ni en trazas de error; el establecimiento o restablecimiento del valor lo realiza el ADMINISTRADOR desde el módulo de gestión de usuarios, que invoca este servicio de hashing y nunca escribe el campo directamente. Flujo: alta o cambio de credencial → el servicio genera `password_salt` y calcula `password_hash` → se persiste el par; verificación en login → recálculo y comparación en tiempo constante. Datos: `password_hash` (string, obligatorio, no seleccionable por las consultas de usuario), `password_salt` (string, obligatorio si el algoritmo no lo embebe), `password_updated_at` (timestamp). Validaciones: rechazo de cualquier intento de persistir una contraseña sin hashear; comparación resistente a ataques de temporización. Errores: 500 "No ha sido posible completar la operación" si falla el cifrado, sin detalle técnico al usuario. Criterios: Given unas credenciales de usuario When se consulta su almacenamiento en base de datos Then la contraseña no figura en texto claro; Given una respuesta cualquiera de la API de usuarios When se inspecciona el cuerpo Then no contiene `password_hash` ni `password_salt`; Given dos usuarios con la misma contraseña When se comparan sus hashes Then son distintos. Seguridad: ningún rol —incluido ADMINISTRADOR— puede leer la contraseña; el ADMINISTRADOR solo puede fijar una nueva. Dependencias: AUT-01. Prioridad: Must.
**Reglas de negocio:**
1. Una contraseña existe en el sistema exclusivamente en forma de hash con sal; no hay representación en texto claro.
2. Dos usuarios con la misma contraseña tienen valores de `password_hash` distintos.
3. Ninguna respuesta de la API, log o traza de error contiene `password_hash` ni `password_salt`.
4. Ningún rol, incluido ADMINISTRADOR, tiene capacidad de lectura de la contraseña de un usuario; el ADMINISTRADOR únicamente puede fijar un valor nuevo.
**Criterios de aceptación:**
1. AC-AUT-03: Dadas unas credenciales de usuario cualesquiera, cuando se inspeccionan la tabla de usuarios, cualquier respuesta de la API de usuarios o de contexto, y los logs y trazas de error, entonces la contraseña no figura en texto claro, no aparecen `password_hash` ni `password_salt` en ninguna respuesta, y dos usuarios con la misma contraseña presentan hashes distintos.
**Validaciones:**
1. Se rechaza cualquier intento de persistir una contraseña que no venga hasheada por el servicio de hashing
2. `password_hash` es obligatorio al escribir la credencial
3. `password_salt` es obligatorio cuando el algoritmo de hash no lo embebe en el propio hash
**Escenarios de error:**
1. Se intenta registrar una contraseña sin el tratamiento de protección exigido
2. No ha sido posible completar la operación de custodia de la credencial
**Campos de datos:**
- `password_hash` (string, obligatorio) — No seleccionable por las consultas de usuario ni expuesto en respuestas API o logs
- `password_salt` (string, opcional) — Obligatorio solo si el algoritmo de hash no la embebe; nunca expuesto en la API
- `password_updated_at` (datetime, opcional) — Se actualiza al fijar un nuevo valor desde el módulo de gestión de usuarios

### REQ-055 — Limitación de intentos de inicio de sesión fallidos consecutivos
El sistema limita los intentos de inicio de sesión fallidos consecutivos sobre una misma cuenta. Reglas: [inferido: el RFP no describe la protección, pero es condición para que el acceso con credenciales propias sea defendible] cada fallo incrementa `failed_login_attempts`; alcanzado el umbral, la cuenta queda bloqueada hasta `locked_until` y todo intento posterior se rechaza aunque la contraseña sea correcta; un login correcto o el vencimiento del bloqueo reinician el contador; el ADMINISTRADOR puede desbloquear manualmente. Flujo: fallo de AUT-01 → incremento de contador → si supera umbral, fijar `locked_until` → respuesta genérica al usuario indicando bloqueo temporal, sin decir cuántos intentos quedan. Datos: `failed_login_attempts` (integer, ≥ 0, por usuario), `locked_until` (timestamp, nullable), `last_failed_login_at` (timestamp, nullable). Validaciones: el contador es por cuenta, no por navegador; el bloqueo no elimina ni desactiva al usuario (`is_active` no se modifica). Errores: 423 "Cuenta bloqueada temporalmente, inténtelo más tarde". Criterios: Given una cuenta con intentos fallidos por debajo del umbral When acierta la contraseña Then inicia sesión y el contador vuelve a 0; Given una cuenta bloqueada When introduce la contraseña correcta Then 423 y no se emite sesión; Given una cuenta bloqueada When vence `locked_until` Then el siguiente intento correcto inicia sesión. Seguridad: el desbloqueo manual es exclusivo de ADMINISTRADOR y queda auditado (TRZ-01); los intentos fallidos se registran sin la contraseña introducida. Eventos: `UserLoginFailed`, `UserAccountLocked` (consumidos por TRZ-01). Dependencias: AUT-01. [gap: umbral de intentos y duración del bloqueo no aportados por el cliente]. Prioridad: Should.
**Reglas de negocio:**
1. Una cuenta con `locked_until` en el futuro no obtiene sesión aunque las credenciales presentadas sean correctas.
2. `failed_login_attempts` es un entero mayor o igual que 0 y es único por cuenta de usuario, no por navegador ni dispositivo.
3. Un bloqueo por intentos fallidos no altera el valor de `is_active` del usuario.
4. Vencido `locked_until`, la cuenta vuelve a admitir un inicio de sesión correcto sin intervención manual.
5. El desbloqueo manual de una cuenta es exclusivo del rol ADMINISTRADOR.
**Criterios de aceptación:**
1. AC-AUT-04: Dada una cuenta que acumula intentos de inicio de sesión fallidos consecutivos, cuando alcanza el umbral configurado, entonces queda bloqueada hasta `locked_until` y todo intento posterior responde 423 aunque la contraseña sea correcta y sin revelar intentos restantes; y cuando el usuario acierta la contraseña por debajo del umbral o vence `locked_until`, entonces inicia sesión y `failed_login_attempts` vuelve a 0. [gap: umbral de intentos y duración del bloqueo no aportados por el cliente — el valor debe fijarse antes del UAT]
2. AC-AUT-05: Dada una cuenta bloqueada por intentos fallidos, cuando un ADMINISTRADOR ejecuta el desbloqueo manual, entonces el usuario puede volver a iniciar sesión con credenciales correctas, `is_active` no se ha modificado en ningún momento y el desbloqueo queda registrado en la auditoría de accesos; y cuando lo intenta un rol distinto de ADMINISTRADOR, entonces obtiene 403.
**Validaciones:**
1. `failed_login_attempts` debe ser un entero mayor o igual que 0
2. `locked_until` y `last_failed_login_at` admiten nulo y, cuando se informan, deben ser timestamps válidos
3. El contador de intentos fallidos se identifica por cuenta de usuario, no por navegador o dispositivo de la petición
**Escenarios de error:**
1. Se intenta acceder a una cuenta bloqueada temporalmente, incluso con la contraseña correcta
2. Se solicita desbloquear una cuenta sin tener el rol de administrador
3. La cuenta indicada para desbloquear no existe
4. La cuenta indicada no se encuentra bloqueada, por lo que no procede desbloquearla
**Campos de datos:**
- `failed_login_attempts` (integer, obligatorio) — Valor ≥ 0, por cuenta y no por navegador; se reinicia a 0 con un login correcto
- `locked_until` (datetime, opcional) — Nullable; mientras esté vigente se rechaza el acceso aunque la contraseña sea correcta
- `last_failed_login_at` (datetime, opcional) — Nullable; se registra sin la contraseña introducida

### REQ-063 — Las contraseñas solo existen como hash con sal, nunca expuestas
Las contraseñas solo existen en forma de hash con sal: no se almacenan, no se devuelven por la API y no se escriben en logs ni en trazas de error, con independencia del rol que ejecute la operación. Aplica a: AUT, módulo de gestión de usuarios y roles.
**Reglas de negocio:**
1. Las contraseñas existen únicamente como hash con sal, con independencia del rol que ejecute la operación que las manipula.
**Criterios de aceptación:**
1. AC-AUT-03: Dadas unas credenciales de usuario cualesquiera, cuando se inspeccionan la tabla de usuarios, cualquier respuesta de la API de usuarios o de contexto, y los logs y trazas de error, entonces la contraseña no figura en texto claro, no aparecen `password_hash` ni `password_salt` en ninguna respuesta, y dos usuarios con la misma contraseña presentan hashes distintos.

### REQ-068 — Cambio de la propia contraseña aportando la actual y una nueva
El usuario autenticado puede cambiar su propia contraseña aportando la contraseña actual y una nueva. Reglas: (1) sólo el titular de la sesión cambia su propia contraseña, nunca la de otro; (2) el cambio exige current_password correcta; (3) new_password debe cumplir la política de PWD-02 y ser distinta de la actual; (4) el cambio es inmediato: «la contraseña queda actualizada y el siguiente inicio de sesión solo funciona con la nueva»; (5) si la actual es incorrecta «el sistema rechaza el cambio y la contraseña anterior sigue siendo válida». Flujo: usuario autenticado → Mi perfil → Cambiar contraseña → introduce actual, nueva y confirmación → confirma → éxito, se marca must_change_password=false y se revocan sesiones (PWD-03). Ramas: actual incorrecta → rechazo e incremento de failed_password_attempts (PWD-05); nueva no conforme → rechazo enumerando las reglas incumplidas. Datos: user_id (uuid, tomado de la sesión, nunca del payload), current_password (string, obligatorio, no persistido), new_password (string, obligatorio), new_password_confirmation (string, obligatorio, idéntico a new_password), password_hash (string, generado), password_updated_at (timestamp). Validaciones: obligatorios no vacíos; confirmación coincidente; nueva ≠ actual; política PWD-02. Errores: 400 «Las contraseñas no coinciden»; 422 «La contraseña actual no es correcta»; 422 «La nueva contraseña no cumple la política de seguridad» con detalle; 423/429 «Cuenta bloqueada temporalmente por intentos fallidos». Criterios de aceptación: Given un usuario con sesión iniciada, When cambia su contraseña aportando la actual y una nueva válida, Then queda actualizada y el siguiente inicio de sesión sólo funciona con la nueva. Given una contraseña actual incorrecta, When confirma, Then el sistema rechaza el cambio y la anterior sigue siendo válida. Seguridad: ejecutable por EMPLEADO, TECNICO_DE_MANTENIMIENTO y ADMINISTRADOR, siempre con alcance de datos restringido a su propia cuenta; sin doble factor [inferido]. Evento de dominio: PasswordChanged [inferido]. Dependencias: PWD-02, PWD-03, PRE-02 e inicio de sesión (otra épica). Prioridad: Must [inferido]. auth_type: SESSION. data_scope: own_only.
**Reglas de negocio:**
1. Un usuario sólo puede cambiar la contraseña de su propia cuenta, identificada por el `user_id` de la sesión y nunca por el del payload
2. Un cambio de contraseña sólo es válido si la `current_password` aportada coincide con la contraseña vigente de la cuenta
3. La nueva contraseña es distinta de la contraseña vigente de la cuenta
4. `new_password_confirmation` es idéntica a `new_password`
5. Tras un cambio con éxito, la contraseña anterior deja de ser válida de forma inmediata para cualquier inicio de sesión
6. Tras un cambio rechazado, la contraseña anterior sigue siendo válida y `password_hash` permanece inalterado
7. Una cuenta que completa un cambio de contraseña con éxito queda con `must_change_password` a false
**Criterios de aceptación:**
1. AC-PWD-01: Dado un usuario con sesión iniciada, cuando cambia su contraseña aportando la actual correcta y una nueva conforme a la política, entonces la operación responde éxito, `password_updated_at` se actualiza, `must_change_password` pasa a false y el siguiente inicio de sesión sólo funciona con la nueva contraseña.
2. AC-PWD-02: Dado un usuario con sesión iniciada, cuando intenta cambiar su contraseña aportando una `current_password` incorrecta, entonces el sistema responde 422 «La contraseña actual no es correcta», la contraseña anterior sigue siendo válida en el siguiente login y `failed_password_attempts` se incrementa en 1.
3. AC-PWD-03: Dado una contraseña que incumple la política (menos de 10 caracteres, o sin mayúscula, o sin minúscula, o sin dígito, o que contiene el `username`), cuando se intenta establecer desde cualquiera de los tres flujos (cambio propio, primer acceso forzado, restablecimiento por administrador), entonces los tres responden 422 con la lista de reglas incumplidas y `password_hash` no se modifica en ninguno.
4. AC-PWD-05: Dado un usuario con sesión abierta simultáneamente en dos navegadores, cuando cambia su contraseña desde uno de ellos, entonces la siguiente petición del otro navegador responde 401 con redirección al login y la sesión desde la que ejecutó el cambio continúa operativa.
**Validaciones:**
1. `current_password` es obligatoria y no puede estar vacía
2. `new_password` es obligatoria y no puede estar vacía
3. `new_password_confirmation` es obligatoria y debe coincidir exactamente con `new_password`
4. `new_password` debe ser distinta de `current_password`
5. `new_password` debe cumplir la política de contraseñas definida en REQ-069 antes de aceptarse
6. El `user_id` se toma siempre de la sesión: si el payload incluye un `user_id`, la petición se rechaza (no se acepta como dato de entrada)
**Escenarios de error:**
1. Falta alguno de los datos obligatorios: contraseña actual, nueva o confirmación
2. La nueva contraseña y su confirmación no coinciden
3. Se intenta cambiar la contraseña de una cuenta distinta a la del titular de la sesión
4. La contraseña actual aportada no es correcta
5. La nueva contraseña no cumple la política de seguridad vigente
6. La nueva contraseña coincide con la que ya está en uso
7. La cuenta está bloqueada temporalmente por intentos fallidos y no admite el cambio
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Se toma siempre de la sesión, nunca del payload
- `current_password` (string, obligatorio) — No se persiste; verificación en tiempo constante
- `new_password` (string, obligatorio) — Debe cumplir la política de REQ-069 y ser distinta de la actual
- `new_password_confirmation` (string, obligatorio) — Idéntica a `new_password`
- `password_hash` (string, obligatorio) — Nunca en claro; algoritmo adaptativo con salt por usuario
- `password_updated_at` (datetime, obligatorio) — Referencia para la revocación de sesiones de REQ-070

### REQ-069 — Política única de contraseñas y almacenamiento irreversible de credenciales
El sistema aplica una política única de contraseñas y almacena las credenciales de forma irreversible en todos los puntos donde se establece una contraseña. Reglas: (1) la contraseña nunca se almacena, transmite ni registra en claro: sólo password_hash con algoritmo adaptativo con salt por usuario (argon2id o bcrypt) [inferido]; (2) la comparación se realiza en tiempo constante; (3) no se permite reutilizar las últimas N contraseñas; (4) la política se evalúa en un único servicio consumido por PWD-01, PWD-04 y RST-01, de modo que una contraseña rechazada en un flujo lo es en todos. Datos: password_hash (string, obligatorio), password_algorithm (string), password_updated_at (timestamp), tabla user_password_history con user_id (uuid), password_hash (string), created_at (timestamp), conservando las N últimas. Validaciones concretas [inferido] a falta de definición del cliente: mínimo 10 caracteres, al menos una mayúscula, una minúscula y un dígito; no contener username ni la parte local de corporate_email; no coincidir con ninguna de las 3 últimas del historial. [gap: el RFP no define la política de contraseñas — longitud mínima, complejidad exigida, caducidad periódica y número de contraseñas no reutilizables]. Errores: 422 con lista de reglas incumplidas, sin revelar el hash ni pistas de la contraseña anterior. Criterios de aceptación: Given una contraseña que incumple la longitud mínima, When se intenta establecer desde cualquier flujo, Then se rechaza con 422 y no se modifica password_hash. Given una contraseña idéntica a una de las 3 últimas, When se intenta establecer, Then se rechaza. Given una contraseña válida, When se establece, Then se persiste sólo su hash y se añade la anterior al historial. Seguridad: servicio interno; ningún rol (incluido ADMINISTRADOR) puede leer ni recuperar una contraseña; prohibido volcarla en logs o trazas. Dependencias: PRE-01. Prioridad: Must [inferido]. auth_type: NONE (servicio interno). data_scope: (no aplica).
**Reglas de negocio:**
1. La única representación persistida de una contraseña es un hash irreversible con salt propio por usuario
2. Una contraseña válida no coincide con ninguna de las 3 últimas contraseñas del historial de ese usuario
3. Una contraseña válida tiene al menos 10 caracteres e incluye al menos una mayúscula, una minúscula y un dígito
4. Una contraseña válida no contiene el `username` del usuario ni la parte local de su `corporate_email`
5. Una contraseña rechazada por la política en un flujo lo es en todos los flujos que establecen contraseña (REQ-068, REQ-071 y REQ-073)
6. `user_password_history` conserva como máximo las 3 últimas contraseñas por usuario
7. Un mensaje de rechazo por política no revela el hash ni pistas sobre la contraseña anterior
**Criterios de aceptación:**
1. AC-PWD-01: Dado un usuario con sesión iniciada, cuando cambia su contraseña aportando la actual correcta y una nueva conforme a la política, entonces la operación responde éxito, `password_updated_at` se actualiza, `must_change_password` pasa a false y el siguiente inicio de sesión sólo funciona con la nueva contraseña.
2. AC-PWD-03: Dado una contraseña que incumple la política (menos de 10 caracteres, o sin mayúscula, o sin minúscula, o sin dígito, o que contiene el `username`), cuando se intenta establecer desde cualquiera de los tres flujos (cambio propio, primer acceso forzado, restablecimiento por administrador), entonces los tres responden 422 con la lista de reglas incumplidas y `password_hash` no se modifica en ninguno.
3. AC-PWD-04: Dado un usuario cuyas 3 últimas contraseñas constan en `user_password_history`, cuando intenta establecer una idéntica a cualquiera de ellas, entonces el sistema la rechaza con 422 y, cuando establece una contraseña válida no reutilizada, entonces persiste sólo el hash y desplaza la anterior al historial.
4. AC-RST-01: Dado un administrador autenticado y un usuario activo que ha olvidado su contraseña, cuando el administrador ejecuta el restablecimiento desde el listado de usuarios, entonces el usuario puede iniciar sesión con la credencial repuesta, es forzado a establecer una contraseña propia antes de operar y el administrador no ve en ningún momento la contraseña anterior ni la nueva definitiva.
**Validaciones:**
1. La contraseña debe tener un mínimo de 10 caracteres
2. La contraseña debe contener al menos una letra mayúscula, una minúscula y un dígito
3. La contraseña no puede contener el `username` ni la parte local del `corporate_email` del usuario
4. La contraseña no puede coincidir con ninguna de las 3 últimas registradas en `user_password_history` del usuario
**Escenarios de error:**
1. La contraseña propuesta no alcanza la longitud mínima exigida
2. La contraseña propuesta no incluye la combinación de mayúsculas, minúsculas y dígitos exigida
3. La contraseña propuesta coincide con alguna de las últimas utilizadas por la cuenta
4. La contraseña propuesta contiene el identificador de usuario o la parte local de su correo corporativo
**Campos de datos:**
- `password_hash` (string, obligatorio) — Hash irreversible con salt por usuario (argon2id o bcrypt) `[inferido]`
- `password_algorithm` (string, obligatorio) — Permite rehash al evolucionar la política
- `password_updated_at` (datetime, obligatorio) — Momento del último establecimiento de contraseña
- `new_password` (string, obligatorio) — Mín. 10 caracteres, ≥1 mayúscula, ≥1 minúscula, ≥1 dígito; no contener `username` ni la parte local de `corporate_email` `[inferido]`
- `history_user_id` (uuid, obligatorio) — Clave de `user_password_history`
- `history_password_hash` (string, obligatorio) — No coincidir con ninguna de las 3 últimas `[inferido]`
- `history_created_at` (datetime, obligatorio) — Se conservan las N últimas (N=3 `[inferido]`)

### REQ-072 — Bloqueo temporal de cuenta tras verificaciones fallidas de contraseña
El sistema bloquea temporalmente la cuenta tras un número máximo de verificaciones fallidas de contraseña. Reglas [inferido: el RFP no menciona bloqueo por intentos fallidos]: (1) toda verificación fallida de contraseña —tanto en el inicio de sesión como en la comprobación de current_password de PWD-01— incrementa failed_password_attempts; (2) alcanzados 5 intentos se fija locked_until = now + 15 min y se rechaza cualquier intento hasta esa hora; (3) una verificación correcta reinicia el contador a 0; (4) el desbloqueo anticipado sólo lo realiza el ADMINISTRADOR (RST-03); (5) los mensajes no revelan si el usuario existe. Datos: user_id (uuid), failed_password_attempts (integer, ≥0, por defecto 0), last_failed_attempt_at (timestamp, nullable), locked_until (timestamp, nullable). [gap: umbral de intentos fallidos y duración del bloqueo no definidos por el cliente]. Validaciones: el contador es por cuenta, no por sesión ni por IP. Errores: 423 «Cuenta bloqueada temporalmente. Inténtalo de nuevo a las HH:MM o contacta con el administrador»; mensaje idéntico para usuario inexistente y contraseña incorrecta. Criterios de aceptación: Given un usuario con 4 fallos consecutivos, When falla una quinta vez, Then la cuenta queda bloqueada y el sexto intento con la contraseña correcta también se rechaza. Given un bloqueo vencido, When el usuario acierta la contraseña, Then accede y el contador vuelve a 0. Seguridad: el estado de bloqueo sólo es visible para el ADMINISTRADOR (RST-02); el propio usuario ve únicamente el mensaje genérico. Evento de dominio: AccountLocked [inferido]. Dependencias: PWD-01, RST-03, inicio de sesión (otra épica). Prioridad: Should [inferido]. auth_type: NONE (aplica también en el login, ruta pública). data_scope: own_only.
**Reglas de negocio:**
1. Una cuenta con 5 verificaciones fallidas consecutivas de contraseña queda bloqueada durante 15 minutos
2. Mientras `locked_until` sea posterior al instante actual, ningún intento de autenticación de esa cuenta es aceptado, aun con la contraseña correcta
3. Una verificación correcta de contraseña deja `failed_password_attempts` a 0
4. El contador de intentos fallidos es único por cuenta, no por sesión ni por dirección IP
5. El mensaje devuelto ante credenciales incorrectas es idéntico para un usuario inexistente y para una contraseña errónea
6. El estado de bloqueo de una cuenta sólo es visible para el ADMINISTRADOR
**Criterios de aceptación:**
1. AC-PWD-02: Dado un usuario con sesión iniciada, cuando intenta cambiar su contraseña aportando una `current_password` incorrecta, entonces el sistema responde 422 «La contraseña actual no es correcta», la contraseña anterior sigue siendo válida en el siguiente login y `failed_password_attempts` se incrementa en 1.
2. AC-PWD-09: Dado una cuenta con 4 verificaciones fallidas consecutivas de contraseña, cuando falla una quinta vez, entonces se fija `locked_until = now + 15 min` y el siguiente intento, aun con la contraseña correcta, se rechaza con 423 indicando la hora de desbloqueo [inferido: umbral y duración no definidos en el RFP].
3. AC-PWD-10: Dado una cuenta con bloqueo vencido o con verificación correcta previa, cuando el usuario acierta la contraseña, entonces accede correctamente y `failed_password_attempts` queda en 0.
4. AC-PWD-11: Dado un intento de autenticación con un usuario inexistente y otro con usuario existente y contraseña incorrecta, cuando se comparan ambas respuestas, entonces el mensaje, el código HTTP y el tiempo de respuesta no permiten distinguir los dos casos (diferencia de latencia media < 50 ms sobre 100 intentos).
5. AC-RST-06: Dado una cuenta bloqueada por intentos fallidos, cuando el administrador la desbloquea desde el listado, entonces `locked_until` queda a null, `failed_password_attempts` a 0, el usuario inicia sesión de inmediato con su contraseña vigente sin cambiarla y sus sesiones no son revocadas.
**Validaciones:**
1. `failed_password_attempts` debe ser un entero mayor o igual a 0 (valor por defecto 0)
**Escenarios de error:**
1. La verificación de contraseña falla; se responde con un mensaje genérico que no revela si la cuenta existe
2. La cuenta ha superado el número máximo de intentos fallidos y queda bloqueada temporalmente
**Campos de datos:**
- `user_id` (uuid, obligatorio) — Contador por cuenta, no por sesión ni por IP
- `failed_password_attempts` (integer, obligatorio) — ≥0, por defecto 0; se reinicia con una verificación correcta; umbral 5 `[inferido]`
- `last_failed_attempt_at` (datetime, opcional) — Vacío si no hay fallos registrados
- `locked_until` (datetime, opcional) — `now + 15 min` al alcanzar el umbral `[inferido]`; `[gap: duración no definida]`

### REQ-074 — Consulta del estado de credencial de los usuarios con filtros por el ADMINISTRADOR
El ADMINISTRADOR puede consultar el estado de credencial de los usuarios con filtros, para decidir sobre quién actuar. Reglas: (1) la vista muestra únicamente estado de credencial y acceso, nunca hashes ni contraseñas; (2) el listado es la puerta de entrada a las acciones RST-01 y RST-03; (3) el orden por defecto sitúa arriba las cuentas bloqueadas y las pendientes de cambio. Flujo: ADMINISTRADOR → Usuarios → listado paginado → filtra o busca → selecciona una fila → acciones «Restablecer contraseña» o «Desbloquear». Datos mostrados: full_name (string), corporate_email (string), role_code (código de cat_roles: EMPLEADO, TECNICO_DE_MANTENIMIENTO, ADMINISTRADOR), password_updated_at (timestamp, nullable), must_change_password (boolean), locked_until (timestamp, nullable), last_login_at (timestamp, nullable). Filtros: por role_code, por estado de bloqueo (bloqueado/no bloqueado), por pendiente de cambio de contraseña; búsqueda libre por nombre o correo; paginación 25 por página [inferido]. Validaciones: filtros con valores del catálogo; búsqueda mínima de 3 caracteres. Errores: 403 «No tienes permisos para consultar usuarios»; 200 con lista vacía y mensaje «No hay usuarios que cumplan los filtros» cuando no hay resultados. Criterios de aceptación: Given un administrador autenticado, When filtra por cuentas bloqueadas, Then ve sólo las cuentas con locked_until en el futuro y puede lanzar el desbloqueo. Given un técnico de mantenimiento, When accede a la ruta del listado, Then recibe 403. Seguridad: exclusivo de ADMINISTRADOR, alcance sobre todos los usuarios; ningún campo de la respuesta contiene password_hash. [inferido] el RFP no describe esta pantalla; se deriva de que «un administrador da de alta a los usuarios y les asigna el rol» y de la necesidad operativa de seleccionar al usuario a restablecer. Dependencias: alta de usuario y asignación de rol (épica de gestión de usuarios, fuera de este alcance). Prioridad: Should [inferido]. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Los valores de los filtros por rol pertenecen al catálogo `cat_roles`
2. El listado devuelve como máximo 25 usuarios por página
3. En el orden por defecto las cuentas bloqueadas y las pendientes de cambio de contraseña preceden al resto
4. Una búsqueda libre por nombre o correo tiene al menos 3 caracteres
5. El listado de estado de credenciales es accesible exclusivamente para el rol ADMINISTRADOR
6. Ningún campo de la respuesta del listado contiene `password_hash` ni contraseña alguna
**Criterios de aceptación:**
1. AC-RST-04: Dado un administrador autenticado, cuando filtra el listado de usuarios por estado de bloqueo, por `role_code` o por pendiente de cambio de contraseña, entonces obtiene únicamente las filas que cumplen el filtro, con `locked_until` en el futuro para las bloqueadas, y ninguna respuesta del endpoint contiene `password_hash` ni ningún material de credencial.
2. AC-RST-05: Dado un técnico de mantenimiento o un empleado autenticados, cuando acceden a la ruta del listado de usuarios, entonces reciben 403 y ninguna fila de datos de otros usuarios.
3. AC-RST-06: Dado una cuenta bloqueada por intentos fallidos, cuando el administrador la desbloquea desde el listado, entonces `locked_until` queda a null, `failed_password_attempts` a 0, el usuario inicia sesión de inmediato con su contraseña vigente sin cambiarla y sus sesiones no son revocadas.
**Validaciones:**
1. El filtro `role_code`, si se informa, debe ser un valor del catálogo `cat_roles`
2. El filtro de estado de bloqueo, si se informa, debe tomar uno de los valores admitidos (bloqueado / no bloqueado)
3. El filtro de pendiente de cambio de contraseña, si se informa, debe ser un booleano
4. El término de búsqueda libre por nombre o correo debe tener al menos 3 caracteres
**Escenarios de error:**
1. El texto de búsqueda libre no alcanza el mínimo de caracteres exigido
2. Se ha indicado un valor de filtro de rol o de estado que no pertenece al catálogo admitido
3. Los parámetros de paginación son inválidos o están fuera del rango admitido
4. El solicitante no tiene permisos para consultar el estado de credencial de los usuarios
**Campos de datos:**
- `full_name` (string, obligatorio) — Campo de búsqueda libre
- `corporate_email` (email, obligatorio) — Campo de búsqueda libre
- `role_code` (enum, obligatorio) — EMPLEADO, TECNICO_DE_MANTENIMIENTO, ADMINISTRADOR (`cat_roles`)
- `password_updated_at` (datetime, opcional) — Vacío si nunca la ha cambiado
- `must_change_password` (boolean, obligatorio) — Criterio de filtro y de orden por defecto
- `locked_until` (datetime, opcional) — Bloqueada si el valor es futuro; vacío si no hay bloqueo
- `last_login_at` (datetime, opcional) — Vacío si nunca ha iniciado sesión
- `filter_role_code` (enum, opcional) — Valores del catálogo `cat_roles`
- `filter_lock_status` (enum, opcional) — Valores: bloqueado, no bloqueado
- `filter_must_change_password` (boolean, opcional) — Filtro por cuentas pendientes de cambio de contraseña
- `search_text` (string, opcional) — Mínimo 3 caracteres
- `page_number` (integer, opcional) — ≥1; 25 resultados por página `[inferido]`

### REQ-075 — Desbloqueo por el ADMINISTRADOR de una cuenta bloqueada por intentos fallidos
El ADMINISTRADOR puede desbloquear una cuenta bloqueada por intentos fallidos sin necesidad de restablecer la contraseña. Reglas: (1) el desbloqueo pone locked_until=null y failed_password_attempts=0, manteniendo intacta la contraseña vigente; (2) sólo aplica a cuentas con bloqueo activo; (3) el ADMINISTRADOR puede encadenar opcionalmente un restablecimiento (RST-01) si el usuario también ha olvidado la contraseña; (4) el desbloqueo no revoca sesiones. Flujo: ADMINISTRADOR → listado (RST-02) filtrado por bloqueadas → selecciona usuario → confirma desbloqueo → la cuenta admite de nuevo intentos de autenticación. Datos: target_user_id (uuid, obligatorio), unlocked_by_user_id (uuid, de la sesión), unlocked_at (timestamp), failed_password_attempts (integer → 0), locked_until (timestamp → null). Validaciones: usuario existente y activo; bloqueo vigente en el momento de la operación. Errores: 403 «No tienes permisos para desbloquear cuentas»; 404 «Usuario no encontrado»; 409 «La cuenta no está bloqueada». Criterios de aceptación: Given una cuenta bloqueada por intentos fallidos, When el administrador la desbloquea, Then el usuario puede iniciar sesión de inmediato con su contraseña vigente. Given una cuenta no bloqueada, When el administrador intenta desbloquearla, Then responde 409 y no se altera ningún dato. Given un empleado, When invoca la operación, Then responde 403. Seguridad: exclusivo de ADMINISTRADOR, alcance sobre todos los usuarios; la acción queda auditada con actor, usuario afectado y marca temporal (ver RN-02). Evento de dominio: AccountUnlocked [inferido]. Dependencias: PWD-05, RST-02. Prioridad: Should [inferido]. auth_type: SESSION. data_scope: all.
**Reglas de negocio:**
1. Sólo puede desbloquearse una cuenta con bloqueo vigente en el momento de la operación
2. Una cuenta desbloqueada queda con `locked_until` a null y `failed_password_attempts` a 0
3. Un desbloqueo no modifica la contraseña vigente del usuario ni revoca ninguna de sus sesiones
4. El desbloqueo de una cuenta es una operación exclusiva del rol ADMINISTRADOR
**Criterios de aceptación:**
1. AC-RST-06: Dado una cuenta bloqueada por intentos fallidos, cuando el administrador la desbloquea desde el listado, entonces `locked_until` queda a null, `failed_password_attempts` a 0, el usuario inicia sesión de inmediato con su contraseña vigente sin cambiarla y sus sesiones no son revocadas.
2. AC-RST-07: Dado una cuenta sin bloqueo activo, cuando el administrador intenta desbloquearla, entonces el sistema responde 409 «La cuenta no está bloqueada» y no se altera ningún dato de la cuenta.
**Validaciones:**
1. `target_user_id` es obligatorio y debe tener formato UUID válido
2. `target_user_id` debe corresponder a un usuario existente en el sistema
**Escenarios de error:**
1. El solicitante no tiene permisos para desbloquear cuentas
2. La cuenta de destino indicada no existe
3. La cuenta de destino no tiene un bloqueo vigente en este momento
4. La cuenta de destino está inactiva y no admite el desbloqueo
**Campos de datos:**
- `target_user_id` (uuid, obligatorio) — Debe existir, estar activo y tener bloqueo vigente
- `unlocked_by_user_id` (uuid, obligatorio) — Se toma de la sesión; sólo rol ADMINISTRADOR
- `unlocked_at` (datetime, obligatorio) — Queda auditada conforme a REQ-077
- `failed_password_attempts` (integer, obligatorio) — Pasa a 0
- `locked_until` (datetime, opcional) — Pasa a vacío; no se altera la contraseña vigente

### REQ-076 — Prohibición de almacenar, transmitir o registrar contraseñas en claro
Ninguna contraseña se almacena, transmite, devuelve por API ni se escribe en logs o trazas en claro: la única representación persistida es un hash irreversible con salt por usuario. Ningún rol, incluido el ADMINISTRADOR, puede leer o recuperar la contraseña de otro usuario. Aplica a: todos los dominios que establecen o verifican credenciales (PWD, RST) y el inicio de sesión de la épica de autenticación. auth_type: NONE (regla transversal). data_scope: (no aplica).
**Reglas de negocio:**
1. Ninguna contraseña existe en claro en almacenamiento, respuestas de API, logs ni trazas
2. Ningún rol, incluido el ADMINISTRADOR, puede leer ni recuperar la contraseña de otro usuario
**Criterios de aceptación:**
1. AC-G-08: Dado un usuario con credencial establecida, cuando se inspeccionan base de datos, respuestas de la API, logs y trazas de la aplicación, entonces no aparece ninguna contraseña en claro (sólo hash irreversible con salt por usuario) y ninguna cuenta —incluida la de ADMINISTRADOR— dispone de operación alguna que devuelva o revele la contraseña de otro usuario.
2. AC-XFN-03: Dado las operaciones de establecer, cambiar, verificar y restablecer contraseña, cuando se revisan base de datos, payloads de respuesta, logs de aplicación y trazas tras ejecutarlas todas, entonces ninguna contiene la contraseña en claro y la única representación persistida es un hash con salt por usuario.
**Escenarios de error:**
1. Se solicita consultar o recuperar una contraseña existente; la operación no está permitida a ningún rol

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-049/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-049/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: validar el modelo de datos que construye esta tarea contra el motor real.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.
El esquema de `TSK-044` ya está APLICADO en este servicio (changelogs Liquibase de su rama mergeada): asume las tablas creadas, no las vuelvas a crear ni las modifiques desde esta tarea.

Esta tarea materializa el MODELO DE DATOS: el motor se levanta VACÍO a propósito, para que valides tu propio changelog contra él. Cómo, exactamente:

```sh
./.mind/TSK-049/liquibase.sh <changelog-maestro>            # aplica (update)
./.mind/TSK-049/liquibase.sh <changelog-maestro> status
./.mind/TSK-049/liquibase.sh <changelog-maestro> rollback-count 99  # reversibilidad
```

Ese script lo genera la plataforma y corre Liquibase como contenedor contra ESTE motor. **NO descargues ni instales Liquibase por tu cuenta**: sus JAR acabarían commiteados en el repo del cliente, y apuntar al `local/*.properties` del arquetipo NO sirve (esa configuración mira a otra BBDD, no a la de tu sesión).

Comprueba las tres cosas antes de entregar: que el `update` termina limpio, que el rollback deshace, y que un segundo `update` es idempotente. Un changelog que nunca se ejecutó no está verificado — y la plataforma lo VUELVE a aplicar por su cuenta tras tu entrega, así que un rojo saldrá igual en el PR.

### Si el entorno no está disponible
Comprueba `.mind/TSK-049/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.