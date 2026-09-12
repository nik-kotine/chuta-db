import struct

# Esta clase se encarga de convertir los registros a binario
# De momento, soporta los siguientes tipos en un objeto: int, float y string.

class RecordPacker:
    def __init__(self, schema: list[str]):
        # ex: schema = ['int', 'float', 'string', 'float']
        self.schema = schema

    def record_encoder(self, values: list) -> bytes:
        if len(values) != len(self.schema):
            raise ValueError(f"Expected {len(self.schema)} values but received {len(values)}")

        num_fields = len(values)
        data_bytes = bytearray()
        offsets = []

        # Empaquetamos los datos segun el tipo definido en `schema`
        for value, type_ in zip(values, self.schema):
            if type_ == "int": 
                data_bytes.extend(struct.pack(">i", value)) # 4 bytes
            elif type_ == "float":
                data_bytes.extend(struct.pack(">f", value)) # 4 bytes
            elif type_ == "string":
                encoded = value.encode("utf-8")
                data_bytes.extend(encoded)
            else:
                raise ValueError(f"Unsupported type: {type_}")
            offsets.append(len(data_bytes))

        # Empaquetamos la cabecera del registro
        header_format = f">H {num_fields}H"
        header_bytes = struct.pack(header_format, num_fields, *offsets)

        return header_bytes + data_bytes

    def record_decoder(self, record_bytes: bytes):
        # Leemos la cantidad de campos
        num_fields = struct.unpack(">H", record_bytes[:2])[0]
        if num_fields != len(self.schema):
            raise ValueError(f"Expected {len(self.schema)} but received {num_fields}")

        # Los dos primeros bytes ocupados por la cant de fields. Posteriormente, la lista de offsets.
        header_size = 2 + (num_fields*2)
        offsets_format = f">{num_fields}H"
        offsets = struct.unpack(offsets_format, record_bytes[2:header_size])

        values = []
        data_start = header_size
        prev_offset =0

        for i, type_ in enumerate(self.schema):
            curr_offset = offsets[i]
            value_bytes = record_bytes[data_start + prev_offset: data_start+curr_offset]
            prev_offset = curr_offset

            if type_ == "int":
                values.append(struct.unpack(">i", value_bytes)[0])
            elif type_ == "float":
                values.append(struct.unpack(">f", value_bytes)[0])
            elif type_ == "string":
                values.append(value_bytes.decode("utf-8"))

        return values
 
if __name__ == "__main__":
    l = ['int', 'float', 'int','int','int','int','int','int','int','int','int','int','int','int','int','int']
    x = [5,3.5,1,1,1,1,1,1,1,1,1,1,1,1,1,1]

    r = RecordPacker(l)
    a = r.record_encoder(x)
    print(a)
    print(r.record_decoder(a))
