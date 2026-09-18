"""
External Hashing (Grace Hash Join / hash aggregate) para el GROUP BY y el JOIN.

En las bases de datos el hash externo resuelve dos problemas con la
misma idea: PARTICIONAR por la clave.

  1. JOIN: en vez de anidar los loops sobre dos relaciones enteras, se
     particionan AMBAS por el hash de la clave de join. Dos filas que
     matchean tienen la misma clave, asi que caen en la misma particion.
     Despues se procesa un par de particiones a la vez: se arma una
     tabla hash en memoria sobre la izquierda (build) y se sondea con la
     derecha (probe). Es el Grace Hash Join clasico.
  2. GROUP BY: se particiona la tabla por el hash de la clave de grupo y,
     por cada particion, se acumulan en memoria los grupos con un dict
     clave -> acumulador. Cada grupo completa vive en una sola
     particion, asi que un dict por particion alcanza.

En ambos casos la memoria nunca ve la relacion completa: solo un item a
la vez al particionar y, despues, una particion entera (o un par) en
memoria por paso. La cantidad de particiones decide cuanto cabe en
memoria (mas particiones => particiones mas chicas).

El modulo no sabe de tablas ni serializadores: los items son pares
(clave, value_bytes) igual que en external_sort.py. La clave se
canoniza con encode_key() (int, float, bool, str) y el hash FNV-1a de
64 bits es estable entre procesos, no depende de PYTHONHASHSEED.
"""

import os
import struct
import tempfile

from indexes.external_sort import decode_key, encode_key

# Cantidad de particiones por defecto. Mas particiones => particiones
# mas chicas => menos memoria por paso (pero mas archivos abiertos).
DEFAULT_BUCKETS = 16

_ENTRY_HEADER = ">II"  # (entry_len, key_len); value_len se lee aparte

# FNV-1a de 64 bits
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_UINT64_MASK = 0xFFFFFFFFFFFFFFFF


def _hash_bytes(data: bytes) -> int:
    """FNV-1a de 64 bits: estable entre procesos y entre llamadas."""
    hash_value = _FNV_OFFSET
    for byte in data:
        hash_value ^= byte
        hash_value = (hash_value * _FNV_PRIME) & _UINT64_MASK
    return hash_value


def bucket_of(key, num_buckets) -> int:
    """Particion (0..num_buckets-1) a la que pertenece una clave."""
    if num_buckets < 1:
        raise ValueError(f"num_buckets debe ser >= 1, se dio {num_buckets}")
    return _hash_bytes(encode_key(key)) % num_buckets


class Partitions:
    """
    Resultado de particionar una relacion: los items viven en disco,
    repartidos entre `num_buckets` archivos. Cada `bucket(b)` abre un
    iterador perezoso sobre esa particion y `cleanup()` borra los
    archivos cuando se termina de usar (se puede cortar a la mitad).
    """

    def __init__(self, num_buckets: int, paths: list, stream_registry: list):
        self.num_buckets = num_buckets
        self._paths = paths
        self._streams = []
        self._stream_registry = stream_registry  # registro global del hasher

    def bucket(self, b: int):
        """Iterador perezoso de los items (clave, value_bytes) de la
        particion `b`."""
        if b < 0 or b >= self.num_buckets:
            raise IndexError(
                f"particion {b} fuera de rango (num_buckets={self.num_buckets})"
            )
        stream = self._iter_entries(self._paths[b])
        self._streams.append(stream)
        self._stream_registry.append(stream)
        return stream

    def buckets(self):
        """Itera sobre todas las particiones, una a la vez."""
        for b in range(self.num_buckets):
            yield self.bucket(b)

    def _iter_entries(self, path):
        # Rinde (clave, value_bytes) leyendo la particion de disco de
        # forma perezosa, una entrada a la vez.
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
        """Cierra streams y borra los archivos de particion. Idempotente."""
        for stream in self._streams:
            stream.close()
        self._streams = []
        for path in self._paths:
            if os.path.exists(path):
                os.remove(path)
        self._paths = []


class ExternalHasher:
    """
    GROUP BY y JOIN con External Hashing.

    Entrada: iterables de pares (clave, value_bytes). El particionado
    es una pasada por disco con un item en memoria a la vez; el GROUP BY
    y el JOIN procesan despues una particion (o un par) por paso y solo
    retienen esa en memoria.
    """

    def __init__(self, num_buckets: int = DEFAULT_BUCKETS, run_dir: str = None):
        if num_buckets < 1:
            raise ValueError(f"num_buckets debe ser >= 1, se dio {num_buckets}")
        self.num_buckets = num_buckets
        self.run_dir = run_dir
        self._owns_dir = run_dir is None
        self._paths = []
        self._streams = []

    # ------------------------------------------------------------------
    # Particionado (nucleo compartido por GROUP BY y JOIN)
    # ------------------------------------------------------------------

    @staticmethod
    def _write_entry(archivo, key_bytes: bytes, value_bytes: bytes):
        # entrada autocontenida (igual que los runs del sorter externo):
        #   [entry_len: 4][key_len: 4][key_bytes][value_len: 4][value_bytes]
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
        """Una pasada que reparte cada item (clave, value_bytes) a su
        particion hash(clave) % num_buckets. Devuelve un Partitions con
        acceso perezoso; hay que llamar a cleanup() (o cerrar el objeto)
        cuando se termine."""
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

    # ------------------------------------------------------------------
    # JOIN (Grace Hash Join)
    # ------------------------------------------------------------------

    def hash_join(self, left_items, right_items):
        """Equi-join por clave entre dos relaciones.

        Particiona ambas entradas por la misma clave de join y procesa
        un par de particiones a la vez: arma una tabla hash en memoria
        sobre la izquierda (build) y la sondea con cada item de la
        derecha (probe). Rinde tuplas (clave, left_bytes, right_bytes)
        con TODOS los pares que matchean (incluidos los duplicados de
        ambos lados).
        """
        izquierda = self.partition(left_items)
        derecha = self.partition(right_items)
        try:
            for b in range(self.num_buckets):
                # build: tabla hash en memoria sobre la particion izquierda
                build = {}
                for clave, left_bytes in izquierda.bucket(b):
                    build.setdefault(clave, []).append(left_bytes)
                # probe: cada item derecho busca sus coincidencias
                for clave, right_bytes in derecha.bucket(b):
                    coincidencias = build.get(clave)
                    if coincidencias:
                        for left_bytes in coincidencias:
                            yield clave, left_bytes, right_bytes
        finally:
            izquierda.cleanup()
            derecha.cleanup()

    # ------------------------------------------------------------------
    # GROUP BY (hash aggregate)
    # ------------------------------------------------------------------

    def group_by(self, items, acumulador_nuevo, acumular):
        """GROUP BY con hash externo sobre la clave de grupo.

        Particiona por el hash de la clave y, por cada particion, arma
        en memoria un dict clave -> acumulador. `acumulador_nuevo()`
        crea un acumulador vacio y `acumular(acc, value_bytes)` lo
        actualiza con una fila, devolviendo el acumulador resultante.
        Rinde (clave, acumulador) uno por grupo, en orden de particion.
        """
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

    # ------------------------------------------------------------------
    # Limpieza
    # ------------------------------------------------------------------

    def cleanup(self):
        """Cierra streams y borra las particiones a disco. Idempotente."""
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