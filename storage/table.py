from storage.buffer_manager import BufferManager
from storage.rid import RID
from storage.files.heap_file import HeapFile 
from storage.files.sequential_file import SequentialFile

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
        key_index: int = 0
    ):
        self.name = name
        self.schema = schema
        self.buffer_manager = buffer_manager
        self.file_type = file_type.lower()
        self.key_index = key_index

        # El archivo físico se llamará como la tabla (ej. "usuarios.dat")
        self.filename = f"{self.name}.dat"

        if self.file_type == "heap":
            self.data_file = HeapFile(self.filename, self.buffer_manager, self.schema)
            
        elif self.file_type == "sequential":
            page_size = self.buffer_manager.file_manager.page_size
            self.data_file = SequentialFile(self.buffer_manager, page_size, self.schema)
            self.data_file.key_index = self.key_index
            
        else:
            raise ValueError(f"Organización de archivo no soportada: {self.file_type}")

    # ---------- Operaciones CRUD por RID ----------

    def insert(self, values: list) -> RID:
        """
        Inserta un registro en la tabla tras validar la cantidad de campos.
        """
        if len(values) != len(self.schema):
            raise ValueError(
                f"La tabla '{self.name}' espera {len(self.schema)} valores, recibió {len(values)}"
            )
        return self.data_file.insert(values)

    def get(self, rid: RID) -> list | None:
        """
        Recupera una tupla dado su RID.
        """
        return self.data_file.fetch(rid)

    def delete(self, rid: RID) -> bool:
        """
        Elimina un registro dado su RID.
        """
        return self.data_file.delete(rid)

    def update(self, rid: RID, new_values: list) -> RID | None:
        """
        Actualiza un registro borrando la versión vieja e insertando la nueva.
        Retorna el nuevo RID asignado.
        """
        if len(new_values) != len(self.schema):
            raise ValueError(
                f"La tabla '{self.name}' espera {len(self.schema)} valores, recibió {len(new_values)}"
            )
        
        if self.delete(rid):
            return self.insert(new_values)
        return None

    # ---------- Búsquedas por Clave ----------

    def search_by_key(self, key_value) -> list[list]:
        """
        Busca registros por el valor de su clave primaria.
        Usa la búsqueda ordenada en SequentialFile o escaneo en HeapFile.
        """
        if self.file_type == "sequential" and hasattr(self.data_file, "search"):
            return [list(record.params) for record in self.data_file.search(key_value)]
        
        # Para HeapFile: escaneo lineal
        results = []
        for _, record_params in self.scan():
            if record_params[self.key_index] == key_value:
                results.append(record_params)
        return results

    def delete_by_key(self, key_value) -> bool:
        """
        Elimina registro(s) que coincidan con la clave primaria.
        """
        if self.file_type == "sequential" and hasattr(self.data_file, "delete_by_key"):
            return self.data_file.delete_by_key(key_value)
        
        deleted_any = False
        for rid, record_params in list(self.scan()):
            if record_params[self.key_index] == key_value:
                if self.delete(rid):
                    deleted_any = True
        return deleted_any

    # ---------- Iteración y Cierre ----------

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