import os
from indexes.b_plus_unclustered import BPlusTreeUnclustered
from indexes.b_plus_clustered import BPlusTreeClustered
from indexes.extendible_hash import HashIndex
from indexes.extendible_hash import PAGE_SIZE as HASH_PAGE_SIZE
from storage.buffer_manager import BufferManager
from storage.file_manager import FileManager
from storage.schema_catalog import SchemaCatalog
from storage.table import Table


HASH_FILE_HEADER_SIZE = 16
HASH_DEFAULT_BUCKET_SIZE = 16
HASH_DEFAULT_DEPTH = 1
HASH_DEFAULT_SEED = 0


class IndexManager:
    """
    Administra la creación, apertura y eliminación de índices B+ y hash en la base de datos.
    """
    def __init__(self, catalog: SchemaCatalog):
        self.catalog = catalog
        self.open_indexes: dict[str, BPlusTreeUnclustered] = {}

    def create_unclustered_index(
        self, 
        index_name: str, 
        table: Table, 
        column_index: int, 
        column_name: str = "col"
    ) -> BPlusTreeUnclustered:
        """
        Crea un índice secundario no agrupado (BPlusTreeUnclustered) sobre una columna.
        Si la tabla ya tiene datos, se encarga de leerlos e indexarlos.
        """
        index_filename = f"{index_name}.idx"

        # Instanciar el árbol B+ Unclustered enlazado al HeapFile de la tabla
        index = BPlusTreeUnclustered(
            index_filename=index_filename,
            heap_file=table.data_file,
            schema=table.schema
        )
    
        # Poblar el índice con los registros existentes en la tabla
        for rid, record_params in table.scan():
            key = record_params[column_index]
            index._insert_ref(key, rid)

        # Registrar en el catálogo
        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=column_index,
            index_type="unclustered"
        )

        self.open_indexes[index_name] = index
        table.attach_index(column_index, index)
        return index

    def create_clustered_index(
        self, 
        index_name: str, 
        table: Table, 
        column_name: str = "pk"
    ) -> BPlusTreeClustered:
        """
        Crea un índice primario agrupado (BPlusTreeClustered).
        Solo se puede aplicar a tablas del tipo 'sequential'.
        """
        if table.file_type != "sequential":
            raise ValueError("Un índice Clustered solo puede crearse sobre una tabla SequentialFile.")

        index_filename = f"{index_name}.idx"
        index = BPlusTreeClustered(index_filename, table.data_file)
        
        # Sincronizamos el índice con los datos que ya tenga el archivo
        index._reindex()

        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=table.key_index, # Siempre usa la clave primaria
            index_type="clustered"
        )

        self.open_indexes[index_name] = index
        table.set_clustered_index(index)
        return index

    def create_hash_index(
        self, 
        index_name: str, 
        table: Table, 
        column_index: int, 
        column_name: str = "col"
    ) -> HashIndex:
        """
        Crea un índice hash no agrupado (extendible hashing) sobre una
        columna. Solo tiene sentido sobre tablas Heap (igual que el B+
        no agrupado). Si la tabla ya tiene datos, se encarga de leerlos
        e indexarlos.
        """
        self.catalog.register_index(
            index_name=index_name,
            table_name=table.name,
            column_name=column_name,
            column_index=column_index,
            index_type="hash"
        )
        return self._build_hash_index(index_name, table, column_index, column_name)

    def _hash_key_config(self, table: Table, column_index: int):
        """
        Traduce el tipo de la columna a la configuración del KVSerializer
        del índice hash: formato struct de la clave y si es variable.
        """
        col_type = table.schema[column_index].strip().lower()
        if col_type in ("int", "integer", "int4", "smallint", "int2", "date"):
            return (">i", False)
        if col_type in ("bigint", "int8"):
            return (">q", False)
        if col_type in ("float", "real", "float4", "double precision", "float8"):
            # ">d" en vez de ">f": el hash y la igualdad trabajan sobre el
            # float de Python, y con 8 bytes no se pierde precisión extra.
            return (">d", False)
        if col_type in ("bool", "boolean"):
            return (">?", False)
        if col_type.startswith("varchar") or col_type in ("text", "string", "str"):
            return ("s", True)
        raise ValueError(
            f"El tipo '{table.schema[column_index]}' no es soportado por "
            f"el índice hash sobre '{table.name}'"
        )

    def _build_hash_index(
        self, 
        index_name: str, 
        table: Table, 
        column_index: int, 
        column_name: str
    ) -> HashIndex:
        """
        Construye el índice hash desde cero y lo enlaza a la tabla.

        El hash no tiene persistencia de páginas (a diferencia del B+):
        siempre se reconstruye barriendo la tabla, así que se descartan
        páginas viejas en caché y se truncate el archivo .idx antes de
        arrancar, para que una sesión anterior no deje basura.
        """
        index_filename = f"{index_name}.idx"
        key_format, key_variable = self._hash_key_config(table, column_index)

        fm = FileManager(index_filename, HASH_PAGE_SIZE, HASH_FILE_HEADER_SIZE)
        bm = BufferManager(fm)
        bm.invalidate_all(fm)
        fm.truncate(fm.file_header_size)

        index = HashIndex(
            table_name=table.name,
            column_name=column_name,
            key_format=key_format,
            key_variable=key_variable,
            buffer_manager=bm,
            max_bucket_size=HASH_DEFAULT_BUCKET_SIZE,
            depth=HASH_DEFAULT_DEPTH,
            seed=HASH_DEFAULT_SEED,
            file_manager=fm
        )

        # Poblar el índice con los registros existentes en la tabla
        for rid, record_params in table.scan():
            index._insert_ref(record_params[column_index], rid)

        self.open_indexes[index_name] = index
        table.attach_index(column_index, index)
        return index

    def load_indexes_for_table(self, table: Table):
        """
        Carga los índices registrados en el catálogo para una tabla dada
        y los enlaza a la instancia de Table para que se actualicen automáticamente.
        """
        registered = self.catalog.get_table_indexes(table.name)
        for meta in registered:
            index_name = meta["index_name"]
            col_idx = meta["column_index"]
            idx_type = meta["index_type"]
            index_filename = f"{index_name}.idx"

            if index_name not in self.open_indexes:
                if idx_type == "unclustered":
                    idx = BPlusTreeUnclustered(index_filename, table.data_file, table.schema)
                    self.open_indexes[index_name] = idx
                    table.attach_index(col_idx, idx)
                    
                elif idx_type == "clustered":
                    idx = BPlusTreeClustered(index_filename, table.data_file)
                    self.open_indexes[index_name] = idx
                    table.set_clustered_index(idx)

                elif idx_type == "hash":
                    # El hash no persiste sus páginas: se reconstruye
                    # desde la tabla en cada apertura.
                    self._build_hash_index(
                        index_name,
                        table,
                        col_idx,
                        meta["column_name"]
                    )

    def drop_index(self, index_name: str):
        """Elimina un índice y su archivo físico .idx."""
        if index_name in self.open_indexes:
            self.open_indexes[index_name].close()
            del self.open_indexes[index_name]

        self.catalog.drop_index_info(index_name)

        filename = f"{index_name}.idx"
        if os.path.exists(filename):
            os.remove(filename)


    def close(self):
        """Cierra todos los índices abiertos."""
        for idx in self.open_indexes.values():
            idx.close()
        self.open_indexes.clear()