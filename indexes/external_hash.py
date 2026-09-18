import os
import struct
import tempfile

from indexes.external_sort import decode_key, encode_key

DEFAULT_BUCKETS = 16
_ENTRY_HEADER = ">II"
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_UINT64_MASK = 0xFFFFFFFFFFFFFFFF

def _hash_bytes(data: bytes) -> int:
    """
    Implementacion sencilla de FNV-1a para una funcion hash. Fuente:
    https://en.wikipedia.org/wiki/Fowler%E2%80%93Noll%E2%80%93Vo_hash_function
    """
    hash_value = _FNV_OFFSET
    for byte in data:
        hash_value ^= byte
        hash_value = (hash_value * _FNV_PRIME) & _UINT64_MASK
    return hash_value

def bucket_of(key, num_buckets) -> int:
    if num_buckets < 1:
        raise ValueError(f"num_buckets debe ser >= 1, se dio {num_buckets}")
    return _hash_bytes(encode_key(key)) % num_buckets

class Partitions:

    def __init__(self, num_buckets: int, paths: list, stream_registry: list):
        self.num_buckets = num_buckets
        self._paths = paths
        self._streams = []
        self._stream_registry = stream_registry

    def bucket(self, b: int):
        if b < 0 or b >= self.num_buckets:
            raise IndexError(
                f"particion {b} fuera de rango (num_buckets={self.num_buckets})"
            )
        stream = self._iter_entries(self._paths[b])
        self._streams.append(stream)
        self._stream_registry.append(stream)
        return stream

    def buckets(self):
        for b in range(self.num_buckets):
            yield self.bucket(b)

    def _iter_entries(self, path):
        with open(path, "rb") as archivo:
            while True:
                cabecera = archivo.read(4)
                if not cabecera:
                    return
                entry_len = struct.unpack(">I", cabecera)[0]
                entry = archivo.read(entry_len)

                key_len = struct.unpack_from(">I", entry, 0)[0]
                key_bytes = entry[4:4 + key_len]

                value_len = struct.unpack_from(">I", entry, 4 + key_len)[0]
                value_bytes = entry[8 + key_len:8 + key_len + value_len]

                yield decode_key(key_bytes), value_bytes

    def cleanup(self):
        for stream in self._streams:
            stream.close()
        self._streams = []
        for path in self._paths:
            if os.path.exists(path):
                os.remove(path)
        self._paths = []


class ExternalHasher:

    def __init__(self, num_buckets: int = DEFAULT_BUCKETS, run_dir: str = None):
        if num_buckets < 1:
            raise ValueError(f"num_buckets debe ser >= 1, se dio {num_buckets}")
        self.num_buckets = num_buckets
        self.run_dir = run_dir
        self._owns_dir = run_dir is None
        self._paths = []
        self._streams = []

    @staticmethod
    def _write_entry(archivo, key_bytes: bytes, value_bytes: bytes):
        entry_len = 8 + len(key_bytes) + len(value_bytes)
        archivo.write(struct.pack(_ENTRY_HEADER, entry_len, len(key_bytes)))
        archivo.write(key_bytes)
        archivo.write(struct.pack(">I", len(value_bytes)))
        archivo.write(value_bytes)

    def _nueva_particion(self) -> str:
        if self.run_dir is None:
            self.run_dir = tempfile.mkdtemp(prefix="external_hash_")
            self._owns_dir = True
        fd, path = tempfile.mkstemp(dir=self.run_dir, suffix=".part")
        os.close(fd)
        self._paths.append(path)
        return path

    def partition(self, items) -> Partitions:
        paths = [self._nueva_particion() for _ in range(self.num_buckets)]
        handles = []
        try:
            for path in paths:
                handles.append(open(path, "wb"))
            for clave, value_bytes in items:
                key_bytes = encode_key(clave)
                b = _hash_bytes(key_bytes) % self.num_buckets
                self._write_entry(handles[b], key_bytes, value_bytes)
        finally:
            for h in handles:
                h.close()
        return Partitions(self.num_buckets, paths, self._streams)

    def hash_join(self, left_items, right_items):
        izquierda = self.partition(left_items)
        derecha = self.partition(right_items)
        try:
            for b in range(self.num_buckets):
                build = {}
                for clave, left_bytes in izquierda.bucket(b):
                    build.setdefault(clave, []).append(left_bytes)

                for clave, right_bytes in derecha.bucket(b):
                    coincidencias = build.get(clave)
                    if coincidencias:
                        for left_bytes in coincidencias:
                            yield clave, left_bytes, right_bytes
        finally:
            izquierda.cleanup()
            derecha.cleanup()

    def group_by(self, items, acumulador_nuevo, acumular):
        particiones = self.partition(items)
        _vacio = object()
        try:
            for b in range(self.num_buckets):
                grupos = {}
                for clave, value_bytes in particiones.bucket(b):
                    acc = grupos.get(clave, _vacio)
                    if acc is _vacio:
                        acc = acumulador_nuevo()
                    grupos[clave] = acumular(acc, value_bytes)
                for clave, acc in grupos.items():
                    yield clave, acc
        finally:
            particiones.cleanup()

    def cleanup(self):
        for stream in self._streams:
            stream.close()
        self._streams = []

        for path in self._paths:
            if os.path.exists(path):
                os.remove(path)
        self._paths = []

        if self._owns_dir and self.run_dir is not None and os.path.isdir(self.run_dir):
            try:
                os.rmdir(self.run_dir)
            except OSError:
                pass
            self.run_dir = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()