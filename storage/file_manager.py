"""
phys_page_id: uso interno para funciones privadas y auxiliares
Pag 0: unica pagina de overflow
Pag 1+: paginas normales
Definimos rid = phys_page_id * records_per_page + slot_id,
con rid = -1 siendo NULL
"""

import os
import struct


class FileManager:

    _wal_logger = None
    _transaction_id_provider = None

    @classmethod
    def configure_wal(cls, wal_logger, transaction_id_provider):
        cls._wal_logger = wal_logger
        cls._transaction_id_provider = transaction_id_provider

    def __init__(self, filename: str, page_size: int, file_header_size: int):
        self.filename: str              = filename
        self.page_size: int             = page_size
        self.file_header_size: int      = file_header_size
        is_new = not os.path.exists(filename)
        self.file_ptr                   = open(filename, "w+b" if is_new else "r+b")

    def _calc_page_offset(self, phys_page_id: int) -> int:
        """Calcula el offset a partir del indice fisico de la pagina."""
        return self.file_header_size + phys_page_id * self.page_size

    def read_page(self, phys_page_id: int) -> bytes:
        """
        Retorna la pagina de indice fisico phys_page_id como binario.
        """
        self.file_ptr.seek(self._calc_page_offset(phys_page_id))
        return self.file_ptr.read(self.page_size)

    def write_page(
            self, phys_page_id: int, page_bin: bytes | bytearray
        ) -> bool:
        """
        Sobreescribe toda una pagina con datos binarios.
        """
        self.file_ptr.seek(self._calc_page_offset(phys_page_id))
        self.file_ptr.write(page_bin)
        return True
    
    def read_header(self) -> bytes:
        """
        Retorna el contenido completo del header del archivo.
        """
        self.file_ptr.seek(0)
        return self.file_ptr.read(self.file_header_size)
    
    def write_header(self, header: bytes | bytearray):
        """
        Sobreescribe el header completo del archivo.
        """
        if len(header) != self.file_header_size:
            raise ValueError("El tamaño del header no coincide.")

        before = self.read_header()
        self._log_physical("header", -1, 0, before, bytes(header))
        self.file_ptr.seek(0)
        self.file_ptr.write(header)

    def allocate_page(self) -> int:
        """
        Crea una nueva pagina vacia (llena de bytes nulos) al final del archivo
        y retorna su indice.
        """
        self.file_ptr.seek(0, 2)
        file_size = self.file_ptr.tell()
        if file_size < self.file_header_size:
            self.file_ptr.write(b"\x00" * (self.file_header_size - file_size))
            file_size = self.file_header_size
        data_size = file_size - self.file_header_size
        page_id = data_size // self.page_size
        page = b"\x00" * self.page_size
        self._log_physical("allocation", page_id, 0, b"", page)
        self.file_ptr.write(page)
        return page_id

    def _log_physical(self, resource_type, page_id, offset, before, after):
        transaction_id = (
            type(self)._transaction_id_provider()
            if type(self)._transaction_id_provider is not None
            else None
        )
        if type(self)._wal_logger is not None and transaction_id is not None:
            type(self)._wal_logger(
                transaction_id,
                self,
                resource_type,
                page_id,
                offset,
                before,
                after,
            )

    def flush(self):
        """
        Limpia el buffer interno del archivo y escribe todo lo que estaba en el
        inmediatamente.
        """
        self.file_ptr.flush()

    def force(self):
        """Hace persistente el contenido del archivo en el dispositivo.

        ``flush()`` solo entrega los bytes al sistema operativo. Esta
        operación agrega ``fsync()`` y sera necesaria para respetar WAL antes
        de confirmar una transaccion.
        """
        self.file_ptr.flush()
        os.fsync(self.file_ptr.fileno())

    def truncate(self, size: int):
        """
        Trunca el archivo a tamaño exactamente size. Todo lo que esta despues es
        eliminado.
        """
        self.file_ptr.seek(0, 2)
        before = self.file_ptr.tell()
        self._log_physical(
            "truncate",
            -1,
            0,
            struct.pack(">Q", before),
            struct.pack(">Q", size),
        )
        self.file_ptr.truncate(size)

    def close(self):
        self.file_ptr.close()
