import struct
from storage.formats.record_format import RecordFormat
from storage.formats.data_types import return_format

class RecordPacker(RecordFormat):
    """
    Empaquetador de registros con cabecera de offsets para HeapFile/SlottedPage.
    Soporta tanto la interfaz RecordFormat (encode/decode) como los alias 
    record_encoder/record_decoder requeridos por la suite de pruebas.
    """
    def __init__(self, schema: list[str]):
        self.schema = schema
        self.parsed_schema = [return_format(token) for token in schema]

    def record_encoder(self, values: list) -> bytes:
        if len(values) != len(self.schema):
            raise ValueError(f"Expected {len(self.schema)} values but received {len(values)}")

        num_fields = len(values)
        data_bytes = bytearray()
        offsets = []

        for val, (fmt, size) in zip(values, self.parsed_schema):
            if size == -1:  # Longitud variable/ilimitada (ej. text, string)
                encoded = val.encode("utf-8") if isinstance(val, str) else bytes(val)
                data_bytes.extend(encoded)
            elif "s" in fmt:  # Cadenas de tamaño acotado (ej. varchar(20))
                encoded = val.encode("utf-8") if isinstance(val, str) else bytes(val)
                padded_encoded = encoded.ljust(size, b"\x00")
                data_bytes.extend(struct.pack(f">{size}s", padded_encoded))
            else:  # Tipos fijos (int, float, boolean, etc.)
                clean_fmt = fmt if fmt.startswith(">") else ">" + fmt
                data_bytes.extend(struct.pack(clean_fmt, val))
            
            offsets.append(len(data_bytes))

        # Cabecera del registro: [num_fields (2 bytes)] + [offsets de campos (2 bytes c/u)]
        header_format = f">H {num_fields}H"
        header_bytes = struct.pack(header_format, num_fields, *offsets)

        return header_bytes + data_bytes

    def record_decoder(self, data: bytes) -> list:
        num_fields = struct.unpack_from(">H", data, 0)[0]
        if num_fields != len(self.schema):
            raise ValueError(f"Expected {len(self.schema)} fields in schema but received {num_fields}")

        header_size = 2 + (num_fields * 2)
        offsets = struct.unpack_from(f">{num_fields}H", data, 2)

        values = []
        data_start = header_size
        prev_offset = 0

        for i, (fmt, size) in enumerate(self.parsed_schema):
            curr_offset = offsets[i]
            value_bytes = data[data_start + prev_offset : data_start + curr_offset]
            prev_offset = curr_offset

            if size == -1 or "s" in fmt:
                values.append(value_bytes.decode("utf-8").rstrip("\x00"))
            else:
                clean_fmt = fmt if fmt.startswith(">") else ">" + fmt
                values.append(struct.unpack(clean_fmt, value_bytes)[0])

        return values

    def encode(self, values: list) -> bytes:
        return self.record_encoder(values)

    def decode(self, data: bytes) -> list:
        return self.record_decoder(data)