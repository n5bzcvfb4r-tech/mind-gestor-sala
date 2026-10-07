# TSK-060 · Índices, vistas de filtro y agregación: bandeja, búsqueda acento-insensible y recuentos

- Componente dueño: `ARC-016`
- Arquetipo del repo: `database-relational` — respeta sus convenciones; NUNCA te salgas de él (ver «Contrato de salida del arquetipo»).
- Zonas de código de ESTA tarea (trabajo principal): `sources/facilities/changelogs/0.0.1/ddl/17-indices-consulta.xml`. Fuera de ellas NO amplíes alcance de negocio — **EXCEPTO** el composition root y el manifiesto del host necesarios para montar lo entregado (sección Composition root).

## Definition of Done
Añade (sin redeclarar ninguna tabla existente, solo `createIndex` y `createView`): índices compuestos de bandeja sobre `incidencia` —(status, created_at DESC, incident_id DESC), (room_id, status), (category_id, status), (assigned_technician_id, status)— que soportan el orden por defecto `created_at DESC` con desempate estable por `incident_id DESC` y la paginación `OFFSET ... FETCH` resuelta en base de datos (REQ-106); índice funcional acento e insensible a mayúsculas `NLSSORT(description,'NLS_SORT=SPANISH_AI')` más índice sobre `UPPER(reference_code)` para la búsqueda textual (REQ-110); vistas `v_filtro_sala`, `v_filtro_oficina` y `v_filtro_categoria` que devuelven los valores ACTIVOS más los INACTIVOS con al menos una incidencia asociada, marcados con un flag `es_inactivo` (REQ-103/REQ-159); vista `v_recuento_incidencias` con LEFT JOIN desde cada catálogo para que todo valor aparezca aunque su recuento sea 0 (REQ-104/AC-CLAS-05). Oráculos contra el Oracle 23ai real, con un dataset sembrado de ~1.200 incidencias: test que recorre las 3 páginas de 25 de un conjunto de 60 y comprueba que no se repite ni se omite ninguna fila y que `total_count` se calcula sobre el conjunto filtrado (AC-BAN-02); test que busca 'proyector' y recupera filas con 'Proyector' y 'PROYECTÓR' (AC-BAN-07); test que ordena por estado y obtiene ABIERTA→EN_CURSO→RESUELTA→CERRADA y no el orden alfabético (AC-BAN-03); test que agrupa por categoría y obtiene 5 filas incluidas las de recuento 0; comprobación del plan de ejecución (`EXPLAIN PLAN`) que confirma uso de índice y ausencia de FULL TABLE SCAN en la consulta de bandeja, con p95 < 2000 ms sobre el dataset (AC-BAN-09). `update`/`rollback-count` verdes.

## Oráculos de verificación (dod-oracles) — OBLIGATORIO

El DoD se evalúa por **comportamiento**, no porque exista un fichero o un string «implementado». Lo siguiente es **Blocker** si lo usas como entrega de producto (los dobles solo valen en tests):

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

Tus zonas (`sources/facilities/changelogs/0.0.1/ddl/17-indices-consulta.xml`) pueden ya contener código de una TSK predecesora mergeada (o del esqueleto). Antes de crear tipos nuevos:

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

### REQ-103 — Alimentar los filtros de sala, oficina y categoría del listado con valores de catálogo, incluidos inactivos con uso histórico
Los desplegables de filtro se construyen desde cat_salas, cat_oficinas y cat_categorias_incidencia, no desde valores libres; se ofrecen los valores activos más los inactivos que tengan al menos una incidencia asociada, marcados como '(inactiva)'. Los filtros son combinables (sala Y categoría Y estado) y el filtro por oficina selecciona todas las salas de esa oficina. Un identificador de filtro fuera de catálogo se rechaza en lugar de devolver el listado sin filtrar. Flujo: el usuario abre el listado, carga los catálogos de filtro, selecciona combinación, se consulta el listado ya acotado por rol y puede limpiar filtros. Datos (parámetros): room_id (number, opcional), office_id (number, opcional), category_id (number, opcional). Validaciones: cada parámetro debe existir en su catálogo; combinación sin resultados devuelve lista vacía, no error. Errores: 422 'El filtro seleccionado no es válido' ante identificadores inexistentes; 403 si el rol no tiene acceso al listado completo. Aceptación: un TECNICO_DE_MANTENIMIENTO que filtra por una oficina obtiene las incidencias de todas las salas de esa oficina dentro de su alcance; un category_id inexistente enviado como filtro provoca rechazo de la petición y no se devuelve un listado sin filtrar. Seguridad: el filtrado se aplica después del acotado de alcance por rol, nunca antes: EMPLEADO solo sobre sus propias incidencias (REQ-024) y TECNICO_DE_MANTENIMIENTO sobre el conjunto completo (REQ-025, REQ-029). No redefine el control de acceso al listado (REQ-024/REQ-025/REQ-026): solo aporta la resolución de los valores de catálogo para los filtros. Dependencias: CAT-01, SAL-01, SAL-03, REQ-024, REQ-025. Prioridad: Must. auth_type: JWT. data_scope: own_only (EMPLEADO) / all (TECNICO_DE_MANTENIMIENTO).
**Reglas de negocio:**
1. Los valores ofrecidos en los filtros de sala, oficina y categoría proceden siempre de los catálogos, nunca de valores libres
2. Un valor de catálogo inactivo aparece en los filtros únicamente si tiene al menos una incidencia asociada, y se marca como inactivo
3. Un identificador de filtro que no existe en su catálogo invalida la consulta; no existe respuesta con listado sin filtrar
4. Una combinación de filtros sin coincidencias devuelve un listado vacío, no un error
5. El alcance de incidencias visibles por rol prevalece sobre cualquier filtro: el filtro nunca amplía el conjunto accesible
**Criterios de aceptación:**
1. AC-INC-01: Dado un empleado autenticado, cuando da de alta una incidencia con sala afectada, categoría (mobiliario, climatización, audiovisual, limpieza, otros), descripción breve y opcionalmente una foto, entonces la incidencia se crea en estado `ABIERTA` con código único visible; y cuando omite sala, categoría o descripción, entonces recibe un error de validación campo a campo y no se crea ningún registro. [pendiente mapping 1.4] (catálogos: REQ-095, REQ-103)
**Validaciones:**
1. Los parámetros de filtro `room_id`, `office_id` y `category_id` son opcionales y, si se informan, deben ser numéricos
2. Cada identificador de filtro informado debe existir en su catálogo correspondiente; un identificador inexistente se rechaza en lugar de ignorarse
**Escenarios de error:**
1. Consulta del listado sin sesión válida
2. Los filtros de sala, oficina y categoría deben enviarse como valores numéricos
3. El filtro seleccionado no es válido
4. El usuario no tiene permisos para consultar el listado solicitado
**Campos de datos:**
- `room_id` (integer, opcional) — Debe existir en `cat_salas`; se admiten inactivas con incidencias asociadas
- `office_id` (integer, opcional) — Debe existir en `cat_oficinas`
- `category_id` (integer, opcional) — Debe existir en `cat_categorias_incidencia`; identificador fuera de catálogo se rechaza

### REQ-104 — Recuento agregado de incidencias por oficina, sala y categoría para análisis de carga
La agregación se calcula sobre las incidencias visibles para el solicitante y devuelve el número de incidencias por cada valor de catálogo, con posibilidad de acotar por rango de fechas de creación y por estado. Los valores de catálogo sin incidencias aparecen con recuento 0 para no ocultar salas sanas. Es una lectura agregada, sin exportación ni cuadro de mando (fuera del alcance del RFP). Flujo: TECNICO_DE_MANTENIMIENTO o EQUIPO_DE_FACILITIES abre la vista de análisis, selecciona agrupación (oficina, sala o categoría) y rango de fechas, y obtiene la tabla de recuentos ordenada de mayor a menor. Datos (parámetros): group_by (varchar2, dominio OFFICE|ROOM|CATEGORY, obligatorio), date_from (date, opcional), date_to (date, opcional, ≥ date_from), status (opcional, del catálogo de estados del ciclo de vida). Salida: group_id, group_name, incident_count (number). Validaciones: group_by dentro del dominio permitido; rango de fechas coherente y no superior a la retención de 2 años (REQ-015). Errores: 422 'El criterio de agrupación no es válido'; 422 'El rango de fechas no es correcto'; 403 al rol EMPLEADO. Aceptación: un TECNICO_DE_MANTENIMIENTO que agrupa por categoría en un rango de fechas obtiene el recuento de cada una de las cinco categorías, incluidas las de recuento 0; un EMPLEADO que invoca la agregación recibe 403 sin revelar datos. Seguridad: accesible a TECNICO_DE_MANTENIMIENTO y, si el cliente lo confirma, a EQUIPO_DE_FACILITIES; denegado a EMPLEADO por implicar alcance sobre incidencias ajenas (REQ-023, REQ-029); resultado agregado sin datos personales del reportante. Dependencias: CLAS-03, CAT-01, SAL-01, SAL-03. [inferido: el RFP no pide reporting; la agregación procede del valor de negocio declarado en EPIC-009] [ambigüedad: la relación entre EQUIPO_DE_FACILITIES y el rol TECNICO_DE_MANTENIMIENTO no está definida en el RFP]. Prioridad: Could. auth_type: JWT. data_scope: all.
**Reglas de negocio:**
1. El recuento agregado se calcula únicamente sobre las incidencias visibles para el solicitante según su rol
2. Todo valor de catálogo aparece en la agregación, con recuento cero si no tiene incidencias asociadas
3. La fecha final de un rango de agregación no es anterior a la fecha inicial
4. Un rango de fechas de agregación no excede el periodo de retención de dos años
5. El rol `EMPLEADO` no tiene acceso al recuento agregado de incidencias
6. El resultado agregado no contiene datos personales del reportante
**Criterios de aceptación:**
1. AC-CLAS-05: **Dado** un `TECNICO_DE_MANTENIMIENTO`, **cuando** solicita el recuento agrupado por oficina, sala o categoría con un rango de fechas válido dentro de los 2 años de retención, **entonces** obtiene el recuento de **cada** valor de catálogo ordenado de mayor a menor, **incluidos los de recuento 0**; y **dado** un `EMPLEADO`, **cuando** invoca la agregación, **entonces** recibe 403 sin revelar ningún dato.
**Validaciones:**
1. `group_by` es obligatorio y debe pertenecer al dominio `OFFICE`|`ROOM`|`CATEGORY`
2. `date_from` y `date_to` son opcionales y deben ser fechas válidas, con `date_to` ≥ `date_from`
3. El rango de fechas solicitado no puede exceder el periodo de retención de 2 años
4. `status`, si se informa, debe pertenecer al catálogo de estados del ciclo de vida de la incidencia
**Escenarios de error:**
1. El usuario no tiene permisos para consultar el recuento agregado
2. El criterio de agrupación es obligatorio y debe enviarse con un valor admitido
3. El criterio de agrupación no es válido
4. Las fechas del rango deben enviarse con un formato de fecha válido
5. El rango de fechas no es correcto
6. El rango de fechas excede el periodo de conservación disponible
7. El estado indicado no pertenece al ciclo de vida de la incidencia
**Campos de datos:**
- `group_by` (enum, obligatorio) — Valores: OFFICE, ROOM, CATEGORY
- `date_from` (date, opcional) — No anterior a la retención de 2 años (REQ-015)
- `date_to` (date, opcional) — Debe ser ≥ date_from
- `status` (enum, opcional) — Valor del catálogo de estados: abierta, en curso, resuelta, cerrada
- `group_id` (integer, obligatorio) — Corresponde a office_id, room_id o category_id según group_by
- `group_name` (string, obligatorio) — Resuelto desde el catálogo correspondiente
- `incident_count` (integer, obligatorio) — ≥ 0; los valores sin incidencias se devuelven con recuento 0

### REQ-106 — Ordenación configurable y paginación estable del listado
Reglas: orden por defecto created_at descendente (lo más reciente arriba); columnas ordenables: created_at, updated_at, status, room_name, category_name, age_days; status ordena por la secuencia del ciclo de vida (abierta → en curso → resuelta → cerrada), no alfabéticamente; el orden es estable con desempate siempre por incident_id descendente; orden y paginación se resuelven en base de datos (Oracle 23ai, OFFSET … FETCH), nunca troceando en la SPA; el total se calcula sobre el conjunto ya filtrado. Datos: sort_by (string, lista blanca, default created_at), sort_dir (enum ASC|DESC, default DESC), page (int ≥1, default 1), page_size (int, valores permitidos 10/25/50, default 25); respuesta con items[], total_count (int), page, page_size, total_pages. Validaciones: sort_by fuera de la lista blanca → 400 «Criterio de ordenación no válido»; page_size no permitido → 400 «Tamaño de página no válido»; page > total_pages → 200 con items vacío y total_count real (no es error). Errores: 400 con mensaje concreto; 401/403 como BAN-01. Aceptación: Dado un técnico en la bandeja, cuando no elige orden, entonces ve las incidencias más recientes primero; Dado un listado de 60 incidencias, cuando solicita página 2 con tamaño 25, entonces recibe los elementos 26–50, total_count=60 y total_pages=3, sin repetir ni omitir filas entre páginas. Seguridad: TECNICO_DE_MANTENIMIENTO; la paginación no amplía el alcance de datos del rol. Dependencias: BAN-01, REQ-025. [gap: el RFP no fija tamaño de página ni orden por defecto; los valores propuestos se dimensionan para el volumen declarado de 50 incidencias/mes y 50 usuarios concurrentes]. Prioridad [inferido]. Prioridad: Must.
**Reglas de negocio:**
1. En ausencia de criterio explícito, el orden de la bandeja es `created_at` descendente
2. Solo son ordenables las columnas `created_at`, `updated_at`, `status`, `room_name`, `category_name` y `age_days`; cualquier otro criterio es inválido
3. La ordenación por `status` sigue la secuencia del ciclo de vida (abierta → en curso → resuelta → cerrada) y no el orden alfabético de la etiqueta
4. Dos incidencias con igual valor en la columna de ordenación se desempatan siempre por `incident_id` descendente, de modo que el orden es determinista
5. Una misma incidencia no aparece en dos páginas distintas ni se omite entre páginas consecutivas de una misma consulta
6. El `total_count` se calcula sobre el conjunto ya filtrado, no sobre el total de incidencias existentes
7. El tamaño de página válido es exclusivamente 10, 25 o 50, siendo 25 el valor por defecto
8. Una página solicitada por encima de `total_pages` no es una condición de error: el resultado es vacío con `total_count` real
9. La paginación y la ordenación no amplían el conjunto de incidencias accesible por el rol
**Criterios de aceptación:**
1. AC-BAN-09: Dado el volumen de la ventana de retención (≈1.200 incidencias) y 50 usuarios concurrentes, cuando un técnico ejecuta consultas de bandeja con filtros combinados y búsqueda por texto durante 7 días laborables en producción, entonces el p95 del endpoint de listado es < 2.000 ms y el 100 % de las consultas resuelve orden y paginación en base de datos (`OFFSET … FETCH`), sin troceo en la SPA. [inferido: umbral no declarado en el RFP]
2. AC-BAN-02: Dado un listado de 60 incidencias y un técnico que no selecciona criterio de orden, cuando carga la bandeja, entonces ve las incidencias ordenadas por fecha de creación descendente; y cuando solicita página 2 con tamaño 25, entonces recibe exactamente los elementos 26–50, `total_count=60` y `total_pages=3`, sin filas repetidas ni omitidas al recorrer las 3 páginas (desempate estable por `incident_id` descendente).
3. AC-BAN-03: Dado un técnico en la bandeja, cuando ordena por estado, entonces el resultado sigue la secuencia del ciclo de vida `ABIERTA → EN_CURSO → RESUELTA → CERRADA` y no el orden alfabético; y cuando envía un `sort_by` fuera de la lista blanca o un `page_size` distinto de 10/25/50, entonces recibe 400 con mensaje concreto («Criterio de ordenación no válido» / «Tamaño de página no válido»); y cuando pide una página superior a `total_pages`, entonces recibe 200 con `items` vacío y `total_count` real, no un error.
**Validaciones:**
1. `sort_by` debe pertenecer a la lista blanca `created_at`, `updated_at`, `status`, `room_name`, `category_name`, `age_days`; en caso contrario se devuelve 400 «Criterio de ordenación no válido»
2. `sort_dir` debe ser uno de los valores del enum `ASC` o `DESC` (por defecto `DESC`)
3. `page` debe ser un entero mayor o igual que 1 (por defecto 1)
4. `page_size` debe ser un entero perteneciente al conjunto de valores permitidos 10, 25 o 50; en caso contrario se devuelve 400 «Tamaño de página no válido»
5. Una `page` superior a `total_pages` no es un error de validación: se responde 200 con `items` vacío y el `total_count` real
**Escenarios de error:**
1. El criterio de ordenación solicitado no está entre las columnas ordenables admitidas
2. El sentido de ordenación solicitado no es ascendente ni descendente
3. El tamaño de página solicitado no es uno de los valores permitidos
4. El número de página solicitado no es un entero mayor o igual que uno
5. La sesión del usuario ha caducado al solicitar una página u ordenación
6. El usuario autenticado no tiene el rol de técnico de mantenimiento para paginar u ordenar la bandeja completa
**Campos de datos:**
- `sort_by` (string, opcional) — Lista blanca: created_at, updated_at, status, room_name, category_name, age_days; default created_at
- `sort_dir` (enum, opcional) — ASC | DESC; default DESC
- `page` (integer, opcional) — ≥ 1; default 1; page > total_pages devuelve items vacío
- `page_size` (integer, opcional) — Valores permitidos 10, 25, 50; default 25
- `total_count` (integer, obligatorio) — Calculado sobre el resultado filtrado, no sobre el total absoluto
- `total_pages` (integer, obligatorio) — Derivado de total_count y page_size

### REQ-110 — Búsqueda por texto libre e identificador de incidencia dentro de la bandeja
Reglas: search_text busca simultáneamente en incident_code, description y room_name; la coincidencia es parcial, insensible a mayúsculas y a acentos (comparación acento-insensible en Oracle); se combina en AND con el resto de filtros y respeta el orden y la paginación vigentes; si el texto coincide exactamente con un incident_code, esa incidencia se muestra en primera posición. Datos: search_text (string, 3–100 caracteres, opcional, se aplica trim). Validaciones: longitud < 3 tras trim → el filtro se ignora y se avisa «Introduce al menos 3 caracteres» sin bloquear el listado [ambigüedad: el RFP no define comportamiento de búsqueda]; longitud > 100 → 400; los comodines % y _ se escapan para evitar búsquedas no intencionadas. Errores: 400 descrito; 401/403 como BAN-01; 500 genérico. Aceptación: Dado un técnico, cuando busca «proyector», entonces obtiene las incidencias cuya descripción o sala contienen ese término, con los filtros activos respetados; Dada una búsqueda sin coincidencias, entonces se muestra el estado vacío de BAN-05. Seguridad: exclusivo TECNICO_DE_MANTENIMIENTO sobre el conjunto completo (REQ-029); la búsqueda no habilita a un EMPLEADO a localizar incidencias ajenas (REQ-023, REQ-024). Rendimiento: índice sobre incident_code; con el volumen declarado (≤50 incidencias/mes, ≈1.200 en la ventana de retención) la búsqueda por patrón sobre description es asumible. Dependencias: BAN-01, BAN-02, BAN-05. [inferido: el RFP no pide búsqueda textual; deriva del valor declarado en EPIC-011 de «localizar en segundos las incidencias abiertas de una sala u oficina concreta»]. Prioridad: Could.
**Reglas de negocio:**
1. Un `search_text` es aplicable si y solo si, tras `trim`, tiene entre 3 y 100 caracteres; por debajo de 3 el filtro es inoperante y por encima de 100 es inválido
2. La coincidencia de búsqueda es parcial e insensible a mayúsculas y a acentos sobre `incident_code`, `description` y `room_name`
3. Una incidencia cuyo `incident_code` coincide exactamente con el texto buscado ocupa la primera posición del resultado
4. Los caracteres `%` y `_` del texto buscado son literales y no operan como comodines
5. La búsqueda textual es acumulativa con el resto de filtros y no amplía el alcance de datos del rol: un EMPLEADO no localiza incidencias ajenas por esta vía
**Criterios de aceptación:**
1. AC-BAN-07: Dado un técnico en la bandeja, cuando busca «proyector», entonces obtiene las incidencias cuyo código, descripción o sala contienen el término con coincidencia parcial, insensible a mayúsculas y a acentos, respetando los filtros, el orden y la paginación vigentes; y cuando el texto coincide exactamente con un `incident_code`, entonces esa incidencia aparece en primera posición; y cuando introduce menos de 3 caracteres, entonces el filtro se ignora con el aviso «Introduce al menos 3 caracteres» sin bloquear el listado.
2. AC-BAN-09: Dado el volumen de la ventana de retención (≈1.200 incidencias) y 50 usuarios concurrentes, cuando un técnico ejecuta consultas de bandeja con filtros combinados y búsqueda por texto durante 7 días laborables en producción, entonces el p95 del endpoint de listado es < 2.000 ms y el 100 % de las consultas resuelve orden y paginación en base de datos (`OFFSET … FETCH`), sin troceo en la SPA. [inferido: umbral no declarado en el RFP]
**Validaciones:**
1. `search_text` es opcional y se le aplica `trim` antes de evaluarlo
2. `search_text` con longitud inferior a 3 caracteres tras `trim` no se aplica: se avisa «Introduce al menos 3 caracteres» sin bloquear el listado
3. `search_text` con longitud superior a 100 caracteres se rechaza con 400
4. Los comodines `%` y `_` presentes en `search_text` se escapan antes de construir el patrón de búsqueda
**Escenarios de error:**
1. El texto de búsqueda supera la longitud máxima admitida de 100 caracteres
2. La sesión del usuario ha caducado al ejecutar la búsqueda
3. El usuario autenticado no tiene el rol de técnico de mantenimiento para buscar sobre el conjunto completo de incidencias
4. No ha sido posible completar la búsqueda y se muestra un mensaje genérico de reintento
**Campos de datos:**
- `search_text` (string, opcional) — Longitud 3–100 tras trim; <3 se ignora con aviso; >100 error 400; comodines % y _ escapados; comparación parcial, insensible a mayúsculas y acentos

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

### REQ-159 — Filtrado, búsqueda y ordenación dentro del listado de incidencias propias
El empleado filtra, busca y ordena dentro del listado de sus propias incidencias para localizar una concreta. Reglas: todo filtro se aplica sobre el alcance de propiedad y nunca lo amplía, jamás puede devolver una incidencia de otro reportante (dep. REQ-024); los filtros se combinan con AND; los selectores de sala, oficina y categoría se alimentan del catálogo incluyendo valores desactivados con uso histórico en incidencias del propio empleado (dep. REQ-103, REQ-096); el conjunto de filtros es reproducible en la URL y se restaura al volver desde el detalle (dep. REQ-109); un filtrado sin resultados devuelve lista vacía con mensaje informativo y acción «limpiar filtros», nunca error. Flujo: EMPLEADO en «Mis incidencias» → selecciona filtros y/o escribe texto de búsqueda → la consulta se re-ejecuta acotada a sus incidencias → resultado paginado → «limpiar filtros» restablece el listado completo propio. Datos de entrada: status_code (string múltiple, opcional, cat_estados_incidencia), category_id (number múltiple, opcional, cat_categorias_incidencia), room_id (number múltiple, opcional, cat_salas), office_id (number, opcional, cat_oficinas), created_from / created_to (date, opcionales, created_from ≤ created_to), search_text (string, opcional, 2–100 caracteres, búsqueda parcial e insensible a mayúsculas/acentos sobre description e incident_id), sort_by (enum: created_at | updated_at | status_code, defecto created_at), sort_dir (enum asc | desc, defecto desc). Catálogos: cat_estados_incidencia, cat_categorias_incidencia, cat_salas, cat_oficinas. Validaciones: valores de catálogo existentes → HTTP 400 «Filtro no válido» sin ejecutar consulta; rango de fechas coherente → 400 «La fecha de inicio no puede ser posterior a la de fin»; search_text con longitud mínima 2 → 400 «Introduce al menos 2 caracteres para buscar». Errores: sin sesión → 401 y redirección al acceso (dep. REQ-058); intento de forzar por parámetro un reportante distinto → se ignora el parámetro y se responde con el ámbito propio, dejando traza (dep. REQ-064); fallo de BD → 500 con mensaje genérico. Seguridad: rol EMPLEADO, alcance «solo las propias»; los filtros por técnico asignado y el resto de criterios de la bandeja completa quedan reservados al TECNICO_DE_MANTENIMIENTO (REQ-107, REQ-108, REQ-110) y no se exponen. Eventos de dominio: ninguno (solo lectura). Criterios de aceptación: Given un EMPLEADO con incidencias en varios estados, when filtra por status_code = abierta, then solo ve sus incidencias abiertas. Given un filtro por categoría desactivada con uso histórico propio, when lo aplica, then sus incidencias antiguas de esa categoría siguen apareciendo. Given una combinación de filtros sin coincidencias, when se ejecuta, then ve mensaje informativo y opción de limpiar filtros, sin error. Given un empleado que aplica filtros y entra en el detalle, when vuelve atrás, then los filtros y la página siguen aplicados. Given una petición manipulada con otro reported_by_user_id, when se ejecuta, then la respuesta contiene únicamente incidencias propias. Dependencias: SEG-01, REQ-024, REQ-103, REQ-109. [gap: el RFP solo enumera los criterios de filtrado del listado completo del equipo de mantenimiento («por sala, categoría y estado») y no indica qué filtros debe ofrecer el listado propio del empleado]. Prioridad: Should [inferido].
**Reglas de negocio:**
1. Ningún filtro amplía el alcance de propiedad: cualquier resultado filtrado pertenece siempre al reportante de la sesión
2. Los filtros aplicados simultáneamente se combinan con conjunción lógica (AND)
3. Un valor de catálogo desactivado con uso histórico en incidencias del propio empleado sigue siendo seleccionable como filtro
4. El conjunto de filtros, el criterio de orden y la página activa son reproducibles desde la URL y se conservan al regresar desde el detalle
5. Un filtrado sin coincidencias produce una lista vacía con opción de limpiar filtros, nunca un error
6. `created_from` es anterior o igual a `created_to` en todo rango de fechas aceptado
7. El texto de búsqueda tiene entre 2 y 100 caracteres y es insensible a mayúsculas y acentos
8. Los criterios de filtrado propios de la bandeja completa, como el técnico asignado, no están disponibles en el listado del empleado reportante
**Criterios de aceptación:**
1. AC-SEG-03: **Dado** un empleado con incidencias propias en varios estados y categorías, **cuando** aplica filtros por estado, categoría, sala, oficina o rango de fechas, busca por texto y ordena, **entonces** los resultados son siempre un subconjunto de sus propias incidencias (el filtrado nunca amplía el alcance), las categorías desactivadas con uso histórico propio siguen siendo seleccionables, un filtrado sin coincidencias devuelve mensaje informativo con acción «limpiar filtros» sin error, y al volver desde el detalle se restauran filtros y página.
2. AC-SEG-05: **Dado** un empleado y un `incident_id` correspondiente a una incidencia reportada por otro usuario, y en otro escenario un `incident_id` inexistente, **cuando** solicita el detalle o el adjunto de cualquiera de ellos, **entonces** recibe en **ambos casos la misma respuesta de denegación uniforme**, sin diferencia observable en código, cuerpo, cabeceras ni tiempo de respuesta que permita inferir la existencia del recurso, y sin exponer dato alguno de la incidencia.
**Validaciones:**
1. Los valores de `status_code` deben existir en el catálogo `cat_estados_incidencia`; en caso contrario HTTP 400 «Filtro no válido» sin ejecutar la consulta
2. Los valores de `category_id` deben ser numéricos y existir en `cat_categorias_incidencia`
3. Los valores de `room_id` deben ser numéricos y existir en `cat_salas`
4. El valor de `office_id` debe ser numérico y existir en `cat_oficinas`
5. `created_from` y `created_to` deben tener formato de fecha válido y cumplir `created_from` ≤ `created_to`; si no, HTTP 400 «La fecha de inicio no puede ser posterior a la de fin»
6. `search_text` debe tener entre 2 y 100 caracteres; con menos de 2 se responde HTTP 400 «Introduce al menos 2 caracteres para buscar»
7. `sort_by` solo admite los valores enumerados `created_at` | `updated_at` | `status_code` (defecto `created_at`)
8. `sort_dir` solo admite los valores enumerados `asc` | `desc` (defecto `desc`)
9. Un parámetro de reportante distinto enviado por el cliente se descarta en la entrada y no altera el ámbito de la consulta
**Escenarios de error:**
1. Valor de filtro de sala, oficina, categoría o estado no reconocido dentro del catálogo vigente
2. Rango de fechas incoherente: la fecha de inicio es posterior a la fecha de fin
3. Texto de búsqueda con menos de 2 caracteres o por encima del máximo admitido
4. Criterio o sentido de ordenación no admitido
5. La sesión del usuario no es válida o ha caducado y debe iniciar sesión de nuevo
6. El resultado filtrado no se ha podido recuperar en este momento; se invita a reintentar
**Campos de datos:**
- `status_code` (enum, opcional) — Valores de `cat_estados_incidencia`; valor inexistente → HTTP 400
- `category_id` (integer, opcional) — Debe existir en `cat_categorias_incidencia`; admite valores desactivados con uso histórico propio
- `room_id` (integer, opcional) — Debe existir en `cat_salas`; admite valores desactivados con uso histórico propio
- `office_id` (integer, opcional) — Debe existir en `cat_oficinas`
- `created_from` (date, opcional) — `created_from` ≤ `created_to`
- `created_to` (date, opcional) — `created_to` ≥ `created_from`
- `search_text` (string, opcional) — Longitud 2–100; insensible a mayúsculas y acentos
- `sort_by` (enum, opcional) — `created_at` | `updated_at` | `status_code`; defecto `created_at`
- `sort_dir` (enum, opcional) — `asc` | `desc`; defecto `desc`

## Entorno de prueba de esta sesión

Antes de arrancar tu sesión, la plataforma levanta los servicios de abajo como contenedores efímeros y deja sus datos de conexión en `.mind/TSK-060/env.sh` (y en `env.json`). Contrato de uso:

- **Haz `source .mind/TSK-060/env.sh` antes de cada build/test** que necesite el entorno; si el fichero no existe, el entorno NO se pudo levantar (ver el final de esta sección).
- Los tests **leen la conexión de esas variables** (o de Testcontainers, ver abajo). NUNCA hardcodees host, puerto ni credenciales, y NUNCA toques la configuración `local/` del arquetipo para apuntarla a este entorno.
- Son servicios de PRUEBA y efímeros: se destruyen al terminar la sesión. No guardes nada que deba sobrevivir ni los uses como almacén de resultados.

### `oracle` — gvenzl/oracle-free:23-slim (capa `db`)
Por qué está: validar el modelo de datos que construye esta tarea contra el motor real.
Variables: `MIND_ENV_ORACLE_HOST`, `MIND_ENV_ORACLE_PORT`, `MIND_ENV_ORACLE_URL`, `MIND_ENV_ORACLE_USER`, `MIND_ENV_ORACLE_PASSWORD`.
El esquema de `TSK-044` ya está APLICADO en este servicio (changelogs Liquibase de su rama mergeada): asume las tablas creadas, no las vuelvas a crear ni las modifiques desde esta tarea.

Esta tarea materializa el MODELO DE DATOS: el motor se levanta VACÍO a propósito, para que valides tu propio changelog contra él. Cómo, exactamente:

```sh
./.mind/TSK-060/liquibase.sh <changelog-maestro>            # aplica (update)
./.mind/TSK-060/liquibase.sh <changelog-maestro> status
./.mind/TSK-060/liquibase.sh <changelog-maestro> rollback-count 99  # reversibilidad
```

Ese script lo genera la plataforma y corre Liquibase como contenedor contra ESTE motor. **NO descargues ni instales Liquibase por tu cuenta**: sus JAR acabarían commiteados en el repo del cliente, y apuntar al `local/*.properties` del arquetipo NO sirve (esa configuración mira a otra BBDD, no a la de tu sesión).

Comprueba las tres cosas antes de entregar: que el `update` termina limpio, que el rollback deshace, y que un segundo `update` es idempotente. Un changelog que nunca se ejecutó no está verificado — y la plataforma lo VUELVE a aplicar por su cuenta tras tu entrega, así que un rojo saldrá igual en el PR.

### Si el entorno no está disponible
Comprueba `.mind/TSK-060/env.json`: si su `status` es `unavailable` o `degraded`, la plataforma no pudo darte (todo) el entorno. En ese caso ESCRIBE igualmente los tests de integración y déjalos en el entregable, y repórtalo como health check **Warning** con `check: entorno-de-prueba` — NO como Blocker: no es un defecto de tu tarea, y la verificación queda diferida al CI. Reserva el Blocker para cuando el entorno SÍ estaba y los tests fallan por el código o por el brief.