# TSK-053 · Tabla incidencia: núcleo, clasificación obligatoria y snapshots históricos de denominación

- Componente dueño: `ARC-016`
- Arquetipo del repo: `database-relational` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/facilities/changelogs/0.0.1/ddl/10-incidencia.xml`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
Crea `incidencia` con los nombres físicos exactos: incident_id PK, reference_code varchar2(30) NOT NULL UNIQUE, room_id FK NOT NULL a `cat_sala`, category_id FK NOT NULL a `cat_categoria_incidencia`, room_name_snapshot / office_name_snapshot / category_name_snapshot NOT NULL (denominación vigente en el alta, REQ-150), description varchar2(500) NOT NULL con check de longitud 10..500 y de contenido no compuesto solo por espacios, status FK NOT NULL a `cat_estado_incidencia` default 'ABIERTA', reported_by FK NOT NULL a `usuario`, created_at NOT NULL default SYSTIMESTAMP, updated_at, version NUMBER NOT NULL default 0. Ninguna FK admite ON DELETE y ninguna referencia a `usuario` admite NULL por desactivación (REQ-087/AC-TRZ-02). Trigger que rechaza con ORA-20010 cualquier UPDATE sobre las tres columnas `*_snapshot` (AC-RET-10). Índices: (reported_by, created_at DESC, incident_id DESC) para «Mis incidencias», (room_id), (category_id), (status). Oráculos de persistencia contra el Oracle 23ai real (no dict en memoria): test que inserta con category_id inexistente o inactivo y falla; test que intenta borrar una sala referenciada y falla por FK; test que ejecuta `UPDATE incidencia SET room_name_snapshot=...` y recibe ORA-20010; test que renombra una sala en `cat_sala` y comprueba que la incidencia conserva su snapshot original (AC-RET-09); test que verifica que el listado acotado por `reported_by = :session_user` sobre 13 filas (3 propias, 10 ajenas) devuelve exactamente 3 (AC-SEG-01). Los DTOs de la API usan estos mismos nombres físicos (`reported_by`, no `reporterId`). `update`/`rollback-count` verdes.

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

Tus zonas (`sources/facilities/changelogs/0.0.1/ddl/10-incidencia.xml`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

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

### REQ-024 — Listado de incidencias del empleado acotado a las propias en la consulta a BD
El sistema acota el listado de incidencias del rol empleado a las que él ha reportado, aplicando el filtro de propiedad en la consulta a base de datos. Reglas: (1) con data_scope = OWN la consulta incorpora obligatoriamente reporter_user_id = session_user_id; (2) el filtro no es opcional ni sobrescribible por parámetros de la petición; (3) los criterios de ordenación y paginación no pueden ampliar el conjunto; (4) el total de resultados y los contadores se calculan sobre el conjunto ya acotado, sin revelar el volumen global. Flujo: empleado abre «Mis incidencias» → API resuelve alcance OWN → consulta filtrada → lista con status y fecha de última actualización. Datos devueltos: incident_id (uuid), room_id (varchar), category_code (varchar), status (varchar), created_at (timestamp), updated_at (timestamp). Catálogos: cat_estados_incidencia, cat_categorias_incidencia (mobiliario, climatización, audiovisual, limpieza, otros), cat_salas [gap: el RFP no aporta el listado de salas de las tres oficinas]. Validaciones: si la petición incluye reporter_user_id distinto del de la sesión, el parámetro se ignora sin error. Errores: sin sesión → 401; rol sin permiso de listado → 403. Aceptación: EMPLEADO con 3 incidencias propias en una base con 40 recibe exactamente 3 y el total declarado es 3; enviando reporter_user_id ajeno sigue recibiendo solo las suyas. Dependencias: PERM-01; endpoint de listado en MOD-002.
**Reglas de negocio:**
1. El conjunto de incidencias alcanzable por un rol con data_scope OWN es exactamente el de las incidencias cuyo reporter_user_id es el usuario de la sesión
2. Ningún parámetro de ordenación, paginación o filtrado de la petición amplía el conjunto acotado por el alcance
3. Los totales y contadores devueltos se calculan sobre el conjunto ya acotado por el alcance
**Criterios de aceptación:**
1. AC-INC-04: Dado un empleado autenticado, cuando consulta «Mis incidencias», entonces ve únicamente las incidencias que él ha reportado con su estado actual, y ninguna incidencia de otro empleado aparece en el listado ni es accesible por identificador directo.
**Validaciones:**
1. Un `reporter_user_id` recibido en la petición que no coincida con el de la sesión se ignora silenciosamente (no genera error de validación)
2. Los parámetros de ordenación y paginación deben validarse contra campos permitidos y no pueden alterar el filtro de propiedad aplicado
**Escenarios de error:**
1. Consulta del listado propio sin sesión iniciada
2. El rol del usuario no tiene permitido el listado solicitado
3. Parámetros de ordenación o paginación no válidos
**Campos de datos:**
- `incident_id` (uuid, obligatorio) — Identificador de cada incidencia del listado propio
- `reporter_user_id` (uuid, obligatorio) — Forzado a `= session_user_id`; si llega por petición con otro valor, se ignora
- `room_id` (string, obligatorio) — Valor de `cat_salas` (catálogo de salas no aportado por el RFP)
- `category_code` (enum, obligatorio) — Valores de `cat_categorias_incidencia`: mobiliario, climatización, audiovisual, limpieza, otros
- `status` (enum, obligatorio) — Valores de `cat_estados_incidencia`
- `created_at` (datetime, obligatorio) — Fecha de alta de la incidencia
- `updated_at` (datetime, obligatorio) — Fecha de última actualización mostrada en el listado

### REQ-025 — Listado completo con filtros por sala, categoría y estado exclusivo del técnico
El sistema habilita el listado completo de incidencias con filtros por sala, categoría y estado exclusivamente al rol técnico de mantenimiento. Reglas: (1) INCIDENT_LIST_ALL solo se autoriza con data_scope = ALL; (2) el técnico ve incidencias de cualquier reportante y de las tres oficinas, con los filtros combinables entre sí; (3) un empleado que invoque el recurso recibe denegación, no una versión recortada: la degradación silenciosa está prohibida porque oculta un fallo de permisos. Flujo: técnico abre «Todas las incidencias» → aplica filtros room_id / category_code / status → recibe el conjunto completo que cumple los filtros, con el reportante identificado. Datos: además de los de ALC-01, reporter_user_id y nombre del reportante, assignee_user_id. Catálogos: cat_salas [gap: listado de salas], cat_categorias_incidencia, cat_estados_incidencia. Validaciones: valores de filtro fuera de catálogo → 400 «Filtro no válido»; combinación sin resultados → lista vacía, no error. Errores: EMPLEADO invocando el recurso → 403 «No tienes permisos para consultar todas las incidencias». Aceptación: TECNICO_MANTENIMIENTO sin filtros recibe todas las incidencias; EMPLEADO recibe 403 y ninguna fila. Seguridad: el listado expone nombre y correo corporativo de terceros, por lo que el alcance se verifica en cada página solicitada. Dependencias: PERM-01; listado con filtros en MOD-002.
**Reglas de negocio:**
1. INCIDENT_LIST_ALL solo se autoriza a roles cuyo data_scope es ALL
2. Un rol sin permiso de listado completo obtiene una denegación, nunca una versión recortada del conjunto
3. Un valor de filtro de sala, categoría o estado fuera de su catálogo no es válido
4. Una combinación de filtros sin coincidencias produce un conjunto vacío, no una condición de error
**Criterios de aceptación:**
1. AC-BAN-01: Dado un `TECNICO_DE_MANTENIMIENTO` autenticado, cuando abre «Bandeja de incidencias», entonces ve incidencias de toda la organización con independencia del reportante y cada fila muestra sala, oficina, categoría, estado, fecha de creación, antigüedad en días, reportante e indicador de foto adjunta; y dada una incidencia sin técnico asignado, entonces la celda de técnico muestra el literal «Sin asignar» y nunca queda vacía.
2. AC-BAN-05: Dado un técnico en la bandeja, cuando filtra del 01/03 al 31/03, entonces el resultado incluye las incidencias creadas el 01/03 y el 31/03 (rango inclusivo, día completo en `Europe/Madrid`) y ninguna fuera del rango, combinando en AND con sala, categoría, estado y asignación; y cuando introduce una fecha de inicio posterior a la de fin o un rango superior a 2 años, entonces recibe el mensaje de error correspondiente y el listado previo permanece visible.
**Validaciones:**
1. El filtro `room_id` debe pertenecer al catálogo `cat_salas`; un valor fuera de catálogo devuelve `400` «Filtro no válido»
2. El filtro `category_code` debe pertenecer al catálogo `cat_categorias_incidencia` (mobiliario, climatización, audiovisual, limpieza, otros); valor fuera de catálogo → `400`
3. El filtro `status` debe pertenecer al catálogo `cat_estados_incidencia` (`ABIERTA`, `EN_CURSO`, `RESUELTA`, `CERRADA`); valor fuera de catálogo → `400`
4. Los filtros son opcionales y combinables entre sí; una combinación válida sin resultados devuelve lista vacía y no un error
**Escenarios de error:**
1. Valor de filtro de sala, categoría o estado fuera del catálogo admitido
2. Usuario sin rol de mantenimiento solicitando el listado completo de incidencias
**Campos de datos:**
- `room_id` (string, opcional) — Valor de `cat_salas`; fuera de catálogo → 400
- `category_code` (enum, opcional) — Valor de `cat_categorias_incidencia`; fuera de catálogo → 400
- `status` (enum, opcional) — Valor de `cat_estados_incidencia`; fuera de catálogo → 400
- `reporter_user_id` (uuid, obligatorio) — Visible solo con `data_scope = ALL`
- `reporter_name` (string, obligatorio) — Dato personal; alcance verificado en cada página solicitada
- `assignee_user_id` (uuid, opcional) — Nulo si la incidencia aún no está asignada

### REQ-029 — Alcance de incidencias: EMPLEADO solo las propias, TECNICO el conjunto completo
Un usuario con rol EMPLEADO solo alcanza las incidencias cuyo reporter_user_id coincide con el usuario de su sesión; un TECNICO_MANTENIMIENTO alcanza el conjunto completo. Aplica a: ALC, PERM (listados, detalle, historial, adjuntos).
**Reglas de negocio:**
1. Un usuario con rol EMPLEADO alcanza exclusivamente las incidencias cuyo reporter_user_id coincide con el usuario de su sesión; un TECNICO_MANTENIMIENTO alcanza el conjunto completo
**Criterios de aceptación:**
1. AC-USR-02: Dado el conjunto de operaciones del sistema, cuando se ejecuta la matriz completa rol×operación (`EMPLEADO`, `TECNICO_DE_MANTENIMIENTO`, `ADMINISTRADOR`), entonces cada celda coincide con la matriz acordada —el empleado solo reporta y consulta lo propio, el técnico ve/asigna/resuelve cualquier incidencia, el administrador gestiona usuarios y no tiene alcance sobre incidencias— y la autorización se evalúa en la API REST en el 100 % de los casos, nunca solo en la SPA.
**Escenarios de error:**
1. Acceso a una incidencia cuyo reportante no es el usuario de la sesión, sin alcance completo; respuesta uniforme

### REQ-087 — Atribución persistente de incidencias, historial y comentarios de usuarios desactivados
Reglas: desactivar nunca rompe una referencia: las incidencias reportadas (reported_by), las asignaciones (assigned_to), las entradas del historial de cambios de estado (changed_by) y el comentario de resolución siguen resolviendo el nombre de la persona aunque su status sea INACTIVO; prohibido sustituir el nombre por un literal genérico («usuario eliminado») o anonimizarlo dentro de la ventana de retención (REQ-047, ver TRZ-02); la UI marca al autor/asignado inactivo con un distintivo «(inactivo)» sin ocultar la identidad; el alcance de datos por rol no cambia por la desactivación: el EMPLEADO sigue viendo solo sus propias incidencias (REQ-024, REQ-029) y el TECNICO_MANTENIMIENTO el conjunto completo (REQ-025). Datos expuestos del usuario inactivo: full_name (visible a todos los roles con acceso a la incidencia), corporate_email (visible solo a ADMINISTRADOR y TECNICO_MANTENIMIENTO) [inferido], status (para el distintivo de inactivo). Flujo: consulta de detalle/listado/historial de una incidencia (MOD-002) → el backend resuelve los datos identificativos por FK contra el registro de usuario, activo o no → la respuesta incluye full_name y is_active. Validaciones: ninguna FK a usuario admite NULL por desactivación; el borrado físico de usuario está prohibido a nivel de modelo (REQ-047). Errores: si la FK no resuelve (dato corrupto), el backend devuelve 500 y registra el incidente en auditoría, en lugar de devolver una incidencia con autor vacío. Criterios de aceptación: Given un usuario desactivado que reportó 3 incidencias, when un TECNICO_MANTENIMIENTO consulta el listado completo, then las 3 siguen mostrando su nombre y siguen siendo atribuibles a él. Given una incidencia cerrada por un técnico posteriormente desactivado, when se consulta su historial de cambios de estado, then cada transición conserva el nombre del técnico que la ejecutó. Given un usuario desactivado, when se intenta eliminarlo físicamente por API, then la operación no existe y se responde 405/403. Seguridad: se hereda el control de acceso de las consultas de incidencia (REQ-026, REQ-029); el único dato personal implicado es nombre y correo corporativo (REQ-049). Dependencias: MOD-002, REQ-047, REQ-048, REQ-026. Prioridad: Must [inferido]. auth_type: JWT. data_scope: own_only (EMPLEADO) / all (TECNICO_MANTENIMIENTO y ADMINISTRADOR).
**Reglas de negocio:**
1. Ninguna referencia de una incidencia, de su historial o de sus comentarios a un usuario queda sin resolver por efecto de una desactivación
2. Ninguna clave foránea a usuario admite valor nulo como consecuencia de una desactivación
3. Dentro de la ventana de retención, el nombre de un usuario inactivo no puede sustituirse por un literal genérico ni anonimizarse en ninguna vista
4. Un usuario no admite borrado físico en ningún punto del sistema
5. El alcance de datos asociado a un rol es independiente del estado de la cuenta: un empleado accede únicamente a sus propias incidencias y un técnico de mantenimiento al conjunto completo, activo o inactivo el autor
6. El correo corporativo de un usuario inactivo solo es visible para los roles `ADMINISTRADOR` y `TECNICO_MANTENIMIENTO`
7. Una incidencia con autor o asignado inactivo se presenta con el distintivo de inactividad sin ocultar la identidad de la persona
**Criterios de aceptación:**
1. AC-G-06: Dado una persona que deja de participar en el proceso y tiene incidencias reportadas y/o resueltas, cuando el administrador desactiva su cuenta, entonces (a) su acceso queda cortado en la siguiente petición, (b) ninguna incidencia, entrada de historial o comentario pierde la atribución a su nombre, y (c) sus datos siguen consultables por el administrador hasta deactivated_at + 2 años. (Verificable por: Test automatizado)
2. AC-TRZ-01: Dado un usuario desactivado que reportó 3 incidencias y cerró otras 2, cuando un TECNICO_MANTENIMIENTO consulta el listado completo, el detalle y el historial de cambios de estado de esas incidencias, entonces en los 5 casos se sigue mostrando el full_name real de la persona (nunca un literal genérico tipo «usuario eliminado» ni un valor anonimizado), acompañado del distintivo «(inactivo)» en la UI. (Verificable por: UAT manual)
3. AC-TRZ-02: Dado un usuario INACTIVO dentro de la ventana de retención, cuando se intenta eliminarlo físicamente por API o por script, entonces no existe operación de borrado expuesta (respuesta 405/403) y el registro permanece en base de datos; y cuando se inspecciona el modelo de datos, entonces ninguna FK a usuario (reported_by, assigned_to, changed_by) admite quedar a NULL como consecuencia de una desactivación. (Verificable por: Audit externo)
4. AC-TRZ-05: Dado la desactivación de un usuario, cuando se consultan las incidencias en las que participó con cada uno de los tres roles, entonces el alcance de datos por rol no cambia: el EMPLEADO sigue viendo únicamente sus propias incidencias, el TECNICO_MANTENIMIENTO el conjunto completo, y el corporate_email del usuario inactivo solo es visible para ADMINISTRADOR y TECNICO_MANTENIMIENTO [inferido: el RFP no concreta si el empleado debe ver el correo del técnico]. (Verificable por: Test automatizado)
**Validaciones:**
1. Las referencias de entrada a usuario (`reported_by`, `assigned_to`, `changed_by`) son obligatorias y no admiten valor nulo
2. Toda referencia a usuario recibida debe resolver contra un registro de usuario existente, con independencia de su `status`
**Escenarios de error:**
1. No tienes permiso para consultar esta incidencia
2. La incidencia indicada no existe
3. La eliminación definitiva de un usuario no está permitida
4. No ha sido posible resolver los datos de autoría de la incidencia
**Campos de datos:**
- `full_name` (string, obligatorio) — Visible a todo rol con acceso a la incidencia; prohibido sustituir por literal genérico o anonimizar
- `corporate_email` (email, opcional) — Visible solo a ADMINISTRADOR y TECNICO_MANTENIMIENTO
- `status` (enum, obligatorio) — Valores: ACTIVO | INACTIVO
- `is_active` (boolean, obligatorio) — Derivado de status; no oculta la identidad
- `reported_by` (uuid, obligatorio) — No admite valor nulo por desactivación; si no resuelve, error 500
- `assigned_to` (uuid, opcional) — Vacío si la incidencia queda sin asignar; nunca se anula por desactivación
- `changed_by` (uuid, obligatorio) — No admite valor nulo por desactivación

### REQ-090 — Alta de incidencia de sala por empleado autenticado con sala, categoría, descripción y foto opcional
El sistema permite a un empleado autenticado dar de alta una incidencia de una sala indicando sala afectada, categoría, descripción breve y, opcionalmente, una foto. Reglas: la incidencia nace siempre en estado ABIERTA; el reportante y el sello temporal los fija el servidor desde la sesión, nunca desde el payload (REQ-064); assigned_to_user_id nace nulo; al persistir se crea el asiento inicial del historial (from_status null → to_status ABIERTA), inmutable y retenido 2 años (REQ-015, REQ-047); no hay borrado físico. Flujo: abrir formulario → seleccionar sala (CAT-02) → seleccionar categoría (CAT-01) → escribir descripción → adjuntar foto opcional (INC-02) → enviar → el sistema valida, persiste y devuelve el identificador. Ramas: error de validación vuelve al formulario conservando lo introducido sin crear nada; fallo al guardar la foto conserva la incidencia. Datos: room_id (number, obligatorio, existente e is_active en cat_rooms), category_code (varchar, obligatorio, vigente en cat_incident_categories), description (varchar, obligatorio, 10–500 caracteres [gap: el RFP dice «descripción breve» sin longitudes]), photo_attachment_id (number, opcional, nullable), status (varchar, lo fija el servidor, FK cat_incident_statuses, inicial ABIERTA), reported_by_user_id (number, del contexto de sesión), created_at (timestamp, servidor), reference_code (varchar, único, formato INC-AAAA-NNNNNN [inferido]). Catálogos: cat_rooms, cat_incident_categories, cat_incident_statuses. Validaciones: obligatoriedad de sala/categoría/descripción; descripción no compuesta solo de espacios; saneamiento de HTML en description; sala existente y activa; categoría vigente. Errores: 400 «Debes indicar la sala afectada» / «Selecciona una categoría» / «Describe brevemente la incidencia»; 401 sin sesión con redirección al acceso (REQ-058); 422 «La sala seleccionada ya no está disponible»; 500 «No hemos podido registrar la incidencia, inténtalo de nuevo». Criterios de aceptación: Given un EMPLEADO autenticado con sala, categoría y descripción When envía el formulario Then se crea la incidencia en estado ABIERTA y se muestra su identificador; Given falta sala, categoría o descripción Then no se crea ninguna incidencia y se indica qué campo obligatorio falta; Given un usuario no autenticado When invoca el endpoint de alta Then 401 y ninguna incidencia creada; Given una incidencia recién creada When se consulta su registro Then constan el empleado reportante y la fecha y hora de creación. Seguridad: ejecutan EMPLEADO y TECNICO_DE_MANTENIMIENTO autenticados (cualquier usuario activo); el alta se atribuye siempre al usuario de la sesión, sin reportar «en nombre de» otro; el reportante solo verá después sus propias incidencias (REQ-024, REQ-029); sin doble factor; autorización vinculante en la API REST, no en la SPA (REQ-013, REQ-032). Eventos de dominio: IncidentReported al completarse el alta, consumido por el módulo de notificaciones para el aviso por correo al equipo de mantenimiento [inferido: el RFP no nombra el evento]. Dependencias: CAT-01, CAT-02, REQ-033, REQ-059, REQ-064. Integración: dispara evento consumido por el envío SMTP, fuera de esta épica. Prioridad: Must.
**Reglas de negocio:**
1. Toda incidencia nace en estado `ABIERTA` y ese estado inicial no es fijable desde el payload del alta
2. El reportante de una incidencia es siempre el usuario de la sesión que la creó; no existe reporte «en nombre de» otro usuario
3. La fecha y hora de creación de una incidencia las fija el servidor, nunca el cliente
4. Una incidencia recién creada no tiene técnico asignado
5. Toda incidencia tiene exactamente un asiento inicial de historial con `from_status` nulo y `to_status` = `ABIERTA`
6. Los asientos del historial de una incidencia son inmutables y se conservan 2 años
7. Una incidencia no puede eliminarse físicamente del sistema
8. Toda incidencia tiene exactamente una sala afectada, exactamente una categoría y una descripción no vacía
9. La descripción de una incidencia tiene entre 10 y 500 caracteres y no puede componerse únicamente de espacios
10. El `reference_code` es único en todo el sistema y corresponde a una sola incidencia
11. Una incidencia solo puede referenciar una sala activa y una categoría vigente en el instante de su alta
12. Un usuario sin sesión válida no origina ninguna incidencia
13. Un intento de alta que no supera las validaciones no deja ninguna incidencia creada
**Criterios de aceptación:**
1. AC-INC-01: Dado un empleado autenticado que ha seleccionado una sala activa, una categoría vigente y escrito una descripción válida, cuando envía el formulario de alta, entonces se crea exactamente una incidencia en estado `ABIERTA`, con `reference_code` único, y la pantalla de confirmación muestra ese identificador y el enlace a su detalle.
2. AC-INC-02: Dado un formulario de alta al que le falta la sala, la categoría o la descripción (o cuya descripción solo contiene espacios o queda fuera del rango 10–500 caracteres), cuando el empleado intenta enviarlo, entonces la API responde `400` indicando qué campo obligatorio falta, no se crea ninguna incidencia y el formulario conserva los datos ya introducidos.
3. AC-INC-03: Dado un cliente sin sesión válida o con sesión caducada, cuando invoca el endpoint de alta de incidencia, entonces la API responde `401`, no se crea ninguna incidencia y la SPA redirige al acceso conservando el aviso de sesión caducada.
4. AC-INC-04: Dado un alta enviada con `reported_by_user_id` o `created_at` manipulados en el payload, cuando el servidor la procesa, entonces el reportante y el sello temporal se fijan desde la sesión del servidor ignorando el payload, y `assigned_to_user_id` y `status` quedan en `null` y `ABIERTA` respectivamente.
5. AC-INC-07: Dado un empleado que no adjunta foto, cuando envía sala, categoría y descripción, entonces el alta se completa sin error ni advertencia; y dado un fallo del almacenamiento posterior a la persistencia de la incidencia, entonces la incidencia se conserva y se informa de que la foto no pudo guardarse.
6. AC-INC-09: Dado el botón de envío del formulario de alta, cuando el usuario lo pulsa dos veces seguidas antes de recibir respuesta, entonces se crea exactamente una incidencia y el botón permanece deshabilitado mientras el POST está en vuelo.
7. AC-INC-10: Dado una incidencia recién creada, cuando se consulta su historial, entonces existe un asiento inicial con `from_status = null` y `to_status = ABIERTA`, con autor y fecha de creación, y ese asiento no admite modificación ni borrado por ninguna operación expuesta.
8. AC-CAT-02: Dado un POST de alta con un `category_code` inexistente, inactivo o fuera del catálogo cerrado, cuando el servidor lo valida, entonces responde `422` «La categoría seleccionada no es válida» y no se crea ninguna incidencia, con independencia de lo que haya validado la SPA.
9. AC-NOT-01: Dado una incidencia con reportante identificado, cuando su estado cambia, entonces se entrega al relay SMTP un correo al reportante en menos de 5 minutos, en español, identificando la incidencia por `reference_code` y su nuevo estado. [pendiente mapping 1.4]
10. AC-NOT-02: Dado el alta de una incidencia nueva, cuando se completa la creación, entonces el evento `IncidentReported` provoca la entrega al relay SMTP de un aviso al buzón/lista del equipo de mantenimiento, sin usar ningún otro canal (ni SMS ni push). [pendiente mapping 1.4]
11. AC-NOT-03: Dado un relay SMTP no disponible, cuando se produce un alta o un cambio de estado, entonces la operación de negocio se persiste igualmente, el fallo de entrega queda registrado con su causa y se reintenta según la política acordada, sin pérdida silenciosa de la notificación. [pendiente mapping 1.4]
**Validaciones:**
1. La sala afectada (`room_id`) es obligatoria: no se acepta el alta sin valor
2. La categoría (`category_code`) es obligatoria: no se acepta el alta sin valor
3. La descripción (`description`) es obligatoria: no se acepta el alta sin valor
4. La descripción no puede estar compuesta únicamente por espacios en blanco
5. La descripción debe tener entre 10 y 500 caracteres
6. La descripción se sanea de HTML antes de aceptarse (no se admite marcado inyectado)
7. El `room_id` recibido debe existir en `cat_rooms` y tener `is_active = 1` en el instante del alta
8. El `category_code` recibido debe existir en `cat_incident_categories` y estar vigente en el instante del alta
9. El `reference_code` generado debe cumplir el formato `INC-AAAA-NNNNNN` y ser único
10. Los campos `reported_by_user_id`, `created_at` y `status` no se aceptan desde el payload: se ignoran y los fija el servidor desde la sesión
**Escenarios de error:**
1. No se ha indicado la sala afectada
2. No se ha seleccionado una categoría
3. No se ha informado la descripción de la incidencia
4. La descripción está compuesta solo por espacios o no respeta la longitud admitida
5. Se intenta dar de alta la incidencia sin sesión válida
6. Se intenta atribuir el alta a un usuario distinto del de la sesión
7. La sala seleccionada ya no está disponible
8. La categoría seleccionada no es válida o ya no está vigente
9. No ha sido posible registrar la incidencia; se puede reintentar el envío
**Campos de datos:**
- `room_id` (integer, obligatorio) — Debe existir en `cat_rooms` y estar activa (`is_active = 1`) en el instante del alta
- `category_code` (enum, obligatorio) — Valor vigente del catálogo cerrado: MOBILIARIO, CLIMATIZACION, AUDIOVISUAL, LIMPIEZA, OTROS
- `description` (string, obligatorio) — 10–500 caracteres [gap: longitud no declarada en el RFP]; no solo espacios; saneado de HTML
- `photo_attachment_id` (integer, opcional) — Nullable; como máximo un adjunto por incidencia
- `status` (enum, obligatorio) — Lo fija el servidor; valor inicial `ABIERTA`; valores: ABIERTA, EN_CURSO, RESUELTA, CERRADA
- `reported_by_user_id` (integer, obligatorio) — Lo fija el servidor desde la sesión, nunca desde el payload
- `created_at` (datetime, obligatorio) — Sello temporal de servidor
- `reference_code` (string, obligatorio) — Único; formato `INC-AAAA-NNNNNN` [inferido]

### REQ-100 — Exigir y validar contra catálogo sala y categoría en el alta, rechazando sin persistir valores fuera de catálogo
El alta solo se acepta si room_id existe en cat_salas y está activa, y si category_id existe en cat_categorias_incidencia y está activa; ambos son obligatorios. Si la validación falla, la incidencia no se persiste en absoluto (transacción completa revertida, incluida la foto adjunta si se subió). El backend resuelve los identificadores contra la base de datos e ignora cualquier literal de sala o categoría enviado por el cliente (mismo principio que REQ-018 para el rol). Flujo: EMPLEADO autenticado completa el alta (sala, categoría, descripción, foto opcional), envía, el backend valida referencialmente sala y categoría antes que el resto de campos; si es válido persiste la incidencia con sus dos claves foráneas, si no devuelve error de validación por campo. Datos: incident_id (number, PK), room_id (number, FK NOT NULL a cat_salas), category_id (number, FK NOT NULL a cat_categorias_incidencia), reported_by (FK usuario de la sesión, REQ-064), created_at (timestamp). Validaciones: room_id y category_id numéricos y presentes; existencia e is_active='Y' de ambos; rechazo de payloads con campo de categoría en texto libre. Errores: 422 'La categoría seleccionada no es válida'; 422 'La sala seleccionada no existe o ya no está disponible'; 422 'Debe indicar la sala y la categoría de la incidencia' si faltan; 401 sin sesión; en todos los casos, cero filas escritas. Aceptación: una petición con category_id fuera del catálogo cerrado se rechaza sin crear incidencia; una petición con room_id inexistente se rechaza sin crear incidencia; una petición válida persiste la incidencia con exactamente una sala y una categoría del catálogo. Seguridad: cualquier usuario autenticado con rol EMPLEADO o TECNICO_DE_MANTENIMIENTO puede reportar; la autoría se toma de la sesión, nunca del payload (REQ-064); sin doble factor. Dependencias: CAT-01, SAL-01, REQ-016, REQ-033. Prioridad: Must. auth_type: JWT. data_scope: own_only.
**Reglas de negocio:**
1. Una incidencia solo puede darse de alta con una sala y una categoría existentes y activas en sus catálogos
2. Un alta de incidencia con sala o categoría fuera de catálogo no deja ninguna huella persistida, incluida la foto adjunta
3. La sala y la categoría de una incidencia son obligatorias en el alta
4. La clasificación de una incidencia se resuelve a partir de identificadores de catálogo; ningún literal de sala o categoría enviado por el cliente tiene efecto
5. La autoría de una incidencia es la del usuario de la sesión, nunca la indicada en el payload
**Criterios de aceptación:**
1. AC-CAT-01: **Dado** un usuario con sesión válida, **cuando** abre el formulario de alta de incidencia o el panel de filtros, **entonces** el selector de categoría ofrece **exactamente las cinco categorías activas** del catálogo cerrado (mobiliario, climatización, audiovisual, limpieza, otros), ordenadas por `display_order`, con selección obligatoria, sin opción vacía por defecto y **sin ninguna vía de texto libre**.
2. AC-CLAS-01: **Dada** una petición de alta de incidencia con `room_id` o `category_id` inexistente, inactivo, ausente o enviado como literal de texto libre, **cuando** se procesa, **entonces** se rechaza con 422 y mensaje por campo y se escriben **cero filas** (incluida la reversión de la foto adjunta si se subió); y **dada** una petición válida, **cuando** se procesa, **entonces** la incidencia se persiste con exactamente una sala y una categoría del catálogo.
**Validaciones:**
1. `room_id` y `category_id` son obligatorios en la petición de alta: si falta cualquiera de los dos, se rechaza la petición
2. `room_id` y `category_id` deben ser valores numéricos válidos
3. `room_id` debe existir en `cat_salas` con `is_active = 'Y'`
4. `category_id` debe existir en `cat_categorias_incidencia` con `is_active = 'Y'`
5. Se rechaza todo payload que incluya la categoría o la sala como literal de texto libre en lugar del identificador de catálogo
**Escenarios de error:**
1. Petición de alta sin sesión válida
2. Los identificadores de sala y de categoría deben enviarse como valores numéricos
3. Debe indicar la sala y la categoría de la incidencia
4. La categoría seleccionada no es válida
5. La sala seleccionada no existe o ya no está disponible
**Campos de datos:**
- `incident_id` (integer, obligatorio) — Generado por el sistema al persistir
- `room_id` (integer, obligatorio) — Numérico; debe existir en `cat_salas` con is_active = Y; obligatorio
- `category_id` (integer, obligatorio) — Numérico; debe existir en `cat_categorias_incidencia` con is_active = Y; se ignora cualquier literal en texto libre
- `reported_by` (integer, obligatorio) — Se toma siempre de la sesión, nunca del payload (REQ-064)
- `created_at` (datetime, obligatorio) — Asignada por el sistema

### REQ-102 — Garantizar de forma permanente una sala y una categoría de catálogo por incidencia, incluso tras desactivar valores
room_id y category_id son claves foráneas NOT NULL en la tabla de incidencias; no existe estado intermedio ni valor 'sin clasificar'. La desactivación de una sala o una categoría (CAT-02, SAL-02) no propaga a las incidencias existentes: estas conservan su clasificación y siguen mostrándola en el detalle, el listado y el historial. No se admite borrado físico de valores de catálogo referenciados (REQ-047). Ninguna ruta de escritura (alta, reclasificación, cambio de estado, cierre con comentario) puede dejar una incidencia sin sala o sin categoría. Flujo: al desactivar un valor referenciado el sistema informa del número de incidencias afectadas y aplica baja lógica; toda lectura posterior resuelve el literal desde el catálogo incluyendo valores inactivos. Datos: restricciones fk_incident_room y fk_incident_category, ambas con NOT NULL y sin ON DELETE; is_active consultado solo en las rutas de escritura de alta/reclasificación, nunca en las de lectura. Validaciones: verificación de integridad en el arranque y en cada operación de escritura. Errores: 409 'No se puede eliminar un valor de catálogo con incidencias asociadas'; 500 controlado si se detecta una incidencia sin clasificación, con registro en auditoría. Aceptación: cualquier incidencia almacenada tiene siempre exactamente una sala del catálogo y una categoría del catálogo asignadas; una categoría desactivada con incidencias asociadas sigue mostrando su literal al TECNICO_DE_MANTENIMIENTO que consulta una de ellas. Seguridad: invariante aplicada en backend con independencia del rol; las lecturas respetan el alcance por rol ya definido (REQ-024, REQ-026, REQ-029). Dependencias: CAT-02, SAL-02, CLAS-01, REQ-047. Prioridad: Must. auth_type: JWT. data_scope: (no aplica).
**Reglas de negocio:**
1. Un valor de catálogo referenciado por al menos una incidencia no puede eliminarse físicamente
2. El literal de una sala o categoría inactiva sigue siendo legible en el detalle, el listado y el historial de las incidencias que la referencian
3. Toda incidencia almacenada tiene exactamente una sala del catálogo y exactamente una categoría del catálogo
4. No existe para una incidencia el estado "sin clasificar" ni valor nulo de sala o categoría
5. La desactivación de una sala o de una categoría no altera la clasificación de las incidencias que ya la referencian
**Criterios de aceptación:**
1. AC-CAT-02: **Dado** un `ADMINISTRADOR` en el mantenimiento de catálogos, **cuando** desactiva una categoría con incidencias asociadas, **entonces** esa categoría deja de ofrecerse en el alta, sigue apareciendo en el detalle y en los filtros de las incidencias históricas y no se borra físicamente; y **cuando** intenta desactivar la última categoría activa, **entonces** recibe 409 "Debe existir al menos una categoría activa" y nada cambia.
2. AC-SAL-03: **Dado** un `ADMINISTRADOR`, **cuando** da de alta una sala en una oficina activa, **entonces** esa sala queda disponible inmediatamente en el selector del alta de incidencia; y **cuando** desactiva una sala con incidencias asociadas, **entonces** desaparece del selector de alta pero las incidencias históricas siguen mostrando su nombre en el detalle y en los filtros.
3. AC-CLAS-01: **Dada** una petición de alta de incidencia con `room_id` o `category_id` inexistente, inactivo, ausente o enviado como literal de texto libre, **cuando** se procesa, **entonces** se rechaza con 422 y mensaje por campo y se escriben **cero filas** (incluida la reversión de la foto adjunta si se subió); y **dada** una petición válida, **cuando** se procesa, **entonces** la incidencia se persiste con exactamente una sala y una categoría del catálogo.
4. AC-CLAS-03: **Dada** cualquier incidencia almacenada, en cualquier punto del ciclo de vida, **cuando** se audita la tabla de incidencias, **entonces** el **100 % de las filas** tiene exactamente una sala y una categoría del catálogo (**0 filas** con `room_id` o `category_id` nulo); y **dada** una sala o categoría desactivada con incidencias asociadas, **cuando** se consulta una de esas incidencias, **entonces** sigue mostrando el literal del valor desactivado.
**Validaciones:**
1. En toda ruta de escritura sobre la incidencia (alta, reclasificación, cambio de estado, cierre) `room_id` y `category_id` deben venir informados y no nulos
**Escenarios de error:**
1. No se puede eliminar un valor de catálogo con incidencias asociadas
2. La operación dejaría la incidencia sin sala o sin categoría asignada
3. Se ha detectado una incidencia sin clasificación completa y la operación se ha detenido
**Campos de datos:**
- `room_id` (integer, obligatorio) — Obligatorio y no nulo; sin borrado en cascada; resoluble aunque la sala esté inactiva
- `category_id` (integer, obligatorio) — Obligatorio y no nulo; sin borrado en cascada; resoluble aunque la categoría esté inactiva
- `is_active` (boolean, opcional) — Se evalúa solo en rutas de escritura, nunca en lectura

### REQ-105 — Composición de la fila y de la respuesta de la bandeja completa de incidencias
Cada fila representa una incidencia y muestra al menos sala, categoría, estado, fecha de creación y técnico asignado cuando lo tenga; si no tiene técnico muestra el literal «Sin asignar», nunca vacío; la fila indica con un distintivo si la incidencia tiene foto adjunta pero no expone la imagen (la descarga se rige por REQ-027); la antigüedad se calcula en backend, no en la SPA. Flujo: técnico autenticado abre «Bandeja de incidencias», el backend resuelve rol y alcance (REQ-060) y devuelve la página de resultados; si el rol no es técnico, corta antes de consultar. Datos: incident_id (number, PK), incident_code (string, obligatorio, único, visible), room_id (number, obligatorio) y room_name (string), office_name (string, derivado de la sala vía REQ-097/REQ-099), category_code + category_name (obligatorio, de cat_categorias_incidencia), status (enum obligatorio de cat_estados_incidencia: ABIERTA | EN_CURSO | RESUELTA | CERRADA), created_at (timestamp, obligatorio), updated_at (timestamp), reporter_name (string), assigned_technician_id (number, nullable), assigned_technician_name (string, nullable), has_photo (boolean), age_days (number, derivado = días naturales desde created_at). Catálogos: cat_categorias_incidencia (REQ-095), cat_salas y cat_oficinas (REQ-097/REQ-099), cat_estados_incidencia — [gap: el RFP enumera los cuatro estados pero no indica si deben persistirse como catálogo mantenible ni sus etiquetas de presentación]. Validaciones: ningún campo derivado se acepta desde el cliente; status fuera del catálogo nunca se devuelve. Errores: 401 «Tu sesión ha caducado, vuelve a iniciar sesión»; 403 uniforme «No tienes permisos para realizar esta acción» sin revelar existencia de datos (REQ-031); 500 «No se ha podido recuperar el listado, inténtalo de nuevo». Aceptación: Dado un TECNICO_DE_MANTENIMIENTO autenticado, cuando abre la bandeja, entonces ve incidencias de toda la organización con independencia del reportante y cada fila incluye sala, oficina, categoría, estado, fecha de creación, antigüedad, reportante e indicador de foto; Dada una incidencia sin técnico, cuando se lista, entonces muestra «Sin asignar». Seguridad: ejecuta TECNICO_DE_MANTENIMIENTO (alcance: todas las incidencias, REQ-029); EMPLEADO denegado (REQ-023); ADMINISTRADOR no tiene alcance sobre incidencias (REQ-014). Sin doble factor (operación de solo lectura, sin datos de categoría especial). Eventos: ninguno (operación de consulta). Dependencias: REQ-025, REQ-029, REQ-078, REQ-097, REQ-103, REQ-027. Prioridad [inferido] (el RFP no declara MoSCoW). Prioridad: Must.
**Reglas de negocio:**
1. Cada fila de la bandeja se corresponde con exactamente una incidencia, y su `incident_code` es único en todo el sistema
2. El estado de una incidencia es siempre uno de los cuatro valores del catálogo `cat_estados_incidencia` (`ABIERTA`, `EN_CURSO`, `RESUELTA`, `CERRADA`); ningún otro valor es observable en la bandeja
3. Una incidencia sin técnico asignado se presenta en la bandeja con el literal «Sin asignar», nunca con la celda vacía
4. La antigüedad de una incidencia (`age_days`) equivale a los días naturales transcurridos desde su `created_at`, y ningún valor de este campo procedente del cliente es válido
5. La bandeja completa solo tiene alcance para el rol TECNICO_DE_MANTENIMIENTO: EMPLEADO y ADMINISTRADOR no obtienen ninguna incidencia ajena por esta vía
6. Una fila indica la existencia de foto adjunta (`has_photo`) pero no expone la imagen: el acceso al fichero es un permiso independiente (REQ-027)
7. Una denegación por rol insuficiente es indistinguible entre «no existe» y «no autorizado»: el mensaje de 403 es uniforme y no revela la existencia de datos
8. El alcance de la bandeja del técnico es la totalidad de las incidencias de la organización, con independencia de quién sea el reportante
**Criterios de aceptación:**
1. AC-BAN-01: Dado un `TECNICO_DE_MANTENIMIENTO` autenticado, cuando abre «Bandeja de incidencias», entonces ve incidencias de toda la organización con independencia del reportante y cada fila muestra sala, oficina, categoría, estado, fecha de creación, antigüedad en días, reportante e indicador de foto adjunta; y dada una incidencia sin técnico asignado, entonces la celda de técnico muestra el literal «Sin asignar» y nunca queda vacía.
2. AC-BAN-08: Dado un usuario con rol `EMPLEADO` o `ADMINISTRADOR`, cuando invoca el endpoint de la bandeja completa, cualquiera de sus filtros o restaura una URL de bandeja compartida, entonces recibe un 403 uniforme sin resultados parciales ni pistas sobre la existencia de incidencias ajenas, y la autorización se reevalúa siempre en la API REST y nunca en la SPA (verificado manipulando la petición al margen del frontend).
3. AC-INC-05: Dada una incidencia con foto adjunta, cuando la descarga el técnico de mantenimiento o el empleado reportante, entonces obtiene el fichero; y cuando la solicita un empleado que no es el reportante, entonces recibe 403 uniforme y la imagen no es accesible por URL directa ni adivinable por enumeración de identificadores.
**Validaciones:**
1. Los campos derivados (`office_name`, `age_days`, `room_name`, `category_name`, `assigned_technician_name`, `has_photo`) no se aceptan desde el cliente: si llegan en la petición se ignoran y se recalculan en backend
2. El valor de `status` de cada fila debe pertenecer al catálogo `cat_estados_incidencia` (`ABIERTA`, `EN_CURSO`, `RESUELTA`, `CERRADA`); cualquier valor fuera del catálogo se rechaza
3. `incident_code` es obligatorio y único, y `room_id`, `category_code`, `status` y `created_at` son obligatorios en la respuesta de cada fila
**Escenarios de error:**
1. La sesión del usuario ha caducado o no se aporta una credencial válida al abrir la bandeja
2. El usuario autenticado no tiene el rol de técnico de mantenimiento y se deniega el acceso a la bandeja completa con un mensaje uniforme, sin revelar si existen incidencias
3. No ha sido posible recuperar el listado de incidencias y se muestra un mensaje genérico de reintento
**Campos de datos:**
- `incident_id` (integer, obligatorio) — Clave primaria; no aceptable desde el cliente
- `incident_code` (string, obligatorio) — Único
- `room_id` (integer, obligatorio) — Debe existir en el catálogo de salas
- `room_name` (string, opcional) — Nombre de la sala para mostrar en la fila
- `office_name` (string, opcional) — Derivado de la sala; no editable
- `category_code` (string, obligatorio) — Valor del catálogo de categorías de incidencia
- `category_name` (string, obligatorio) — Nombre de la categoría para mostrar en la fila
- `status` (enum, obligatorio) — ABIERTA | EN_CURSO | RESUELTA | CERRADA; nunca se devuelve un valor fuera del catálogo
- `created_at` (datetime, obligatorio) — Fecha y hora de creación de la incidencia
- `updated_at` (datetime, opcional) — Fecha y hora de la última modificación
- `reporter_name` (string, opcional) — Nombre del empleado que reportó la incidencia
- `assigned_technician_id` (integer, opcional) — Nulo si la incidencia no está asignada
- `assigned_technician_name` (string, opcional) — Si es nulo, la fila muestra el literal «Sin asignar»
- `has_photo` (boolean, opcional) — Solo indicador; no expone la imagen
- `age_days` (integer, opcional) — Derivado en backend desde created_at; ≥ 0; no aceptable desde el cliente

### REQ-150 — Conservar la denominación histórica de sala, oficina y categoría vigente en el alta ante renombrados posteriores
La consulta de una incidencia conserva la denominación de sala, oficina y categoría vigente en el momento del alta, aunque el catálogo se renombre después. Reglas: en el alta se persiste una copia textual de las denominaciones (room_name_snapshot, office_name_snapshot, category_name_snapshot) junto al vínculo por identificador; el vínculo por id se mantiene siempre (REQ-102, que garantiza la permanencia del valor aunque el catálogo se desactive), y este requisito cubre un caso distinto: el renombrado posterior del elemento de catálogo, que de otro modo reescribiría el pasado; cuando la denominación actual y la histórica difieren, el detalle y el historial muestran la actual y, entre paréntesis, la histórica, mientras en el listado prevalece la actual para que los filtros sigan siendo coherentes; los snapshots son inmutables una vez creados. Flujo: consulta de detalle/historial de una incidencia, el sistema compara snapshot vs. denominación vigente y, si difieren, renderiza ambas. Datos: room_name_snapshot (varchar 120, obligatorio), office_name_snapshot (varchar 120, obligatorio), category_name_snapshot (varchar 60, obligatorio), todos poblados en el alta desde cat_salas, cat_oficinas, cat_categorias_incidencia. Validaciones: ninguna operación de actualización puede modificar los campos *_snapshot; el alta rechaza valores fuera de catálogo (REQ-100). Errores: 409 «El registro histórico no admite modificaciones» si se intenta alterar un snapshot; 500 controlado si falta el snapshot en un registro antiguo → se muestra la denominación vigente con aviso [inferido]. Criterios de aceptación: Given una incidencia cerrada de la sala «Sala 2», When el ADMINISTRADOR renombra esa sala a «Sala Norte» y después se consulta el detalle de la incidencia, Then se muestra «Sala Norte (en el momento del alta: Sala 2)». Given una categoría desactivada tras el cierre, When se consulta el detalle, Then la clasificación sigue presente y legible. Seguridad: misma visibilidad y alcance que el detalle (RET-01); la operación de renombrado de catálogo es de ADMINISTRADOR (REQ-098, REQ-099). Dependencias: REQ-102, REQ-098, REQ-099, RET-01. Prioridad Should [inferido]. Integración: —. Fase: —.
**Reglas de negocio:**
1. Cada incidencia conserva, desde su alta, una copia textual de las denominaciones de sala, oficina y categoría vigentes en ese momento
2. Las denominaciones históricas registradas en el alta son inmutables: ninguna operación posterior las modifica
3. El vínculo por identificador de una incidencia con su sala, oficina y categoría se mantiene aunque el elemento de catálogo se renombre o se desactive
4. Cuando la denominación vigente de un elemento de catálogo difiere de la registrada en el alta, el detalle y el historial muestran ambas y el listado muestra la vigente
5. Una incidencia no puede darse de alta con sala, oficina o categoría ajenas al catálogo
**Criterios de aceptación:**
1. AC-RET-09: **Dado** una incidencia cerrada de la sala «Sala 2», **cuando** el ADMINISTRADOR renombra esa sala a «Sala Norte» y después se consulta el detalle de la incidencia, **entonces** se muestra «Sala Norte (en el momento del alta: Sala 2)»; y **dado** una categoría desactivada tras el cierre, **cuando** se consulta el detalle, **entonces** la clasificación sigue presente y legible.
2. AC-RET-10: **Dado** una incidencia con snapshots de denominación ya persistidos, **cuando** cualquier operación de actualización intenta modificar `room_name_snapshot`, `office_name_snapshot` o `category_name_snapshot`, **entonces** la operación se rechaza con `409` «El registro histórico no admite modificaciones» y ningún dato cambia.
**Validaciones:**
1. `room_name_snapshot` y `office_name_snapshot` (máx. 120 caracteres) y `category_name_snapshot` (máx. 60 caracteres) son obligatorios y se pueblan en el alta
2. El alta rechaza valores de sala, oficina o categoría que no existan en los catálogos `cat_salas`, `cat_oficinas` y `cat_categorias_incidencia`
3. Ninguna operación de actualización puede modificar los campos `*_snapshot`
**Escenarios de error:**
1. El registro histórico no admite modificaciones
2. No ha sido posible recuperar la denominación histórica de la incidencia; se muestra la vigente
**Campos de datos:**
- `room_name_snapshot` (string, obligatorio) — Longitud máxima 120; inmutable tras su creación
- `office_name_snapshot` (string, obligatorio) — Longitud máxima 120; inmutable tras su creación
- `category_name_snapshot` (string, obligatorio) — Longitud máxima 60; inmutable tras su creación

### REQ-158 — Listado de incidencias propias reportadas por el empleado con su estado actual
El empleado autenticado consulta el listado de las incidencias que él mismo ha reportado, con su estado actual. Reglas: devuelve todas y solo las incidencias cuyo reported_by_user_id coincide con el usuario de la sesión, sin excepción por antigüedad o estado; incluye las cerradas dentro de la ventana de retención de 2 años (dep. REQ-147); el alcance se resuelve en la consulta a BD y nunca por filtrado en la SPA (dep. REQ-024, REQ-029); si no hay incidencias, lista vacía con HTTP 200 y mensaje informativo, nunca error; el estado mostrado es siempre el vigente en BD (sin caché de negocio). Flujo: EMPLEADO entra en «Mis incidencias» desde la navegación (dep. REQ-010) → el backend resuelve identidad y rol de la sesión (dep. REQ-059, REQ-018) → consulta paginada acotada a las propias → render de tabla → al seleccionar fila navega al detalle (SEG-03); sin resultados → estado vacío informativo (dep. REQ-109). Datos por fila: incident_id (number, PK, obligatorio), room_name (string, obligatorio, cat_salas, conservando la denominación histórica vigente en el alta — dep. REQ-150), office_name (string, obligatorio, cat_oficinas), category_name (string, obligatorio, cat_categorias_incidencia), created_at (timestamp, obligatorio), status_code (string, obligatorio, cat_estados_incidencia: abierta | en curso | resuelta | cerrada), assigned_technician_name (string, opcional/nullable mientras no haya asignación), updated_at (timestamp, obligatorio, fecha del último cambio de estado). Catálogos: cat_salas, cat_oficinas, cat_categorias_incidencia, cat_estados_incidencia (provistos por REQ-093..REQ-099, REQ-117). Orden y paginación: orden por defecto created_at DESC; paginación con page (int ≥ 1) y page_size (int, defecto 20, máx 100), estable ante inserciones concurrentes (desempate por incident_id DESC) [inferido]. Validaciones: page y page_size enteros dentro de rango; cualquier parámetro de identidad enviado por el cliente se ignora (dep. REQ-064). Errores: sin sesión válida → HTTP 401 + redirección a la pantalla de acceso, mensaje «Tu sesión ha caducado. Vuelve a iniciar sesión» (dep. REQ-058); paginación inválida → HTTP 400 «Parámetros de consulta no válidos»; fallo de BD → HTTP 500 «No se ha podido recuperar tu listado de incidencias. Inténtalo de nuevo» sin detalle técnico. Seguridad: rol EMPLEADO sobre su propio ámbito de datos, alcance «solo las propias» (dep. REQ-014, REQ-029); el listado completo con filtros del TECNICO_DE_MANTENIMIENTO es REQ-025/REQ-105; no requiere doble factor (REQ-049). Eventos de dominio: ninguno (solo lectura). Criterios de aceptación: Given un EMPLEADO con 3 incidencias propias y otras 10 de terceros, when abre «Mis incidencias», then la respuesta contiene exactamente esas 3 y ninguna ajena. Given un EMPLEADO sin incidencias, when abre la vista, then ve listado vacío con mensaje informativo y ningún error. Given una incidencia propia que un técnico acaba de pasar a «en curso», when recarga, then la fila muestra status_code = en curso y el updated_at de esa transición. Given una incidencia propia cerrada hace 6 meses, when abre el listado, then sigue apareciendo. Dependencias: REQ-024, REQ-029, REQ-010, REQ-059, REQ-147, REQ-150, REQ-109. Prioridad: Must [inferido].
**Reglas de negocio:**
1. El listado «Mis incidencias» de un empleado contiene todas y solo las incidencias cuyo reportante es el usuario de la sesión, sin excepción por antigüedad ni por estado
2. Una incidencia en estado `cerrada` sigue siendo visible en el listado de su reportante mientras se encuentre dentro de la ventana de retención de 2 años
3. El alcance de propiedad del listado queda determinado por la consulta a base de datos; ningún resultado ajeno llega al cliente aunque este no lo muestre
4. Un empleado sin incidencias reportadas tiene un listado vacío, y un listado vacío no es una condición de error
5. El estado mostrado para cada incidencia es el vigente en base de datos en el instante de la consulta; no existe estado de negocio cacheado
6. La identidad del reportante proviene exclusivamente de la sesión: ningún identificador de usuario recibido del cliente altera el conjunto de resultados
7. El tamaño de página del listado es como máximo 100 y su valor por defecto es 20; el número de página es un entero mayor o igual que 1
8. El orden del listado es determinista: `created_at` descendente con desempate por `incident_id` descendente, de modo que dos páginas consecutivas nunca repiten ni omiten una incidencia ante inserciones concurrentes
**Criterios de aceptación:**
1. AC-SEG-01: **Dado** un empleado autenticado con 3 incidencias propias en un sistema que contiene además 10 incidencias reportadas por terceros, **cuando** abre la vista «Mis incidencias», **entonces** el listado devuelve **exactamente esas 3 y ninguna ajena**, con sala, oficina, categoría, fecha de creación, estado vigente, técnico asignado (o vacío) y fecha del último cambio, y el acotado se resuelve en la consulta a BD (verificable porque la manipulación de parámetros de identidad no altera el resultado).
2. AC-SEG-02: **Dado** un empleado sin ninguna incidencia reportada y, en otro escenario, un empleado con una incidencia propia `cerrada` hace 6 meses, **cuando** cada uno abre «Mis incidencias», **entonces** el primero recibe HTTP 200 con lista vacía y mensaje informativo (nunca un error) y el segundo sigue viendo su incidencia cerrada dentro de la ventana de retención de 2 años.
3. AC-SEG-05: **Dado** un empleado y un `incident_id` correspondiente a una incidencia reportada por otro usuario, y en otro escenario un `incident_id` inexistente, **cuando** solicita el detalle o el adjunto de cualquiera de ellos, **entonces** recibe en **ambos casos la misma respuesta de denegación uniforme**, sin diferencia observable en código, cuerpo, cabeceras ni tiempo de respuesta que permita inferir la existencia del recurso, y sin exponer dato alguno de la incidencia.
**Validaciones:**
1. `page` debe ser un número entero mayor o igual que 1
2. `page_size` debe ser un número entero dentro del rango permitido (valor por defecto 20, máximo 100)
3. Cualquier parámetro de identidad de usuario (p. ej. `reported_by_user_id`) recibido del cliente se ignora: la identidad se toma siempre de la sesión
4. Si los parámetros de paginación no son válidos, la consulta no se ejecuta y se responde HTTP 400 «Parámetros de consulta no válidos»
**Escenarios de error:**
1. Parámetros de paginación ausentes, no numéricos o fuera del rango permitido
2. La sesión del usuario no es válida o ha caducado y debe iniciar sesión de nuevo
3. El listado de incidencias propias no se ha podido recuperar en este momento; se invita a reintentar
**Campos de datos:**
- `incident_id` (integer, obligatorio) — Clave primaria de la incidencia
- `room_name` (string, obligatorio) — Valor de `cat_salas`; se conserva la denominación histórica vigente en el alta
- `office_name` (string, obligatorio) — Valor de `cat_oficinas`
- `category_name` (string, obligatorio) — Valor de `cat_categorias_incidencia`
- `created_at` (datetime, obligatorio) — Orden por defecto del listado: `created_at` DESC
- `status_code` (enum, obligatorio) — `abierta` | `en curso` | `resuelta` | `cerrada` (`cat_estados_incidencia`)
- `assigned_technician_name` (string, opcional) — Nullable mientras no exista asignación
- `updated_at` (datetime, obligatorio) — Fecha del último cambio de estado de la incidencia
- `page` (integer, opcional) — Entero ≥ 1; fuera de rango → HTTP 400
- `page_size` (integer, opcional) — Entero; defecto 20, máximo 100

### REQ-160 — Detalle de incidencia propia con estado, técnico asignado y seguimiento completo
El empleado reportante accede al detalle de una incidencia propia con su estado actual, el técnico asignado y el seguimiento completo. Reglas: el detalle solo es accesible si el reported_by_user_id coincide con el usuario de la sesión; en caso contrario, denegación uniforme que no revela la existencia ni los datos del recurso (dep. REQ-023, REQ-031); el detalle es de solo lectura para el EMPLEADO, sin acciones de ciclo de vida, asignación ni reclasificación (dep. REQ-022, REQ-030); muestra el estado vigente y, si está cerrada, el comentario de resolución íntegro con autor y fecha, reutilizando REQ-115; la línea temporal del historial de cambios de estado se embebe por referencia a REQ-124/REQ-127, sin reimplementarla, con su mismo alcance de datos (dep. REQ-026); la foto adjunta, si existe, se muestra y descarga vía el recurso protegido de REQ-027/REQ-091 con el mismo control de alcance. Flujo: EMPLEADO selecciona una fila de SEG-01/SEG-02 (o navega por incident_id) → el backend comprueba propiedad → devuelve ficha + historial + adjunto → puede volver al listado conservando filtros (SEG-02); incidencia inexistente o ajena → denegación uniforme. Datos mostrados: incident_id (number), room_name + office_name (string, denominación histórica — REQ-150), category_name (string), description (string, íntegra), photo_id (opcional/nullable, con enlace de descarga protegido), status_code (string, cat_estados_incidencia), created_at (timestamp), updated_at (timestamp), assigned_technician_name (string, nullable; vacío mientras no haya asignación), resolution_comment (string, nullable, solo si status_code = cerrada), closed_at (timestamp, nullable), historial: lista de {from_status, to_status, actor_name, changed_at, comment} (dep. REQ-124, REQ-127). Catálogos: cat_estados_incidencia, cat_categorias_incidencia, cat_salas, cat_oficinas. Validaciones: incident_id numérico y existente → si no, denegación uniforme; no se aceptan parámetros de identidad del cliente (dep. REQ-064). Errores: incidencia de otro reportante o inexistente → misma respuesta HTTP 404 «No se ha encontrado la incidencia» (o 403 uniforme según política de REQ-078), sin efectos laterales ni diferencia observable; sin sesión → 401 + redirección (dep. REQ-058); adjunto no recuperable → la ficha se muestra igualmente con aviso «La imagen adjunta no está disponible en este momento» (dep. REQ-149). Seguridad: rol EMPLEADO con alcance «solo las propias» (dep. REQ-014, REQ-026); el TECNICO_DE_MANTENIMIENTO accede al detalle de cualquier incidencia por REQ-026/REQ-030; el control es siempre vinculante en la API REST, nunca en la SPA (dep. REQ-013, REQ-032); acceso a datos personales limitado a nombre y correo corporativo (REQ-049); no requiere doble factor. Eventos de dominio: ninguno (solo lectura). Criterios de aceptación: Given un EMPLEADO y una incidencia propia «en curso» con técnico asignado, when abre el detalle, then ve descripción, foto si la hubiera, categoría, sala, estado actual y nombre del técnico asignado. Given una incidencia reportada por otro usuario, when solicita su detalle por incident_id, then recibe la denegación uniforme y ningún dato. Given una incidencia propia cerrada, when abre el detalle, then ve el comentario de resolución íntegro con autor y fecha de cierre. Given una incidencia propia con tres cambios de estado, when abre el detalle, then ve la línea temporal con estado origen/destino, autor y fecha de cada transición en orden cronológico. Given una incidencia propia sin técnico asignado, when abre el detalle, then el campo de técnico aparece vacío o como «sin asignar», sin error. Dependencias: SEG-01, REQ-023, REQ-026, REQ-027, REQ-091, REQ-115, REQ-124, REQ-127, REQ-149, REQ-150. [gap: el RFP no precisa qué dato del técnico asignado es visible para el reportante (nombre completo, alias o solo el hecho de la asignación)]. Prioridad: Must [inferido].
**Reglas de negocio:**
1. El detalle de una incidencia solo es accesible para el usuario cuyo identificador coincide con el `reported_by_user_id` de esa incidencia
2. Una incidencia inexistente y una incidencia ajena producen respuestas indistinguibles para el empleado, sin diferencia observable en código, cuerpo ni tiempo de respuesta
3. El detalle del reportante es de solo lectura: no existe ninguna acción de ciclo de vida, asignación ni reclasificación disponible para el rol EMPLEADO
4. El comentario de resolución está presente en el detalle si y solo si la incidencia está en estado `cerrada`
5. El historial mostrado al reportante tiene el mismo alcance de datos que el historial general de la incidencia y sus entradas están ordenadas cronológicamente
6. El técnico asignado es un dato ausente mientras la incidencia no tenga asignación, y su ausencia no es una condición de error
7. El adjunto fotográfico de una incidencia está sujeto al mismo alcance de propiedad que su detalle: solo su reportante y el técnico de mantenimiento pueden descargarlo
8. Un adjunto no recuperable no impide la visualización del resto de la ficha
9. La sala y la oficina mostradas en el detalle conservan la denominación vigente en el momento del alta de la incidencia, no la denominación actual del catálogo
10. El control de alcance del detalle es vinculante en la API REST; la ocultación en la SPA no constituye por sí sola una restricción de acceso
**Criterios de aceptación:**
1. AC-SEG-04: **Dado** un empleado y una incidencia propia en estado `en curso` con técnico asignado, **cuando** abre su detalle, **entonces** ve descripción íntegra, sala y oficina con su denominación histórica, categoría, foto adjunta descargable si existe, estado vigente y nombre del técnico asignado; y **dado** que la incidencia está `cerrada`, **cuando** abre el detalle, **entonces** ve además el comentario de resolución íntegro con autor y fecha de cierre, sin que se le ofrezca ninguna acción de ciclo de vida o asignación.
2. AC-SEG-05: **Dado** un empleado y un `incident_id` correspondiente a una incidencia reportada por otro usuario, y en otro escenario un `incident_id` inexistente, **cuando** solicita el detalle o el adjunto de cualquiera de ellos, **entonces** recibe en **ambos casos la misma respuesta de denegación uniforme**, sin diferencia observable en código, cuerpo, cabeceras ni tiempo de respuesta que permita inferir la existencia del recurso, y sin exponer dato alguno de la incidencia.
3. AC-SEG-06: **Dado** una incidencia propia con tres cambios de estado registrados, **cuando** el empleado reportante abre su detalle, **entonces** obtiene la línea temporal embebida con estado origen, estado destino, actor y fecha de cada transición en orden cronológico, con el mismo alcance de datos que el historial canónico, y si el adjunto no es recuperable la ficha se muestra igualmente con el aviso «La imagen adjunta no está disponible en este momento».
**Validaciones:**
1. `incident_id` debe ser numérico y corresponder a una incidencia existente; en caso contrario se responde con la denegación uniforme
2. No se aceptan parámetros de identidad de usuario enviados por el cliente: se ignoran y la identidad se resuelve desde la sesión
**Escenarios de error:**
1. La sesión del usuario no es válida o ha caducado y debe iniciar sesión de nuevo
2. No se ha encontrado la incidencia solicitada (respuesta uniforme tanto si no existe como si no pertenece al usuario, sin revelar dato alguno)
3. Identificador de incidencia con formato no admitido; se responde con la misma denegación uniforme sin revelar existencia
4. La imagen adjunta no está disponible en este momento; la ficha se devuelve completa salvo el adjunto, con aviso al usuario
5. El detalle de la incidencia no se ha podido recuperar en este momento; se invita a reintentar
**Campos de datos:**
- `incident_id` (integer, obligatorio) — Numérico y existente; si no, denegación uniforme
- `room_name` (string, obligatorio) — Valor de `cat_salas`
- `office_name` (string, obligatorio) — Valor de `cat_oficinas`
- `category_name` (string, obligatorio) — Valor de `cat_categorias_incidencia`
- `description` (string, obligatorio) — Se muestra completa, sin truncar
- `photo_id` (string, opcional) — Nullable; descarga vía recurso protegido con el mismo alcance que el detalle
- `status_code` (enum, obligatorio) — `abierta` | `en curso` | `resuelta` | `cerrada` (`cat_estados_incidencia`)
- `created_at` (datetime, obligatorio) — Fecha y hora de alta de la incidencia
- `updated_at` (datetime, obligatorio) — Fecha del último cambio de estado
- `assigned_technician_name` (string, opcional) — Nullable; se muestra vacío o «sin asignar» si no hay asignación
- `resolution_comment` (string, opcional) — Solo presente si `status_code = cerrada`
- `closed_at` (datetime, opcional) — Nullable; informado solo si la incidencia está cerrada
- `historial_from_status` (enum, opcional) — Valores de `cat_estados_incidencia`; vacío en la transición inicial
- `historial_to_status` (enum, obligatorio) — Valores de `cat_estados_incidencia`
- `historial_actor_name` (string, obligatorio) — Dato personal limitado a nombre y correo corporativo
- `historial_changed_at` (datetime, obligatorio) — Listado en orden cronológico
- `historial_comment` (string, opcional) — Nullable

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-053/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-053/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: validar el modelo de datos que construye esta tarea contra el motor real.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.
El esquema de `TSK-044` ya está APLICADO en este servicio (changelogs Liquibase de su rama mergeada): asume las tablas creadas, no las vuelvas a crear ni las modifiques desde esta tarea.

Esta tarea materializa el MODELO DE DATOS: el motor se levanta VACÍO a propósito, para que valides tu propio changelog contra él. Cómo, exactamente:

```sh
./.mind/TSK-053/liquibase.sh <changelog-maestro>            # aplica (update)
./.mind/TSK-053/liquibase.sh <changelog-maestro> status
./.mind/TSK-053/liquibase.sh <changelog-maestro> rollback-count 99  # reversibilidad
```

Ese script lo genera la plataforma y corre Liquibase como contenedor contra ESTE motor. **NO descargues ni instales Liquibase por tu cuenta**: sus JAR acabarían commiteados en el repo del cliente, y apuntar al `local/*.properties` del arquetipo NO sirve (esa configuración mira a otra BBDD, no a la de tu sesión).

Comprueba las tres cosas antes de entregar: que el `update` termina limpio, que el rollback deshace, y que un segundo `update` es idempotente. Un changelog que nunca se ejecutó no está verificado — y la plataforma lo VUELVE a aplicar por su cuenta tras tu entrega, así que un rojo saldrá igual en el PR.

### Si el entorno no está disponible
Comprueba `.mind/TSK-053/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.