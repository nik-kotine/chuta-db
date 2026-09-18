# chutaDB

chutaDB es un **DBMS (Sistema de Gestión de Bases de Datos) educativo** escrito en Python, que imita el funcionamiento interno de un motor de base de datos real: un *front-end* SQL que parsea sentencias, un ejecutor que las traduce a operaciones sobre el almacenamiento, y un *back-end* de páginas, buffer pool, archivos e índices que persisten los datos en disco.

Aunque es un juguete ("toy DBMS"), cada componente está inspirado en los mecanismos que usan motores reales (PostgreSQL, InnoDB, SQL Server): páginas con directorio de slots, buffer pool con clock-sweep, árboles B+, hash extensible, hashing y ordenamiento externo. La documentación apunta a ser comprensible para cualquiera que quiera entender *qué pasa por detrás* cuando se ejecuta un `SELECT`, sin dejar de ser rigurosa.

---

## 1. Funcionalidades disponibles

### Lenguaje SQL (lo que el parser entiende)

- **DDL**
  - `CREATE TABLE ... USING HEAP` o `USING SEQUENTIAL`, con columna `PRIMARY KEY` obligatoria.
  - `CREATE INDEX ON tabla (columna) USING BTREE|HASH [CLUSTERED]`.
- **DML**
  - `SELECT` con proyección (`*` o columnas con/sin calificación `tabla.col`), `JOIN ... ON`, `WHERE` (con paréntesis, `AND`, `OR`, `BETWEEN` y los operadores `=`, `!=`/`<>`, `<`, `<=`, `>`, `>=`), `GROUP BY`, `ORDER BY ... ASC|DESC` y `LIMIT n`.
  - Funciones de agregación: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` (y `COUNT(*)`).
  - `INSERT INTO tabla VALUES (...)`.
  - `DELETE FROM tabla WHERE ...` — el `WHERE` es **obligatorio**, para no borrar la tabla completa por accidente.
- **Transacciones**
  - `BEGIN TRANSACTION` / `END TRANSACTION`, aceptadas por el parser (todavía sin rollback/recovery).
- **Detalles del lenguaje**
  - Palabras reservadas insensibles a mayúsculas (`select` = `SELECT`).
  - Comentarios de línea con `--`.
  - Cadenas entre comillas simples; `''` dentro de una cadena representa una comilla literal.
  - Literales `TRUE` / `FALSE`.

### Tipos de datos

`INT`, `FLOAT`, `BOOL`, `DATE` y `VARCHAR(n)`. Internamente el motor soporta además una tabla de tipos más amplia (`smallint`, `bigint`, `text`, `char(n)`, `timestamp`, etc.), aunque solo los cinco de arriba se alcanzan a escribir desde SQL.

### Motor de almacenamiento

- **Dos organizaciones físicas de archivo**: `HeapFile` (registros sin orden) y `SequentialFile` (registros ordenados por clave con lista enlazada y página de overflow).
- **Buffer pool global (singleton)** con algoritmo **clock-sweep**: todas las tablas, índices y catálogo comparten un mismo conjunto de páginas en memoria.
- **Catálogo del sistema en disco** (`sys_tables.dat`, `sys_columns.dat`, `sys_indexes.dat`): la metadata sobrevive al cierre del programa.
- **Páginas de tamaño fijo** con cabecera y tabla de slots, de largo fijo (`FixedPage`) y de largo variable (`VariablePage`).
- **Serialización** de registros a bytes, de largo fijo y variable.
- **Restricciones**: la `PRIMARY KEY` se valida en cada `INSERT` (rechaza `NULL` y duplicados). También hay soporte interno (no expuesto a SQL) para `NOT NULL`, `UNIQUE` y `CHECK`.

### Índices

- **B+ árbol agrupado** (`CLUSTERED`): solo sobre tablas `USING SEQUENTIAL` y sobre la columna `PRIMARY KEY`; el orden del índice coincide con el orden físico de los datos.
- **B+ árbol no agrupado**: sobre tablas `USING HEAP`; guarda referencias (RID) a las filas del heap.
- **Hash extensible** no agrupado: sobre tablas `USING HEAP`; resuelve búsquedas por igualdad.
- Los índices **se registran en el catálogo** y **se recalgan automáticamente** al reabrir la base; el CRUD (`INSERT`/`DELETE`) los mantiene al día.
- El parser/ejecutor **rechaza combinaciones inválidas** (p. ej. `HASH CLUSTERED`, índice B+ no agrupado sobre tabla sequential).

### Procesamiento de consultas

- **Planificador con uso estratégico de índices**: si el `WHERE` tiene un predicado en contexto conjuntivo (`AND`) que un índice puede responder exactamente, se recorre el índice (búsqueda por punto o por rango) en lugar de barrer la tabla completa; el resultado se afina luego con el resto de la condición.
- **Grace Hash Join** para `JOIN` (particiona ambas tablas por la clave del `ON` y une las particiones de a pares, sin cargar nunca todo en memoria).
- **Ordenamiento externo (k-way merge)** para `ORDER BY`, que usa disco cuando el resultado no entra en memoria.
- **Agregación con hash externo** para `GROUP BY` y agregados globales.
- Todas estas técnicas limitan la memoria con un *budget* configurable, exactamente como hacen los motores serios.

### Lo que *no* está implementado (aún)

- `UPDATE`, `DROP TABLE` / `DROP INDEX` desde SQL (existen internamente en el motor).
- `ROLLBACK`/recovery de transacciones: `BEGIN`/`END TRANSACTION` solo se parsean.
- Joins de más de dos tablas, `HAVING`, subconsultas, `DISTINCT`.
- Ningún grado de concurrencia ni bloqueos (el motor es de un solo proceso).

---

## 2. Arquitectura

El código está dividido en tres grandes capas, imitando la separación clásica de un DBMS:

```
┌────────────────────────────────────────────────────────────┐
│                    parser/   (front-end)                   │
│  scanner → tokens ──► parser ──► Árbol (AST) ──► visitor   │
└───────────────────────────────┬────────────────────────────┘
                                │  executor.py (puente)
┌───────────────────────────────▼────────────────────────────┐
│                   storage/   (back-end)                    │
│  storage_manager ─ Table ─ files (heap/sequential)         │
│       ├─ schema_catalog  (sys_tables, sys_columns, ...)    │
│       ├─ buffer_manager  (buffer pool global, clock-sweep) │
│       ├─ file_manager    (lectura/escritura de páginas)    │
│       ├─ pages/          (FixedPage, VariablePage)         │
│       └─ formats/        (serializadores, tipos de datos)  │
└───────────────────────────────┬────────────────────────────┘
                                │
┌───────────────────────────────▼────────────────────────────┐
│                    indexes/   (índices + ext.)              │
│  B+ tree (BPlusTreeBase + subclases)                       │
│  extendible_hash  (HashIndex)                              │
│  external_sort / external_hash  (ORDER BY, JOIN, GROUP BY) │
└────────────────────────────────────────────────────────────┘
```

El flujo de una consulta es siempre el mismo:

1. **`Scanner`** convierte el texto SQL en una lista de *tokens* (palabras, números, cadenas, operadores), descartando espacios y comentarios.
2. **`Parser`** consume los tokens siguiendo la gramática y construye un **AST** (árbol sintáctico), con nodos tipados definidos en `ast_sql.py`. Detección de errores léxicos, sintácticos y semánticos con número de línea y columna.
3. **`ExecuteVisitor`** (un *visitor*, en `executor.py`) recorre el AST y traduce cada sentencia a llamadas concretas del `StorageManager`. El parser no conoce el motor y el motor no conoce el parser: el visitor es el único que habla los dos idiomas.
4. **`StorageManager`** orquesta el acceso a las tablas, que a su vez operan sobre un archivo físico (`HeapFile` o `SequentialFile`). Toda lectura/escritura de páginas pasa por el **buffer pool** global, y el **catálogo** guarda la metadata para poder reabrir la base más adelante.
5. Para `JOIN`, `GROUP BY` u `ORDER BY`, el ejecutor delega en los algoritmos externos de `indexes/`.

Conceptos clave que conviene tener en mente:

- **Página**: unidad de almacenamiento de tamaño fijo (4 KiB en tablas, 4 KiB en los B+, 8 KiB en el hash). Todo en disco vive dentro de páginas.
- **RID** (`page_id`, `slot_id`): la "dirección" de un registro dentro de un archivo. Los índices guardan RIDs para localizar las filas.
- **Buffer pool global**: un único arreglo de *frames* compartido por todo el motor. Una página se identifica por el par **(archivo, número de página)**, porque dos archivos distintos pueden tener su propia "página 0".
- **Catálogo**: tres tablas del sistema que describen las tablas del usuario (nombres, tipos, orden de columnas), la organización del archivo y los índices existentes.

---

## 3. Sintaxis aceptada por el parser

El parser acepta un **programa** como una secuencia de sentencias separadas por `;` (el último `;` es opcional):

```
programa ::= sentencia { ";" sentencia }* [ ";" ]
```

### Gramática informal de cada sentencia

```
CREATE TABLE id "(" ColDec { "," ColDec }* ")" USING (HEAP | SEQUENTIAL)
ColDec ::= id (INT | FLOAT | BOOL | DATE | VARCHAR "(" num ")") [PRIMARY KEY]

CREATE INDEX ON id "(" id ")" USING (BTREE | HASH) [CLUSTERED]

SELECT ("*" | SelectItem { "," SelectItem }*) FROM id
        [JOIN id ON ColRef = ColRef]
        [WHERE Cond]
        [GROUP BY ColRef]
        [ORDER BY ColRef (ASC | DESC)?]
        [LIMIT num]

SelectItem ::= ColRef | (COUNT | SUM | AVG | MIN | MAX) "(" (ColRef | "*") ")"

INSERT INTO id VALUES "(" Valor { "," Valor }* ")"

DELETE FROM id WHERE Cond

BEGIN TRANSACTION | END TRANSACTION

Cond  ::= And { OR And }*          (OR liga más flojo)
And   ::= Pred { AND Pred }*
Pred  ::= ColRef (op | BETWEEN Valor AND Valor) | "(" Cond ")"
op    ::= =  !=  <>  <  <=  >  >=
ColRef::= id | id "." id
Valor ::= num | 'texto' | TRUE | FALSE
```

Notas importantes sobre la sintaxis:

- **`DELETE` exige `WHERE`.** `DELETE FROM t;` sin condición es un error.
- **`CREATE TABLE` exige una `PRIMARY KEY`.** Si no se declara ninguna, el ejecutor la rechaza. La PK puede estar en cualquier columna, no solo la primera.
- **`CREATE INDEX` con `USING HASH CLUSTERED` es inválido** (un hash no preserva orden, no puede definir el orden físico). El parser lo rechaza con un error semántico.
- Las **agregaciones** (`SUM`, `AVG`, `MIN`, `MAX`) rechazan `*` como argumento; solo `COUNT` admite `COUNT(*)`. Una columna sin agregar en un `SELECT` agregado debe aparecer en el `GROUP BY`.
- Las condiciones pueden agruparse con paréntesis y se respeta la precedencia clásica `AND` > `OR`.
- Las columnas en `WHERE`, `ORDER BY`, etc. pueden ser calificadas (`ventas.id`) para desambiguar en joins; una columna sin calificar que exista en ambas tablas del join se rechaza como **ambigua**.

### Ejemplos de queries que funcionan

Creación de tablas (la `PRIMARY KEY` puede ir en cualquier columna, pero es una sola):

```sql
CREATE TABLE clientes (id INT PRIMARY KEY, nombre VARCHAR(20), activo BOOL, saldo FLOAT) USING HEAP;
-- Una tabla HEAP no guarda ningún orden; una SEQUENTIAL mantiene los
-- registros ordenados físicamente por la clave primaria:
CREATE TABLE diario (id INT PRIMARY KEY, monto FLOAT, fecha DATE) USING SEQUENTIAL;
```

Conjunto de datos de ejemplo:

```sql
CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;
INSERT INTO ventas VALUES (1, 'Ana', 100.0);
INSERT INTO ventas VALUES (2, 'Beto', 250.0);
INSERT INTO ventas VALUES (3, 'Carlos', 300.0);

CREATE TABLE dept (id INT PRIMARY KEY, depto VARCHAR(20)) USING HEAP;
INSERT INTO dept VALUES (1, 'IA');
INSERT INTO dept VALUES (2, 'DB');
```

Consultas básicas:

```sql
SELECT * FROM ventas;                                   -- todas las columnas y filas
SELECT cliente FROM ventas WHERE id = 2;                -- proyección con filtro
SELECT id FROM ventas WHERE monto > 200 AND id = 3 OR id = 1;  -- precedencia AND > OR
SELECT id FROM ventas WHERE id BETWEEN 1 AND 2;         -- rango cerrado
SELECT cliente FROM ventas WHERE monto >= 300;          -- operadores relacionales
SELECT cliente FROM ventas WHERE nombre <> 'Ana';       -- desigualdad con <>
SELECT cliente FROM ventas ORDER BY monto DESC LIMIT 2; -- orden + tope de filas
SELECT monto, count(*) FROM ventas GROUP BY monto;      -- agregación por grupo
SELECT count(*), sum(monto), avg(monto), min(monto), max(monto) FROM ventas;  -- agregación global
```

Joins:

```sql
-- Equi-join con calificación de columnas
SELECT ventas.cliente, dept.depto FROM ventas JOIN dept ON ventas.id = dept.id;
-- Join + filtro
SELECT ventas.cliente FROM ventas JOIN dept ON ventas.id = dept.id WHERE ventas.monto > 150.0;
-- Join + orden externo
SELECT ventas.cliente FROM ventas JOIN dept ON ventas.id = dept.id ORDER BY ventas.monto DESC;
```

Índices:

```sql
-- B+ no agrupado sobre un Heap (sirve con igualdad y rangos)
CREATE INDEX ON ventas (monto) USING BTREE;
-- Hash extensible sobre un Heap (sirve solo con igualdad)
CREATE INDEX ON ventas (cliente) USING HASH;
-- B+ agrupado: exige una tabla USING SEQUENTIAL e indexa la PRIMARY KEY
CREATE INDEX ON diario (id) USING BTREE CLUSTERED;
```

Borrado y "transacciones":

```sql
DELETE FROM ventas WHERE id = 3;
BEGIN TRANSACTION;
END TRANSACTION;
```

---

## 4. Recorrido por el código, archivo por archivo

Esta sección describe el rol de cada módulo y de cada función/método importante de todos los archivos `.py` del proyecto que **no** son de testing (las carpetas `tests/` y `run_all_tests.py` quedan fuera).

---

### 4.1 `parser/` — el front-end SQL

#### `token_sql.py`
Define la unidad mínima del lenguaje.

- **`Token.Type`** (enum): todos los tipos de token del lenguaje: palabras reservadas (`CREATE`, `TABLE`, `INDEX`, `SELECT`, `INSERT`, `DELETE`, `BEGIN`, `END`, `JOIN`, `WHERE`, `GROUP`, `ORDER`, `AND`, `OR`, `BETWEEN`, funciones de agregación, tipos de dato, organizaciones `HEAP`/`SEQUENTIAL`, tipos de índice `BTREE`/`HASH`...), operadores (`=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`), puntuación (`(`, `)`, `,`, `;`, `*`, `.`), y los tokens especiales `NUM`, `STR`, `ID`, `ERR`, `END`.
- **`Token.__init__`**: guarda el tipo, el texto del lexema y la posición (línea/columna) que luego estampa el Scanner.
- **`Token.__str__`/`__repr__`**: representación legible `TOKEN(TIPO, "texto")`, útil para depurar con la salida del Scanner.

#### `scanner.py`
Análisis léxico: texto SQL → flujo de tokens.

- **`is_white_space(c)`**: dice si un carácter es espacio, salto de línea, retorno o tabulación.
- **`a_mayusculas(s)`**: normaliza a mayúsculas. Como SQL no distingue mayúsculas en palabras reservadas, el lexema se compara siempre en mayúsculas.
- **`PALABRAS_RESERVADAS`** (dict): tabla que mapea cada palabra reservada (en mayúsculas) a su tipo de token.
- **`Scanner.__init__`**: guarda el texto de entrada y las posiciones `first`/`current` (inicio y cursor de lectura), más la fila y columna actuales.
- **`Scanner.avanzar()`**: consume un carácter. Es el único lugar donde se mueve el cursor, lo que mantiene línea y columna siempre sincronizadas.
- **`Scanner.saltar_espacios()`**: salta espacios en blanco y comentarios de línea `-- ...` hasta el `\n`.
- **`Scanner.next_token()`**: producen el siguiente token. Distingue números (`1`, `1.5` — el punto solo es decimal si le sigue un dígito, para no confundir con `tabla.columna`), identificadores y palabras reservadas, cadenas entre comillas simples (con `''` = comilla literal), operadores (`<` puede ser `<=` o `<>`, `!` debe ir seguido de `=`), puntuación, y caracteres inválidos (token `ERR`).
- **`Scanner.sigue(c)`**: verifica si el siguiente carácter es el esperado (para operadores de dos caracteres).
- **`ejecutar_scanner(scanner, input_file)`**: modo de prueba del scanner aislado: vuelca todos los tokens a un archivo `<nombre>_tokens.txt` e indica si el análisis fue exitoso o dónde falló.

#### `ast_sql.py`
Define los **nodos del AST** (el árbol que produce el parser). Cada nodo sabe "aceptar" un visitor.

- **Enums**: `FileOrg` (`HEAP_ORG`, `SEQUENTIAL_ORG`), `IndexKind` (`BTREE_IDX`, `HASH_IDX`), `DataType` (`INT`, `FLOAT`, `BOOL`, `DATE`, `VARCHAR`), `RelOp` (los 6 operadores relacionales), `AggFun` (`NONE_AGG` + COUNT/SUM/AVG/MIN/MAX), `SortDir` (`ASC_DIR`, `DESC_DIR`).
- **Valores literales**: jerarquía `Value` (abstracto) con `IntValue`, `FloatValue`, `StrValue`, `BoolValue`.
- **`ColRef`**: referencia a columna, opcionalmente calificada (`tabla.col` o solo `col`).
- **Condiciones `WHERE`**: jerarquía `Cond` con `OrCond` (lista de condiciones unidas por `OR`), `AndCond` (unidas por `AND`), `CompareCond` (`col op valor`) y `BetweenCond` (`col BETWEEN inf AND sup`). La jerarquía `OrCond → AndCond → predicado` codifica la precedencia.
- **`SelectItem`**: un elemento de la proyección (columna simple, o `COUNT/SUM/AVG/MIN/MAX` con su argumento, incl. `estrella` para `COUNT(*)`).
- **`JoinClause`**: una cláusula `JOIN tabla ON izquierda = derecha`.
- **`ColumnDec`**: declaración de columna en `CREATE TABLE` (nombre, tipo, longitud si es `VARCHAR`, si es `PRIMARY KEY`).
- **Sentencias** (jerarquía `Stmt`): `CreateTableStmt`, `CreateIndexStmt`, `SelectStmt`, `InsertStmt`, `DeleteStmt`, `TransactionStmt`.
- **`Programa`**: la raíz del árbol; contiene la lista de sentencias.

#### `parser.py`
Análisis sintáctico y semántico: tokens → AST.

- **`SqlError`**: excepción con tipo (`léxico`, `sintáctico`, `semántico`), mensaje y posición (línea, columna) para reportar errores precisos.
- **`Parser.__init__`**: guarda el scanner y precarga el primer token (si es `ERR`, llena el error léxico de entrada).
- **Métodos utilitarios**: `match`, `check`, `advance`, `is_at_end` implementan el patrón clásico de *recursive descent*; `error`, `error_semantico`, `error_lexico` lanzan `SqlError` con el mensaje y la posición adecuados.
- **Reglas gramaticales** (un método por producción, las principales):
  - `parse_program` / `parse_p`: secuencia de sentencias separadas por `;`.
  - `parse_stmt`: decide qué sentencia es (la primera palabra).
  - `parse_create`: distingue `CREATE TABLE` de `CREATE INDEX`.
  - `parse_create_table`: valida la forma `CREATE TABLE id (ColDec...) USING HEAP|SEQUENTIAL`.
  - `parse_column_dec`: parsea el tipo y el opcional `PRIMARY KEY`; para `VARCHAR` exige la longitud entre paréntesis.
  - `parse_create_index`: `CREATE INDEX ON t (col) USING BTREE|HASH [CLUSTERED]`, y **rechaza** `HASH CLUSTERED` (el hash no preserva orden).
  - `parse_select`: arma la proyección, `FROM` (con JOIN opcional), `WHERE`, `GROUP BY`, `ORDER BY ASC/DESC`, `LIMIT`.
  - `parse_select_item`: columna simple o función de agregación; solo `COUNT` admite `*`.
  - `parse_join`: `JOIN t ON ColRef = ColRef`.
  - `parse_insert`: `INSERT INTO t VALUES (v, ...)`.
  - `parse_delete`: `DELETE FROM t WHERE cond` (el `WHERE` es obligatorio).
  - `parse_transaction`: `BEGIN TRANSACTION` / `END TRANSACTION`.
  - `parse_cond` / `parse_and` / `parse_pred`: precedencia `OR` < `AND`; predicados con operadores relacionales, `BETWEEN ... AND ...`, o agrupación con paréntesis.
  - `parse_col_ref`: identificador, opcionalmente `tabla.columna`.
  - `parse_value`: número (entero o real), cadena o `TRUE`/`FALSE`.

#### `visitor.py`
El patrón **Visitor** sobre el AST.

- **`Visitor`** (clase abstracta): declara un método por cada tipo de nodo (`visit_int_value`, `visit_col_ref`, `visit_compare_cond`, `visit_select_stmt`, etc.). Cada nodo del AST llama a su método correspondiente desde `accept(self, visitor)`.
- **`PrintVisitor`**: visitor concreto que *reimprime* el AST como SQL. Su propósito es mostrar que el parseo capturó bien la estructura: reconstruye la sentencia original (normalizada a mayúsculas y espacios estandarizados) a partir de los nodos. Es la herramienta de verificación del parser.

#### `main.py`
Entry point de línea de comandos del parser aislado:

- **`main()`**: toma un archivo con SQL, corre el Scanner (vuelca los tokens), lo parsea y manda el AST al `PrintVisitor`. Devuelve código de error si no se puede abrir el archivo o si el parseo falla.
- Uso: `python parser/main.py archivo.sql`.

#### `executor.py`
El **puente** entre el parser y el motor (ver §2). 

- **`ExecutionError`**: excepción para errores de ejecución (tabla inexistente, columna inexistente, violaciones de consistencia, etc.).
- **`TIPOS`** y **`tipo_a_str()**`: convierten el `DataType` del parser (`INT`, `FLOAT`, `BOOL`, `DATE`, `VARCHAR(n)`) al string de tipo que entiende el motor (`integer`, `float`, `boolean`, `date`, `varchar(20)`).
- **`buscar_key_index(columnas)`**: devuelve la posición de la columna marcada `PRIMARY KEY` (o `-1` si no hay).
- **`Resultado`**: estructura de salida de una sentencia ejecutada: un mensaje (para DDL/DML) o columnas + filas (para `SELECT`), con `__str__` que la formatea como tabla.
- **`ExecuteVisitor`** (visitor que ejecuta el AST contra el `StorageManager`):
  - `ejecutar(programa)`: corre todas las sentencias y devuelve la lista de resultados.
  - `visit_create_table_stmt`: valida formato de tipos, exige `PRIMARY KEY`, y crea la tabla pidiendo la organización `heap`/`sequential`.
  - `visit_insert_stmt`: valida la cantidad de valores y delega en `Table.insert`.
  - `visit_select_stmt`: orquesta la consulta completa (ver abajo).
  - `visit_delete_stmt`: recorre las filas, evalúa la condición y borra las que coincidan.
  - `visit_create_index_stmt`: valida las reglas de uso del índice (HASH→HEAP, CLUSTERED→SEQUENTIAL + columna PK, B+ no agrupado→HEAP) y crea el índice correspondiente.
  - `visit_transaction_stmt`: responde `BEGIN/END TRANSACTION` (sin semántica de transacción).
  - **Ayudantes de consultas**: `_abrir` (abre una tabla), `_resolver_columnas` (traduce `ColRef` → posición dentro del registro combinado, rechazando columnas ambiguas), `_tiene_agregados`, `_nombre_item` (nombre de columna de salida, p. ej. `count(*)`), `_scan_filtrado` (barre la tabla o usa un plan de índice), `_plan_indice` (busca un predicado que un B+ pueda servir: punto `EQ` o `RANGO`, solo en contexto conjuntivo), `_candidatos_con_indice` (trae las filas que postula el índice), `_select_ordenado` y `_ordenar_externo` (ORDER BY con External Sorter), `_filas_join` (Grace Hash Join), `_proyeccion_agregada` (GROUP BY + agregados con hash externo), `_ordenar_resultado` (ORDER BY sobre resultado agregado ya materializado), `_valor` (extrae el valor Python de un literal), `_evaluar` (evalúa recursivamente una condición sobre un registro).
  - El resto de `visit_*` son no-operaciones, porque esos nodos son *piezas* (columnas, condiciones, valores) y no sentencias.

---

### 4.2 `storage/` — el back-end

#### `rid.py`
Define la identidad de los registros.

- **`RID`** (`namedtuple` con `page_id`, `slot_id`): la dirección física de un registro dentro de un archivo (página + número de slot). `RID(-1, -1)` es el **`NULL_RID`**.
- **`RID_FORMAT`/`RID_SIZE`**: formato `struct` (`ii`) y tamaño en bytes de un RID, usado por páginas y archivos para serializarlo.
- **`DELETED_FORMAT`/`DELETED_SIZE`**: formato y tamaño del flag booleano que marca un registro como eliminado.

#### `file_manager.py`
La capa más baja de E/S: leer y escribir páginas en un archivo binario.

- **`FileManager.__init__`**: abre (o crea) el archivo en modo binario; el archivo está dividido en un header fijo + páginas de `page_size` bytes.
- **`_calc_page_offset(phys_page_id)`**: calcula el offset en bytes de una página desde el inicio del archivo.
- **`read_page`**: lee una página completa como bytes. `write_page`: sobreescribe una página. `read_header` / `write_header`: leen/escriben el header del archivo (el header de los índices B+ guarda la ubicación de la raíz; el de sequential guarda contadores).
- **`allocate_page`**: agrega una página vacía (ceros) al final del archivo y devuelve su índice.
- **`flush`**: vacía el buffer del sistema a disco. **`truncate`**: recorta el archivo a un tamaño. **`close`**: cierra el archivo.

#### `buffer_manager.py`
El **buffer pool global** (singleton) con política de reemplazo **clock-sweep**.

- **`Frame`**: una celda del pool. Contiene el `FileManager` del archivo al que pertenece su página, el `phys_page_id`, los bytes de la página, el `pin_count` (cuántos componentes la están usando), `dirty` (hay cambios en RAM sin persistir) y `reference` (usada recientemente, para el clock-sweep).
- **`BufferManager`**:
  - `__new__`/`get_instance`: patrón **singleton** — una única instancia y un único pool para todo el proceso.
  - `_register_file` / `_resolve_file`: registran el archivo "activo" (default cuando el llamador no pasa `file_manager`) y resuelven cuál usar.
  - `_update_clock_hand`: avanza el puntero circular del reloj.
  - `_find_victim`: algoritmo **clock-sweep** para elegir qué frame desalojar: salta frames pínnados; a los usados recientemente les baja la referencia y les da una segunda vuelta; a las páginas sucias las persiste antes de reemplazarlas.
  - `fetch_page`: devuelve la página buscada (por `phys_page_id` + `file_manager`), cargándola de disco si no está, y la deja pínnada.
  - `unpin_page`: decrementa el contador de pines. `mark_dirty`: marca páginas como modificadas. `flush_page`: persiste una página; `flush_file`: persiste todas las sucias de un archivo; `flush_all`: persiste todas las del pool.
  - `close(file_manager=None)`: persiste y descarta del pool las páginas de un archivo concreto, o de todo el pool si se llama sin argumentos.
  - `invalidate_all(file_manager=None)`: descarta páginas cacheadas *sin* persistirlas, para cuando un archivo fue reescrito por fuera (como al reconstruir un índice).

#### `pages/seq_page.py`
- **`Page`**: clase base/interface común de las páginas: `n_records`, `has_space`, `get_record`, `set_record`, `insert`, `delete_slot`, `reset`, `ensure_initialized`. Sus subclases implementan el layout concreto.

#### `pages/fixed_page.py`
- **`FixedPage`**: página de **slots de tamaño fijo** (para esquemas sin `varchar`): el header guarda cuántos registros hay; cada registro ocupa exactamente `slot_size` bytes (datos + `next_rid` + flag `deleted`). Métodos: `n_records` (contador), `free_slots`, `has_space` (¿hay slot libre?), `_slot_offset` (posición del slot), `get_record`, `set_record` (sobreescribe usando `FixedLengthRecordSerializer.pack_slot`), `insert` (primer slot libre), `delete_slot` (borrado lógico), `reset`.

#### `pages/variable_page.py`
- **`VariablePage`**: página de **slots de tamaño variable**, usada por el Heap y por Sequential con `varchar`. La tabla de slots crece desde el inicio de la página y los datos desde el final; además mantiene una **free list** de slots muertos (`first_free_slot`) para reciclar espacio en O(1). Métodos y propiedades: `offset`, `size`/`n_records`, `first_free_slot`, `free_space_bytes` (espacio libre), `has_space`, `get_record`, `set_record`, `insert` (escribe el registro y recicla un slot de la free list si hay), `delete_slot` (borrado lógico, usado por Sequential), `delete_record` (borrado físico: engancha el slot a la free list, usado por Heap), `defragment` (compacta los datos activos al final de la página), `reset`, `ensure_initialized`.
- **`SLOT_FORMAT`/`SLOT_SIZE`**: cada entrada del directorio es un par `(offset, size)` de 8 bytes. `NULL_SLOT` = -1, centinela de free list vacía.

#### `formats/data_types.py`
- **`FIXED_DATA_TYPES`**: diccionario que mapea cada tipo (`int`, `integer`, `smallint`, `bigint`, `float`, `double precision`, `bool`, `date`, `timestamp`, `char`, `oid`, ...) a su formato `struct` y su tamaño en bytes. Muchos tipos son alias a propósito (p. ej. `int` = `int4` = `integer`).
- **`STRING_DATA_TYPE_STARTS` / `STRING_DATA_TYPES`**: los tipos de cadena de largo fijo acotado (`char`, `varchar`, `bit`) y los de largo libre (`text`, `json`, `xml`, ...).
- **`return_format(tipo)`**: traduce un tipo a `(formato_struct, tamaño)`. `-1` significa longitud variable; `varchar(n)` se traduce a `f"{n}s"` con tamaño `n`.

#### `formats/record_format.py`
- **`RecordFormat`**: interfaz abstracta `encode(values) → bytes` y `decode(bytes) → values` para empaquetar/desempaquetar registros.

#### `formats/record_packer.py`
- **`RecordPacker`**: empaqueta registros con una **cabecera de offsets**: antepone `num_fields` y un offset por campo, para que la decodificación sepa dónde termina cada columna. Expone la interfaz `RecordFormat` (`encode`/`decode`) y los alias `record_encoder`/`record_decoder`. Maneja campos de largo libre (con su largo), cadenas acotadas (con relleno `\x00`) y tipos fijos.

#### `formats/serializers/record_serializer.py`
- **`RecordSerializer`**: clase base de los serializadores "de páginas". Combina `RecordFormat` (con `encode`/`decode`) con las operaciones que las páginas necesitan: `get_size_of(params)`, `serialize(params)`, `deserialize(data)`.

#### `formats/serializers/fixed_length_serializer.py`
- **`FixedLengthRecordSerializer`**: serializa esquemas de largo fijo. Al construir, calcula `record_size`, arma el `format` único (`>campo1campo2...`) y el `slot_format` que incluye el `next_rid` + flag `deleted`; `slot_size` es el tamaño total de un slot. `_prepare`/`_prepare_field` convierten strings a bytes UTF-8; `get_size_of` siempre devuelve `record_size`; `serialize`/`deserialize` empaquetan/desempaquetan con `struct`; `pack_slot` arma la tupla completa de un slot.

#### `formats/serializers/variable_length_serializer.py`
- **`VariableLengthRecordSerializer`**: serializa esquemas con al menos un campo variable. Cada campo va concatenado: cadenas de largo libre van precedidas de un entero con su largo; cadenas acotadas van rellenas a tamaño fijo; números van empaquetados big-endian. `serialize`/`deserialize` y `get_size_of` (que devuelve el largo real en bytes).

#### `record_file.py`
- **`RecordFile`**: interfaz abstracta de los archivos de registros: `insert`, `fetch`, `delete`, `scan`, `reorganize`, `close`. La implementan `HeapFile` y `SequentialFile`.

#### `seq_record.py`
- **`Record`**: representación en memoria de un registro lógico para los archivos: `params` (los valores), `next_rid` (puntero de la lista enlazada en Sequential) y `deleted` (flag de borrado lógico).

#### `files/heap_file.py`
Archivo plano sin orden (organización `HEAP`). Página 0 = **directorio persistente de espacio libre** (guarda cuántas páginas de datos hay y el espacio libre de cada una), para no escanear el archivo al insertar. Cuando el directorio se llena, se encadenan más "páginas de directorio" (`next_dir_page_id`), así el heap no tiene techo de páginas.

- **Constantes de layout**: `DIR_HEADER_FORMAT` (`>II`: `page_count`, `next_dir_page_id`), `DIR_ENTRY_FORMAT` (espacio libre por página), `MAX_RECORD_SIZE` (límite de tamaño de registro por página).
- **`__init__`**: crea (página 0) o abre (recorre toda la cadena de directorios) el archivo; elige serializador fijo o variable según el esquema.
- **Directorio**: `_entry_location` (dónde vive la entrada de una página), `_get_free_space`, `_set_free_space`, `_update_page_count`, `_needs_new_dir_page`, `_add_dir_page` (encadena una página de directorio nueva), `_is_data_page` (distingue páginas de datos de directorios).
- **I/O de páginas**: `_load` (trae una página), `_sync_page` (persiste y actualiza el directorio), `next_page_id`, `_new_page` (crea una página de datos).
- **API**: `insert(values)` (busca en el directorio la primera página que entre el registro; si ninguna alcanza, crea una; devuelve el `RID`), `fetch(rid)` (lee un registro), `delete(rid)` (borrado físico vía `delete_record`, reciclable), `compact(page_id)` / `reorganize()` (desfragmentación puntual o de todo el archivo), `scan()` (genera `(RID, valores)` de todos los registros vivos), `close()`.

#### `files/sequential_file.py`
Archivo **ordenado por clave** (organización `SEQUENTIAL`). Los registros se mantienen enlazados por `next_rid` formando una lista ordenada; las páginas "principales" guardan el grueso de los datos y la **página 0 es una página de overflow** para registros que no entran en su lugar. Cuando el espacio desperdiciado supera el umbral (`WASTED_RATIO`), el archivo se reorganiza: ordena todo y lo reescribe compacto.

- **Header del archivo** (`>iiii`): `n_pages`, `first_rid`, `n_records`, `n_deleted`. Helper `_load_header`/`_write_header`, y conversiones `_rid_to_int`/`_int_to_rid` (RID empaquetado con 16 bits por campo).
- **Búsqueda por binaria + vecinos**: `_last_page_lt` (búsqueda binaria sobre las páginas para hallar dónde debería estar una clave), `_main_neighbors` y `_overflow_neighbors` (encuentran predecesor y sucesor de una clave, en las páginas principales y en overflow), `_find_neighbors` (combina ambos).
- **`insert(params)`**: halla los vecinos de la clave, inserta en overflow, encadena el `next_rid`; si el overflow está lleno, reorganiza y reintenta. El primer registro inicializa el archivo.
- **`fetch(rid)`**: lee un registro. **`search(key)`**: devuelve los registros con esa clave recorriendo la lista desde el vecino correcto.
- **`delete(rid)` / `delete_by_key(key)`**: borrado lógico (flag `deleted`), que gatilla `reorganize` cuando el espacio desperdiciado pasa el umbral.
- **`reorganize()`**: junta los registros vivos, los ordena por clave y los reescribe desde la página 1, encadenando los `next_rid`; trunca las páginas sobrantes. `scan()`: recorre la lista enlazada saltando borrados. `_truncate`: recorta el archivo.

#### `storage_manager.py`
El coordinador central del back-end.

- **`StorageManager.__init__`**: crea el catálogo en disco, el `IndexManager` y la caché en memoria de tablas abiertas.
- **`create_table`**: registra la tabla en el catálogo (o falla si ya existe) y la abre.
- **`open_table`**: si no está cacheada, lee su metadata del catálogo, crea su `FileManager` + `BufferManager`, construye la `Table` y **recarga sus índices** desde el catálogo.
- **`drop_table`**: elimina la metadata del catálogo y borra el archivo `.dat` (existe internamente; sin sentencia SQL asociada).
- **`close`**: cierra todas las tablas, el catálogo y los índices. Soporta el protocolo `with` (`__enter__`/`__exit__`).

#### `table.py`
La vista lógica de una tabla: encapsula el esquema, el archivo físico y los índices enlazados.

- **`Table.__init__`**: valida nombres/aridad, elige `HeapFile` (con `file_type="heap"`) o `SequentialFile` (con `file_type="sequential"`), inicializa el `ConstraintsManager`, y deja listos los contenedores de índices (`clustered_index`, `secondary_indexes`).
- **`column_index(nombre)`**: traduce el nombre de columna a su posición en el registro (fundamental para que el resto del motor trabaje con posiciones).
- **`attach_index`** / **`set_clustered_index`**: enlazan índices secundarios / el índice agrupado para su mantenimiento automático.
- **`insert(values)`**: valida restricciones, inserta a través del índice agrupado (si existe) o directo en el archivo, y actualiza todos los índices secundarios con el nuevo RID.
- **`get(rid)`**: recupera una fila. **`delete(rid)`**: borra la fila del archivo y limpia las entradas de los índices (borra la referencia específica para no tocar duplicados).
- **`search_by_key` / `delete_by_key`**: buscan/borran por clave primaria priorizando el índice agrupado, el archivo sequential, o un escaneo lineal como último recurso.
- **`scan()`**: genera `(RID, valores)` de todas las filas vivas. **`close()`**: cierra el archivo.

#### `schema_catalog.py`
El **catálogo del sistema**: metadata sobre las tablas, guardada en tablas físicas (`sys_tables`, `sys_columns`, `sys_indexes`), todas `HeapFile`.

- **Constantes**: nombres de las tablas del sistema, sus esquemas (`varchar(64)...`) y los nombres de sus columnas.
- **`__init__`**: crea/abre las tres tablas del sistema y hace "bootstrap" (se auto-registran) si el catálogo es nuevo.
- **`_clean_str`**: quita caracteres nulos y espacios de relleno de las cadenas leídas de disco.
- **`register_table`**: inserta la fila en `sys_tables` y una fila por columna en `sys_columns`.
- **`get_table_info(name)`**: reconstruye la metadata completa de una tabla (esquema y nombres ordenados según `column_order`) escaneando `sys_tables` y `sys_columns`.
- **`drop_table_info`** (dos definiciones, la segunda gana): elimina la metadata, protegiendo las tablas del sistema de ser borradas.
- **`register_index` / `get_table_indexes` / `drop_index_info`**: alta, consulta y baja de índices en `sys_indexes`.
- **`close`**: cierra los archivos del catálogo.

#### `index_manager.py`
Maneja el ciclo de vida de los índices.

- **`create_unclustered_index`**: construye un `BPlusTreeUnclustered` sobre el heap de la tabla, lo puebla con los datos existentes y lo registra en el catálogo.
- **`create_clustered_index`**: exige tabla `sequential`; construye un `BPlusTreeClustered` y lo sincroniza con los datos (`_reindex`), siempre sobre la `PRIMARY KEY`.
- **`create_hash_index`**: registra el índice en el catálogo y delega en `_build_hash_index`.
- **`_hash_key_config`**: traduce el tipo de la columna a la configuración del serializador del hash (`struct format`, variable o fijo).
- **`_build_hash_index`**: como el hash **no persiste páginas**, descarta la caché antigua, trunca el archivo `.idx`, construye el `HashIndex` desde cero y lo puebla con la tabla.
- **`load_indexes_for_table`**: al abrir una tabla, recarga del catálogo sus índices: B+ no agrupado, B+ agrupado y hash (que se reconstruye).
- **`drop_index`**: cierra el índice, lo quita del catálogo y borra su archivo `.idx`. **`close`**: cierra todos los índices abiertos.

#### `constraints_manager.py`
Enforcement de restricciones de integridad.

- **`IntegrityError`**: excepción para violaciones de integridad.
- **`ConstraintsManager.__init__`**: recibe la tabla, la posición de la PK y listas de índices `NOT NULL`, `UNIQUE` y funciones `CHECK`. `check_primary_key=False` desactiva la validación de PK (se usa en tablas del sistema con clave compuesta, que este manager no modela).
- **`validate_insert(values)`**: antes de aceptar una fila: valida aridad; para la PK exige no-NULL y unicidad (vía `search_by_key`); valida las columnas `NOT NULL`, las `UNIQUE` (escaneando la tabla) y las funciones `CHECK`.

---

### 4.3 `indexes/` — índices y algoritmos externos

#### `b_tree_key_codec.py`
Codificación de **claves** para el árbol B+.

- **`MAX_KEY_SIZE`**: tope de 512 bytes por clave codificada; garantiza que siempre entren varias entradas por página.
- **`encode_key(key)`**: convierte `int`, `float`, `bool` o `str` a bytes con un *tag* de tipo de 1 byte al inicio (para poder decodificarlo después). Lanza error si la clave excede `MAX_KEY_SIZE`.
- **`decode_key(data)`**: revierte `encode_key` devolviendo el valor original según el tag.

#### `b_tree_leaf_page.py`
La **hoja** del B+.

- **Constantes de layout**: `PAGE_SIZE=4096`, header `>IHIH` (`page_id`, `n_entries`, `next_leaf_id`, `free_space_high`), directorio por entrada `>HHii` (offset y largo de la clave + `ref.page_id` + `ref.slot_id`), `NULL_LEAF=0`, y el umbral de underflow (25% de la página).
- **`BTreeLeafPage.__init__`**: crea vacía o carga desde bytes. `save_header`/`load_header`; `_dir_offset`, `_read_dir`, `_read_entry` (lee una entrada y decodifica la clave).
- **Búsqueda**: `_find_index` (binaria: posición de la clave, o dónde insertarse), `find(key)` (devuelve el ref o `None`).
- **Estructura**: `_all_entries`, `used_bytes` (ocupación real), `has_space`, `_rewrite` (reescribe la página entera compactada — las mutaciones pasan por acá porque las claves son de tamaño variable: el directorio queda ordenado justo tras el header y los bytes de las claves quedan desde el final).
- **Mutaciones**: `insert(key, ref)` (si no hay espacio, `False` → el llamador hace `split`), `delete(key)`, `split(new_page_id)` (divide a la mitad, actualiza `next_leaf_id` y devuelve la clave separadora), `is_underflow`, `can_lend`, `borrow_from_left`/`borrow_from_right` (redistribución con hermano), `merge_with_right` (fusión).

#### `b_tree_internal_page.py`
El **nodo interno** (guía) del B+: solo claves de ruteo y punteros a hijos. Mismo esquema de largo variable que la hoja, pero los **hijos son fijos** (4 bytes) y van después del directorio de claves.

- Constantes: header `>IHH` (`page_id`, `n_keys`, `free_space_high`), `KEY_DIR_FORMAT` (offset/largo de cada clave), `CHILD_FORMAT` (`>I`) y el umbral de underflow del 25%.
- **`BTreeInternalPage`**: `save_header/load_header`, `_key_dir_offset`, `_children_base`, `_child_offset`, `_read_key`, `_read_child`, `_write_child`, `_all_keys`, `_all_children`, `used_bytes`, `has_space`, `_rewrite(keys, children)` (reescribe página completa: claves desde el final, directorio ordenado, hijos fijos en medio), `_write_key`.
- **Navegación**: `_find_key_index` (binaria), `find_child_index(key)` (índice del hijo correcto, con la invariante child[i] cubre `[key[i-1], key[i])`), `find_child(key)`.
- **Mutaciones**: `insert_key(key, right_child)`, `init_as_root` (raíz nueva con 1 clave y 2 hijos), `split` (devuelve la clave que sube), `is_underflow`, `can_lend`, `delete_key_at`, `borrow_from_left`/`borrow_from_right` (con la clave separadora del padre), `merge_with_right`.

#### `b_tree_base.py`
La lógica común de los B+ (búsqueda, inserción, borrado, rebalanceo), independiente del almacenamiento subyacente.

- **Header del archivo** (`>I?B`): `root_page_id`, `root_is_leaf`, `height` (la altura del árbol).
- **`BPlusTreeBase.__init__`**: crea un índice vacío o carga el header de la raíz.
- **Persistencia**: `_init_empty_index` (recrea de cero), `_load_root_header`/`_save_root_header`, `_load_leaf`/`_load_internal` (pin+unpin en el mismo método, sin dejar páginas "prestadas"), `_save_page`, `_allocate_page_id`.
- **Hooks abstractos** que implementan las subclases: `_store_record`, `_fetch_record`, `_delete_record`.
- **API pública**: `search(key)` (desciende hasta la hoja y hace `find`), `insert(key, params)` (persiste el dato y luego inserta el ref), `_insert_ref(key, ref)` (todo menos persistir el dato; guarda el camino descendido para el split), `_propagate_split` (empuja la clave mediana hacia arriba, creando una raíz nueva cuando la cascada llega a la raíz), `delete(key)` (borra la entrada y rebalancea si la hoja queda en underflow), `_rebalance_leaf` / `_rebalance_internal` (redistribuyen con hermanos o fusionan, subiendo de nivel si el padre quedó en underflow), `range_search(start, end)` (desciende una vez y luego sigue la cadena de hojas por `next_leaf_id`).

#### `b_plus_unclustered.py`
B+ **no agrupado**: las hojas guardan RIDs hacia un `HeapFile` compartido.

- **`BPlusTreeUnclustered.__init__`**: recibe el `HeapFile` del heap; mantiene un set `_keys_with_duplicates` para saber qué claves tienen varias filas.
- **Hooks**: `_store_record` (inserta en el heap), `_fetch_record` (trae la fila del heap), `_delete_record` (borra del heap por RID).
- **`_insert_ref`**: registra el ref y recuerda si la clave ya existía (para los duplicados).
- **`search(key)`**: recorredesde la hoja más a la izquierda y junta **todas** las referencias con esa clave (a diferencia del B+ base que devuelve una sola); por compatibilidad devuelve un RID único si nunca hubo duplicados.
- **`delete_ref(key, ref)`**: borra una referencia específica (para no eliminar otras filas duplicadas). `close()` cierra solo el archivo del índice, no el heap compartido.

#### `b_plus_clustered.py`
B+ **agrupado**: las hojas guardan RIDs hacia un `SequentialFile`, que mantiene los datos físicamente ordenados por la misma clave.

- **`_ReindexNeeded`**: excepción interna de control de flujo.
- **`BPlusTreeClustered.__init__`**: recibe el `SequentialFile` y registra su contador de reorganizaciones.
- **Hooks**: `_store_record` (inserta en sequential, que devuelve el RID), `_fetch_record` (lee del sequential), `_delete_record` (borra por clave).
- **`_check_reindex`**: si el `SequentialFile` se reorganizó solo durante una operación (lo que invalida todos los RIDs), aborta con `_ReindexNeeded`.
- **`insert`/`delete`**: envuelven al base; si `_ReindexNeeded` salta, reconstruyen el índice entero con `_reindex` (relee los registros vivos del archivo reorganizado y vuelve a indexarlos).

#### `extendible_hash.py`
Índice **hash extensible** no agrupado (clave → RID).

- **`murmurhash3_32(data, seed)`**: función hash MurmurHash3 de 32 bits. **`hash_key(key, key_format, seed)`**: hashea la clave (string UTF-8 o empaquetada según `key_format`).
- **`PAGE_SIZE=8192`**, `BUCKETS_PER_PAGE=8`, `MAX_KV_SIZE=800`, `MAX_DEPTH=20`.
- **Clases de páginas**: `DirectoryPage` (página del directorio: pares `(bucket_page_id, last_bucket_page_id)`), `MetadataPage` (indices de páginas de directorio).
- **`KV`** y **`KVSerializer`**: par clave/RID con flag `deleted`, y su serialización/deserialización (claves de largo variable, cadenas fijas o numéricas; siempre seguidos de `rid` + `deleted`).
- **`VariableBucketPage`**: la página *bucket*. Layout: header (`offset`, `size`, `local_depth`, `next_bucket_page`) + tabla de slots + datos desde el final. Métodos: `has_space_int`/`has_space_two_int`, `_slot_offset`, `get_kv_by_slot_id`, `set_by_slot_id_same_size`, `insert` (al final de la página), `delete_slot` (lógico), `delete_all_permanently` (borra todo conservando el header), `compact` (empaqueta los vivos).
- **`HashIndex`**:
  - Control del tamaño: `max_capacity` (`1 << depth`), `max_directory_page_capacity` (cuántas entradas caben en las páginas de directorio).
  - **`__init__`**: arma el metadata, las páginas de directorio y los buckets iniciales.
  - Directorio: `_locate_bucket`/`_locate_last_bucket`/`_set_bucket_page`/`_set_last_bucket_page` (mapeo índice→página de bucket, doble puntero para encadenar overflow).
  - Crecimiento: `_split_bucket` (re-hashea un bucket lleno en dos, creciendo su `local_depth`), `_add_overflow_bucket` (encadena un bucket de overflow cuando ya se llegó a `MAX_DEPTH`), `_double_in_size` (duplica el tamaño del directorio).
  - **`insert(key, rid)`**: localiza el bucket, compacta/splittea/dobla según haga falta y guarda el par. **`search(key)`**: recorre la cadena de buckets de la partición y devuelve los `KV` con esa clave no borrados. **`delete(key)`** / **`delete_ref(key, rid)`**: borran todas las coincidencias o una fila puntual.
  - **`_insert_ref`**: alias de mantenimiento desde `Table.insert`. **`close`**: cierra el archivo del índice.

#### `external_sort.py`
**Ordenamiento externo** (k-way merge) para el `ORDER BY`.

- **`encode_key`/`decode_key`** (a nivel de módulo): codifican claves a bytes **preservando el orden** — los enteros invierten el bit de signo y los flotantes se recodifican como comparables lexicográficamente (con su truco de complemento), de modo que el orden de los bytes coincide con el orden numérico. Esto es distinto del codec del B+ (que no necesita ese orden).
- **`_ReverseKey`**: wrapper que invierte la comparación, para que el min-heap del merge entregue primero la clave *mayor* (orden descendente).
- **`ExternalSorter`**: 
  - `__init__`: `budget` (items máximos en memoria por run), `reverse`, directorio de runs.
  - `_nuevo_run` / `_write_entry` / `_write_run`: genera un archivo temporal con un run ordenado. `_iter_run`: lee un run perezosamente.
  - `_heap_key` / `_merge`: **k-way merge** manteniendo un min-heap con la cabeza de cada run.
  - `sort(items)`: si todo entra en `budget`, ordena en RAM (camino rápido); si no, genera runs y los mergea. Devuelve un iterador de pares `(clave, value_bytes)` ya ordenados.
  - `cleanup` / `_drop_runs`: cierran streams y borran los archivos temporales (idempotente).

#### `external_hash.py`
**Hashing externo** para `JOIN` (Grace Hash Join) y `GROUP BY` (hash aggregate).

- Depende de `encode_key`/`decode_key` de `external_sort` para canonizar claves; `_hash_bytes` implementa **FNV-1a de 64 bits** (estable entre procesos, a diferencia del hash de Python).
- **`bucket_of(key, num_buckets)`**: la partición a la que pertenece una clave.
- **`Partitions`**: el resultado de particionar: los items viven en archivos de disco (uno por bucket). `bucket(b)` devuelve un iterador perezoso de una partición; `buckets()` itera todas; `cleanup()` borra los archivos.
- **`ExternalHasher`**:
  - `_write_entry` / `_nueva_particion` / `partition(items)`: una pasada que reparte cada `(clave, value_bytes)` a su partición `hash % num_buckets`.
  - `hash_join(left, right)`: particiona ambas entradas por la misma clave de join y procesa un par de particiones a la vez: **build** (tabla hash en memoria con la izquierda) + **probe** (sondea con cada item de la derecha). Rinde todas las tuplas `(clave, left_bytes, right_bytes)` que matchean, incluyendo duplicados.
  - `group_by(items, acumulador_nuevo, acumular)`: particiona por la clave de grupo y, por cada partición, acumula en un dict `clave → acumulador` en memoria. Rinde un `(clave, acumulador)` por grupo.
  - `cleanup`: borra los archivos de las particiones.

---

## 5. Cómo correr los tests

El runner descubre todos los `test_*.py` bajo `tests/` y los ejecuta como subprocesos aislados:

```bash
python run_all_tests.py        # corre todo
python run_all_tests.py -v     # muestra la salida de todos
python run_all_tests.py heap   # filtra por nombre
```