import struct
from storage.formats.record_format import RecordFormat
"""
rid: (page_id, slot_id)
page_id representa en que pagina se encuentra (0+, 0 siendo overflow) y
slot_id el numero de registro dentro de esa pagina. (-1, -1) es un registro
nulo
"""
RID_FORMAT = "ii"
RID_SIZE = struct.calcsize(RID_FORMAT)

"""
deleted: bool
Representa si el registro fue marcado como eliminado
"""
DELETED_FORMAT = "?"
DELETED_SIZE = struct.calcsize(DELETED_FORMAT)

class Record:
    def __init__(self, params, next_rid=None, deleted=False):
        self.params = params
        self.next_rid = next_rid
        self.deleted = deleted

class FixedLengthRecordSerializer(RecordFormat):
    def __init__(self, record_format: str):
        self.record_format = record_format
        self.record_size = struct.calcsize(record_format)
        self.slot_format = self.record_format + RID_FORMAT + DELETED_FORMAT
        self.slot_size = struct.calcsize(self.slot_format)

    def serialize(self, params) -> bytes:
        """
        Serializa los datos proporcionados por el usuario.
        """
        return struct.pack(self.record_format, *params)

    def deserialize(self, data: bytes):
        """
        Deserializa los datos proporcionados por el usuario.
        """
        return struct.unpack(self.record_format, data)

    def encode(self, values: list) -> bytes:
        return self.serialize(values)

    def decode(self, data: bytes) -> list:
        return list(self.deserialize(data))


