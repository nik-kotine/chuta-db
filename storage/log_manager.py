"""Write-Ahead Log (WAL) para la primera fase de transacciones."""

from dataclasses import dataclass
from enum import Enum, auto
import os
import struct
import threading
import zlib


class LogRecordType(Enum):
    """Eventos que pueden aparecer en el WAL."""

    BEGIN = auto()
    UPDATE = auto()
    COMMIT = auto()
    ABORT = auto()
    CLR = auto()
    CHECKPOINT = auto()


class LogCorruptionError(RuntimeError):
    """Indica que un registro completo del WAL no es valido."""


@dataclass(frozen=True)
class LogRecord:
    """Registro logico deserializado desde el WAL."""

    lsn: int
    prev_lsn: int
    transaction_id: int
    record_type: LogRecordType
    operation: str | None
    file_name: str
    resource_type: str | None
    page_id: int
    offset: int
    before: bytes
    after: bytes
    page_lsn: int


@dataclass(frozen=True)
class LogScanResult:
    """Resultado de recorrer el WAL hasta el ultimo registro valido."""

    records: list[LogRecord]
    valid_end_offset: int
    truncated_tail: bool = False


class LogManager:
    """Escribe y lee un WAL append-only de forma segura entre hilos.

    El LSN es el offset del registro dentro del archivo. ``append()`` escribe
    el registro, pero ``force()`` es la operacion que garantiza durabilidad.
    """

    _LENGTH_FORMAT = ">I"
    _HEADER_FORMAT = ">QQQ B H H H H q Q Q Q Q"
    _CRC_FORMAT = ">I"
    _LENGTH_SIZE = struct.calcsize(_LENGTH_FORMAT)
    _HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)
    _CRC_SIZE = struct.calcsize(_CRC_FORMAT)
    _MAX_RECORD_SIZE = 64 * 1024 * 1024

    def __init__(self, path: str = "chuta_wal.log"):
        """Abre ``path``, valida sus registros y recupera el siguiente LSN."""
        self.path = path
        self._lock = threading.RLock()
        self._file = open(path, "a+b")
        self._records: list[LogRecord] = []
        self._last_lsn = 0
        self._last_lsn_by_transaction: dict[int, int] = {}
        self._valid_end_offset = 0
        self._has_truncated_tail = False

        scan = self.read_all()
        self._records = scan.records
        if scan.records:
            self._last_lsn = scan.records[-1].lsn
            for record in scan.records:
                self._last_lsn_by_transaction[record.transaction_id] = record.lsn
        self._valid_end_offset = scan.valid_end_offset
        self._has_truncated_tail = scan.truncated_tail

    @staticmethod
    def _encode_text(value: str | None) -> bytes:
        return b"" if value is None else value.encode("utf-8")

    @staticmethod
    def _decode_text(value: bytes) -> str | None:
        return value.decode("utf-8") if value else None

    @classmethod
    def _serialize(cls, record: LogRecord) -> bytes:
        operation = cls._encode_text(record.operation)
        file_name = record.file_name.encode("utf-8")
        resource_type = cls._encode_text(record.resource_type)
        for name, value in (("operation", operation), ("file_name", file_name), ("resource_type", resource_type)):
            if len(value) > 0xFFFF:
                raise ValueError(f"{name} es demasiado largo para el WAL")

        header = struct.pack(
            cls._HEADER_FORMAT,
            record.lsn, record.prev_lsn, record.transaction_id,
            record.record_type.value, len(operation), len(file_name),
            len(resource_type), 0, record.page_id, record.offset,
            len(record.before), len(record.after), record.page_lsn,
        )
        payload = header + operation + file_name + resource_type + record.before + record.after
        crc = struct.pack(cls._CRC_FORMAT, zlib.crc32(payload) & 0xFFFFFFFF)
        body = payload + crc
        if len(body) > cls._MAX_RECORD_SIZE:
            raise ValueError("el registro WAL excede el tamaño maximo")
        return struct.pack(cls._LENGTH_FORMAT, len(body)) + body

    @classmethod
    def _deserialize(cls, raw: bytes, offset: int) -> LogRecord:
        if len(raw) < cls._HEADER_SIZE + cls._CRC_SIZE:
            raise LogCorruptionError(f"registro WAL incompleto en offset {offset}")
        payload = raw[:-cls._CRC_SIZE]
        expected_crc = struct.unpack(cls._CRC_FORMAT, raw[-cls._CRC_SIZE:])[0]
        if zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
            raise LogCorruptionError(f"CRC invalido en offset {offset}")

        values = struct.unpack(cls._HEADER_FORMAT, payload[:cls._HEADER_SIZE])
        (lsn, prev_lsn, transaction_id, record_type_value,
         operation_length, file_name_length, resource_length, _reserved,
         page_id, change_offset, before_length, after_length, page_lsn) = values
        data = payload[cls._HEADER_SIZE:]
        expected_size = operation_length + file_name_length + resource_length + before_length + after_length
        if len(data) != expected_size:
            raise LogCorruptionError(f"longitudes invalidas en offset {offset}")

        cursor = 0
        operation = data[cursor:cursor + operation_length]
        cursor += operation_length
        file_name = data[cursor:cursor + file_name_length]
        cursor += file_name_length
        resource_type = data[cursor:cursor + resource_length]
        cursor += resource_length
        before = data[cursor:cursor + before_length]
        cursor += before_length
        after = data[cursor:cursor + after_length]
        try:
            record_type = LogRecordType(record_type_value)
            operation_text = cls._decode_text(operation)
            file_name_text = file_name.decode("utf-8")
            resource_text = cls._decode_text(resource_type)
        except (ValueError, UnicodeDecodeError) as error:
            raise LogCorruptionError(f"cabecera invalida en offset {offset}") from error

        return LogRecord(lsn, prev_lsn, transaction_id, record_type,
                         operation_text, file_name_text, resource_text,
                         page_id, change_offset, before, after, page_lsn)

    def append(self, record_type: LogRecordType, transaction_id: int, *,
               prev_lsn: int = 0, operation: str | None = None,
               file_name: str = "", resource_type: str | None = None,
               page_id: int = -1, offset: int = 0, before: bytes = b"",
               after: bytes = b"", page_lsn: int = 0) -> int:
        """Agrega un registro y devuelve su LSN físico.

        Si ``prev_lsn`` es cero, se completa con el último registro de la
        misma transacción. El método no hace ``fsync`` por sí solo.
        """
        if not isinstance(record_type, LogRecordType):
            raise TypeError("record_type debe ser LogRecordType")
        if transaction_id < 0 or prev_lsn < 0 or page_lsn < 0:
            raise ValueError("los identificadores del WAL no pueden ser negativos")
        if not isinstance(before, bytes) or not isinstance(after, bytes):
            raise TypeError("before y after deben ser bytes")

        with self._lock:
            if self._has_truncated_tail:
                self._file.truncate(self._valid_end_offset)
                self._has_truncated_tail = False
            expected_prev = self._last_lsn_by_transaction.get(transaction_id, 0)
            if prev_lsn == 0:
                prev_lsn = expected_prev
            elif prev_lsn != expected_prev:
                raise ValueError("prev_lsn no coincide con la cadena de la transaccion")
            self._file.seek(0, os.SEEK_END)
            lsn = self._file.tell()
            record = LogRecord(lsn, prev_lsn, transaction_id, record_type,
                               operation, file_name, resource_type, page_id,
                               offset, before, after, page_lsn)
            self._file.write(self._serialize(record))
            self._records.append(record)
            self._last_lsn = lsn
            self._last_lsn_by_transaction[transaction_id] = lsn
            return lsn

    def flush(self) -> None:
        """Vacía el buffer de Python, sin garantizar persistencia física."""
        with self._lock:
            self._file.flush()

    def force(self) -> None:
        """Ejecuta ``flush`` y ``fsync``; es requisito antes de COMMIT."""
        with self._lock:
            self._file.flush()
            os.fsync(self._file.fileno())

    def read_all(self) -> LogScanResult:
        """Lee registros válidos y distingue una cola truncada por crash."""
        with self._lock:
            self._file.flush()
            self._file.seek(0)
            records = []
            offset = 0
            previous_lsn = -1
            known_lsns = set()
            while True:
                length_bytes = self._file.read(self._LENGTH_SIZE)
                if not length_bytes:
                    return LogScanResult(records, offset, False)
                if len(length_bytes) < self._LENGTH_SIZE:
                    return LogScanResult(records, offset, True)
                body_length = struct.unpack(self._LENGTH_FORMAT, length_bytes)[0]
                if body_length < self._HEADER_SIZE + self._CRC_SIZE or body_length > self._MAX_RECORD_SIZE:
                    raise LogCorruptionError(f"longitud invalida en offset {offset}")
                body = self._file.read(body_length)
                if len(body) < body_length:
                    return LogScanResult(records, offset, True)
                record = self._deserialize(body, offset)
                if record.lsn != offset or record.lsn <= previous_lsn:
                    raise LogCorruptionError(f"LSN invalido en offset {offset}")
                if record.prev_lsn and record.prev_lsn not in known_lsns:
                    raise LogCorruptionError(f"prev_lsn invalido en offset {offset}")
                records.append(record)
                known_lsns.add(record.lsn)
                previous_lsn = record.lsn
                offset += self._LENGTH_SIZE + body_length

    def iter_records(self):
        """Itera los registros completos; una cola truncada se omite."""
        yield from self.read_all().records

    def last_lsn(self) -> int:
        """Devuelve el LSN del último registro, o cero si el WAL está vacío."""
        with self._lock:
            return self._last_lsn

    def close(self) -> None:
        """Fuerza y cierra el archivo WAL sin eliminarlo."""
        with self._lock:
            if not self._file.closed:
                self.force()
                self._file.close()