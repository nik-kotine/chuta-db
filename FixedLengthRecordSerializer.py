import struct

from RecordSerializer import (
    DELETED_FORMAT,
    DELETED_SIZE,
    RID_FORMAT,
    RID_SIZE,
    RecordSerializer,
    return_format,
)

class FixedLengthRecordSerializer(RecordSerializer):

    def __init__(self, record_format: list[str]):
        super().__init__(record_format)
        self.field_formats = [return_format(t) for t in record_format]
        type_codes = []
        self.record_size = 0
        
        for fmt, size in self.field_formats:
            if size == -1:
                raise RuntimeError("non fixed-length format")
            
            if fmt.startswith(">"):
                fmt = fmt[1:]
            type_codes.append(fmt)
            self.record_size += size

        self.record_format_str = ">" + "".join(type_codes)
        self.slot_format = \
            self.record_format_str + RID_FORMAT[1:] + DELETED_FORMAT[1:]
        self.slot_size = struct.calcsize(self.slot_format)

    def _prepare_field(self, field_format: str, value):
        """
        Prepara un campo para ser empaquetado. Los campos string se
        codifican a UTF-8 (struct exige bytes para 's').
        """
        if field_format[-1] == ("s"):
            if isinstance(value, str):
                return value.encode("utf-8")
        return value

    def _prepare(self, params) -> list:
        result = []
        for index, (fmt, _) in enumerate(self.field_formats):
            result.append(self._prepare_field(fmt, params[index]))
        return result

    def get_size_of(self, params) -> int:
        """
        Todos los registros de este serializador ocupan exactamente lo mismo,
        asi que retornamos record_size.
        """
        return self.record_size

    def serialize(self, params) -> bytes:
        """
        Serializa los datos proporcionados por el usuario.
        """
        return struct.pack(self.record_format_str, *self._prepare(params))

    def deserialize(self, data: bytes):
        """
        Deserializa los datos proporcionados por el usuario. Los campos
        string se decodifican de vuelta a str.
        """
        unpacked = struct.unpack(self.record_format_str, data)

        result = []
        for (fmt, _), value in zip(self.field_formats, unpacked):
            if fmt[-1] == "s":
                result.append(value.decode("utf-8"))
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