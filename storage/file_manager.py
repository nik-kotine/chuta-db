"""
phys_page_id: uso interno para funciones privadas y auxiliares
Pag 0: unica pagina de overflow
Pag 1+: paginas normales
Definimos rid = phys_page_id * records_per_page + slot_id,
con rid = -1 siendo NULL
"""

import os


class FileManager:

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
        self.file_ptr.write(b"\x00" * self.page_size)
        return page_id

    def flush(self):
        """
        Limpia el buffer interno del archivo y escribe todo lo que estaba en el
        inmediatamente.
        """
        self.file_ptr.flush()

    def truncate(self, size: int):
        """
        Trunca el archivo a tamaño exactamente size. Todo lo que esta despues es
        eliminado.
        """
        self.file_ptr.truncate(size)

    def close(self):
        self.file_ptr.close()
