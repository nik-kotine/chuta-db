# Índice B+ Tree

Este documento se va completando a medida que se implementa cada archivo. Por ahora solo `b_tree_leaf_page.py` tiene contenido; `b_tree_internal_page.py`, `b_tree_base.py`, `b_plus_clustered.py` y `b_plus_unclustered.py` todavía estan vacios.

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
