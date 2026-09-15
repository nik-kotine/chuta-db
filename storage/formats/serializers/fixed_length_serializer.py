import struct
from storage.formats.serializers.record_serializer import RecordSerializer
from storage.formats.data_types import return_format
from storage.rid import RID_SIZE, DELETED_SIZE

class FixedLengthRecordSerializer(RecordSerializer):
    def __init__(self, record_format: list[str] | str):
        if isinstance(record_format, str):
            record_format = [record_format]
            
        super().__init__(record_format)

        struct_formats = []
        for token in record_format:
            fmt, size = return_format(token)
            if size == -1:
                raise ValueError(f"Variable-length type '{token}' is not allowed in FixedLengthRecordSerializer")
            if fmt.startswith(">"):
                fmt = fmt[1:]
            struct_formats.append(fmt)

        self.struct_format = ">" + "".join(struct_formats)
        self.record_size = struct.calcsize(self.struct_format)
        
        # Calculamos el tamaño total del slot (Datos + RID + Deleted)
        self.slot_size = self.record_size + RID_SIZE + DELETED_SIZE

    def serialize(self, params) -> bytes:
        processed_params = []
        for val, token in zip(params, self.record_format):
            if isinstance(val, str):
                processed_params.append(val.encode("utf-8"))
            else:
                processed_params.append(val)
        return struct.pack(self.struct_format, *processed_params)

    def deserialize(self, data: bytes):
        unpacked = struct.unpack(self.struct_format, data)
        result = []
        for val, token in zip(unpacked, self.record_format):
            if isinstance(val, bytes):
                result.append(val.decode("utf-8").rstrip("\x00"))
            else:
                result.append(val)
        return tuple(result)

    def get_size_of(self, params=None) -> int:
        return self.record_size + RID_SIZE + DELETED_SIZE