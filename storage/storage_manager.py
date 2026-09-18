import os
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table
from storage.schema_catalog import SchemaCatalog
from storage.index_manager import IndexManager
from storage.files.sequential_file import FILE_HEADER_SIZE


class StorageManager:
    """
    Administrador central que coordina el catálogo del sistema en disco
    y el acceso/caché a las tablas físicas.
    """
    def __init__(
        self,
        page_size: int = 4096,
        buffer_frames: int = 10,
        # un mismo header_size se usa para el archivo de CUALQUIER tabla
        # (heap o sequential) -- HeapFile es indiferente al valor exacto,
        # asi que tiene que alcanzar para lo que SI le importa el
        # contenido: el header real de SequentialFile.
        header_size: int = FILE_HEADER_SIZE
    ):
        self.page_size = page_size
        self.buffer_frames = buffer_frames
        self.header_size = header_size

        # Inicializamos el catálogo en disco basado en HeapFiles
        self.catalog = SchemaCatalog(
            page_size=self.page_size,
            header_size=self.header_size,
            buffer_frames=self.buffer_frames
        )
        self.index_manager = IndexManager(self.catalog)
        self.tables: dict[str, Table] = {}  # Caché de tablas abiertas en memoria

    def create_table(
        self, 
        name: str, 
        schema: list[str], 
        file_type: str = "heap", 
        key_index: int = 0,
        column_names: list[str] = None
    ) -> Table:
        """
        Registra una tabla en las tablas del sistema y la abre.
        """
        if self.catalog.get_table_info(name) is not None:
            raise ValueError(f"La tabla '{name}' ya existe en el catálogo.")

        self.catalog.register_table(name, schema, file_type, key_index, column_names)
        return self.open_table(name)

    def open_table(self, name: str) -> Table:
        """
        Abre una tabla cargando su metadata desde el catálogo del sistema.
        """
        if name in self.tables:
            return self.tables[name]

        meta = self.catalog.get_table_info(name)
        if meta is None:
            raise KeyError(f"La tabla '{name}' no existe en el catálogo.")

        filename = f"{name}.dat"
        fm = FileManager(filename, self.page_size, self.header_size)
        bm = BufferManager(fm, self.buffer_frames)

        table = Table(
            name=name,
            schema=meta["schema"],
            buffer_manager=bm,
            file_type=meta["file_type"],
            key_index=meta["key_index"],
            column_names=meta["column_names"],
            file_manager=fm
        )

        # Reacopla los indices B+ que el catalogo diga que esta tabla tiene
        # (creados en una sesion anterior o en esta misma), para que las
        # consultas los puedan usar y el CRUD los mantenga al dia.
        self.index_manager.load_indexes_for_table(table)

        self.tables[name] = table
        return table

    def drop_table(self, name: str):
        """
        Elimina la metadata de la tabla del catálogo y remueve el archivo .dat de disco.
        """
        meta = self.catalog.get_table_info(name)
        if meta is None:
            raise KeyError(f"La tabla '{name}' no existe en el catálogo.")

        if name in self.tables:
            self.tables[name].close()
            del self.tables[name]

        self.catalog.drop_table_info(name)

        filename = f"{name}.dat"
        if os.path.exists(filename):
            os.remove(filename)

    def close(self):
        """
        Guarda los cambios y cierra todas las tablas y el catálogo.
        """
        for name, table in list(self.tables.items()):
            table.close()
        self.tables.clear()

        self.catalog.close()

        self.index_manager.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()