# Benchmark de estructuras de indexación

Compara **B+ agrupado (clustered)**, **B+ no agrupado (unclustered)** y **Hash
extensible**, en construcción, consultas (exacta / rango / orden), espacio
adicional y comportamiento bajo inserciones/eliminaciones frecuentes.

> Este documento cubre el deliverable de **Estructuras de Indexación**.

**Cómo reproducirlo** (desde la raíz del repo):

```bash
python -m benchmark.indices.index_benchmark          # tamaños por defecto: 500, 2000, 5000
python -m benchmark.indices.index_benchmark 1000 3000 # tamaños custom
python -m benchmark.indices.plot_index_benchmark      # regenera las graficas desde el JSON
```

Vive fuera de `tests/` a propósito: es un benchmark de rendimiento, no una
prueba de corrección, así que `run_all_tests.py` no lo corre solo — hay que
invocarlo a mano con los comandos de arriba.

Cada número es el promedio de 3 corridas (`REPEATS = 3` en el script), como
pide el enunciado.

## Resultados

### Construcción y espacio adicional

| índice          |     N | construcción (ms) | ms/insert | datos (KB) | índice (KB) |
| --------------- | ----: | ----------------: | --------: | ---------: | ----------: |
| B+ clustered    |   500 |             598.7 |     1.198 |       12.0 |        20.0 |
| B+ unclustered  |   500 |             175.4 |     0.351 |       20.0 |        20.0 |
| Hash extensible |   500 |              41.7 |     0.083 |       20.0 |   **400.0** |
| B+ clustered    | 2,000 |           3,083.4 |     1.542 |       40.0 |        68.0 |
| B+ unclustered  | 2,000 |             807.5 |     0.404 |       56.0 |        68.0 |
| Hash extensible | 2,000 |             196.3 |     0.098 |       56.0 | **1,440.0** |
| B+ clustered    | 5,000 |           9,250.6 |     1.850 |       88.0 |       184.0 |
| B+ unclustered  | 5,000 |           2,231.2 |     0.446 |      128.0 |       136.0 |
| Hash extensible | 5,000 |             571.2 |     0.114 |      128.0 | **3,680.0** |

> Números post-fix del `SequentialFile` (ver sección "Antes/después" más
> abajo) — el clustered pasó de ~O(N²) a O(N) real en construcción/inserción.

![Tiempo de construcción](construccion_ms.png)
![Espacio adicional](espacio_indice_kb.png)

### Tiempo de consulta

| índice          |     N | µs/exacta |     µs/rango | ms/orden completo |
| --------------- | ----: | --------: | -----------: | ----------------: |
| B+ clustered    |   500 |      15.8 |         82.9 |              0.61 |
| B+ unclustered  |   500 |     119.4 |        113.9 |              0.86 |
| Hash extensible |   500 |      41.7 |  **1,506.2** |              1.46 |
| B+ clustered    | 2,000 |      24.4 |        133.4 |              3.90 |
| B+ unclustered  | 2,000 |      73.2 |         96.1 |              2.70 |
| Hash extensible | 2,000 |      61.3 |  **6,011.0** |              6.65 |
| B+ clustered    | 5,000 |      26.4 |        172.8 |              9.31 |
| B+ unclustered  | 5,000 |     142.2 |        204.8 |              7.59 |
| Hash extensible | 5,000 |      57.3 | **17,040.7** |             18.68 |

![Búsqueda exacta](busqueda_exacta_us.png)
![Búsqueda por rango](busqueda_rango_us.png)
![Ordenamiento](orden_completo_ms.png)

### Rendimiento con inserciones/eliminaciones frecuentes (churn)

| índice          |     N | churn ops |  ops/seg | µs/exacta post-churn |
| --------------- | ----: | --------: | -------: | -------------------: |
| B+ clustered    |   500 |       200 |    609.5 |                 20.0 |
| B+ unclustered  |   500 |       200 |  2,461.8 |                 82.2 |
| Hash extensible |   500 |       200 | 12,678.3 |                 42.0 |
| B+ clustered    | 2,000 |       400 |    352.9 |                 25.3 |
| B+ unclustered  | 2,000 |       400 |  2,781.7 |                101.3 |
| Hash extensible | 2,000 |       400 |  7,503.6 |                 52.3 |
| B+ clustered    | 5,000 |     1,000 |    750.9 |                 25.9 |
| B+ unclustered  | 5,000 |     1,000 |  2,318.4 |                113.9 |
| Hash extensible | 5,000 |     1,000 |  6,443.5 |                 67.1 |

![Throughput con churn](churn_ops_seg.png)
![Degradación post-churn](degradacion_post_churn_us.png)

## Tabla resumen: cuándo usar cada técnica

| Técnica             | Fuerte en                                                                                              | Débil en                                                                                                                                                                                        | Usar cuando...                                                                                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **B+ clustered**    | Rango y orden más baratos (los datos ya están físicamente ordenados); búsqueda exacta rápida y estable; construcción O(N) tras el fix de `SequentialFile` (ver "Antes/después") | Sigue siendo el más caro en construcción/churn de los tres (aunque ya no cuadrático) — cada insert reordena físicamente el archivo | La tabla se lee mucho más de lo que se escribe, y las consultas son por rango/orden (ej. reportes, series de tiempo) sobre la clave primaria               |
| **B+ unclustered**  | Balance razonable en todo; no sufre el costo de reorganización del clustered; soporta rango y orden    | Búsqueda exacta más lenta que el hash a estos tamaños (indirección extra hacia el heap)                                                                                                         | Índices secundarios sobre columnas que no son la clave física, o cuando hay muchas inserciones/eliminaciones y no se puede pagar el costo del clustered    |
| **Hash extensible** | Construcción e inserción individual más rápidas de las tres; buen throughput bajo churn                | **No soporta rango ni orden** — sin eso cae a un scan completo del heap (de ~1.5ms a ~17.0ms según crece N); espacio del índice mucho mayor (pre-asigna buckets por capacidad, no por uso real) | Solo hay búsquedas por igualdad exacta (ej. lookup de un ID único) y nunca se necesita `WHERE col BETWEEN`, `ORDER BY` esa columna, ni recorrerla en orden |

## Verificación de complejidad (páginas leídas por operación)

Además de los tiempos de arriba (que tienen ruido de disco/SO), hay tests
dedicados que cuentan **páginas físicas leídas por operación** — una medida
de complejidad que no depende de qué tan rápida esté la máquina:

- `tests/indexes/test_b_tree_complexity.py` — B+ (clustered/unclustered
  comparten la misma base): con N de 100 a 100,000 (1000x), páginas/search
  pasa de 1.00 a 3.00 — crecimiento logarítmico, como corresponde a un B+.
- `tests/indexes/test_hash_complexity.py` — Hash extensible: con el mismo
  rango de N, páginas/search se mantiene **fijo en 5** y páginas/delete en
  **3**, sin importar N. Confirma el O(1) esperado de un hash — el precio es
  el espacio (ver limitación #3 abajo): ~750-820 bytes/registro en disco,
  constante también, pero mucho más alto que los B+ porque pre-asigna
  buckets completos de 8KB por capacidad, no por uso real.

Ambos corren solos con `run_all_tests.py` (viven en `tests/`, a diferencia
del benchmark de arriba).

## Antes/después: el fix de `SequentialFile` (rama `seqfile-reorganize-fix`)

El punto #1 de limitaciones más abajo (el O(N²) del clustered) **ya se
arregló**, en una rama aparte para mantener este benchmark como referencia
del "antes". Medido con `SequentialFile` solo (sin el árbol B+ encima),
`ms/insert` promedio:

| N | antes (medido) | después (medido) |
| --: | --: | --: |
| 1,000 | 2.75 ms | 0.35 ms |
| 5,500 | 6.49 ms | 0.41 ms |
| 100,000 | horas (extrapolado del O(N²) medido) | 0.50 ms — **50 s totales** |

"Antes" a N=1,000/5,500 sube con N (2.75→6.49ms, más del doble); "después"
se mantiene prácticamente plano en el mismo rango (0.35→0.41ms) y sigue
plano hasta N=100,000 — O(N) real, no O(N²). El punto de 100,000 para
"antes" no se corrió literalmente (tomaría horas); es la extrapolación de
la curva cuadrática medida en los otros dos puntos, marcada como tal. Con
el índice clustered encima (que es lo que muestran las tablas de este
documento), construcción a N=5,000:

| | antes | después | mejora |
| --- | --: | --: | --: |
| tiempo de construcción | 23,749.7 ms | 9,250.6 ms | 2.6x |
| throughput bajo churn (ops/seg) | 143.5 | 750.9 | 5.2x |

**Qué cambió, en dos partes:**

1. **`SequentialFile`** dejó de reorganizar cuando se llena una única página
   de overflow de tamaño fijo (disparo a intervalo constante → O(N²)) y pasó
   a reorganizar cuando el overflow supera un **% del archivo**
   (`OVERFLOW_RATIO = 0.3`, con un piso de 20 registros para no reorganizar
   archivos chiquitos) — mismo principio que ya usaba `WASTED_RATIO` para
   los deletes, ahora aplicado también a los inserts. Los reorganizes se
   espacian geométricamente en vez de a intervalo fijo (amortizado O(N)).
2. Eso solo no alcanzaba: dejar crecer el overflow hacía que
   `_find_neighbors` (llamado en cada insert) escaneara una cadena de
   overflow cada vez más grande, reintroduciendo el costo cuadrático por
   otra vía. Se reemplazó ese escaneo completo por un **recorrido local**
   de la cadena `next_rid` entre los dos vecinos de página principal más
   cercanos (ya ubicados con binary search) — solo atraviesa los registros
   de overflow que quedaron enganchados en ese tramo puntual, no el
   overflow completo.

`tests/files/test_sequential_file.py` (49 tests) sigue pasando completo.

## Antes/después: routing de duplicados en el B+ (rama `storage-fixes`)

El punto #2 de limitaciones más abajo **también se arregló**, en dos capas:

1. **Routing en `search()`/`range_search()`/`delete_ref()`:** se agregó
   `find_leftmost_child()` en `BTreeInternalPage` — una variante de
   `find_child()` que desempata a la **izquierda** en vez de a la derecha,
   para aterrizar en la *primera* hoja de un grupo de duplicados en vez de
   la última. `find_child()` no se tocó (`insert()` sigue necesitando
   desempatar a la derecha para ser consistente con `split()`).
2. **El bug real de fondo, más profundo de lo documentado originalmente:**
   al confirmar el fix de arriba con 3,000 duplicados, seguía fallando —
   `_find_key_index` (la que decide dónde insertar un separador nuevo
   cuando un split empuja una clave hacia el padre) desempataba al
   **revés** que `find_child_index`. Con muchos splits seguidos de la
   misma clave, cada separador nuevo se insertaba *antes* de los
   existentes en vez de después, dejando la lista de `children` del nodo
   padre en un orden corrupto (confirmado imprimiéndola:
   `[0, 31, 30, 29, ..., 3, 1, 32]` en vez de creciente). Ningún fix de
   routing podía arreglar la búsqueda mientras esto siguiera roto. Se
   corrigió `_find_key_index` para que desempate igual que
   `find_child_index`.

Verificado con 3,000 duplicados de una misma clave: `search()`,
`range_search()` y `delete_ref()` encuentran los 3,000 (antes: 97, luego
194, con cada capa del fix), y el recorrido global queda ordenado.
`_find_key_index` vive en `BTreeInternalPage`, compartida por clustered y
unclustered, así que el fix beneficia a ambos (sin cambiar nada para el
caso sin duplicados masivos, que es el que ya cubrían los tests).

## Limitaciones conocidas y mejoras planteadas

### 1. ~~`BPlusTreeClustered` escala ~O(N²) en construcción/inserción masiva~~ — RESUELTO

Ver la sección "Antes/después: el fix de `SequentialFile`" arriba.

### 2. ~~`BPlusTreeUnclustered`: routing incorrecto con muchos duplicados de la misma clave~~ — RESUELTO

Ver la sección "Antes/después: routing de duplicados en el B+" arriba.

### 3. Hash extensible: sin soporte de rango/orden (por diseño, no es un bug)

Una hash table no mantiene ningún orden entre claves, es esperable, no un
defecto de la implementación. `HashIndexAdapter` (en el benchmark) resuelve
`range_search`/orden con un scan completo del `HeapFile` subyacente porque
es la única forma correcta de responderlos sin un índice ordenado. El costo
medido (~1.5ms → ~17.0ms de N=500 a N=5,000) es el precio real de intentar
usar un hash para algo que no es su caso de uso.
