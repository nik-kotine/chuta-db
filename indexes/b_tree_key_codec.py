import struct

# Tope de bytes para una key ya codificada (incluye el tag de tipo).
# Con PAGE_SIZE=4096, esto garantiza que siempre entren varias
# entradas por pagina incluso en el peor caso (keys al maximo tamaño),
# igual que hacen los motores reales (Postgres corta ~2700 bytes,
# InnoDB ~767-3072 segun el motor de almacenamiento) -- sin un tope,
# una sola key gigante podria no entrar ni en una pagina.
MAX_KEY_SIZE = 512

_INT_FORMAT = ">q"
_TAG_INT = b"i"
_TAG_STR = b"s"


def encode_key(key) -> bytes:
    """
    Codifica una key (int o str) a bytes, con un tag de 1 byte que
    identifica el tipo para poder decodificarla despues.
    """
    if isinstance(key, str):
        encoded = _TAG_STR + key.encode("utf-8")
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
    """
    Decodifica bytes producidos por encode_key() de vuelta al valor
    original (int o str).
    """
    tag, payload = bytes(data[:1]), bytes(data[1:])

    if tag == _TAG_STR:
        return payload.decode("utf-8")
    if tag == _TAG_INT:
        return struct.unpack(_INT_FORMAT, payload)[0]

    raise ValueError(f"tag de key desconocido: {tag!r}")
