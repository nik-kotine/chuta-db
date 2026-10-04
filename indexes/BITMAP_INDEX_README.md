# Bitmap index (`indexes/bitmap_index.py`)

Un índice **bitmap no agrupado**: para cada valor distinto de una columna guarda
una **máscara de bits** que dice en qué filas del heap está ese valor.

Es el índice clásico de columnas de **baja cardinalidad** (`pais`, `activo`,
`genero`, un `status` de 5 valores...). La idea es que si el filtro es
selectivo, la máscara permite saltear casi todo el heap: del archivo de datos
solo se leen las páginas que la máscara efectivamente señala.

```sql
CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), pais VARCHAR(20), monto FLOAT) USING HEAP;
CREATE INDEX ON ventas (pais) USING BITMAP;

SELECT id FROM ventas WHERE pais = 'AR';                    -- una mascara
SELECT id FROM ventas WHERE pais = 'AR' AND pais = 'MX';   -- interseccion (vacio)
SELECT id FROM ventas WHERE pais = 'AR' OR pais = 'MX';    -- union
SELECT id FROM ventas WHERE monto BETWEEN 100 AND 500;      -- rango
```

## Cómo funciona

El heap tiene páginas de 4 KiB que dan para unos 510 registros. El RID de una
fila (`page_id`, `slot_id`) se aplana a un entero:

```
pos = page_id * 512 + slot_id
```

Ese entero es **directamente el número de bit** de la máscara. Como el heap
crece solo hacia adelante, las máscaras son dispersas por naturaleza (para mil
filas hay que guardar tres páginas de 64 bytes, no un `bytearray` de un
millón), y por eso el índice guarda **solo los chunks de las páginas del heap
que la máscara toca**, en una cadena de páginas de *spill* dentro del propio
`.idx`.

### Anatomía del archivo `.idx`

```
cabecera del archivo   (FILE_HEADER_FORMAT = ">IIi")
    root_page_id      primera página de datos (la que abre la lista)
    n_data_pages      cuántas páginas de datos tiene el índice
    free_head         primera página libre de la lista de reciclado (-1 = ninguna)

página de datos        (PAGE_HEADER_FORMAT = ">iiii")
    offset            dónde termina lo escrito (los datos crecen desde el final)
    n_entries         cuántas claves hay en esta página
    next_page         siguiente página de datos de la lista, o -1
    (reservado)
    directorio        hasta MAX_ON_PAGE entradas de ENTRY_DIR_FORMAT:
                      (key_offset, key_len, spill_head)
    payloads          al final de la página, para atrás:
                      key_len | key | n_chunks | chunk_0 | chunk_1 | ...
```

Las páginas de datos forman una **lista enlazada**: `_append` busca de atrás
hacia adelante la primera con lugar y, si ninguna tiene, agrega una al final.
Cada **chunk** (`CHUNK_FORMAT` = número de página del heap + 64 bytes) es la
máscara de las 512 filas de esa página. De cada clave quedan `MAX_ON_PAGE`
bytes dentro de la página y el resto cuelga de una cadena de páginas de spill
(cada una con `PAGE_SIZE - 4` bytes útiles, encadenadas por el `siguiente`).

### Qué se guarda

Las claves se serializan con `encode_key` de `external_sort`, que invierte el
bit de signo de los enteros y canonicaliza los flotantes, de modo que **el
orden lexicográfico de los bytes coincide con el orden de los valores**. Eso es
lo que permite buscar por rango sin tener las claves en un tipo homogéneo.

Al abrir el archivo, `BitmapIndex` carga en RAM un **directorio**
(`self._directory`): un `dict` de `clave -> (page_id, slot_id)`. El directorio
es la única estructura en memoria; las máscaras viven en el archivo. Como el
directorio está cargado, `search` de una clave que no existe devuelve una
máscara vacía sin tocar ni un solo página del índice.

### Operaciones

| Operación | Qué hace |
| --- | --- |
| `insert_ref(clave, rid)` | pone el bit del RID en la máscara de la clave |
| `delete_ref(clave, rid)` | limpia ese bit; si la máscara queda vacía, borra la clave |
| `search(clave)` | devuelve la máscara exacta (o una vacía si no está) |
| `search_range(low, high)` | unión de las claves del rango, extremos abiertos con `None` |
| `stats()` | claves, páginas de datos, páginas leídas y tamaño del archivo |

`search_range` devuelve `None` cuando el rango abarca demasiados valores
distintos (`max_keys`): armar esa unión en RAM costaría más que barrer el heap,
así que el planificador prefiere otro índice.

## Combinación de máscaras

`Bitmap` implementa la álgebra, con un atajo importante: `intersect(None)`
devuelve la otra máscara, porque en un `AND` "`todo` ∧ X" es simplemente `X`.

```python
pais = "AR", genero = "F"          -> pais["AR"].intersect(genero["F"])
pais = "AR" or pais = "MX"        -> pais["AR"].union(pais["MX"])
```

El ejecutor (`parser/executor.py`) arma el plan así:

- `_plan_bitmap` recorre el `WHERE` recursivamente.
- `AND` → intersección. `OR` → unión, **solo si todas las ramas tienen bitmap**
  (si alguna no, el `OR` entero se cae al plan normal).
- Una hoja se resuelve con `search` (igualdad) o `search_range` (`<`, `<=`,
  `>`, `>=`, `BETWEEN`) sobre el `BitmapIndex` de esa columna.
- El plan es `(mascara, predicados_pendientes)`. Las máscaras se combinan
  enteras en RAM; después `_scan_con_bitmap` recorre los RIDs **en orden**
  (página, y dentro de la página slot), trae esas filas del heap y **vuelve a
  evaluar el `WHERE` original** sobre cada una.

Ese último paso es lo que hace correcto al índice aunque la máscara sea una
sobre-aproximación: `search_range` es inclusivo en los dos extremos, así que
`monto < 200` trae también las filas con `monto = 200` y el filtro final las
descarta.

`!=` no se resuelve con bitmap: la máscara de "no soy yo" sería el complemento
y no está guardada en ninguna clave.

## Por qué el plan es una buena idea (y cuándo no)

Con 1503 filas donde 1500 son `AR` y 3 son `MX`:

| Consulta | Plan | Filas | Páginas del heap leídas |
| --- | --- | --- | --- |
| `WHERE pais = 'MX'` | `BITMAP INDEX SCAN` | 3 | 1 |
| `WHERE pais = 'AR' AND pais = 'MX'` | `BITMAP INDEX SCAN` (intersección vacía) | 0 | 0 |
| `WHERE pais = 'AR'` | `BITMAP INDEX SCAN` | 1500 | 25 |
| `WHERE pais = 'MX' OR pais = 'AR'` | `BITMAP INDEX SCAN` | 1503 | 25 |
| `WHERE pais <> 'AR'` | `SEQUENTIAL SCAN` | 3 | 27 |

(27 páginas de heap en total; el índice entero —las dos claves, con su
máscara— pesa 4108 bytes: **una** página.)

El bitmap gana cuando el filtro es **selectivo**. Con un valor que aparece en
el 99 % de las filas, resolver la máscara y después leer sus 25 páginas no
aporta nada frente a un barrido secuencial. El índice no lo decide por
estadística —no las tiene— así que el planificador elige bitmap siempre que
puede, y el plan reporta `matches` y `heap_pages` para que se vea qué pasó.

## Persistencia y mantenimiento

- Al reabrir la base, `IndexManager.load_indexes_for_table` lee
  `index_type="bitmap"` del catálogo y reabre el `.idx`; no se reconstruye
  (a diferencia del hash, que reconstruye).
- El `.idx` tiene su propio nombre: `idx_tabla_columna_bitmap.idx`, distinto
  del `idx_tabla_columna.idx` del B+ y del hash. Así los tres pueden convivir
  sobre la misma columna.
- El CRUD lo mantiene solo: `Table.insert` y `Table.delete` llaman a
  `_insert_ref` / `delete_ref` a través del mismo mecanismo que usa el resto de
  los índices secundarios.

## Límites

- Solo sobre tablas `USING HEAP` (y `USING BITMAP CLUSTERED` es inválido: una
  máscara no define el orden físico).
- La igualdad y el `BETWEEN` funcionan en cualquier tipo de columna (las claves
  se comparan por su valor), pero `<`, `<=`, `>` y `>=` solo se resuelven con
  bitmap en columnas **numéricas**; en una columna de texto el planificador se
  abstiene y deja que el B+ o el hash resuelvan.
- Sin estadísticas: no hay umbral de selectividad que decida entre bitmap y
  barrido secuencial.
- La intersección entre dos columnas distintas de tablas distintas (un join)
  no usa bitmaps todavía.

## Tests

- `tests/indexes/test_bitmap_index.py`: álgebra de `Bitmap`, spilling,
  reciclado de páginas, persistencia, rangos, mantenimiento.
- `tests/table/test_executor_bitmap.py`: `CREATE INDEX ... USING BITMAP`,
  planificación (`BITMAP INDEX SCAN`), `AND`/`OR`, convivencia con B+ y hash,
  CRUD, persistencia entre sesiones y el ahorro de páginas del heap.
