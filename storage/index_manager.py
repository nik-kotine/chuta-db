import os
from indexes.b_plus_unclustered import BPlusTreeUnclustered
from indexes.b_plus_clustered import BPlusTreeClustered
from storage.schema_catalog import SchemaCatalog
from storage.table import Table


class IndexManager:
    """
    Administra la creación, apertura y eliminación de índices B+ en la base de datos.
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