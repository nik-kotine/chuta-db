# Benchmark de estructuras de indexación

Compara **B+ agrupado (clustered)**, **B+ no agrupado sobre heap
(unclustered)**, **B+ no agrupado sobre SequentialFile (unclustered seq)** y
**Hash extensible**, en construcción, consultas (exacta / rango / orden),
espacio adicional y comportamiento bajo inserciones/eliminaciones frecuentes.

La variante **unclustered (seq)** es un índice secundario B+ sobre una tabla
`SequentialFile`, indexando la **misma columna** por la que el archivo está
físicamente ordenado (la clave). Como los RID de un `SequentialFile` no son
estables (se reubican en cada reorganización), el índice se reconstruye de
forma perezosa cuando detecta que el archivo se reorganizó —el mismo esquema
que usa el clustered—. Está en la comparación para ver qué pasa cuando un
índice *no agrupado* apunta justo a la clave de ordenamiento del archivo.

> Este documento cubre el deliverable de **Estructuras de Indexación**.

**Cómo reproducirlo** (desde la raíz del repo):

```bash
python -m benchmark.indices.index_benchmark          # tamaños por defecto: 1000, 10000, 50000
python -m benchmark.indices.index_benchmark 500 2000  # tamaños custom (corrida rapida)
python -m benchmark.indices.plot_index_benchmark      # regenera las graficas desde el JSON
```

Vive fuera de `tests/` a propósito: es un benchmark de rendimiento, no una
prueba de corrección, así que `run_all_tests.py` no lo corre solo — hay que
invocarlo a mano con los comandos de arriba.

Cada número es el promedio de 3 corridas (`REPEATS = 3` en el script), como
pide el enunciado.

## Resultados

### Construcción y espacio adicional

| índice               |      N | construcción (ms) | ms/insert | datos (KB) | índice (KB) |
| -------------------- | -----: | -----------------: | --------: | ---------: | ----------: |
| B+ clustered         |  1,000 |            1,461.2 |     1.461 |       24.0 |        36.0 |
| B+ unclustered       |  1,000 |              400.2 |     0.400 |       32.0 |        36.0 |
| B+ unclustered (seq) |  1,000 |            1,456.7 |     1.457 |       24.0 |        36.0 |
| Hash extensible      |  1,000 |               86.1 |     0.086 |       32.0 |   **752.0** |
| B+ clustered         | 10,000 |           19,944.4 |     1.994 |      172.0 |       372.0 |
| B+ unclustered       | 10,000 |            4,611.1 |     0.461 |      252.0 |       284.0 |
| B+ unclustered (seq) | 10,000 |           20,586.8 |     2.059 |      172.0 |       372.0 |
| Hash extensible      | 10,000 |            1,463.8 |     0.146 |      252.0 | **7,456.0** |
| B+ clustered         | 50,000 |          100,052.3 |     2.001 |      840.0 |     1,552.0 |
| B+ unclustered       | 50,000 |           34,808.0 |     0.696 |    1,232.0 |     1,544.0 |
| B+ unclustered (seq) | 50,000 |          100,147.8 |     2.003 |      840.0 |     1,552.0 |
| Hash extensible      | 50,000 |           15,620.3 |     0.312 |    1,232.0 | **35,712.0** |

> Números post-fix del `SequentialFile` (ver sección "Antes/después" más
> abajo) — el clustered pasó de ~O(N²) a O(N) real en construcción/inserción.
> Tamaños subidos de 500/2,000/5,000 a 1,000/10,000/50,000 una vez arreglado
> el O(N²): a esta escala se nota mucho mejor la curva logarítmica del B+
> frente al O(1) del hash (ver "Tiempo de consulta" abajo).
>
> La variante **unclustered (seq)** paga esencialmente lo mismo que el
> clustered en construcción (~100 s a N=50,000): comparten el `SequentialFile`
> y cada reorganización del archivo le cuesta un reindexado completo del B+.
> Su archivo de datos y de índice coinciden con los del clustered porque ambos
> ordenan físicamente por la misma clave; el índice secundario no agrega
> estructura extra respecto del clustered en este caso.

![Tiempo de construcción](construccion_ms.png)
![Espacio adicional](espacio_indice_kb.png)

### Tiempo de consulta

| índice               |      N | µs/exacta |      µs/rango | ms/orden completo |
| -------------------- | -----: | --------: | ------------: | -----------------: |
| B+ clustered         |  1,000 |      22.3 |         112.4 |               1.62 |
| B+ unclustered       |  1,000 |     112.1 |          98.6 |               1.39 |
| B+ unclustered (seq) |  1,000 |      79.6 |         101.3 |               1.23 |
| Hash extensible      |  1,000 |      45.3 |   **3,266.1** |               3.62 |
| B+ clustered         | 10,000 |      28.9 |         297.9 |              19.58 |
| B+ unclustered       | 10,000 |     111.5 |         230.2 |              16.14 |
| B+ unclustered (seq) | 10,000 |      82.1 |         255.7 |              18.05 |
| Hash extensible      | 10,000 |      60.5 |  **33,613.8** |              38.47 |
| B+ clustered         | 50,000 |      31.3 |         829.7 |              95.58 |
| B+ unclustered       | 50,000 |     112.3 |         764.0 |             107.22 |
| B+ unclustered (seq) | 50,000 |      97.2 |         782.5 |              83.22 |
| Hash extensible      | 50,000 |     118.0 | **169,140.1** |             218.58 |

Punto clave: aunque el B+ crece con N (log N) y el hash en teoría se
mantiene O(1), a N=50,000 el hash (118.0µs) es de hecho el **más lento** de
los cuatro en búsqueda exacta, perdiendo contra el clustered (31.3µs) y
hasta contra la variante seq (97.2µs) — ver la explicación en "Tabla
resumen" y "Verificación de complejidad" más abajo (el hash tiene un costo
fijo de 5 páginas por operación; el B+ con este fanout no llega a
necesitar tantas ni siquiera a N=50,000).

Sobre la variante **unclustered (seq)**: en búsqueda exacta queda entre el
clustered y el unclustered-heap (~80-97µs). Paga la misma indirección que
cualquier índice no agrupado (índice → `fetch` del dato) más el chequeo del
contador de reorganización, pero le gana claramente al unclustered-heap
porque sus RID, al indexar la clave de ordenamiento del archivo, caen casi
en orden físico. En rango y orden se pega al clustered (lecturas casi en
orden físico): a N=50,000 incluso marca el mejor tiempo de orden completo
(83.2 ms) de los cuatro.

![Búsqueda exacta](busqueda_exacta_us.png)
![Búsqueda por rango](busqueda_rango_us.png)
![Ordenamiento](orden_completo_ms.png)

### Rendimiento con inserciones/eliminaciones frecuentes (churn)

| índice               |      N | churn ops |  ops/seg | µs/exacta post-churn |
| -------------------- | -----: | --------: | -------: | -------------------: |
| B+ clustered         |  1,000 |       200 |    395.5 |                 19.4 |
| B+ unclustered       |  1,000 |       200 |  2,448.4 |                 82.5 |
| B+ unclustered (seq) |  1,000 |       200 |    406.2 |                 72.0 |
| Hash extensible      |  1,000 |       200 |  6,882.8 |                 57.2 |
| B+ clustered         | 10,000 |     2,000 |    485.2 |                 41.8 |
| B+ unclustered       | 10,000 |     2,000 |  2,124.4 |                113.7 |
| B+ unclustered (seq) | 10,000 |     2,000 |    557.9 |                 82.3 |
| Hash extensible      | 10,000 |     2,000 |  5,314.0 |                 74.1 |
| B+ clustered         | 50,000 |     2,000 |    475.9 |                 39.5 |
| B+ unclustered       | 50,000 |     2,000 |  1,343.1 |                139.4 |
| B+ unclustered (seq) | 50,000 |     2,000 |    563.9 |                132.0 |
| Hash extensible      | 50,000 |     2,000 |  1,598.4 |                153.1 |

![Throughput con churn](churn_ops_seg.png)
![Degradación post-churn](degradacion_post_churn_us.png)

## Tabla resumen: cuándo usar cada técnica

| Técnica             | Fuerte en                                                                                              | Débil en                                                                                                                                                                                        | Usar cuando...                                                                                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **B+ clustered**    | Rango y orden más baratos (los datos ya están físicamente ordenados); búsqueda exacta la más rápida de las tres incluso contra el hash a estos N (ver "Tiempo de consulta"); construcción O(N) tras el fix de `SequentialFile` (ver "Antes/después") | Sigue siendo el más caro en construcción/churn de los tres (aunque ya no cuadrático) — cada insert reordena físicamente el archivo | La tabla se lee mucho más de lo que se escribe, y las consultas son por rango/orden (ej. reportes, series de tiempo) sobre la clave primaria               |
| **B+ unclustered**  | Balance razonable en todo; no sufre el costo de reorganización del clustered; soporta rango y orden    | Búsqueda exacta la más lenta a estos tamaños (indirección extra hacia el heap: primero el índice, después el `HeapFile`)                                                                                                         | Índices secundarios sobre columnas que no son la clave física, o cuando hay muchas inserciones/eliminaciones y no se puede pagar el costo del clustered    |
| **B+ unclustered (seq)** | Rango/orden casi tan baratos como el clustered (sus RID caen casi en orden físico al indexar la clave de ordenamiento del archivo); a N=50,000 marca el mejor tiempo de orden completo de los cuatro; búsqueda exacta mejor que el unclustered-heap | Comparte el costo de reorganización del clustered: construcción y churn tan caros como él (~100 s a N=50,000), porque cada reorganización del `SequentialFile` fuerza un reindexado completo del B+; sigue pagando una indirección de `fetch` que el clustered no tiene | Índice secundario sobre una tabla `SequentialFile` cuando la columna indexada coincide (o casi) con la clave de ordenamiento del archivo, y las consultas son por rango/orden más que exactas |
| **Hash extensible** | Construcción e inserción individual más rápidas de todas; buen throughput bajo churn                | **No soporta rango ni orden** — sin eso cae a un scan completo del heap (de ~3.3 ms a ~169 ms según crece N); espacio del índice mucho mayor (pre-asigna buckets por capacidad, no por uso real); búsqueda exacta pierde contra los tres B+ a N=50,000 (costo fijo de 5 páginas por operación, ver "Verificación de complejidad") | Solo hay búsquedas por igualdad exacta (ej. lookup de un ID único) y nunca se necesita `WHERE col BETWEEN`, `ORDER BY` esa columna, ni recorrerla en orden |

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

**Por qué el hash pierde en búsqueda exacta contra el B+ pese a ser O(1):**
las 5 páginas del hash son un costo *fijo*, no cero. El B+ con este fanout
(~8 hijos por nodo interno) necesita 1-3 páginas hasta N=100,000 — menos
que 5. El hash recién le ganaría al B+ cuando la altura del árbol supere
las 5 páginas, algo que con este fanout no pasa ni remotamente cerca de
los tamaños que se pueden probar en un benchmark razonable. Es un caso
real de "la constante importa más que el Big-O dentro del rango que
realmente se mide".

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
la curva cuadrática medida en los otros dos puntos, marcada como tal.

Con el índice clustered encima, esta es la comparación en los dos tamaños
donde sí se corrió el código viejo completo (antes de subir la escala del
benchmark oficial a 1,000/10,000/50,000):

| N | antes (medido) | después (medido) | mejora |
| --: | --: | --: | --: |
| 500 | 693.4 ms | 598.7 ms | 1.2x |
| 5,000 | 23,749.7 ms | 9,250.6 ms | 2.6x |
| 50,000 | ~40 min (extrapolado del O(N²) medido) | 94,118.9 ms (**1.6 min**, medido) | ~25x |

| | antes (N=5,000) | después (N=5,000) | mejora |
| --- | --: | --: | --: |
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
medido (~3.3 ms → ~169 ms de N=1,000 a N=50,000) es el precio real de
intentar usar un hash para algo que no es su caso de uso.
