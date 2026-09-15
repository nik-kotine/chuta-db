import struct
from storage.formats.serializers.record_serializer import RecordSerializer
from storage.formats.data_types import return_format
from storage.rid import RID_SIZE, DELETED_SIZE

class VariableLengthRecordSerializer(RecordSerializer):
    def __init__(self, record_format: list[str]):
        super().__init__(record_format)
        self.parsed_formats = [return_format(token) for token in record_format]

    def serialize(self, params) -> bytes:
        packed = bytearray()
        for val, (fmt, size) in zip(params, self.parsed_formats):
            if size == -1:
                # Campo de longitud ilimitada (prefijado por su tamaño en un entero de 4 bytes)
                encoded = val.encode("utf-8") if isinstance(val, str) else bytes(val)
                packed.extend(struct.pack(f">I{len(encoded)}s", len(encoded), encoded))
            else:
                clean_fmt = fmt if fmt.startswith(">") else ">" + fmt
                if isinstance(val, str):
                    val_bytes = val.encode("utf-8")
                    packed.extend(struct.pack(clean_fmt, val_bytes))
                else:
                    packed.extend(struct.pack(clean_fmt, val))
        return bytes(packed)

    def deserialize(self, data: bytes):
        params = []
        offset = 0
        for fmt, size in self.parsed_formats:
            if size == -1:
                length = struct.unpack_from(">I", data, offset)[0]
                offset += 4
                raw_val = struct.unpack_from(f">{length}s", data, offset)[0]
                offset += length
                params.append(raw_val.decode("utf-8"))
            else:
                clean_fmt = fmt if fmt.startswith(">") else ">" + fmt
                field_size = struct.calcsize(clean_fmt)
                raw_val = struct.unpack_from(clean_fmt, data, offset)[0]
                offset += field_size
                if isinstance(raw_val, bytes):
                    params.append(raw_val.decode("utf-8").rstrip("\x00"))
                else:
                    params.append(raw_val)
        return tuple(params)

    def get_size_of(self, params) -> int:
        return len(self.serialize(params)) + RID_SIZE + DELETED_SIZE