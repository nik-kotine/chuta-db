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
char[8]     dni, campo de ejemplo_
char[32]    purchase_desc, campo de ejemplo, temporalmente 8
int         rid del siguiente registro en orden
int         flag de eliminacion
"""
RECORD_FORMAT       = "i 8s 8s i i"
RECORD_SIZE         = struct.calcsize(RECORD_FORMAT)
RECORDS_PER_PAGE    = 8

PAGE_SIZE = PAGE_HEADER_SIZE + RECORDS_PER_PAGE * RECORD_SIZE

class Record:

    def __init__(self, _key, _dni, _purchase_desc, _rid, _flag):
        self.key = _key
        self.dni = _dni
        self.purchase_desc = _purchase_desc
        self.rid = _rid
        self.flag = _flag

class Page:

    def __init__(self, _records):
        self.records = _records
        self.n_records = len(_records)

    def _print_all_records(self):
        print(self.records)

class SequentialFile:

    def __init__(self, _filename):
        self.filename = _filename
        self.file_ptr = open(_filename, "r+b")
        
    def _make_rid(self, phys_page_id, slot_id) -> int:
        return phys_page_id * RECORDS_PER_PAGE + slot_id

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
        n_records, = struct.unpack(PAGE_HEADER_FORMAT, page[:PAGE_HEADER_SIZE])

        records = []
        offset = PAGE_HEADER_SIZE
        for _ in range(n_records):
            temp = struct.unpack(RECORD_FORMAT, page[offset:(offset+RECORD_SIZE)])
            record = Record(temp[0], temp[1], temp[2], temp[3], temp[4])
            records.append(record)
            offset += RECORD_SIZE
        return Page(records)

    """
    Retorna el registro que se encuentra en rid.
    """
    def _read_record_at_rid(self, rid) -> Record | None:
        if rid == -1:
            return None

        phys_page_id = rid // RECORDS_PER_PAGE
        slot_id = rid % RECORDS_PER_PAGE
        page = self._read_by_phys_page_id(phys_page_id)
        
        if slot_id >= page.n_records:
            return None
        
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

            if key < page.records[0].key:
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
            if page.records[mid].key <= key:
                lo = mid + 1
            else:
                hi = mid

        return lo

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
            page_bin += struct.pack(
                RECORD_FORMAT,
                record.key,
                record.dni,
                record.purchase_desc,
                record.rid,
                record.flag
            )
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
    Inserta record manteniendo el orden fisico.
    Retorna el rid del nuevo registro y -1 si la pagina esta llena,
    next_rid es el registro que logicamente sigue al nuevo registro.
    """
    def _insert_into_page(self, phys_page_id, record) -> tuple[int, int]:
        page = self._read_by_phys_page_id(phys_page_id)

        if page.n_records == RECORDS_PER_PAGE:
            return -1, -1

        outgoing_rid = -1
        if page.n_records > 0:
            outgoing_rid = page.records[-1].rid

        pos = self._find_insert_position(page, record.key)
        page.records.insert(pos, record)
        page.n_records += 1

        for i in range(page.n_records - 1):
            page.records[i].rid = self._make_rid(
                phys_page_id,
                i + 1
            )

        page.records[-1].rid = outgoing_rid

        new_rid = self._make_rid(phys_page_id, pos)

        self._write_page_by_phys_id(phys_page_id, page)
        return new_rid, pos

    def _set_next_rid(self, rid, next_rid):
        if rid == -1:
            return

        phys_page_id = rid // RECORDS_PER_PAGE
        slot_id = rid % RECORDS_PER_PAGE
        
        page = self._read_by_phys_page_id(phys_page_id)
        page.records[slot_id].rid = next_rid
        self._write_page_by_phys_id(phys_page_id, page)
    
    def _set_predecessor_next_rid(self, page, key, next_rid):
        pos = self._find_insert_position(page, key)
        if pos == 0:
            return
        
        page.records[pos-1].rid = next_rid
        
    def _find_neighbors(self, key):
        n_pages, _ = self._read_file_header()
        previous = None
        next_record = None

        for phys_page_id in range(1, n_pages + 1):
            page = self._read_by_phys_page_id(phys_page_id)

            for slot_id, record in enumerate(page.records):
                rid = self._make_rid(phys_page_id, slot_id)

                if record.key <= key:
                    if previous is None or record.key > previous[0] or (
                        record.key == previous[0] and rid > previous[1]
                    ):
                        previous = (record.key, rid)

                if record.key > key:
                    if next_record is None or record.key < next_record[0] or (
                        record.key == next_record[0] and rid < next_record[1]
                    ):
                        next_record = (record.key, rid)

        overflow = self.read_overflow()

        for slot_id, record in enumerate(overflow.records):
            rid = self._make_rid(0, slot_id)

            if record.key <= key:
                if previous is None or record.key > previous[0] or (
                    record.key == previous[0] and rid > previous[1]
                ):
                    previous = (record.key, rid)

            if record.key > key:
                if next_record is None or record.key < next_record[0] or (
                    record.key == next_record[0] and rid < next_record[1]
                ):
                    next_record = (record.key, rid)

        previous_rid = -1 if previous is None else previous[1]
        next_rid = -1 if next_record is None else next_record[1]

        return previous_rid, next_rid

    def _merge_overflow(self):
        n_pages, _ = self._read_file_header()

        records = []

        for phys_page_id in range(1, n_pages + 1):
            page = self._read_by_phys_page_id(phys_page_id)
            for record in page.records:
                if not record.flag:
                    records.append(record)

        overflow = self.read_overflow()
        for record in overflow.records:
            if not record.flag:
                records.append(record)

        records.sort(key=lambda record: record.key)

        new_n_pages = (
            len(records) + RECORDS_PER_PAGE - 1
        ) // RECORDS_PER_PAGE

        if new_n_pages == 0:
            self._write_page_by_phys_id(0, Page([]))
            self._write_file_header(0, -1)
            self.file_ptr.truncate(FILE_HEADER_SIZE + PAGE_SIZE)
            return

        pages = []

        for i in range(new_n_pages):
            start = i * RECORDS_PER_PAGE
            end = min(start + RECORDS_PER_PAGE, len(records))
            pages.append(Page(records[start:end]))

        for i, page in enumerate(pages):
            phys_page_id = i + 1

            for j in range(page.n_records - 1):
                page.records[j].rid = self._make_rid(
                    phys_page_id,
                    j + 1
                )

            if i + 1 < len(pages):
                page.records[-1].rid = self._make_rid(
                    phys_page_id + 1,
                    0
                )
            else:
                page.records[-1].rid = -1

            self._write_page_by_phys_id(phys_page_id, page)

        self._write_page_by_phys_id(0, Page([]))
        self._write_file_header(
            new_n_pages,
            self._make_rid(1, 0)
        )

        self.file_ptr.truncate(
            FILE_HEADER_SIZE + (new_n_pages + 1) * PAGE_SIZE
        )

    def _insert_record(self, record) -> bool:
        n_pages, _ = self._read_file_header()
        record.flag = 0

        if n_pages == 0:
            record.rid = -1
            page = Page([record])
            overflow = Page([])
            self._write_page_by_phys_id(0, overflow)
            self._write_page_by_phys_id(1, page)
            self._write_file_header(1, self._make_rid(1, 0))
            return True

        previous_rid, next_rid = self._find_neighbors(record.key)

        page_id = self._find_page_id_by_record_key(record.key)
        phys_page_id = page_id + 1
        page = self.read_page(page_id)

        if page.n_records < RECORDS_PER_PAGE:
            new_rid, pos = self._insert_into_page(phys_page_id, record)

            if pos == page.n_records:
                self._set_next_rid(new_rid, next_rid)

            if previous_rid == -1:
                self._write_file_header(n_pages, new_rid)
            elif pos == 0:
                self._set_next_rid(previous_rid, new_rid)

            return True

        overflow = self.read_overflow()

        if overflow.n_records == RECORDS_PER_PAGE:
            self._merge_overflow()

            n_pages, _ = self._read_file_header()
            previous_rid, next_rid = self._find_neighbors(record.key)

            page_id = self._find_page_id_by_record_key(record.key)
            phys_page_id = page_id + 1
            page = self.read_page(page_id)

            if page.n_records < RECORDS_PER_PAGE:
                new_rid, pos = self._insert_into_page(phys_page_id, record)

                if pos == page.n_records:
                    self._set_next_rid(new_rid, next_rid)

                if previous_rid == -1:
                    self._write_file_header(n_pages, new_rid)
                elif pos == 0:
                    self._set_next_rid(previous_rid, new_rid)

                return True

            overflow = self.read_overflow()

        new_rid, pos = self._insert_into_page(0, record)

        if new_rid == -1:
            return False

        # overflow is not contiguous with the normal pages
        self._set_next_rid(new_rid, next_rid)

        if previous_rid == -1:
            self._write_file_header(n_pages, new_rid)
        else:
            self._set_next_rid(previous_rid, new_rid)

        return True
    
    def insert_record(self, key, dni, purchase_desc) -> bool:
        record = Record(key, dni, purchase_desc, -1, 0)
        return self._insert_record(record)
    
    def delete_record(self, key) -> bool:
        _, first_rid = self._read_file_header()
        current_rid = first_rid

        while current_rid != -1:
            phys_page_id = current_rid // RECORDS_PER_PAGE
            slot_id = current_rid % RECORDS_PER_PAGE
            page = self._read_by_phys_page_id(phys_page_id)
            record = page.records[slot_id]

            if record.key > key:
                return False

            if record.key == key and not record.flag:
                record.flag = 1
                self._write_page_by_phys_id(phys_page_id, page)
                return True

            current_rid = record.rid

        return False


with open("test.bin", "wb") as f:
    f.write(struct.pack(FILE_HEADER_FORMAT, 0, -1))

s = SequentialFile("test.bin")

s.insert_record(50, b"12345678", b"purchase")
s.insert_record(80, b"12345678", b"purchase")
s.insert_record(10, b"12345678", b"purchase")
s.insert_record(40, b"12345678", b"purchase")
s.insert_record(100, b"12345678", b"purchase")
s.insert_record(30, b"12345678", b"purchase")
s.insert_record(60, b"12345678", b"purchase")
s.insert_record(70, b"12345678", b"purchase")
s.insert_record(911, b"12345678", b"purchase")
s.insert_record(90, b"12345678", b"purchase")
s.insert_record(20, b"12345678", b"purchase")


rid = s._read_file_header()[1]

while rid != -1:
    record = s._read_record_at_rid(rid)

    if not record.flag:
        print(record.key)

    rid = record.rid
