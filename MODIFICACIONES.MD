lo que se cambio:

storage/constraints_manager.py
Nuevo parámetro check_primary_key: bool = True  para validacion de la PK

storage/table.py
Nuevos parámetros column_names: list[str] = None y check_primary_key: bool = True para la correcta generacion de las columnas de la tabla
Nuevo método column_index(column_name), es la posición de la columna en el registro

torage/schema_catalog.py
Nuevas constantes SYS_TABLES_COLUMNS, SYS_COLUMNS_COLUMNS, SYS_INDEXES_COLUMNS. Las tablas ahora también necesitan nombres para sus propias columnas, porque se estan registrando asi mismas
_init_sys_table recibe column_names y usa check_primary_key=False.

register_table inserta una fila en sys_columns por cada columna de la tabla, y todas llevan el mismo table_name. Como sys_columns se creaba con el key_index=0 por defecto (es decir, table_name como PK), la segunda columna de cualquier tabla se rechazaba por "clave duplicada".

register_table acepta column_names y lo guarda.
get_table_info devuelve también column_names.
Al agregar una columna, las posiciones que se leen de sys_columns corrieron un lugar (params[1] era el tipo, ahora es el nombre). Se ajustó la lectura y el dict que devuelve suma la clave column_names, ordenada por column_order igual que schema.

storage/storage_manager.py
create_table acepta column_names y lo pasa al catálogo.
open_table lee meta["column_names"] y lo pasa a Table.








executor.py
conexcion de parser a clase tabla ExecuteVisitor(Visitor) recorre el AST y llama al StorageManager


para probar los test correr estos comandos

PYTHONPATH=. python3 tests/table/test_executor.py
PYTHONPATH=. python3 probar.py