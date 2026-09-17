import os
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table

SYS_TABLES_NAME = "sys_tables"
SYS_COLUMNS_NAME = "sys_columns"
SYS_INDEXES_NAME = "sys_indexes"

# Esquemas de las tablas del sistema
SYS_TABLES_SCHEMA = ["varchar(64)", "varchar(20)", "integer"]  # (table_name, file_type, key_index)
SYS_COLUMNS_SCHEMA = ["varchar(64)", "varchar(64)", "varchar(64)", "integer"]  # (table_name, column_name, data_type, column_order)
# (index_name, table_name, column_name, column_index, index_type)
SYS_INDEXES_SCHEMA = ["varchar(64)", "varchar(64)", "varchar(64)", "integer", "varchar(20)"]

# Nombres de columna de las propias tablas del sistema
SYS_TABLES_COLUMNS = ["table_name", "file_type", "key_index"]
SYS_COLUMNS_COLUMNS = ["table_name", "column_name", "data_type", "column_order"]
SYS_INDEXES_COLUMNS = ["index_name", "table_name", "column_name", "column_index", "index_type"]
class SchemaCatalog:
    """
    Catálogo del sistema que gestiona la metadata de la base de datos
    utilizando tablas físicas de tipo HeapFile (sys_tables y sys_columns).
    """
    def __init__(
        self, 
        page_size: int = 4096, 
        header_size: int = 16, 
        buffer_frames: int = 10
    ):
        self.page_size = page_size
        self.header_size = header_size
        self.buffer_frames = buffer_frames

        # Instanciamos directamente las tablas del sistema
        self.sys_tables = self._init_sys_table(SYS_TABLES_NAME, SYS_TABLES_SCHEMA, SYS_TABLES_COLUMNS)
        self.sys_columns = self._init_sys_table(SYS_COLUMNS_NAME, SYS_COLUMNS_SCHEMA, SYS_COLUMNS_COLUMNS)
        self.sys_indexes = self._init_sys_table(SYS_INDEXES_NAME, SYS_INDEXES_SCHEMA, SYS_INDEXES_COLUMNS)

        # Auto-registro inicial si el catálogo es nuevo
        self._bootstrap_if_needed()

    def _init_sys_table(self, name: str, schema: list[str], column_names: list[str]) -> Table:
        filename = f"{name}.dat"
        fm = FileManager(filename, self.page_size, self.header_size)
        bm = BufferManager(fm, self.buffer_frames)
        # check_primary_key=False: sys_columns y sys_indexes tienen clave
        # compuesta (table_name + column_order), que ConstraintsManager no
        # modela. Con la PK simple activada, la segunda columna de cualquier
        # tabla se rechazaria por "clave duplicada".
        return Table(name, schema, bm, file_type="heap",
                     column_names=column_names, check_primary_key=False)

    def _clean_str(self, val) -> str:
        """Limpia caracteres nulos y espacios de relleno en cadenas fixed/padded."""
        if isinstance(val, str):
            return val.rstrip("\x00").strip()
        return str(val)

    def _bootstrap_if_needed(self):
        """Registra las propias tablas del sistema en el catálogo si es la primera vez."""
        if self.get_table_info(SYS_TABLES_NAME) is None:
            self.register_table(SYS_TABLES_NAME, SYS_TABLES_SCHEMA, "heap", 0, SYS_TABLES_COLUMNS)
            self.register_table(SYS_COLUMNS_NAME, SYS_COLUMNS_SCHEMA, "heap", 0, SYS_COLUMNS_COLUMNS)
            self.register_table(SYS_INDEXES_NAME, SYS_INDEXES_SCHEMA, "heap", 0, SYS_INDEXES_COLUMNS)

    def register_table(self, name: str, schema: list[str], file_type: str, key_index: int,
                       column_names: list[str] = None):
        """Registra la metadata de una nueva tabla en sys_tables y sys_columns."""
        if self.get_table_info(name) is not None:
            raise ValueError(f"La tabla '{name}' ya existe en el catálogo.")

        # Insertar en sys_tables
        self.sys_tables.insert([name, file_type.lower(), key_index])

        # Insertar cada columna en sys_columns
        if column_names is None:
            column_names = [f"col{i}" for i in range(len(schema))]
        for idx, col_type in enumerate(schema):
            self.sys_columns.insert([name, column_names[idx], col_type, idx])

    def get_table_info(self, name: str) -> dict | None:
        """Busca y reconstruye la metadata de una tabla desde sys_tables y sys_columns."""
        target_table = None

        # Escanear sys_tables
        for _, params in self.sys_tables.scan():
            tbl_name = self._clean_str(params[0])
            if tbl_name == name:
                target_table = {
                    "name": name,
                    "file_type": self._clean_str(params[1]),
                    "key_index": params[2],
                    "schema": [],
                    "column_names": []
                }
                break

        if target_table is None:
            return None

        # Escanear sys_columns para recuperar el esquema ordenado
        columns = []
        for _, params in self.sys_columns.scan():
            tbl_name = self._clean_str(params[0])
            if tbl_name == name:
                col_name = self._clean_str(params[1])
                data_type = self._clean_str(params[2])
                col_order = params[3]
                columns.append((col_order, col_name, data_type))

        columns.sort(key=lambda x: x[0])
        target_table["schema"] = [col[2] for col in columns]
        target_table["column_names"] = [col[1] for col in columns]

        return target_table

    def drop_table_info(self, name: str):
        """Elimina la metadata de una tabla de sys_tables y sys_columns."""
        if name in (SYS_TABLES_NAME, SYS_COLUMNS_NAME):
            raise ValueError("No se pueden eliminar las tablas del catálogo del sistema.")

        # Eliminar de sys_tables
        for rid, params in list(self.sys_tables.scan()):
            if self._clean_str(params[0]) == name:
                self.sys_tables.delete(rid)

        # Eliminar de sys_columns
        for rid, params in list(self.sys_columns.scan()):
            if self._clean_str(params[0]) == name:
                self.sys_columns.delete(rid)

    def register_index(self, index_name: str, table_name: str, column_name: str, column_index: int, index_type: str):
            """Registra un nuevo índice en sys_indexes."""
            self.sys_indexes.insert([index_name, table_name, column_name, column_index, index_type.lower()])

    def get_table_indexes(self, table_name: str) -> list[dict]:
        """Recupera la lista de índices registrados para una tabla dada."""
        indexes = []
        for _, params in self.sys_indexes.scan():
            if self._clean_str(params[1]) == table_name:
                indexes.append({
                    "index_name": self._clean_str(params[0]),
                    "table_name": table_name,
                    "column_name": self._clean_str(params[2]),
                    "column_index": params[3],
                    "index_type": self._clean_str(params[4])
                })
        return indexes

    def drop_index_info(self, index_name: str):
        """Elimina un índice de sys_indexes."""
        for rid, params in list(self.sys_indexes.scan()):
            if self._clean_str(params[0]) == index_name:
                self.sys_indexes.delete(rid)

    def drop_table_info(self, name: str):
        if name in (SYS_TABLES_NAME, SYS_COLUMNS_NAME, SYS_INDEXES_NAME):
            raise ValueError("No se pueden eliminar las tablas del catálogo del sistema.")

        for rid, params in list(self.sys_tables.scan()):
            if self._clean_str(params[0]) == name:
                self.sys_tables.delete(rid)

        for rid, params in list(self.sys_columns.scan()):
            if self._clean_str(params[0]) == name:
                self.sys_columns.delete(rid)

        for rid, params in list(self.sys_indexes.scan()):
            if self._clean_str(params[1]) == name:
                self.sys_indexes.delete(rid)

    def close(self):
        """Cierra los archivos del catálogo."""
        self.sys_tables.close()
        self.sys_columns.close()
        self.sys_indexes.close()