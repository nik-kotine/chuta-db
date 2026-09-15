from storage.buffer_manager import BufferManager
from storage.rid import RID
from storage.files.heap_file import HeapFile 
from storage.files.sequential_file import SequentialFile

class Table:
    """
    Representa una tabla lógica en la base de datos.
    Expone operaciones CRUD de alto nivel.
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
        
        # El archivo físico se llamará como la tabla (ej. "usuarios.dat")
        self.filename = f"{self.name}.dat"

        # Dependiendo del tipo de organización, instanciamos el archivo correcto
        if self.file_type == "heap":
            # Asumiendo que adaptaste HeapFile para recibir el esquema
            self.data_file = HeapFile(self.filename, self.buffer_manager, self.schema)
            
        elif self.file_type == "sequential":
            # SequentialFile maneja el orden, necesita saber qué columna es la clave principal
            page_size = self.buffer_manager.file_manager.page_size
            self.data_file = SequentialFile(self.buffer_manager, page_size, self.schema)
            self.data_file.key_index = key_index
            
        else:
            raise ValueError(f"Organización de archivo no soportada: {self.file_type}")

    def insert(self, values: list) -> RID:
        """
        Inserta un registro en la tabla.
        Aquí se podría añadir validación para verificar que `values`
        coincida con los tipos definidos en `self.schema`.
        """
        if len(values) != len(self.schema):
            raise ValueError(f"La tabla {self.name} espera {len(self.schema)} valores, recibió {len(values)}")
        
        return self.data_file.insert(values)

    def get(self, rid: RID) -> list | None:
        """
        Recupera una tupla (lista de valores) dado su RID.
        """
        return self.data_file.fetch(rid)

    def delete(self, rid: RID) -> bool:
        """
        Elimina un registro de la tabla usando su RID.
        """
        return self.data_file.delete(rid)

    def scan(self):
        """
        Itera sobre todos los registros vivos de la tabla.
        Retorna tuplas de (RID, lista_de_valores).
        """
        if self.file_type == "heap":
            # HeapFile ya implementa un generator scan() que devuelve (RID, bytes_desempaquetados)
            yield from self.data_file.scan()
        else:
            # SequentialFile usa _iter_records() devolviendo (RID, Record)
            for rid, record in self.data_file._iter_records():
                if not record.deleted:
                    yield rid, record.params
                    
    def close(self):
        """
        Cierra y guarda los cambios del archivo.
        """
        if hasattr(self.data_file, "close"):
            self.data_file.close()