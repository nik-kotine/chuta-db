import struct

MAX_KEY_SIZE = 512
_INT_FORMAT = ">q"
_TAG_INT = b"i"
_TAG_STR = b"s"
_TAG_FLOAT = b"f"
_TAG_BOOL = b"b"

def encode_key(key) -> bytes:
    if isinstance(key, str):
        encoded = _TAG_STR + key.encode("utf-8")
    elif isinstance(key, bool):
        encoded = _TAG_BOOL + struct.pack(">B", int(key))
    elif isinstance(key, float):
        encoded = _TAG_FLOAT + struct.pack(">d", key)
    elif isinstance(key, int):
        encoded = _TAG_INT + struct.pack(_INT_FORMAT, key)
    else:
        raise TypeError(f"tipo de key no soportado: {type(key).__name__}")

    if len(encoded) > MAX_KEY_SIZE:
        raise ValueError(
            f"key codificada ({len(encoded)} bytes) excede MAX_KEY_SIZE={MAX_KEY_SIZE}"
        )

    return encoded


def decode_key(data: bytes):
    tag, payload = bytes(data[:1]), bytes(data[1:])

    if tag == _TAG_STR:
        return payload.decode("utf-8")
    if tag == _TAG_INT:
        return struct.unpack(_INT_FORMAT, payload)[0]
    if tag == _TAG_FLOAT:
        return struct.unpack(">d", payload)[0]
    if tag == _TAG_BOOL:
        return bool(payload)

    raise ValueError(f"tag de key desconocido: {tag!r}")
