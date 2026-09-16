from storage.buffer_manager import BufferManager
from storage.rid import RID
from storage.files.heap_file import HeapFile 
from storage.files.sequential_file import SequentialFile
from storage.constraints_manager import ConstraintsManager
class Table:
    """
    Representa una tabla lógica en la base de datos.
    Encapsula el esquema, la organización física del archivo subyacente
    (HeapFile o SequentialFile) y expone operaciones CRUD completas.
    """
    def __init__(
        self, 
        name: str, 
        schema: list[str], 
        buffer_manager: BufferManager, 
        file_type: str = "heap",
        key_index: int = 0,
        not_null_columns: list[int] = None,
        unique_columns: list[int] = None,
        check_constraints: list[callable] = None
    ):
        self.name = name
        self.schema = schema
        self.buffer_manager = buffer_manager
        self.file_type = file_type.lower()
        self.key_index = key_index
        self.filename = f"{self.name}.dat"
        
        self.constraints_manager = ConstraintsManager(
            table=self,
            not_null_columns=not_null_columns,
            unique_columns=unique_columns,
            check_constraints=check_constraints
        )

        # Referencia al índice primario agrupado (si existe)
        self.clustered_index = None

        # Diccionario para índices secundarios: {column_index: [lista_de_indices_unclustered]}
        self.secondary_indexes: dict[int, list] = {}

        if self.file_type == "heap":
            self.data_file = HeapFile(self.filename, self.buffer_manager, self.schema)
        elif self.file_type == "sequential":
            page_size = self.buffer_manager.file_manager.page_size
            self.data_file = SequentialFile(self.buffer_manager, page_size, self.schema)
            self.data_file.key_index = self.key_index
        else:
            raise ValueError(f"Organización de archivo no soportada: {self.file_type}")

    def attach_index(self, column_index: int, index_obj):
        """Enlaza un índice secundario para actualización automática."""
        if column_index not in self.secondary_indexes:
            self.secondary_indexes[column_index] = []
        self.secondary_indexes[column_index].append(index_obj)

    def set_clustered_index(self, index_obj):
        """Asigna el índice primario agrupado (BPlusTreeClustered) para esta tabla."""
        self.clustered_index = index_obj

    def insert(self, values: list) -> RID:
        """
        Inserta un registro. Si hay un índice Clustered, pasa a través de él;
        de lo contrario, inserta directo en el archivo de datos y actualiza los índices secundarios.
        """
        if len(values) != len(self.schema):
            raise ValueError(
                f"La tabla '{self.name}' espera {len(self.schema)} valores, recibió {len(values)}"
            )

        self.constraints_manager.validate_insert(values)

        # Inserción guiada por el Árbol B+ Clustered o directa en el archivo físico
        if self.clustered_index:
            key = values[self.key_index]
            rid = self.clustered_index.insert(key, values)
        else:
            rid = self.data_file.insert(values)

        # Actualización automática de índices secundarios (Unclustered)
        if rid is not None:
            for col_idx, indexes in self.secondary_indexes.items():
                key = values[col_idx]
                for idx in indexes:
                    idx._insert_ref(key, rid)

        return rid

    def get(self, rid: RID) -> list | None:
        """
        Recupera una tupla dado su RID.
        """
        return self.data_file.fetch(rid)

    def delete(self, rid: RID) -> bool:
        """
        Elimina un registro y limpia sus entradas en los índices secundarios.
        """
        record_values = self.get(rid)
        if record_values is None:
            return False

        ok = self.data_file.delete(rid)

        # Remover referencias de los índices secundarios
        if ok:
            for col_idx, indexes in self.secondary_indexes.items():
                key = record_values[col_idx]
                for idx in indexes:
                    if hasattr(idx, "delete_ref"):
                        idx.delete_ref(key, rid)
                    else:
                        idx.delete(key)

        return ok

    def search_by_key(self, key_value) -> list[list]:
        """
        Busca por clave priorizando el Árbol B+ Clustered si está disponible.
        """
        if self.clustered_index:
            refs = self.clustered_index.search(key_value)
            if refs is None:
                return []
            if not isinstance(refs, list):
                refs = [refs]
            return [self.data_file.fetch(ref) for ref in refs]

        if self.file_type == "sequential" and hasattr(self.data_file, "search"):
            return [list(record.params) for record in self.data_file.search(key_value)]
        
        results = []
        for _, record_params in self.scan():
            if record_params[self.key_index] == key_value:
                results.append(record_params)
        return results

    def delete_by_key(self, key_value) -> bool:
        """
        Elimina registro(s) que coincidan con la clave primaria.
        """
        if self.clustered_index:
            return self.clustered_index.delete(key_value)

        if self.file_type == "sequential" and hasattr(self.data_file, "delete_by_key"):
            return self.data_file.delete_by_key(key_value)
        
        deleted_any = False
        for rid, record_params in list(self.scan()):
            if record_params[self.key_index] == key_value:
                if self.delete(rid):
                    deleted_any = True
        return deleted_any

    def scan(self):
        """
        Itera sobre todos los registros vivos de la tabla.
        Genera tuplas de (RID, lista_de_valores).
        """
        yield from self.data_file.scan()

    def close(self):
        """
        Cierra el archivo subyacente y libera recursos.
        """
        if hasattr(self.data_file, "close"):
            self.data_file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()