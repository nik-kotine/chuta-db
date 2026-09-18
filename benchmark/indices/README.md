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
| B+ clustered    |   500 |             693.4 |     1.387 |       16.0 |        24.0 |
| B+ unclustered  |   500 |             172.9 |     0.346 |       20.0 |        20.0 |
| Hash extensible |   500 |              38.9 |     0.078 |       20.0 |   **400.0** |
| B+ clustered    | 2,000 |           5,218.2 |     2.609 |       40.0 |        80.0 |
| B+ unclustered  | 2,000 |             813.3 |     0.407 |       56.0 |        68.0 |
| Hash extensible | 2,000 |             173.9 |     0.087 |       56.0 | **1,440.0** |
| B+ clustered    | 5,000 |      **23,749.7** |     4.750 |       88.0 |       200.0 |
| B+ unclustered  | 5,000 |           2,061.3 |     0.412 |      128.0 |       132.0 |
| Hash extensible | 5,000 |             520.8 |     0.104 |      128.0 | **3,680.0** |

![Tiempo de construcción](construccion_ms.png)
![Espacio adicional](espacio_indice_kb.png)

### Tiempo de consulta

| índice          |     N | µs/exacta |     µs/rango | ms/orden completo |
| --------------- | ----: | --------: | -----------: | ----------------: |
| B+ clustered    |   500 |      25.2 |         92.6 |              0.84 |
| B+ unclustered  |   500 |      90.3 |        113.8 |              0.75 |
| Hash extensible |   500 |      52.0 |  **1,455.4** |              1.55 |
| B+ clustered    | 2,000 |      18.6 |         97.4 |              3.01 |
| B+ unclustered  | 2,000 |     101.6 |        138.6 |              3.26 |
| Hash extensible | 2,000 |      41.1 |  **5,872.6** |              8.18 |
| B+ clustered    | 5,000 |      22.6 |        122.7 |              7.51 |
| B+ unclustered  | 5,000 |      99.1 |        188.7 |              7.78 |
| Hash extensible | 5,000 |      43.9 | **14,752.6** |             16.57 |

![Búsqueda exacta](busqueda_exacta_us.png)
![Búsqueda por rango](busqueda_rango_us.png)
![Ordenamiento](orden_completo_ms.png)

### Rendimiento con inserciones/eliminaciones frecuentes (churn)

| índice          |     N | churn ops |  ops/seg | µs/exacta post-churn |
| --------------- | ----: | --------: | -------: | -------------------: |
| B+ clustered    |   500 |       200 |  1,518.9 |                 13.9 |
| B+ unclustered  |   500 |       200 |  2,505.5 |                 86.6 |
| Hash extensible |   500 |       200 | 11,536.1 |                 43.7 |
| B+ clustered    | 2,000 |       400 |    343.1 |                 21.8 |
| B+ unclustered  | 2,000 |       400 |  2,679.2 |                 88.8 |
| Hash extensible | 2,000 |       400 |  8,104.5 |                 60.1 |
| B+ clustered    | 5,000 |     1,000 |    198.5 |                 32.7 |
| B+ unclustered  | 5,000 |     1,000 |  2,395.7 |                108.9 |
| Hash extensible | 5,000 |     1,000 |  7,709.1 |                112.5 |

![Throughput con churn](churn_ops_seg.png)
![Degradación post-churn](degradacion_post_churn_us.png)

## Tabla resumen: cuándo usar cada técnica

| Técnica             | Fuerte en                                                                                              | Débil en                                                                                                                                                                                        | Usar cuando...                                                                                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **B+ clustered**    | Rango y orden más baratos (los datos ya están físicamente ordenados); búsqueda exacta rápida y estable | Construcción e inserciones masivas — escala **~O(N²)** en esta implementación (ver limitaciones)                                                                                                | La tabla se lee mucho más de lo que se escribe, y las consultas son por rango/orden (ej. reportes, series de tiempo) sobre la clave primaria               |
| **B+ unclustered**  | Balance razonable en todo; no sufre el costo de reorganización del clustered; soporta rango y orden    | Búsqueda exacta más lenta que el hash a estos tamaños (indirección extra hacia el heap)                                                                                                         | Índices secundarios sobre columnas que no son la clave física, o cuando hay muchas inserciones/eliminaciones y no se puede pagar el costo del clustered    |
| **Hash extensible** | Construcción e inserción individual más rápidas de las tres; buen throughput bajo churn                | **No soporta rango ni orden** — sin eso cae a un scan completo del heap (de ~1.5ms a ~14.8ms según crece N); espacio del índice mucho mayor (pre-asigna buckets por capacidad, no por uso real) | Solo hay búsquedas por igualdad exacta (ej. lookup de un ID único) y nunca se necesita `WHERE col BETWEEN`, `ORDER BY` esa columna, ni recorrerla en orden |

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

## Limitaciones conocidas y mejoras planteadas

Se documentan acá a paran mantener este benchmark como el "antes" de referencia.

### 1. `BPlusTreeClustered` escala ~O(N²) en construcción/inserción masiva

**Causa:** `SequentialFile` usa una única página de overflow de **tamaño
fijo** (`storage/files/sequential_file.py`, página física 0). Cuando se
llena — cada ~250 inserts, sin importar cuán grande sea ya el archivo —
dispara `reorganize()`, que reescribe **todos** los registros vivos
(`O(N_actual)`). Como el intervalo entre reorganizaciones es constante pero
el costo de cada una crece con N, el total es `O(N²)`. Encima,
`BPlusTreeClustered._check_reindex()` reconstruye el árbol B+ completo
(`_reindex()`, también `O(N_actual)`) cada vez que esto pasa, porque
`reorganize()` invalida todos los RID que el árbol tenía guardados.

Medido directamente instrumentando la construcción (ver el hallazgo en el
historial de la rama): a N=5,500, van 22 reorganizaciones acumuladas y 35.7s
de construcción. Extrapolando, N=100,000 tardaría horas, por eso el
benchmark usa tamaños chicos (500/2,000/5,000) por defecto.

**Cómo se debería arreglar:** la causa raíz está en `SequentialFile`, no en
el índice — necesita una estrategia de reorganización que no dispare a
intervalo constante (como overflow con capacidad proporcional al tamaño
actual del archivo, o reorganización incremental en vez de "todo o nada").
Mientras eso no cambie, `BPlusTreeClustered` seguirá pagando un reindex
completo por cada reorganización.

### 2. `BPlusTreeUnclustered`: routing incorrecto con muchos duplicados de la misma clave

**Ya corregido en esta rama** (`search()` bajaba siempre por el hijo más
izquierdo y escaneaba el árbol entero — O(N) en vez de O(log N); ahora
desciende por la clave real, igual que `range_search`).

**Lo que queda pendiente:** si una misma clave dispara **más de un split**
(muchos duplicados exactos), el nodo interno termina con varios
separadores idénticos, y la regla de desempate "clave igual va a la
derecha" (necesaria para que `insert()` funcione) hace que `find_child()`
aterrice en la **última** hoja del grupo, no en la primera. Un
descenso-y-scan-hacia-adelante (lo que usan `search()` y `range_search()`)
se pierde las hojas anteriores del grupo. Confirmado con una prueba manual:
insertando 3,000 copias de una misma clave, `search()`/`range_search()`
sólo encontraban 97.

No afecta a este benchmark (claves únicas), pero sí a cualquier uso real de
este índice como secundario sobre una columna con muchos valores repetidos.

**Cómo se debería arreglar (2 alternativas):**

- **Clave compuesta** `(valor, RID)` en vez de `valor` sola, nunca hay
  separadores repetidos, y "todas las filas con valor=K" pasa a ser un
  `range_search` de `(K, mínimo)` a `(K, máximo)`. Es el enfoque estándar
  para índices secundarios no únicos.
- **Dos reglas de descenso separadas**: mantener la clave simple, pero
  agregar un descenso "más a la izquierda posible" específico para
  búsqueda/rango, sin tocar el descenso que usa `insert()`.

### 3. Hash extensible: sin soporte de rango/orden (por diseño, no es un bug)

Una hash table no mantiene ningún orden entre claves, es esperable, no un
defecto de la implementación. `HashIndexAdapter` (en el benchmark) resuelve
`range_search`/orden con un scan completo del `HeapFile` subyacente porque
es la única forma correcta de responderlos sin un índice ordenado. El costo
medido (~1.5ms → ~14.8ms de N=500 a N=5,000) es el precio real de intentar
usar un hash para algo que no es su caso de uso.
