import struct
from storage.rid import DELETED_FORMAT, RID_FORMAT
from storage.formats.data_types import field_value_count, return_format
from storage.formats.serializers.record_serializer import RecordSerializer

class FixedLengthRecordSerializer(RecordSerializer):

    def __init__(self, record_format: list[str]):
        super().__init__(record_format)
        self.field_formats = [return_format(t) for t in record_format]
        # Cuantos valores ocupa cada campo: 1 para la mayoria, 2 para
        # "point", 3 para "interval". Permite aplanar al empaquetar y
        # reagrupar al desempaquetar.
        self.field_counts = [field_value_count(fmt) for fmt, _ in self.field_formats]
        type_codes = []
        self.record_size = 0
        
        for fmt, size in self.field_formats:
            if size == -1:
                raise RuntimeError("non fixed-length format")
            
            fmt_clean = fmt.lstrip("><=@!")
            type_codes.append(fmt_clean)
            self.record_size += size

        self.record_format_str = ">" + "".join(type_codes)
        
        safe_rid_fmt = RID_FORMAT.replace(">", "")
        safe_del_fmt = DELETED_FORMAT.replace(">", "")
        
        self.slot_format = self.record_format_str + safe_rid_fmt + safe_del_fmt
        self.slot_size = struct.calcsize(self.slot_format)

    def _prepare_field(self, field_format: str, value):
        """
        Prepara un campo para ser empaquetado. Los campos string se
        codifican a UTF-8 (struct exige bytes para 's').
        """
        if field_format[-1] == "s":
            if isinstance(value, str):
                return value.encode("utf-8")
        return value

    def _prepare(self, params) -> list:
        """
        Aplana los campos a la lista de valores que espera struct.pack.
        Un campo multi-valor ("point") aporta sus componentes por separado.
        """
        result = []
        for index, (fmt, _) in enumerate(self.field_formats):
            value = params[index]
            if self.field_counts[index] > 1:
                if not isinstance(value, (tuple, list)):
                    raise TypeError(
                        f"el campo {index} ({self.record_format[index]}) espera "
                        f"{self.field_counts[index]} valores, se recibio {value!r}"
                    )
                if len(value) != self.field_counts[index]:
                    raise ValueError(
                        f"el campo {index} ({self.record_format[index]}) espera "
                        f"{self.field_counts[index]} valores, se recibieron {len(value)}"
                    )
                result.extend(value)
            else:
                result.append(self._prepare_field(fmt, value))
        return result

    def get_size_of(self, params) -> int:
        """
        Todos los registros de este serializador ocupan exactamente lo mismo,
        así que retornamos record_size.
        """
        return self.record_size

    def serialize(self, params) -> bytes:
        """
        Serializa los datos proporcionados por el usuario.
        """
        return struct.pack(self.record_format_str, *self._prepare(params))

    def deserialize(self, data: bytes):
        """
        Deserializa los datos proporcionados por el usuario. Delimita el tamaño
        del búfer y decodifica manteniendo los bytes de padding requeridos por el test.
        """
        record_bytes = data[:self.record_size]
        unpacked = struct.unpack(self.record_format_str, record_bytes)

        result = []
        pos = 0
        for (fmt, _), count in zip(self.field_formats, self.field_counts):
            if count > 1:
                # campo multi-valor: se devuelve como tupla
                result.append(tuple(unpacked[pos:pos + count]))
                pos += count
                continue

            value = unpacked[pos]
            pos += 1
            if fmt[-1] == "s":
                if isinstance(value, bytes):
                    result.append(value.decode("utf-8").rstrip("\x00"))
                else:
                    result.append(value)
            else:
                result.append(value)

        return tuple(result)

    def pack_slot(self, params, next_rid, deleted):
        """
        Prepara todos los valores que se escriben en un slot de la página:
        los campos del registro + next_rid + el booleano de eliminado.
        """
        next_rid = (-1, -1) if next_rid is None else next_rid
        return tuple(self._prepare(params)) + next_rid + (deleted,)