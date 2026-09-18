"""
External Sorting (k-way merge) para el ORDER BY.

Tiene dos "fases" separadas;
    - Leer la entrada en bloques que SI caben en memoria, ordenar cada bloque y
    escribirlo como un archivo temporario (run).
    - Conservar un min-heap con la cabeza de cada run y extraer el menor de
    todos repetidamente. La cantidad de runs que se mezclan a la vez es el "k"
    del k-way merge.
"""

import heapq
import os
import struct
import tempfile

DEFAULT_BUDGET = 1000

_TAG_INT = b"i"
_TAG_BOOL = b"b"
_TAG_FLOAT = b"f"
_TAG_STR = b"s"
_INT_FORMAT = ">q"
_FLOAT_FORMAT = ">d"
_UINT64_FORMAT = ">Q"

_UINT64_SIGN = 0x8000000000000000
_UINT64_MASK = 0xFFFFFFFFFFFFFFFF

_ENTRY_HEADER = ">II"

def _encode_int(value: int) -> bytes:
    payload = bytearray(struct.pack(_INT_FORMAT, value))
    payload[0] ^= 0x80
    return bytes(payload)


def _decode_int(data: bytes) -> int:
    payload = bytearray(data)
    payload[0] ^= 0x80
    return struct.unpack(_INT_FORMAT, bytes(payload))[0]


def _encode_float(value: float) -> bytes:
    bits = struct.unpack(_UINT64_FORMAT, struct.pack(_FLOAT_FORMAT, value))[0]
    if bits & _UINT64_SIGN:
        bits = (~bits) & _UINT64_MASK
    else:
        bits |= _UINT64_SIGN
    return struct.pack(_UINT64_FORMAT, bits)


def _decode_float(data: bytes) -> float:
    bits = struct.unpack(_UINT64_FORMAT, data)[0]
    if bits & _UINT64_SIGN:
        bits &= ~_UINT64_SIGN
    else:
        bits = (~bits) & _UINT64_MASK
    return struct.unpack(_FLOAT_FORMAT, struct.pack(_UINT64_FORMAT, bits))[0]


def encode_key(key) -> bytes:
    """
    Codifica una clave a bytes preservando el orden de los valores.
    """
    if isinstance(key, bool):
        return _TAG_BOOL + (b"\x01" if key else b"\x00")
    if isinstance(key, int):
        return _TAG_INT + _encode_int(key)
    if isinstance(key, float):
        return _TAG_FLOAT + _encode_float(key)
    if isinstance(key, str):
        return _TAG_STR + key.encode("utf-8")
    raise TypeError(
        f"clave de ordenamiento no soportada: {type(key).__name__} "
        "(soportadas: int, float, bool, str)"
    )


def decode_key(data: bytes):
    """
    Devuelve el valor original de una clave codificada con encode_key().
    """
    tag, payload = data[:1], data[1:]
    if tag == _TAG_INT:
        return _decode_int(payload)
    if tag == _TAG_BOOL:
        return payload == b"\x01"
    if tag == _TAG_FLOAT:
        return _decode_float(payload)
    if tag == _TAG_STR:
        return payload.decode("utf-8")
    raise ValueError(f"tag de clave desconocido: {tag!r}")


class _ReverseKey:
    """
    Envuelve una clave invirtiendo su comparacion.
    Permite que el min-heap del merge entregue la clave MAYOR primero:
    con reverse=True los runs se escriben descendentes y el heap los
    mezcla como si todos compararan al reves.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return self.value > other.value


class ExternalSorter:
    """
    ORDEN BY con External Sorting (k-way merge).
    Entrada: un iterable de pares (clave, value_bytes). Salida: un
    iterador con los pares ya ordenados por clave. Solo se retiene en
    memoria hasta `budget` items a la vez durante la generacion de runs
    y, durante el merge, un item por run (la cabeza del heap).
    """

    def __init__(self, budget: int = DEFAULT_BUDGET, reverse: bool = False,
                 run_dir: str = None):
        if budget < 1:
            raise ValueError(f"budget debe ser >= 1, se dio {budget}")
        self.budget = budget
        self.reverse = reverse
        self.run_dir = run_dir
        self._owns_dir = run_dir is None
        self._run_paths = []
        self._streams = []
        self.run_count = 0

    def _nuevo_run(self) -> str:
        if self.run_dir is None:
            self.run_dir = tempfile.mkdtemp(prefix="external_sort_")
            self._owns_dir = True
        fd, path = tempfile.mkstemp(dir=self.run_dir, suffix=".run")
        os.close(fd)
        return path

    @staticmethod
    def _write_entry(archivo, key_bytes: bytes, value_bytes: bytes):
        entry_len = 8 + len(key_bytes) + len(value_bytes)
        archivo.write(struct.pack(_ENTRY_HEADER, entry_len, len(key_bytes)))
        archivo.write(key_bytes)
        archivo.write(struct.pack(">I", len(value_bytes)))
        archivo.write(value_bytes)

    def _write_run(self, buffer) -> str:
        buffer.sort(key=lambda item: item[0], reverse=self.reverse)
        path = self._nuevo_run()
        self._run_paths.append(path)
        with open(path, "wb") as archivo:
            for clave, value_bytes in buffer:
                self._write_entry(archivo, encode_key(clave), value_bytes)
        self.run_count += 1
        return path

    def _iter_run(self, path):
        with open(path, "rb") as archivo:
            seq = 0
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

                yield key_bytes, seq, value_bytes
                seq += 1

    def _heap_key(self, key_bytes):
        return _ReverseKey(key_bytes) if self.reverse else key_bytes

    def _merge(self, run_paths):
        heap = []
        for index, path in enumerate(run_paths):
            stream = self._iter_run(path)
            self._streams.append(stream)
            try:
                key_bytes, seq, value_bytes = next(stream)
            except StopIteration:
                continue
            heap.append(
                (
                    self._heap_key(key_bytes),
                    seq, index,
                    key_bytes,
                    value_bytes,
                    stream
                )
            )
        heapq.heapify(heap)

        while heap:
            _, _, index, key_bytes, value_bytes, stream = heapq.heappop(heap)
            yield decode_key(key_bytes), value_bytes
            try:
                next_key, next_seq, next_value = next(stream)
            except StopIteration:
                stream.close()
                continue
            heapq.heappush(
                heap,
                (
                    self._heap_key(next_key),
                    next_seq,
                    index,
                    next_key,
                    next_value,
                    stream,
                ),
            )


    def sort(self, items):
        """
        Ordena un iterable de (clave, value_bytes) y rinde los pares
        ya ordenados, sin materializar el input completo.

        Si todo el input entra en `budget` items, el resultado sale de
        un solo sort en memoria (camino rapido, sin tocar disco). Si no,
        se generan runs a disco y se mezclan con el k-way merge.
        """
        run_paths = []
        buffer = []
        try:
            for item in items:
                buffer.append(item)
                if len(buffer) >= self.budget:
                    run_paths.append(self._write_run(buffer))
                    buffer = []

            if not run_paths:
                if not buffer:
                    return
                buffer.sort(key=lambda item: item[0], reverse=self.reverse)
                for item in buffer:
                    yield item
                return

            if buffer:
                run_paths.append(self._write_run(buffer))

            yield from self._merge(run_paths)
        finally:
            self._drop_runs()

    def cleanup(self):
        """Cierra streams y borra los runs a disco. Idempotente: se
        puede llamar desde el executor ademas del finally de sort()."""
        self._drop_runs()

    def _drop_runs(self, run_paths=None):
        for stream in self._streams:
            stream.close()
        self._streams = []

        if run_paths is None:
            run_paths = list(self._run_paths)
        for path in run_paths:
            if path in self._run_paths:
                self._run_paths.remove(path)
            if os.path.exists(path):
                os.remove(path)

        if self._owns_dir and self.run_dir is not None and os.path.isdir(
            self.run_dir
        ):
            try:
                os.rmdir(self.run_dir)
            except OSError:
                pass
            self.run_dir = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()