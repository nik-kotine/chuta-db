from abc import ABC, abstractmethod

class RecordFormat(ABC):
    @abstractmethod
    def encode(self, values: list) -> bytes:
        """Convierte una lista de valores en bytes."""
        pass

    @abstractmethod
    def decode(self, data: bytes) -> list:
        """Convierte bytes de vuelta a una lista de valores."""
        pass