import struct
from storage.rid import DELETED_SIZE, RID_SIZE
from storage.formats.data_types import field_value_count, return_format
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
                val_str = str(params[index])
                attencoded = val_str.encode("utf-8")
                output += struct.pack(">i", len(attencoded)) + attencoded
            elif clean_fmt.endswith("s"):
                length = size
                val_bytes = params[index].encode("utf-8") if isinstance(params[index], str) else params[index]
                fmt_str = f">{length}s"
                output += struct.pack(fmt_str, val_bytes)
            else:
                fmt_str = ">" + clean_fmt
                value = params[index]
                if field_value_count(fmt) > 1:
                    # campo multi-valor ("point"): se aplana en sus componentes
                    output += struct.pack(fmt_str, *value)
                else:
                    output += struct.pack(fmt_str, value)

        return bytes(output)

    def deserialize(self, data: bytes):
        unpacking_index = 0
        output = []
        for index in range(0, len(self.record_format)):
            fmt, size = return_format(self.record_format[index])
            clean_fmt = fmt.lstrip("><=@!")
            
            if size == -1:
                field_length = struct.unpack(">i", data[unpacking_index:unpacking_index + 4])[0]
                unpacking_index += 4
                val_str = data[unpacking_index:unpacking_index + field_length].decode("utf-8")
                output.append(val_str)
                unpacking_index += field_length
            elif clean_fmt.endswith("s"):
                length = size
                fmt_str = f">{length}s"
                val_bytes = struct.unpack(fmt_str, data[unpacking_index:unpacking_index + length])[0]
                output.append(val_bytes.decode("utf-8").rstrip("\x00"))
                unpacking_index += length
            else:
                record_size = size
                fmt_str = ">" + clean_fmt
                valores = struct.unpack(
                    fmt_str,
                    data[unpacking_index:unpacking_index + record_size],
                )
                if field_value_count(fmt) > 1:
                    output.append(tuple(valores))   # campo multi-valor
                else:
                    output.append(valores[0])
                unpacking_index += record_size

        return output