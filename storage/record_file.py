from abc import ABC, abstractmethod
from storage.rid import RID

# clase abstracta que funciona como interfaz para heap_file y sequential_file

class RecordFile:
    @abstractmethod
    def insert(self,values) -> RID:
        pass

    @abstractmethod
    def fetch(self, rid: RID):
        pass

    @abstractmethod
    def delete(self, rid: RID) -> bool:
        pass

    @abstractmethod
    def scan(self):
        pass

    @abstractmethod
    def reorganize(self):
        pass

    @abstractmethod
    def close(self):
        pass

