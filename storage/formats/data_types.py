import struct

FIXED_DATA_TYPES = {
    # Alias primitivos / cortos
    "int": [">i", 4],
    "float": [">f", 4],
    "bool": [">?", 1],
    # Tipos SQL / PostgreSQL estándar
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
    "char": [">b", 1],
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

STRING_DATA_TYPE_STARTS = ["bit", "char", "varchar"]
STRING_DATA_TYPES = ["text", "bytea", "varbit", "json", "jsonb", "xml", "string", "str"]


def return_format(primitive_type: str) -> list:
    if primitive_type is None or primitive_type == "":
        raise TypeError("Type is null")

    primitive_type_clean = primitive_type.strip().lower()

    if primitive_type_clean in FIXED_DATA_TYPES:
        return [FIXED_DATA_TYPES[primitive_type_clean][0], FIXED_DATA_TYPES[primitive_type_clean][1]]

    if primitive_type_clean in STRING_DATA_TYPES:
        return ["s", -1]

    textlist = primitive_type_clean.split("(")
    if (
        primitive_type_clean[-1] == ")"
        and len(textlist) == 2
        and textlist[0] in STRING_DATA_TYPE_STARTS
        and textlist[1][:-1].isnumeric()
        and int(textlist[1][:-1]) > 0
    ):
        length = int(textlist[1][:-1])
        if textlist[0] in ("char", "varchar"):
            return [f"{length}s", length]
        if textlist[0] == "bit":
            num_bytes = (length - 1) // 8 + 1
            return [f"{num_bytes}s", num_bytes]

    raise TypeError(f"Non-existing type: {primitive_type}")