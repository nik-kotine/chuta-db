import struct
from storage.rid import DELETED_SIZE, RID_SIZE
from storage.formats.data_types import return_format
from storage.formats.serializers.record_serializer import RecordSerializer

class VariableLengthRecordSerializer(RecordSerializer):

    def get_size_of(self, params) -> int:
        record_bytes = self.serialize(params)
        return len(record_bytes)

    def serialize(self, params) -> bytes:
        output = bytearray()
        for index in range(0, len(params)):
            fmt, size = return_format(self.record_format[index])
            clean_fmt = fmt.lstrip("><=@!")
            
            if size == -1:
                # Campo de longitud variable (cadena/varchar/text)
                val_str = str(params[index])
                attencoded = val_str.encode("utf-8")
                output += struct.pack(">i", len(attencoded)) + attencoded
            elif clean_fmt.endswith("s"):
                # Campo de cadena de longitud fija
                length = size
                val_bytes = params[index].encode("utf-8") if isinstance(params[index], str) else params[index]
                fmt_str = f">{length}s"
                output += struct.pack(fmt_str, val_bytes)
            else:
                # Campo numérico o tipo fijo (forzando big-endian)
                fmt_str = ">" + clean_fmt
                output += struct.pack(fmt_str, params[index])
                
        return bytes(output)

    def deserialize(self, data: bytes):
        unpacking_index = 0
        output = []
        for index in range(0, len(self.record_format)):
            fmt, size = return_format(self.record_format[index])
            clean_fmt = fmt.lstrip("><=@!")
            
            if size == -1:
                # Campo de longitud variable
                field_length = struct.unpack(">i", data[unpacking_index:unpacking_index + 4])[0]
                unpacking_index += 4
                val_str = data[unpacking_index:unpacking_index + field_length].decode("utf-8")
                output.append(val_str)
                unpacking_index += field_length
            elif clean_fmt.endswith("s"):
                # Campo de cadena de longitud fija
                length = size
                fmt_str = f">{length}s"
                val_bytes = struct.unpack(fmt_str, data[unpacking_index:unpacking_index + length])[0]
                output.append(val_bytes.decode("utf-8").rstrip("\x00"))
                unpacking_index += length
            else:
                # Campo numérico o tipo fijo
                record_size = size
                fmt_str = ">" + clean_fmt
                record_value = struct.unpack(
                    fmt_str,
                    data[unpacking_index:unpacking_index + record_size],
                )[0]
                output.append(record_value)
                unpacking_index += record_size

        return output