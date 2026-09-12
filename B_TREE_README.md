# Índice B+ Tree

Este documento se va completando a medida que se implementa cada archivo.

## `b_tree_leaf_page.py`

En este archivo se maneja la pagina hoja de un B+ Tree: guarda un arreglo denso y **siempre ordenado por `key`** de entradas de tamaño fijo `(key, ref)`, donde `ref` es una referencia opaca al registro real (un `RID` hacia `HeapFile`, para la variante no agrupada, o hacia `SequentialFile`, para la variante agrupada). A diferencia de `SlottedPage` (`heapfile/page.py`), no usa un slot directory de largo variable: como las entradas son de tamaño fijo, insertar/eliminar se hace desplazando el arreglo, lo que permite hacer busqueda binaria dentro de la pagina y que `split()` pueda dividirla por la mitad de forma directa.

Las hojas se encadenan entre si via `next_leaf_id`, formando una lista enlazada que permite recorrer el indice por rango sin volver a subir por el arbol.

1. `find`

**Input**: `key: int`

**Output**: `RID | None`

Busca `key` mediante busqueda binaria sobre el arreglo ordenado. Retorna el `ref` asociado, o `None` si `key` no esta presente.

2. `insert`

**Input**: `key: int`, `ref: RID`

**Output**: `bool` (si la insercion fue efectiva)

Inserta `(key, ref)` en la posicion que mantiene el arreglo ordenado, desplazando las entradas mayores una posicion a la derecha. Retorna `False` si la pagina ya esta llena -- el caller (`b_tree_base.py`) debe hacer `split()` y reintentar.

3. `delete`

**Input**: `key: int`

**Output**: `bool` (si el borrado fue efectivo)

Elimina la entrada con esa `key` desplazando el resto del arreglo. A diferencia de `SlottedPage`, no queda tombstone: `n_entries` siempre refleja entradas vivas, porque el arbol necesita ese numero real para detectar cuando un nodo quedo por debajo de la mitad y hay que fusionar/redistribuir.

4. `split`

**Input**: `new_page_id: int` (asignado por el caller -- esta clase no sabe de reserva de paginas en el archivo)

**Output**: `(split_key: int, new_page: BTreeLeafPage)`

Divide la pagina en dos mitades cuando no entra una insercion. La segunda mitad se muda a `new_page_id`, se actualiza el encadenamiento `next_leaf_id` para no romper la lista enlazada de hojas, y se retorna la clave mediana que el nodo padre necesita para rutear hacia la nueva hoja.

5. `is_underflow` / `can_lend`

Chequean si la hoja quedó por debajo del mínimo de entradas (`MIN_ENTRIES = MAX_ENTRIES // 2`) o si tiene de sobra para prestarle una a un hermano sin quedar ella misma corta.

6. `borrow_from_left` / `borrow_from_right`

Redistribuye una entrada con un hermano adyacente que tiene de sobra (`can_lend() == True`), moviendo la última entrada del hermano izquierdo (o la primera del derecho) y devolviendo la nueva clave separadora para el padre.

7. `merge_with_right`

Fusiona el hermano derecho completo dentro de esta hoja, cuando ninguno de los dos hermanos tiene de sobra para redistribuir. El hermano derecho queda huérfano (lo saca de la cadena `b_tree_base.py`, sacando su clave del padre).

## `b_tree_internal_page.py`

Página interna del B+ Tree: guarda claves de ruteo y punteros a páginas hijas, sin ningún dato del archivo real (los nodos internos son puro directorio). Misma idea de arreglo denso y ordenado que la hoja, pero acá son dos arreglos paralelos: N claves y N+1 hijos.

1. `find_child`

**Input**: `key: int`

**Output**: `page_id: int`

Busca por búsqueda binaria a qué hijo hay que descender para encontrar `key`.

2. `has_space`

**Output**: `bool`

Chequea si hay lugar para una clave más antes de intentar insertarla.

3. `insert_key`

**Input**: `key: int`, `right_child_page_id: int`

**Output**: `bool` (si la inserción fue efectiva)

Inserta una clave de ruteo nueva junto con el hijo que queda a su derecha (el que resultó de dividir un hijo que ya estaba ahí). Retorna `False` si el nodo ya está lleno.

4. `init_as_root`

**Input**: `left_child_page_id: int`, `key: int`, `right_child_page_id: int`

Inicializa una página recién creada como raíz nueva con una sola clave y sus dos hijos. Único caso en que un nodo interno arranca con contenido sin pasar por `insert_key` (que siempre asume que ya hay un hijo a la izquierda).

5. `split`

**Input**: `new_page_id: int`

**Output**: `(clave_empujada_hacia_arriba: int, new_page: BTreeInternalPage)`

Divide el nodo en dos mitades. A diferencia de la hoja, la clave mediana **no** se copia a ninguna mitad: se saca del arreglo y se empuja hacia el padre.

6. `find_child_index`

**Input**: `key: int`

**Output**: `index: int`

Igual que `find_child`, pero devuelve el índice del hijo en vez del `page_id`. Lo usa `b_tree_base.py` para saber qué hermanos son adyacentes durante el rebalanceo de `delete()`.

7. `is_underflow` / `can_lend` / `delete_key_at`

Mismo rol que en la hoja, más `delete_key_at(index)`, que quita una clave y su hijo de la derecha tras una fusión.

8. `borrow_from_left` / `borrow_from_right`

**Input**: hermano, más `separator_key: int` (la clave que tenía el padre entre ambos nodos)

**Output**: la nueva clave separadora

A diferencia de la hoja, acá la clave no se mueve directo entre hermanos: la separadora del padre baja a este nodo, y la clave que se pide prestada del hermano sube a ser la nueva separadora.

9. `merge_with_right`

**Input**: hermano derecho, `separator_key: int`

Fusiona el hermano derecho completo, bajando en el medio la clave separadora del padre (acá sí hace falta, porque los nodos internos no guardan ningún dato propio, solo separadores).

## `b_tree_base.py`

Clase base con toda la lógica del árbol (búsqueda, inserción con split en cascada, borrado). No sabe nada de `HeapFile` ni `SequentialFile` -- eso lo resuelven las subclases llenando 3 hooks de almacenamiento (`_store_record`, `_fetch_record`, `_delete_record`). Guarda la raíz y la altura del árbol en la página 0 de su propio archivo de índice.

1. `search`

**Input**: `key: int`

**Output**: `ref | None`

Desciende desde la raíz hasta la hoja correspondiente y busca `key` ahí.

2. `insert`

**Input**: `key: int`, `params` (lo que sea que espere `_store_record`)

**Output**: `ref`

Persiste el registro real, baja hasta la hoja que le corresponde, y si está llena divide en cascada hacia arriba, creando una raíz nueva si la división llega hasta el tope.

3. `delete`

**Input**: `key: int`

**Output**: `bool`

Baja hasta la hoja, borra la entrada y el registro real. Si la hoja queda por debajo del mínimo, delega en `_rebalance_leaf` (redistribuir con un hermano o fusionarse con él). Si una fusión deja a un nodo interno también en underflow, `_rebalance_leaf` llama a `_rebalance_internal`, que repite lo mismo un nivel más arriba -- y si la cascada llega hasta la raíz y esta se queda sin claves, el árbol pierde un nivel (su único hijo pasa a ser la raíz nueva).

4. `range_search`

**Input**: `start_key: int`, `end_key: int`

**Output**: `list[(key, ref)]`

Desciende una sola vez hasta la hoja donde arrancaría `start_key`, y de ahí en más recorre `next_leaf_id` de hoja en hoja sin volver a subir por el árbol.

## `b_plus_clustered.py`

Variante agrupada: conecta `BPlusTreeBase` con `SequentialFile`, que mantiene los datos ordenados físicamente por la misma clave del índice. `_store_record` delega en `SequentialFile.insert()`, `_fetch_record` en su lectura por RID, y `_delete_record` en `SequentialFile.delete(key)` (borrado lógico).

**Reindexado automático:** `SequentialFile` puede reorganizarse sola (overflow lleno en `insert`, espacio desperdiciado en `delete`), y `reorganize()` reasigna el RID de todos los registros vivos. Si eso pasa a mitad de una operación del árbol, seguir como si nada dejaría el índice apuntando a RIDs viejos. Por eso `SequentialFile.py` ahora expone `reorganize_count` (contador en RAM, se incrementa cada vez que `reorganize()` corre), y `b_plus_clustered.py` lo chequea después de cada `insert`/`delete`: si cambió, corta la operación con una excepción interna (`_ReindexNeeded`) y reconstruye el índice entero desde cero releyendo `SequentialFile` (`_reindex`). Probado con `page_size` chico para forzar reorganizaciones seguidas -- ver `test_b_plus_clustered.py`.

## `b_plus_unclustered.py`

Variante no agrupada: conecta `BPlusTreeBase` con un `HeapFile` ya abierto (para que se pueda compartir entre varios índices no agrupados de la misma tabla). Usa un `RecordPacker` propio para empaquetar/desempaquetar los registros, porque `HeapFile.add()` pide bytes ya codificados (a diferencia de `SequentialFile`, que empaqueta solo).

## Tests

- `test_b_tree_base.py`: lógica del árbol sola (storage falso en memoria) -- insert/search, split de hoja forzado, `range_search` cruzando hojas, persistencia, delete simple, delete forzando redistribución/fusión, y colapso de la raíz.
- `test_b_plus_clustered.py`: contra `SequentialFile` real -- insert/search/delete, que el orden físico coincida con el del índice, persistencia, y el reindexado automático tras un `reorganize()` forzado.
- `test_b_plus_unclustered.py`: contra `HeapFile` real -- insert/search/delete, que `range_search` ordene bien aunque el heap esté desordenado, y dos índices compartiendo el mismo `HeapFile`.
- `test_b_tree_complexity.py`: mide páginas de índice leídas por `search()` a medida que crece N (100 a 100 000 registros), para confirmar empíricamente el costo `D·log_(R/2)(M)` de la slide de complejidad.

## Complejidad medida

Con `MAX_ENTRIES = 340` por hoja y `MAX_KEYS = 510` por nodo interno (fanout ≈ 511), corriendo `test_b_tree_complexity.py`:

| N | altura | páginas leídas por `search` |
|---|---|---|
| 100 | 0 | 1.00 |
| 1 000 | 1 | 2.00 |
| 10 000 | 1 | 2.00 |
| 50 000 | 1 | 2.00 |
| 100 000 | 1 | 2.00 |

N creció 1000x (de 100 a 100 000) y las páginas leídas por búsqueda solo crecieron de 1 a 2 -- confirma que el costo es logarítmico (`D·log_(R/2)(M)`), no lineal. Con este fanout, un único nodo raíz interno alcanza para indexar hasta ~511 × 340 ≈ 173 000 registros en altura 1; recién con más de eso el árbol pasaría a altura 2.
