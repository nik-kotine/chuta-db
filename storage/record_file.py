from abc import ABC, abstractmethod
from storage.rid import RID

class RecordFile:
    """
    clase abstracta que funciona como interfaz para heap_file y sequential_file
    """
    @abstractmethod
    def insert(self,values) -> RID:
        """ Inserta un registro y devuelve su RID """
        pass

    @abstractmethod
    def fetch(self, rid: RID):
        """ Recupera un registro dado su RID """
        pass

    @abstractmethod
    def delete(self, rid: RID) -> bool:
        """ Elimina lógicamente un registro dado su RID """
        pass

    @abstractmethod
    def scan(self):
        """ Recorre todos los registros válidos del archivo """
        pass

    @abstractmethod
    def reorganize(self):
        """ Compacta y recupera espacio muerto """
        pass

    @abstractmethod
    def close(self):
        """ Persiste cambios y cierra el archivo """
        pass

