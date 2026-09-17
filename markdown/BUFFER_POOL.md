# Buffer Pool Global (Singleton)

## Objetivo

Antes, cada archivo de la base (cada tabla, cada tabla del catálogo y cada
índice B+) creaba su **propio** `BufferManager`, es decir, su propio pool de
frames independiente. Eso significa que con N archivos abiertos había N
pools, N `page_table` y N relojes (clock-sweep) distintos, y la RAM total
del motor crecía con la cantidad de archivos abiertos.

Ahora existe **un único `BufferManager` (singleton) y por lo tanto un único
buffer pool** para todo el proceso. Tablas heap, tablas sequential, el
catálogo del sistema (`sys_tables`, `sys_columns`, `sys_indexes`) e índices
B+ comparten el mismo arreglo de frames y el mismo clock-sweep.

Todo el trabajo se hizo en la rama `global_buffer_pool`. **No se hizo ningún
commit, add ni push.**

---

## El problema adicional: páginas de archivos distintos colisionaban

Un pool global no puede indexar las páginas solo por `phys_page_id`. Todos
los archivos numeran sus páginas desde 0 (página 0, 1, 2, …), así que la
página 0 de `sys_tables.dat` y la página 0 de `ventas.dat` son **dos páginas
diferentes** aunque tengan el mismo id local. Si el pool global las tratara
como la misma entrada, una pisaría a la otra y se corrompería la base.

Solución: una página del pool se identifica por el par
**`(FileManager, phys_page_id)`**. Cada `Frame` recuerda a qué `FileManager`
pertenece su página, para saber a qué archivo escribirla cuando hay que
desalojarla (clock-sweep).

```
page_table[(file_manager, phys_page_id)] -> frame_id
```

---

## Cambios por archivo

### `storage/buffer_manager.py` (núcleo, reescrito)

- **Singleton**: `__new__` devuelve siempre la misma instancia. Nuevo método
  de clase `BufferManager.get_instance()` para que cualquier componente
  acceda al pool global. La primera construcción fija `max_frames`; las
  siguientes llamadas a `BufferManager(fm, n)` solo registran/actualizan el
  archivo, sin recrear el pool.
- **`Frame`** suma el atributo `file_manager` (archivo dueño de la página).
- **`page_table`** pasa de `{phys_page_id: frame_id}` a
  `{(file_manager, phys_page_id): frame_id}`.
- **`fetch_page` / `unpin_page` / `mark_dirty` / `flush_page`** ahora reciben
  `(phys_page_id, file_manager)`. El `file_manager` es opcional: si se omite
  se usa `self.active_file` (el último registrado), para mantener compatibles
  los usos de un solo archivo.
- **`_find_victim`** escribe las páginas sucias usando
  `frame.file_manager.write_page(...)`, es decir, cada página se persiste en
  su propio archivo.
- **`flush_file(file_manager)`** (nuevo): persiste solo las páginas sucias de
  un archivo.
- **`flush_all()`**: persiste todas las páginas sucias del pool global.
- **`close(file_manager=None)`**:
  - con `file_manager`: persiste y **descarta del pool** solo las páginas de
    ese archivo y cierra su `FileManager` (equivale al antiguo `close()`
    por-archivo, pero sin tocar los demás archivos abiertos);
  - sin argumentos: cierra todos los archivos conocidos y vacía el pool
    (comportamiento histórico, usado por tests).
- **`invalidate_all(file_manager=None)`**: descarta del pool solo las páginas
  del archivo indicado (antes descartaba TODO el pool). Se sigue usando al
  reconstruir un índice desde cero.
- Se registran los `FileManager` que pasan por el pool (`_known_files`) para
  poder cerrarlos en `close()` sin argumentos.
- Guardas `_is_closed(...)` para que `flush`/`close` sean idempotentes y no
  fallen si un archivo ya fue cerrado.

### `storage/files/heap_file.py`

- El constructor recibe `file_manager` opcional (si no se pasa, usa
  `buffer_manager.active_file`). Ya no lee `buffer_manager.file_manager`.
- **Todas** las llamadas a `fetch_page`, `mark_dirty`, `unpin_page` y `close`
  pasan `self.file_manager` como contexto de archivo.

### `storage/files/sequential_file.py`

- El constructor recibe `file_manager` opcional (default:
  `buffer_manager.active_file`). Ya no lee `buffer_manager.file_manager`.
- Todas las llamadas al buffer manager pasan `self.file_manager`.
- `_truncate()` usa `flush_file(self.file_manager)` en lugar de `flush_all()`
  para no tocar los demás archivos antes de truncar este.

### `indexes/b_tree_base.py`

- `_load_leaf`, `_load_internal` y `_save_page` pasan `self.file_manager`.
- `_init_empty_index()` usa `invalidate_all(self.file_manager)` (solo invalida
  las páginas del índice que se está reconstruyendo, no las de las tablas).

### `indexes/b_plus_clustered.py` y `indexes/b_plus_unclustered.py`

- `close()` llama a `buffer_manager.close(self.file_manager)`, así cierra
  únicamente el archivo `.idx` del índice y no el heap/tabla compartida.

### `storage/table.py`

- Nuevo parámetro opcional `file_manager` (default: `buffer_manager.active_file`).
- Guarda `self.file_manager` y se lo pasa a `HeapFile` / `SequentialFile`.
- El `page_size` para las tablas sequential ahora sale de `self.file_manager`
  en lugar de `buffer_manager.file_manager`.

### `storage/storage_manager.py`

- `open_table()` le pasa `file_manager=fm` a `Table` (el `FileManager` del
  `.dat` de la tabla).

### `storage/schema_catalog.py`

- `_init_sys_table()` le pasa `file_manager=fm` a `Table` (el `FileManager`
  de `sys_tables.dat`, `sys_columns.dat` o `sys_indexes.dat`).

### `tests/files/test_sequential_file.py`

- El helper `close_file(fm, bm)` ya no itera `bm.page_table` (cuyas claves
  ahora son tuplas) ni llama `bm.flush_page(int)`. Ahora usa `bm.close(fm)`,
  que persiste y descarta solo ese archivo.

---

## Compatibilidad

- El parámetro `file_manager` de los métodos del pool es **opcional**: si se
  omite, se resuelve contra `active_file` (el último `FileManager` registrado).
  Por eso el código que hace `bm = BufferManager(fm, n)` y luego
  `bm.fetch_page(p)` sobre un solo archivo sigue funcionando sin cambios.
- `close()` sin argumentos y `flush_all()` conservan la semántica global
  anterior (cerrar/persistir todo), por lo que los tests que solo tenían un
  archivo activo no necesitaron cambios.
- La interfaz pública de `HeapFile`, `SequentialFile`, `Table`, `StorageManager`
  y los árboles B+ no cambia: `file_manager` es siempre un parámetro nuevo con
  default.

### Comprobación del singleton

Se verificó aparte que:

- `BufferManager(fmA, n) is BufferManager(fmB, n) is BufferManager.get_instance()`
  (una sola instancia);
- la página 0 de dos archivos distintos ocupa **dos frames distintos** del
  mismo pool y cada una se persiste en su propio archivo;
- tras `close()` de ambos archivos el pool queda vacío.
