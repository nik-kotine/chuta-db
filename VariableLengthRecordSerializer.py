import struct

from RecordSerializer import (
    DELETED_SIZE,
    RID_SIZE,
    RecordSerializer,
    return_format,
)

class VariableLengthRecordSerializer(RecordSerializer):

    def get_size_of(self, params) -> int:
        record_bytes = self.serialize(params)
        return len(record_bytes) + RID_SIZE + DELETED_SIZE

    def serialize(self, params) -> bytes:
        output = bytearray()
        for index in range(0, len(params)):
            struct_format_tuple = return_format(self.record_format[index])
            if struct_format_tuple[1] == -1:
                if struct_format_tuple[0] == "s":
                    attencoded = params[index].encode("utf-8")
                    output += struct.pack(">i", len(attencoded)) + attencoded
            elif struct_format_tuple[0][-1] == "s" and struct_format_tuple[0][:-1].isnumeric():
                length = struct_format_tuple[1]
                attencoded = params[index].encode("utf-8")
                if len(attencoded) > length:
                    attencoded = attencoded[0:length]
                else:
                    padded = bytearray(length)
                    padded[0:len(attencoded)] = attencoded
                    attencoded = padded
                output += attencoded
            else:
                output += struct.pack(struct_format_tuple[0], params[index])
        return output

    def deserialize(self, data: bytes):
        unpacking_index = 0
        output = []
        for index in range(0, len(self.record_format)):
            struct_format_tuple = return_format(self.record_format[index])
            if struct_format_tuple[1] == -1:
                field_length = struct.unpack(">i", data[unpacking_index:unpacking_index + 4])[0]
                unpacking_index += 4
                if struct_format_tuple[0] == "s":
                    output.append(
                        data[unpacking_index:unpacking_index + field_length].decode("utf-8"))
                unpacking_index += field_length
            elif struct_format_tuple[0][-1] == "s" and struct_format_tuple[0][:-1].isnumeric():
                length = struct_format_tuple[1]
                output.append(
                    data[unpacking_index:unpacking_index + length].decode("utf-8"))
                unpacking_index += length
            else:
                record_size = struct_format_tuple[1]
                record_value = struct.unpack(
                    struct_format_tuple[0],
                    data[unpacking_index:unpacking_index + record_size],
                )[0]
                output.append(record_value)
                unpacking_index += record_size
        return output