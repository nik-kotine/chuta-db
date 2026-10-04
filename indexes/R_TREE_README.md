# Índice R-Tree

Implementación de un índice espacial R-Tree (Guttman 1984) para datos
geográficos 2D (lat/lon), con búsqueda por rango, k-NN, intersección con
polígonos, y métricas Euclidiana y Geodésica (Haversine).

> Comparación de rendimiento contra búsqueda secuencial (tiempos, espacio,
> y cuándo conviene cada técnica): ver `benchmark/spatial/README.md`.

## Contexto y requisitos

Este índice espacial soporta datos geográficos 2D (lat/lon) con:

- Búsqueda por rango (ej. todas las tiendas en un radio de 5 km)
- k-NN (k vecinos más cercanos)
- Intersección con polígonos
- Métricas Euclidiana y Geodésica (Haversine)

El algoritmo de referencia es el de Guttman (1984): nodos hoja
`(P, id_tupla)`, nodos internos `(R, puntero_hijo)` con `R` cubriendo el MBR
de todo el subárbol, `ChooseLeaf` + `QuadraticSplit` para insertar,
`RangeSearch` / `NearestNeighborSearch` con poda por `MINDIST`.

## Decisiones de diseño

**¿Por qué el formato de página es más simple que el del B+?** Un MBR 2D
es tamaño fijo (4 doubles), a diferencia de las keys del B+ (largo
variable). Eso permite un arreglo plano de entradas fijas, sin directorio
de offsets ni reescritura compactando bytes.

**MBR de los hijos inline en el nodo padre:** cada entrada de un nodo
interno guarda `(MBR_del_hijo, child_page_id)` — decidir a qué hijo
descender, o si hace falta descender, nunca requiere leer la página del
hijo primero. Es el formato `(R, puntero_hijo)` estándar del algoritmo de
Guttman, no una variante propia.

**Primary key:** el R-Tree no necesita un `key_format` configurable
(int32/64/str) como el B+/Hash — indexa por geometría, no por PK. La hoja
guarda `(punto, RID)`, con `RID = namedtuple("RID", ["page_id", "slot_id"])`
reusado tal cual de `b_tree_leaf_page.py`.

**Convención lat/lon:** `Point(x, y)` sigue la convención de PostGIS
(`ST_MakePoint(x, y)`, x=longitud, y=latitud) — **no** es `(lat, lon)`.

**Para indexar una tabla con este R-Tree hace falta una columna de tipo
punto/geometría** (igual que `GEOMETRY(Point, 4326)` en PostGIS) — no un
tipo escalar como `int`/`varchar`. Esa parte del catálogo/parser todavía no
existe (ver [Limitaciones](#limitaciones-conocidas-y-alcance-diferido)).

## Arquitectura de archivos

```
┌──────────────────────────────────────────────────────────────┐
│                   spatial/geometry.py                        │
│   Point, Rectangle, mindist, euclidean, haversine,            │
│   point_in_polygon                                             │
└─────────────────────────────┬────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────┐
│                 indexes/r_tree_node.py                        │
│   RTreeNode: pagina de entradas fijas + quadratic_split         │
└─────────────────────────────┬────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────┐
│                 indexes/r_tree_base.py                        │
│   RTreeBase: ChooseLeaf, AdjustTree, CondenseTree,              │
│   range_search / radius_search / knn / polygon_search            │
│                                                                  │
│   también usa:                                                  │
│     - indexes/b_tree_leaf_page.py  (RID, reusado tal cual)       │
│     - storage/file_manager.py                                   │
│     - storage/buffer_manager.py                                 │
└─────────────────────────────┬────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────┐
│                     indexes/r_tree.py                          │
│   RTree: conecta RTreeBase con un HeapFile                      │
│   (storage/files/heap_file.py)                                  │
└──────────────────────────────────────────────────────────────┘
```

## Formato de página (`r_tree_node.py`)

Una hoja indexa *puntos*, no geometrías con área. Guardar un MBR completo
(4 doubles, con `min==max` en ambos ejes) por cada punto desperdiciaría la
mitad de esos bytes. Por eso el tipo de entrada se separa por nivel
(`LeafEntry`/`InternalEntry`, en vez de un `Entry` genérico): la hoja guarda
solo `(x, y)`.

```
Header (7 bytes, >IH?):      page_id(4) | n_entries(2) | is_leaf(1)

Entrada de hoja (24 bytes):  punto (>dd, 16 bytes) | RID (>ii, 8 bytes)
Entrada interna (36 bytes):  MBR (>dddd, 32 bytes) | child_page_id (>I, 4 bytes)

PAGE_SIZE = 4096
LEAF_M     = (4096 - 7) // 24 = 170     LEAF_m     = 85
INTERNAL_M = (4096 - 7) // 36 = 113     INTERNAL_m = 56
```

Esto le da 170 entradas por hoja en vez de las 102 que daría un MBR
completo (+67% de fanout), y baja el tamaño del índice en disco ~35% (ver
[Benchmarks](#benchmarks-y-ablaciones)). El algoritmo de split
(`quadratic_split`) sigue operando sobre regiones (`Rectangle`) de forma
uniforme para ambos tipos de nodo: para una hoja, la región de una entrada
se calcula al vuelo como `Rectangle.from_point(entry.point)` solo durante
el split (operación poco frecuente), sin materializarla en la entrada
persistida.

Mutar la lista de entradas en memoria (`insert_entry`/`delete_at`/
`replace_entry`) **no** serializa a bytes de inmediato: mientras se decide
si hace falta split, la lista puede tener transitoriamente `M+1` entradas,
que no entrarían en el buffer fijo de `PAGE_SIZE` bytes. La serialización
(`node.flush()`) se hace recién en `RTreeBase._save_node()`, justo antes de
escribir a la página del buffer pool, cuando el split (si hizo falta) ya
redujo las entradas al tope real. Este fue un bug real encontrado durante
el desarrollo (`struct.error: pack_into requires a buffer of at least 4119
bytes... actual buffer size is 4096`), no una decisión de diseño previa.

## Inserción: ChooseLeaf + AdjustTree + QuadraticSplit

1. `insert`

**Input**: `point: Point`, `params` (lo que sea que espere `_store_record`)

**Output**: `ref` (RID hacia el registro real)

Persiste el registro real (`_store_record`) y llama a `_insert_entry_into_tree`.

2. `_choose_leaf` (ChooseLeaf)

**Input**: `target_mbr: Rectangle`

**Output**: `path` (lista de nodos, de la raíz a la hoja elegida)

Baja desde la raíz eligiendo en cada nivel el hijo con **menor
`enlargement`** respecto al MBR a insertar (empate: menor área). Devuelve
el camino completo recorrido, porque `AdjustTree` necesita subir por él
después.

3. `_insert_entry_into_tree` (AdjustTree)

**Input**: `point: Point`, `ref`

Inserta la entrada en la hoja elegida. Si la hoja queda llena
(`len(entries) > LEAF_M`), la divide con `quadratic_split`. Sube por el
camino recorrido en `ChooseLeaf`: en cada ancestro, actualiza el MBR de la
entrada que apunta al hijo, e inserta la entrada del nuevo hermano si hubo
split — si eso también llena al ancestro, se repite el split un nivel más
arriba. Si el split llega hasta la raíz, se crea una raíz nueva con los 2
hijos resultantes y el árbol crece un nivel (`height += 1`).

4. `quadratic_split` (QuadraticSplit, en `RTreeNode`)

**Input**: `new_page_id: int`

**Output**: nuevo `RTreeNode` (el hermano producto del split)

Algoritmo de Guttman: elige los dos "seeds" que desperdiciarían más área si
se agruparan juntos, y reparte el resto por mínima expansión de área,
forzando el mínimo `m` si a algún grupo le faltan entradas para
completarlo.

## Borrado: FindLeaf + CondenseTree

1. `delete`

**Input**: `point: Point`

**Output**: `bool` (si el borrado fue efectivo)

Ubica la hoja con `_find_leaf_path` (FindLeaf), borra la entrada, llama a
`_delete_record` sobre el dato real, y dispara `_condense_tree`.

2. `_find_leaf_path` (FindLeaf)

**Input**: `point: Point`

**Output**: `path | None`

DFS desde la raíz probando **todos** los hijos cuyo MBR contenga el punto
(pueden solaparse entre sí, a diferencia de un B-Tree) hasta encontrar la
hoja que lo tiene.

3. `_condense_tree` (CondenseTree)

**Input**: `path` (el camino devuelto por `FindLeaf`)

Sube por el camino desde la hoja hacia la raíz. En cada nivel: si el nodo
quedó en underflow (`len(entries) < m`), se saca **entero** del padre (no
hay borrow/merge con hermanos como en un B-Tree — los hermanos de un
R-Tree no tienen un orden que permita redistribuir de forma significativa)
y sus entradas se aplanan a nivel hoja (`_flatten_entries`, recursivo) para
reinsertarlas después; si no, solo se reajusta el MBR de la entrada en el
padre. Si la raíz queda con un solo hijo, se colapsa un nivel (el hijo pasa
a ser la nueva raíz). Al final, cada punto huérfano se reinserta desde cero
con `ChooseLeaf`.

> **Nota de diseño:** el Guttman original reinserta subárboles completos
> preservando su altura. Acá se simplificó: se aplana cualquier nodo
> eliminado hasta el nivel hoja (`_flatten_entries`, recursivo) y se
> reinserta punto por punto. Es más trabajo que el óptimo, pero bastante
> más simple de implementar correctamente, y el costo amortizado sigue
> siendo razonable porque el underflow no es el camino común. Se apartó
> deliberadamente de resolver el borrado "como en un B-Tree" (redistribuir
> con hermanos) porque eso no es correcto para un R-Tree: sus hermanos no
> tienen una relación de orden que permita redistribuir entradas de forma
> significativa.

## Búsqueda por rango y por radio

`range_search(rect)` implementa `RangeSearch` de la forma estándar
(poda por `intersects`, sin tocar `MINDIST`). `radius_search(point, radius,
metric)` añade la poda por `MINDIST(point, child.mbr, metric) <= radius`
antes de bajar a cada hijo, y verifica con la métrica exacta (euclidiana o
haversine) al llegar a la hoja — mismo patrón de dos fases que usa PostGIS
(`&&` para filtrar por bounding box, después la función exacta).

## k-NN: best-first search con cola de prioridad

`knn`

**Input**: `point: Point`, `k: int`, `metric: str`

**Output**: `[(Point, ref), ...]` (los k más cercanos)

En vez de ordenar los hijos de cada nodo por `MINDIST` en cada paso del
recorrido (como hace el pseudocódigo clásico de `NearestNeighborSearch`),
se usa una cola de prioridad (`heapq`) que evita ese sort repetido —
best-first search:

- El heap arranca con un solo elemento: la raíz, prioridad 0.
- En cada paso se saca (`heappop`) el elemento de menor prioridad.
  - Si es un **punto**, se agrega a los resultados.
  - Si es un **nodo interno**, se calcula `MINDIST(point, child.mbr)` para
    cada hijo y se empuja cada uno al heap con esa prioridad.
  - Si es una **hoja**, se calcula la distancia exacta a cada entrada y se
    empuja cada una al heap como candidato "punto".
- Se repite hasta juntar `k` puntos o vaciar el heap.

Cada candidato (nodo o punto) entra una sola vez al heap con su
`MINDIST`/distancia exacta, y `heapq` mantiene el mínimo en O(log n) por
operación en vez de un sort O(n log n) repetido en cada nivel.

## Intersección con polígonos

`polygon_search(polygon)`: primero un `range_search` con el MBR del
polígono (`Rectangle.from_points`) como pre-filtro barato, después
`point_in_polygon` (ray casting) exacto sobre cada candidato — mismo patrón
de dos fases que `ST_Intersects` en PostGIS.

## Tests

| Archivo | Qué valida |
|---|---|
| `tests/test_geometry.py` | `Rectangle` (area/union/enlargement/intersects/from_points), `MINDIST` (proyección por eje contra un MBR conocido), `haversine` (1° de longitud en el ecuador ≈ 111.2 km), `point_in_polygon` |
| `tests/indexes/test_r_tree_base.py` | Storage falso (mismo patrón que `FakeBPlusTree` de `test_b_tree_base.py`): insert/search, split forzado, `range_search`/`radius_search` (ambas métricas)/`knn`/`polygon_search` **comparados contra fuerza bruta**, delete simple, delete forzando CondenseTree + reinserción, persistencia (cerrar y reabrir) |
| `tests/indexes/test_r_tree.py` | Lo mismo contra un `HeapFile` real, de punta a punta |

Toda consulta compleja (rango, radio, knn, polígono) se valida comparando el
resultado del árbol contra un cálculo de fuerza bruta sobre los mismos
puntos, no contra valores fijados a mano — así cualquier bug de poda,
MINDIST o routing se detecta como una diferencia de conjuntos.

## Limitaciones conocidas y alcance diferido

Esta implementación cubre el índice en sí: ambas métricas de distancia y
los tres tipos de consulta (rango, k-NN, polígono). Quedan fuera, para una
entrega separada:

- Panel de mapa interactivo en el frontend (Leaflet/Google Maps).
- Extensión del parser SQL (`WHERE distancia(...) < X`,
  `ORDER BY distancia(...) LIMIT k`) y el tipo de columna punto/geometría en
  el catálogo de tablas.
- Comparación formal contra PostGIS/GiST — el benchmark de
  `benchmark/spatial/` compara el R-Tree solo contra búsqueda secuencial en
  Python, sin levantar un motor externo.
- Variantes R+/R* — no implementadas, solo el R-Tree clásico.
- `CondenseTree` aplana subárboles completos a nivel hoja en vez de
  reinsertar preservando altura (ver nota en la sección de borrado) —
  correcto, pero no es el Guttman óptimo en trabajo de reinserción.
- `MINDIST` con métrica `"haversine"` es una aproximación (proyección por
  eje asumiendo geometría plana, después medida con Haversine) — razonable
  para áreas no continentales, documentado en `spatial/geometry.py`.

## Cómo reproducir los tests

```bash
python run_all_tests.py geometry r_tree
python run_all_tests.py
```

Benchmark de rendimiento (R-Tree vs búsqueda secuencial): ver
`benchmark/spatial/README.md`.
