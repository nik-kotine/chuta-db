import struct

"""
Pag 0: overflow
Pag 1 en adelante: paginas
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
int         key
char[8]     dni
char[32]    purchase_desc
int         rid = page_id * RECORDS_PER_PAGE + record_id, -1 is NULL
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

    def _read_file_header(self):

        self.file_ptr.seek(0)
        n_pages, overflow_id = struct.unpack(FILE_HEADER_FORMAT, self.file_ptr.read(FILE_HEADER_SIZE))
        return n_pages, overflow_id

    def _read_by_page_id(self, page_id) -> Page:

        self.file_ptr.seek(FILE_HEADER_SIZE + page_id * PAGE_SIZE)
        page = self.file_ptr.read(PAGE_SIZE)

        header = struct.unpack(PAGE_HEADER_FORMAT, page[:PAGE_HEADER_SIZE])
        n_records, = struct.unpack(PAGE_HEADER_FORMAT, page[:PAGE_HEADER_SIZE])

        records = []
        offset = PAGE_HEADER_SIZE

        for _ in range(n_records):
            record = struct.unpack(RECORD_FORMAT, page[offset:(offset+RECORD_SIZE)])
            records.append(record)
            offset += RECORD_SIZE

        return Page(header, records, n_records)

    def _write_file_header(self, _n_pages, _overflow_id):

        self.file_ptr.seek(0)
        new_file_header = struct.pack(FILE_HEADER_FORMAT, _n_pages, _overflow_id)
        self.file_ptr.write(new_file_header)

        return True

    def read_page(self, page_id) -> Page:
        return self._read_by_page_id(page_id + 1)

    def read_overflow(self) -> Page:
        return self._read_by_page_id(0)

    def _read_by_record_id(self, record_id):
        n_pages, overflow_id = self._read_file_header()

        lo, hi = 1, n_pages
        while (lo < hi):
            mid = (lo+hi)//2
            mid_page = self._read_by_page_id(mid)
            if (mid_page.records[0]

