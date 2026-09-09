# Estructura Heap File

## `record.py`

En este archivo se maneja el empaquetado de los registros, se soportan esquemas con los siguientes tipos:
- int
- string
- float

1. `record_encoder`

**Input**: `values: list` (lista que contiene los datos del registro)

**Output**: `header_bytes + data_bytes` (bytes de la cabecera y los datos del registro)

Se empaquetan los datos y se genera la cabecera del registro. 

**La cabecera:** 
Esta contiene la cantidad de columnas y mapea los offsets.

2. `record_decoder`

**Input**: `record_bytes: bytes` (bytes del registro: cabecera + datos)

**Output**: `values: list` (lista con los datos recuperados en sus tipos originales)

Reconstruye los valores del registro leyendo la cabecera e interpretando el bloque de datos.

## `page.py`

En este archivo se implementa la pagina de tamaño fijo (`PAGE_SIZE = 4096` bytes) con slot directory, que guarda registros de largo variable sin necesitar saber su contenido. Cada pagina mantiene su propio header (`page_id`, `slot_count`, `free_space_high`) y un directorio de slots (`offset`, `length`) que crece desde el inicio de la pagina mientras los datos crecen desde el final.

1. `insert`

**Input**: `record_data: bytes` (bytes del registro a guardar, largo arbitrario)

**Output**: `slot_id: int` (posicion del slot usado, o `-1` si la pagina no tiene espacio)

Busca un slot vacio para reusar o reserva uno nuevo, compacta la pagina con `defragment` si hace falta, y escribe el registro en el espacio libre del centro de la pagina.

2. `get_record`

**Input**: `slot_id: int` (indice del slot a leer)

**Output**: `bytes` con el registro, o `None` si el slot no existe o esta borrado

Busca el `(offset, length)` del slot y devuelve el bloque de bytes correspondiente.

3. `delete_record`

**Input**: `slot_id: int` (indice del slot a borrar)

**Output**: `bool` (si el borrado fue efectivo)

Marca el slot como vacio (`offset=0, length=0`). El espacio que ocupaba el registro no se recupera hasta el proximo `defragment`.

4. `defragment`

**Input**: ninguno

**Output**: ninguno

Reubica los registros activos de forma compacta desde el final de la pagina, actualiza los offsets de sus slots y limpia el espacio liberado en el centro.

## `heapfile.py`

En este archivo se maneja el heap file: un archivo donde se guardan e identifican registros (bytes de largo arbitrario, ya empaquetados por `record.py`) sin importar si su esquema es de largo fijo o variable. Cada registro se identifica con un `RID` (`page_id`, `slot_id`). La pagina 0 del archivo esta reservada como un directorio persistente que guarda `page_count` y el `free_space_bytes` de cada pagina de datos, para no tener que escanear el archivo completo en cada operacion.

1. `add`

**Input**: `record_data: bytes` (registro ya empaquetado, de cualquier largo <= `MAX_RECORD_SIZE`)

**Output**: `RID` (`page_id`, `slot_id`) del registro insertado

Busca en el directorio una pagina con espacio suficiente e inserta ahi el registro; si ninguna alcanza, crea una pagina nueva. Levanta `ValueError` si el registro no cabe en ninguna pagina vacia.

2. `get`

**Input**: `rid: RID`

**Output**: `bytes` con el registro, o `None` si no existe o esta borrado

Lee la pagina de `rid.page_id` y delega en `SlottedPage.get_record`.

3. `remove`

**Input**: `rid: RID`

**Output**: `bool` (si el borrado fue efectivo)

Delega en `SlottedPage.delete_record` sobre la pagina de `rid.page_id` y sincroniza el directorio.

4. `compact`

**Input**: `page_id: int`

**Output**: ninguno

Fuerza `defragment` sobre una pagina puntual, para recuperar espacio muerto que quedo tras varios `remove` sin un `add` posterior que lo reclame.

5. `vacuum`

**Input**: ninguno

**Output**: ninguno

Aplica `compact` sobre todas las paginas de datos del archivo.
