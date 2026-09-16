import os
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table

SYS_TABLES_NAME = "sys_tables"
SYS_COLUMNS_NAME = "sys_columns"

# Esquemas de las tablas del sistema
SYS_TABLES_SCHEMA = ["varchar(64)", "varchar(20)", "integer"]  # (table_name, file_type, key_index)
SYS_COLUMNS_SCHEMA = ["varchar(64)", "varchar(64)", "integer"]  # (table_name, data_type, column_order)


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
        self.sys_tables = self._init_sys_table(SYS_TABLES_NAME, SYS_TABLES_SCHEMA)
        self.sys_columns = self._init_sys_table(SYS_COLUMNS_NAME, SYS_COLUMNS_SCHEMA)

        # Auto-registro inicial si el catálogo es nuevo
        self._bootstrap_if_needed()

    def _init_sys_table(self, name: str, schema: list[str]) -> Table:
        filename = f"{name}.dat"
        fm = FileManager(filename, self.page_size, self.header_size)
        bm = BufferManager(fm, self.buffer_frames)
        return Table(name, schema, bm, file_type="heap")

    def _clean_str(self, val) -> str:
        """Limpia caracteres nulos y espacios de relleno en cadenas fixed/padded."""
        if isinstance(val, str):
            return val.rstrip("\x00").strip()
        return str(val)

    def _bootstrap_if_needed(self):
        """Registra las propias tablas del sistema en el catálogo si es la primera vez."""
        if self.get_table_info(SYS_TABLES_NAME) is None:
            self.register_table(SYS_TABLES_NAME, SYS_TABLES_SCHEMA, "heap", 0)
            self.register_table(SYS_COLUMNS_NAME, SYS_COLUMNS_SCHEMA, "heap", 0)

    def register_table(self, name: str, schema: list[str], file_type: str, key_index: int):
        """Registra la metadata de una nueva tabla en sys_tables y sys_columns."""
        if self.get_table_info(name) is not None:
            raise ValueError(f"La tabla '{name}' ya existe en el catálogo.")

        # 1. Insertar en sys_tables
        self.sys_tables.insert([name, file_type.lower(), key_index])

        # 2. Insertar cada columna en sys_columns
        for idx, col_type in enumerate(schema):
            self.sys_columns.insert([name, col_type, idx])

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
                    "schema": []
                }
                break

        if target_table is None:
            return None

        # Escanear sys_columns para recuperar el esquema ordenado
        columns = []
        for _, params in self.sys_columns.scan():
            tbl_name = self._clean_str(params[0])
            if tbl_name == name:
                data_type = self._clean_str(params[1])
                col_order = params[2]
                columns.append((col_order, data_type))

        columns.sort(key=lambda x: x[0])
        target_table["schema"] = [col[1] for col in columns]

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

    def close(self):
        """Cierra los archivos del catálogo."""
        self.sys_tables.close()
        self.sys_columns.close()