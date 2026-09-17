import struct
from storage.formats.record_format import RecordFormat
from storage.rid import RID_SIZE, DELETED_SIZE

class RecordSerializer(RecordFormat):
    """
    Serializador abstracto que combina la interfaz RecordFormat (encode/decode)
    con las operaciones requeridas por las páginas y SequentialFile.
    """

    def __init__(self, record_format: list[str]):
        self.record_format = record_format

    def get_size_of(self, params) -> int:
        """
        Retorna el tamaño en bytes que ocupará el registro en disco.
        """
        raise NotImplementedError

    def serialize(self, params) -> bytes:
        raise NotImplementedError

    def deserialize(self, data: bytes):
        raise NotImplementedError

    def encode(self, values: list) -> bytes:
        return self.serialize(values)

    def decode(self, data: bytes) -> list:
        return list(self.deserialize(data))