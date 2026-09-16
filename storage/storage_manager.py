import os
import json
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table

class StorageManager:
    """
    Administrador central de almacenamiento y catálogo del sistema.
    Gestiona la creación, apertura, eliminación y persistencia de múltiples tablas.
    """
    def __init__(
        self, 
        db_name: str = "chutadb", 
        page_size: int = 4096, 
        buffer_frames: int = 10, 
        header_size: int = 16
    ):
        self.db_name = db_name
        self.page_size = page_size
        self.buffer_frames = buffer_frames
        self.header_size = header_size
        
        # Archivo donde se persistirá el catálogo de tablas
        self.catalog_filename = f"{self.db_name}_catalog.json"
        
        self.tables: dict[str, Table] = {}  # Caché de tablas abiertas en memoria
        self.catalog: dict[str, dict] = {}  # Metadata persistente de tablas
        
        self._load_catalog()

    def _load_catalog(self):
        """Carga el catálogo de tablas desde disco si existe."""
        if os.path.exists(self.catalog_filename):
            with open(self.catalog_filename, "r", encoding="utf-8") as f:
                self.catalog = json.load(f)
        else:
            self.catalog = {}
            self._save_catalog()

    def _save_catalog(self):
        """Guarda el estado actual del catálogo en disco."""
        with open(self.catalog_filename, "w", encoding="utf-8") as f:
            json.dump(self.catalog, f, indent=4)

    def create_table(
        self, 
        name: str, 
        schema: list[str], 
        file_type: str = "heap", 
        key_index: int = 0
    ) -> Table:
        """
        Crea una nueva tabla, registra su metadata en el catálogo 
        y devuelve la instancia de Table lista para operar.
        """
        if name in self.catalog:
            raise ValueError(f"La tabla '{name}' ya existe en el catálogo.")
        
        # Registrar metadatos
        self.catalog[name] = {
            "schema": schema,
            "file_type": file_type.lower(),
            "key_index": key_index
        }
        self._save_catalog()

        return self.open_table(name)

    def open_table(self, name: str) -> Table:
        """
        Abre una tabla existente a partir del catálogo.
        Si ya está abierta en memoria, la retorna directamente desde la caché.
        """
        if name not in self.catalog:
            raise KeyError(f"La tabla '{name}' no existe en el catálogo.")
        
        if name in self.tables:
            return self.tables[name]
        
        meta = self.catalog[name]
        filename = f"{name}.dat"
        
        # Instanciamos los componentes de bajo nivel para esta tabla
        fm = FileManager(filename, self.page_size, self.header_size)
        bm = BufferManager(fm, self.buffer_frames)
        
        table = Table(
            name=name,
            schema=meta["schema"],
            buffer_manager=bm,
            file_type=meta["file_type"],
            key_index=meta["key_index"]
        )
        
        self.tables[name] = table
        return table

    def drop_table(self, name: str):
        """
        Elimina una tabla del catálogo y borra todos sus archivos físicos de disco.
        """
        if name not in self.catalog:
            raise KeyError(f"La tabla '{name}' no existe.")
        
        # Cerrar y remover de memoria si está abierta
        if name in self.tables:
            self.tables[name].close()
            del self.tables[name]
        
        # Borrar archivos físicos de datos
        filename = f"{name}.dat"
        for f in [filename, filename + ".fmt"]:
            if os.path.exists(f):
                os.remove(f)
        
        # Actualizar catálogo
        del self.catalog[name]
        self._save_catalog()

    def close(self):
        """
        Cierra todas las tablas abiertas y libera los recursos del StorageManager.
        """
        for name, table in list(self.tables.items()):
            table.close()
        self.tables.clear()
        self._save_catalog()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()