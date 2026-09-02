"""
phys_page_id: uso interno para funciones privadas y auxiliares
Pag 0: unica pagina de overflow
Pag 1+: paginas normales

page_id: uso para funciones publicas
Equivalente a phys_page_id - 1

Definimos rid = phys_page_id * RECORDS_PER_PAGE + slot_id, con rid = -1 siendo NULL
"""

class SequentialFileManager:

    def __init__(self, filename: str, page_size: int, file_header_size: int):
        self.filename = filename
        self.page_size = page_size
        self.file_header_size = file_header_size
        self.file_ptr = open(filename, "r+b")

    def _calc_page_offset(self, phys_page_id: int) -> int:
        """Calcula el offset a partir del indice fisico de la pagina."""
        return self.file_header_size + phys_page_id * self.page_size

    def read_page(self, phys_page_id: int) -> bytes:
        """
        Retorna la pagina de indice fisico phys_page_id como binario.
        """
        self.file_ptr.seek(self._calc_page_offset(phys_page_id))
        return self.file_ptr.read(self.page_size)

    def write_page(self, phys_page_id: int, page_bin: bytes) -> bool:
        """
        Sobreescribe toda una pagina con datos binarios.
        """
        self.file_ptr.seek(self._calc_page_offset(phys_page_id))
        self.file_ptr.write(page_bin)
        return True

    def allocate_page(self) -> int:
        """
        Crea una nueva pagina vacia (llena de bytes nulos) al final del archivo
        y retorna su indice.
        """
        self.file_ptr.seek(0, 2)
        file_size = self.file_ptr.tell()
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
        """Cierra el archivo."""
        self.file_ptr.close()
