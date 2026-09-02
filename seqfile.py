import struct

"""
phys_page_id: uso interno para funciones privadas y auxiliares
Pag 0: unica pagina de overflow
Pag 1+: paginas normales

page_id: uso para funciones publicas
Equivalente a phys_page_id - 1

Definimos rid = phys_page_id * RECORDS_PER_PAGE + slot_id, con rid = -1 siendo NULL
"""

"""
int n_pages
int overflow_id
"""
FILE_HEADER_FORMAT = "ii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

"""
int n_records
"""
PAGE_HEADER_FORMAT = "i"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)

"""
int         key, llave del registro
char[8]     dni, campo de ejemplo
char[32]    purchase_desc, campo de ejemplo
int         rid del siguiente registro en orden
"""
RECORD_FORMAT       = "i 8s 32s i"
RECORD_SIZE         = struct.calcsize(RECORD_FORMAT)
RECORDS_PER_PAGE    = 8

PAGE_SIZE = PAGE_HEADER_SIZE + RECORDS_PER_PAGE * RECORD_SIZE
PAGES_BEGIN = FILE_HEADER_SIZE + PAGE_SIZE

class Record:

    def __init__(self, _key, _dni, _purchase_desc, _rid):
        self.key = _key
        self.dni = _dni
        self.purchase_desc = _purchase_desc
        self.rid = _rid

class Page:

    def __init__(self, _header, _records, _n_records):
        self.header = _header
        self.records = _records
        self.n_records = _n_records

    def _print_all_records(self):
        print(self.records)

class SequentialFile:

    def __init__(self, _filename):
        self.filename = _filename
        self.file_ptr = open(_filename, "r+b")
        
    def _make_rid(self, phys_page_id, slot_id) 

    """
    Retorna el header del archivo como una tupla (n_pages, overflow_id).
    """
    def _read_file_header(self) -> tuple[int, int]:
        self.file_ptr.seek(0)
        n_pages, overflow_id = struct.unpack(FILE_HEADER_FORMAT, self.file_ptr.read(FILE_HEADER_SIZE))
        return n_pages, overflow_id

    """
    Retorna un objeto Page con todos los registros de la pagina en phys_page_id.
    """
    def _read_by_phys_page_id(self, phys_page_id) -> Page:
        self.file_ptr.seek(
            FILE_HEADER_SIZE +
            phys_page_id * PAGE_SIZE
        )
        page = self.file_ptr.read(PAGE_SIZE)
        header = struct.unpack(PAGE_HEADER_FORMAT, page[:PAGE_HEADER_SIZE])
        n_records = header[0]

        records = []
        offset = PAGE_HEADER_SIZE
        for _ in range(n_records):
            temp = struct.unpack(RECORD_FORMAT, page[offset:(offset+RECORD_SIZE)])
            record = Record(temp[0], temp[1], temp[2], temp[3])
            records.append(record)
            offset += RECORD_SIZE
        return Page(header, records, n_records)

    """
    Retorna el registro que se encuentra en rid.
    """
    def _read_record_at_rid(self, rid) -> Record | None:
        if rid == -1:
            return None

        phys_page_id = rid // RECORDS_PER_PAGE
        slot_id = rid % RECORDS_PER_PAGE
        page = self._read_by_phys_page_id(phys_page_id)
        return page.records[slot_id]

    """
    Busca el indice de la  pagina en la que se encontraria
    un registro con llave key.
    """
    def _find_page_id_by_record_key(self, key) -> int:
        n_pages, _ = self._read_file_header()
        if n_pages == 0:
            return -1

        lo, hi = 0, n_pages-1
        result = 0
        while lo <= hi:
            mid = (lo+hi)//2
            page = self.read_page(mid)

            if page.n_records == 0 or key < page.records[0].key:
                hi = mid - 1
            else:
                result = mid
                lo = mid + 1

        return result

    """
    Busca un registro en todo el archivo por su llave.
    Esto se puede optimizar mas, luego lo hago!!!
    """
    def _find_record_by_record_key(self, key) -> Record | None:
        page_id = self._find_page_id_by_record_key(key)
        if page_id == -1:
            return None

        page = self.read_page(page_id)
        for record in page.records:
            if record.key == key:
                return record

        for record in page.records:
            if record.rid == -1:
                continue
            next_record = self._read_record_at_rid(record.rid)
            if next_record is None:
                continue
            if next_record.key == key:
                return next_record

        return None

    def _find_insert_position(self, page, key):
        lo, hi = 0, page.n_records

        while lo < hi:
            mid = (lo+hi)//2
            if page.records[mid].key < key:
                lo = mid + 1
            else:
                hi = mid

        return lo

    """
    Inserta un nuevo registro en la pagina phys_page_id. Por ahora usa
    busqueda lineal para mantener el orden, pero deberia usar
    busqueda binaria.
    """
    def _insert_into_page(self, phys_page_id, record) -> bool:
        page = self._read_by_phys_page_id(phys_page_id)
        
        if page.n_records == RECORDS_PER_PAGE:
            return False
        
        pos = 0
        while pos < page.n_records and page.records[pos].key < record.key:
            pos += 1

        page.records.insert(pos, record)
        page.n_records += 1
        self._write_page_by_phys_id(phys_page_id, page)
        return True

    """
    Sobreescribe el header del archivo.
    """
    def _write_file_header(self, n_pages, overflow_id) -> bool:
        self.file_ptr.seek(0)
        new_file_header = struct.pack(FILE_HEADER_FORMAT, n_pages, overflow_id)
        self.file_ptr.write(new_file_header)
        return True

    """
    Sobreescribe la pagina entera en phys_page_id con el objeto Page page.
    """
    def _write_page_by_phys_id(self, phys_page_id, page) -> bool:
        self.file_ptr.seek(
            FILE_HEADER_SIZE +
            phys_page_id * PAGE_SIZE
        )
        page_bin = struct.pack(PAGE_HEADER_FORMAT, page.n_records)
        for record in page.records:
            page_bin += struct.pack(RECORD_FORMAT, record.key, record.dni, record.purchase_desc, record.rid)
        self.file_ptr.write(page_bin)
        return True

    """
    Retorna una pagina entera especifica.
    input:
        page_id: numero logico de la pagina en cuestion.
    output:
        Un objeto Page con toda la informacion de la pagina.
    """
    def read_page(self, page_id) -> Page:
        return self._read_by_phys_page_id(page_id + 1)

    """
    Retorna la pagina de overflow.
    output:
        Un objeto Page con toda la informacion de la pagina de overflow.
    """
    def read_overflow(self) -> Page:
        return self._read_by_phys_page_id(0)

    """
    Inserta un nuevo registro en su pagina correspondiente. Si no hay
    espacio en la pagina correcta, agrega el registro al overflow. Si no
    hay espacio en el overflow, mergea la pagina de overflow con el resto
    de paginas y prueba otra vez.
    """
    def insert_record(self, record) -> bool:
        return True


