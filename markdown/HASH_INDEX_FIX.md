# Fix: doble resolución de bucket en búsqueda exacta del Hash Index

> **Resumen en una frase:** el índice hash buscaba el mismo bucket
> dos veces por cada búsqueda exacta, en vez de una; se borró la búsqueda
> repetida. Resultado: cada búsqueda exacta ahora lee 3 páginas de disco en
> vez de 5 (40% menos trabajo).

Este documento va junto con el commit del fix, por separado del trabajo del
R-Tree (ver `indexes/R_TREE_README.md`), que es un cambio no relacionado.

## Diagnóstico

`indexes/extendible_hash.py`, en el método de búsqueda exacta, resolvía el
bucket **dos veces**: una llamada a `_locate_bucket(bucket_to_insert)` (que
el código no usaba para nada más que validar) y, enseguida,
`_iter_kvs_in_bucket(bucket_to_insert)`, que internamente ya vuelve a
resolver el mismo bucket. Eso costaba de **3 a 5 páginas leídas** por
búsqueda exacta en vez de 3.

## Medición antes/después

`tests/indexes/test_hash_complexity.py` cuenta páginas físicas leídas por
operación (metadata + directorio + bucket):

|                    | páginas/search (antes) | páginas/search (con el fix) |
| ------------------ | ---------------------: | --------------------------: |
| N = 100 .. 100,000 |   **5.00** (constante) |        **3.00** (constante) |

Una baja del **40%** en I/O, constante en todo el rango de N. Este número
es confiable sin importar el ruido de la máquina porque es un conteo exacto
de llamadas, no un tiempo de reloj.

## ¿Eso hace que el hash le gane al B+ clustered en búsqueda exacta?

Se armó un A/B controlado (mismo proceso, búsquedas intercaladas entre
hash y B+, para cancelar el ruido de la máquina):

|                                       N | B+ clustered (µs) | Hash con fix (µs) | ¿quién gana?         |
| --------------------------------------: | ----------------: | ----------------: | -------------------- |
|     20,000 (B+ en 2 páginas, hash en 3) |            ~69–87 |          ~124–155 | B+ clustered, ~1.8x  |
| 100,000 (B+ y hash, ambos en 3 páginas) |               ~49 |               ~76 | B+ clustered, ~1.55x |

**No.** El hash sigue perdiendo incluso cuando empata en páginas leídas,
porque el conteo de páginas no captura todo el costo: resolver el
directorio extensible, calcular el hash de la key y escanear el bucket
comparando `kv.key == key` es trabajo en Python puro antes de tocar
cualquier página, mientras que el B+ hace una búsqueda binaria sobre un
arreglo ya ordenado.

De paso se encontró que la documentación de `indexes/B_TREE_README.md`
sobre el fanout del B+ (`MAX_ENTRIES≈340`/`MAX_KEYS≈510`) está
desactualizada: el fanout real medido hoy es ~8 (el cálculo usa el peor
caso con `MAX_KEY_SIZE=512`, que da un número chico), lo que explica por
qué el B+ ya necesita 3 páginas desde N≈50,000 en vez de desde ~173,000
como decía el documento viejo. No se tocó ese documento en este trabajo,
queda como pendiente aparte.

**Conclusión honesta:** el fix es una mejora real y medible de I/O (40%
menos páginas), pero no revierte la comparación de tiempo real contra el B+
clustered a estos tamaños — sí la acerca (de 1.8x a 1.55x de diferencia). El
hash seguiría siendo la elección correcta cuando el acceso es _solo_
igualdad exacta y la tabla es demasiado grande para que el B+ se mantenga en
pocas páginas, o cuando el patrón de carga es con mucho churn (inserciones/
eliminaciones frecuentes), donde el hash ya gana claramente (ver
`benchmark/indices/README.md`, sección de churn).
