import struct

"""
Serializador base y formatos binarios compartidos.

Todos los registros, sea cual sea su longitud (fija o variable), guardan al
final del espacio que ocupan el RID del registro siguiente de la cadena
logica (next_rid) y un booleano que indica si fueron marcados como
eliminados. Esos dos campos se declaran aca para que la página y los dos
serializadores usen exactamente la misma representacion.

Se usa big-endian (">") en todo el archivo. # this came from variable length
"""

# this came from variable length
RID_FORMAT = ">ii"
RID_SIZE = struct.calcsize(RID_FORMAT)

# this came from variable length
DELETED_FORMAT = ">?"
DELETED_SIZE = struct.calcsize(DELETED_FORMAT)

# this came from variable length
SLOT_FORMAT = ">ii"
SLOT_SIZE = struct.calcsize(SLOT_FORMAT)

# ---------------------------------------------------------------------------
# Tipos de dato y traduccion de tokens -> struct.
# this came from variable length (era un metodo estatico dentro de
# VariableLengthRecordSerializer y se movio aqui para que lo compartan ambos
# serializadores y el SequentialFile)
# ---------------------------------------------------------------------------

FIXED_DATA_TYPES = {
    "smallint": [">h", 2],
    "int2": [">h", 2],
    "integer": [">i", 4],
    "int4": [">i", 4],
    "bigint": [">q", 8],
    "int8": [">q", 8],
    "real": [">f", 4],
    "float4": [">f", 4],
    "double precision": [">d", 8],
    "float8": [">d", 8],
    "boolean": [">?", 1],
    "char": [">b", 1],  # o ">B",
    "oid": [">I", 4],
    "xid": [">I", 4],
    "cid": [">I", 4],
    "date": [">i", 4],
    "timestamp": [">q", 8],
    "timestampz": [">q", 8],
    "time": [">q", 8],
    "timetz": [">ql", 12],
    "interval": [">qii", 16],
    "money": [">q", 8],
    "uuid": ["16s", 16],
    "name": ["64s", 64],
}

# this came from variable length
STRING_DATA_TYPE_STARTS = ["bit", "char", "varchar"]  # FALTA NUMERIC

# this came from variable length
STRING_DATA_TYPES = ["text", "bytea", "varbit", "json", "jsonb", "xml"]


# this came from variable length
def return_format(primitive_type: str) -> list:
    """
    Por cada tipo retorna [formato de struct, tamaño en bytes].
    Un tamaño de -1 significa que el campo es de longitud ilimitada
    (ej: text) y se serializa con un prefijo de longitud.
    """
    if primitive_type is None or primitive_type == "":
        raise TypeError("Type is null")
    if primitive_type in FIXED_DATA_TYPES.keys():
        return [FIXED_DATA_TYPES[primitive_type][0], FIXED_DATA_TYPES[primitive_type][1]]
    if primitive_type in STRING_DATA_TYPES:
        return ["s", -1]
    textlist = primitive_type.split("(")
    if (
        primitive_type[-1] == ")"
        and len(textlist) == 2
        and textlist[0] in STRING_DATA_TYPE_STARTS
        and textlist[1][:-1].isnumeric()
        and int(textlist[1][:-1]) > 0
    ):
        if textlist[0] == "char" or textlist[0] == "varchar":
            return [f"{textlist[1][:-1]}s", int(textlist[1][:-1])]
        if textlist[0] == "bit":
            return [f"{(int(textlist[1][:-1]) - 1) // 8 + 1}s", (int(textlist[1][:-1]) - 1) // 8 + 1]
    raise TypeError("Non-existing type")


class RecordSerializer:
    """
    Serializador abstracto: define la interfaz comun que usan
    FixedLengthRecordSerializer y VariableLengthRecordSerializer.
    # this is new
    """

    def __init__(self, record_format: list[str]):
        self.record_format = record_format

    def get_size_of(self, params) -> int:
        """
        Retorna el tamaño en bytes que ocupara el registro (incluyendo el RID
        del siguiente y el booleano de eliminado).
        """
        raise NotImplementedError

    def serialize(self, params) -> bytes:
        """
        Convierte los campos proporcionados por el usuario a binario.
        """
        raise NotImplementedError

    def deserialize(self, data: bytes):
        """
        Convierte el binario de un registro de vuelta a sus campos.
        """
        raise NotImplementedError