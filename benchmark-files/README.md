# Benchmark de organizaciones de archivo

Compara **Heap File** y **Sequential File** en construcción, espacio, consultas
(clave / rango / recorrido en orden), eliminación y reorganización.

> Este documento cubre el deliverable de **Organización de Archivos**.

**Cómo reproducirlo** (desde la raíz del repo):

```bash
python -m benchmark.files.file_benchmark            # tamaños por defecto: 1000, 5000, 20000
python -m benchmark.files.file_benchmark 500 2000   # tamaños custom (corrida rápida)
python -m benchmark.files.plot_file_benchmark       # regenera las gráficas desde el JSON
```

Vive fuera de `tests/` a propósito: es un benchmark de rendimiento, no una
prueba de corrección, así que `run_all_tests.py` no lo corre solo.

Cada número es el promedio de 3 corridas (`REPEATS = 3`). La corrida completa
tarda ~40 segundos. El esquema es `["integer", "integer"]` y las claves se
insertan en orden aleatorio.

## Resultados

### Construcción y espacio

| organización    |      N | construcción (ms) | µs/insert | archivo (KB) | bytes/registro |
| --------------- | -----: | ----------------: | --------: | -----------: | -------------: |
| Heap File       |  1,000 |               8.2 |       8.2 |         32.0 |           32.8 |
| Sequential File |  1,000 |             203.6 |     203.6 |         24.0 |           24.6 |
| Heap File       |  5,000 |              79.7 |      15.9 |        128.0 |           26.2 |
| Sequential File |  5,000 |           1,226.2 |     245.2 |         88.0 |           18.0 |
| Heap File       | 20,000 |             862.9 |      43.1 |        496.0 |           25.4 |
| Sequential File | 20,000 |           5,115.4 |     255.8 |        340.0 |           17.4 |

El heap inserta entre **6× y 25× más rápido**: escribe donde haya hueco y no
mantiene ningún orden. El secuencial paga ~250 µs por registro para ubicarlo en
su posición y absorber la cadena de overflow.

A cambio, el secuencial ocupa **menos espacio** (17.4 vs 25.4 bytes por
registro con N=20,000): sus páginas quedan mejor llenas porque el `reorganize`
las compacta, mientras que el heap deja huecos de fragmentación.

### Tiempo de consulta

| organización    |      N | µs/clave | µs/rango 1% | ms/orden completo |
| --------------- | -----: | -------: | ----------: | ----------------: |
| Heap File       |  1,000 |  1,830.2 |     1,913.9 |              2.09 |
| Sequential File |  1,000 |    200.1 |       241.5 |              3.09 |
| Heap File       |  5,000 |  9,233.5 |     9,102.5 |             10.66 |
| Sequential File |  5,000 |    207.5 |       392.7 |             13.98 |
| Heap File       | 20,000 | 37,898.6 |    37,676.1 |             48.48 |
| Sequential File | 20,000 |    256.6 |       768.4 |             60.35 |

Acá se invierte la ventaja. La búsqueda por clave del heap **escala con N**
(1.8 ms → 37.9 ms, ×21 al multiplicar N por 20) porque no hay más opción que
recorrer el archivo entero. La del secuencial se mantiene **plana** (200 → 257
µs): ubica la clave sin leer todo. Con N=20,000 la diferencia es de **148×**.

El rango se comporta igual, con un matiz: el del secuencial sí crece (241 → 768
µs), pero porque el ancho pedido es el 1% de N, así que devuelve más registros.
El costo por registro devuelto se mantiene.

El **recorrido completo en orden** es el único caso donde el heap gana, y por
poco (48 vs 60 ms). El heap lee todo y ordena en memoria; el secuencial ya lo
tiene ordenado pero paga el recorrido de su lista enlazada, que va saltando
entre páginas.

### Eliminación y reorganización

| organización    |      N | µs/delete | KB tras borrar 30% | reorganize (ms) | KB tras reorganize |
| --------------- | -----: | --------: | -----------------: | --------------: | -----------------: |
| Heap File       |  1,000 |     911.2 |               32.0 |             1.0 |               32.0 |
| Sequential File |  1,000 |     212.3 |               16.0 |             7.9 |               16.0 |
| Heap File       |  5,000 |   5,162.1 |              128.0 |             4.7 |              128.0 |
| Sequential File |  5,000 |     250.0 |               64.0 |            37.1 |               64.0 |
| Heap File       | 20,000 |  19,202.5 |              496.0 |            20.7 |              496.0 |
| Sequential File | 20,000 |     226.0 |              240.0 |           160.9 |              240.0 |

El `µs/delete` mide una eliminación **por clave**, que es lo que hace
`DELETE FROM t WHERE id = k`. El heap tiene que encontrar el registro primero,
así que hereda el costo de su búsqueda; el secuencial no.

Las dos columnas de KB muestran que **cada organización recupera el espacio de
forma distinta**:

- El **secuencial se achica** (340 → 240 KB): cuando el espacio muerto pasa el
  30%, `reorganize` reescribe el archivo compactado. De hecho salta solo
  durante los borrados, por eso las dos columnas ya coinciden.
- El **heap no se achica** (496 → 496 KB): marca los slots como libres y los
  recicla en los inserts siguientes, pero nunca devuelve páginas al archivo.

Ninguno de los dos está mal; son estrategias distintas. Lo que sí cuesta es
reorganizar el secuencial: 161 ms con N=20,000, ~8× lo que tarda el heap.

### Reutilización del espacio y posición de la clave

| organización    |      N | µs/insert intercalado | µs/insert al final | creció (KB) |
| --------------- | -----: | --------------------: | -----------------: | ----------: |
| Heap File       |  1,000 |                   8.4 |               11.1 |         0.0 |
| Sequential File |  1,000 |                 211.7 |              488.9 |         4.0 |
| Heap File       |  5,000 |                   8.8 |               12.3 |         0.0 |
| Sequential File |  5,000 |                 216.8 |              657.7 |         4.0 |
| Heap File       | 20,000 |                   8.7 |               12.2 |         0.0 |
| Sequential File | 20,000 |                 244.3 |              545.8 |         4.0 |

Reinsertando 300 claves previamente borradas, el archivo del heap **no crece**:
los slots liberados se reutilizan.

La última columna es la más interesante para el secuencial. Insertar una clave
**mayor que todas las existentes** (el patrón típico de un `id` autoincremental)
cuesta entre **2× y 3× más** que insertarla intercalada. La razón es que después
de compactar, las páginas principales están llenas, así que toda clave nueva al
final va a la cadena de overflow, y el costo de cada insert sube con la longitud
de esa cadena hasta que el `reorganize` automático la vacía. Medido en bloques
con N=10,000:

| bloque de 555 inserts | µs/insert | registros en overflow |
| --------------------: | --------: | --------------------: |
|                     1 |     1,005 |                   555 |
|                     2 |     2,397 |                 1,110 |
|                     3 |     3,815 |                 1,665 |
|                     4 |     5,343 |                 2,220 |
|                     5 |     6,564 |                 2,775 |
|                     6 |     2,050 |    472 (reorganizó) ↺ |

No es un problema de escala con N — los inserts sueltos se mantienen planos
(~500 µs para cualquier N). Es el costo de acumular overflow entre dos
reorganizaciones. Si el proyecto llega a usar tablas con `id` autoincremental
sobre archivos secuenciales, conviene tenerlo presente.

## Cuándo conviene cada una

| escenario                                | conviene        |
| ---------------------------------------- | --------------- |
| Carga masiva de datos                    | Heap            |
| Muchos `INSERT`, pocas consultas          | Heap            |
| Búsqueda por clave sin índice            | Sequential      |
| Consultas por rango                      | Sequential      |
| `ORDER BY` sobre la clave                | Sequential      |
| Espacio en disco limitado                | Sequential      |
| `id` autoincremental con muchos inserts  | Heap            |

El resumen: el heap optimiza la escritura, el secuencial la lectura por clave.
Es justamente la razón por la que el heap necesita un índice para ser útil en
consultas, y el secuencial no.

## Nota sobre los RID en `SequentialFile`

Los RID que devuelve `insert()` **no son estables**: `reorganize()` reubica los
registros, y salta automáticamente cuando el overflow o el espacio muerto pasan
el 30%. Con N=500, apenas 141 de 500 RID seguían siendo válidos al terminar de
construir el archivo.

Usar un RID viejo no da error: `delete(rid_viejo)` devuelve `True` y borra **otra
fila**. Por eso el benchmark borra por clave en el secuencial (`delete_by_key`) y
solo usa RID en el heap, donde nada los mueve.

Vale tenerlo en cuenta para los índices no agrupados, que guardan RID apuntando
al archivo de datos.
