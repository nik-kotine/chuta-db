import struct
from storage.buffer_manager import BufferManager
from storage.record_file import RecordFile
from storage.rid import RID
from storage.pages.fixed_page import FixedPage, FixedLengthRecordSerializer, Record

"""
La cabecera del archivo contiene:
    n_pages: cantidad de paginas principales
    first_rid: RID del primer registro de la secuencia logica
    n_records: cantidad de registros vivos
    n_deleted: cantidad de registros marcados como eliminados
"""
FILE_HEADER_FORMAT = "iiii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

"""
La cabecera de cada pagina contiene:
    n_records: cantidad de registros almacenados fisicamente en la pagina
"""
PAGE_HEADER_FORMAT = "i"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)
WASTED_RATIO = 0.5

class SequentialFile(RecordFile):
    def __init__(
        self, buffer_manager: BufferManager, page_size: int, record_format: str
    ):
        self.buffer_manager = buffer_manager
        self.file_manager = buffer_manager.file_manager
        self.page_size = page_size
        self.key_index = 0
        self.serializer = FixedLengthRecordSerializer(record_format)
        self.max_records_per_page = (
            page_size - PAGE_HEADER_SIZE
        ) // self.serializer.slot_size
        self.first_rid = None
        self.n_pages = 0
        self.n_records = 0
        self.n_deleted = 0
        # contador en RAM (no persiste) que se incrementa cada vez que
        # reorganize() corre. Sirve para que quien mantenga una
        # estructura externa basada en RID (como un indice B+
        # agrupado) pueda detectar cuando sus RID guardados quedaron
        # invalidos porque reorganize() reasigno todo.
        self.reorganize_count = 0
        header = self.file_manager.read_header()
        if len(header) == 0:
            return
        self._load_header()

    def _load_header(self):
        """
        Carga la metadata del SequentialFile desde el header del archivo.
        """
        header = self.file_manager.read_header()

        if len(header) != FILE_HEADER_SIZE:
            raise RuntimeError("invalid or corrupt header file")

        self.n_pages, first_rid, self.n_records, self.n_deleted = struct.unpack(
            FILE_HEADER_FORMAT, header
        )

        if first_rid == -1:
            self.first_rid = None
        else:
            self.first_rid = self._int_to_rid(first_rid)

    def _write_header(self):
        """
        Persiste la metadata del SequentialFile en el header del archivo.
        """
        first_rid = self._rid_to_int(self.first_rid)

        header = struct.pack(
            FILE_HEADER_FORMAT, self.n_pages, first_rid, self.n_records, self.n_deleted
        )

        self.file_manager.write_header(header)

    def _rid_to_int(self, rid):
        """
        Convierte un RID a una representacion entera para el header.
        """
        if rid is None:
            return -1

        phys_page_id, slot_id = rid
        return phys_page_id * self.max_records_per_page + slot_id

    def _int_to_rid(self, value):
        """
        Convierte una representacion entera a un RID.
        """
        if value == -1:
            return None

        return (value // self.max_records_per_page, value % self.max_records_per_page)

    def _make_rid(self, phys_page_id: int, slot_id: int):
        """
        Construye un RID a partir de indices de pagina fisica y slot.
        """
        return phys_page_id, slot_id

    def _load_page(self, phys_page_id: int) -> FixedPage:
        """
        Obtiene una pagina del BufferManager y crea una interfaz
        FixedPage sobre el bytearray almacenado en el frame.
        """
        page_ba = self.buffer_manager.fetch_page(phys_page_id)

        if len(page_ba) == 0:
            page_ba.extend(b"\x00" * self.page_size)
        elif len(page_ba) < self.page_size:
            page_ba[:] = page_ba + b"\x00" * (self.page_size - len(page_ba))

        return FixedPage(page_ba, self.page_size, self.serializer)

    def _get_record(self, rid):
        """
        Obtiene un registro a partir de su RID.
        """
        if rid is None:
            return None

        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            return page.get_record_by_slot_id(slot_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _set_record(self, rid, record: Record):
        """
        Sobreescribe un registro identificado por su RID.
        """
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            page.set_record_in_slot_id(slot_id, record)
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _find_page_for_key(self, key):
        """
        Busca mediante busqueda binaria la ultima pagina principal cuyo primer
        registro tiene una clave menor o igual a key.
        """
        if self.n_pages == 0:
            return None

        low = 1
        high = self.n_pages
        result = 1

        while low <= high:
            mid = (low + high) // 2
            page = self._load_page(mid)

            try:
                if page.n_records == 0:
                    high = mid - 1
                    continue

                first_record = page.get_record_by_slot_id(0)
                first_key = first_record.params[self.key_index]

                if first_key <= key:
                    result = mid
                    low = mid + 1
                else:
                    high = mid - 1
            finally:
                self.buffer_manager.unpin_page(mid)

        return result

    def _find_neighbors(self, key):
        """
        Busca los registros inmediatamente anterior y posterior a una clave
        recorriendo la secuencia logica desde first_rid.
        """
        if self.first_rid is None:
            return None, None

        previous_rid = None

        for current_rid, record in self._iter_records(self.first_rid):
            if record.deleted:
                continue

            current_key = record.params[self.key_index]

            if current_key >= key:
                return previous_rid, current_rid

            previous_rid = current_rid

        return previous_rid, None

    def _append_page(self) -> int:
        """
        Crea una nueva pagina principal al final del archivo.
        """
        phys_page_id = self.file_manager.allocate_page()
        self.n_pages += 1
        page = self._load_page(phys_page_id)

        page_ba = self.buffer_manager.fetch_page(phys_page_id)
        if len(page_ba) == 0:
            page_ba.extend(b"\x00" * self.page_size)
        elif len(page_ba) < self.page_size:
            page_ba[:] = page_ba + b"\x00" * (self.page_size - len(page_ba))

        page = FixedPage(page_ba, self.page_size, self.serializer)
        
        try:
            page.n_records = 0
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

        return phys_page_id

    def _insert_into_overflow(self, record: Record) -> tuple:
        """
        Inserta un registro en la pagina de overflow y retorna su rid.
        """
        page = self._load_page(0)

        try:
            if not page.has_space():
                return None

            slot_id = page.overflow_insert(record)
            self.buffer_manager.mark_dirty(0)

            return self._make_rid(0, slot_id)

        finally:
            self.buffer_manager.unpin_page(0)
            
    def _iter_records(self, start_rid = None):
        """
        Recorre la secuencia logica desde start_rid y retorna cada RID junto
        con su registro.
        """
        current_rid = self.first_rid if start_rid is None else start_rid

        while current_rid is not None:
            record = self._get_record(current_rid)

            if record is None:
                break

            yield current_rid, record
            current_rid = record.next_rid

    def insert(self, values) -> RID:
        """
        Inserta un registro nuevo en overflow y actualiza la cadena
        logica para conservar el orden por clave.
        """
        record = Record(values)

        if self.first_rid is None:
            if self.n_pages == 0:
                self._append_page()

            page = self._load_page(1)

            try:
                rid = self._make_rid(1, page.overflow_insert(record))
                self.first_rid = rid
                self.n_records = 1
                self.buffer_manager.mark_dirty(1)
            finally:
                self.buffer_manager.unpin_page(1)

            self._write_header()
            return rid

        previous_rid, next_rid = self._find_neighbors(values[self.key_index])

        record.next_rid = next_rid

        new_rid = self._insert_into_overflow(record)

        if new_rid is None:
            self.reorganize()
            return self.insert(values)

        if previous_rid is None:
            self.first_rid = new_rid
        else:
            previous_record = self._get_record(previous_rid)
            previous_record.next_rid = new_rid
            self._set_record(previous_rid, previous_record)

        self.n_records += 1
        self._write_header()

        return new_rid

    def fetch(self, rid: RID):
        """
        Recupera los valores de un registro dado su RID
        """
        record = self._get_record(rid)
        if record is None or record.deleted:
            return None
        return list(record.params)

    def search(self, key):
        """
        Retorna todos los registros vivos cuya clave coincide con key.
        """
        results = []
        _, current_rid = self._find_neighbors(key)
        for current_rid, record in self._iter_records(current_rid):
            current_key = record.params[self.key_index]
            if current_key > key:
                break
            if current_key == key and not record.deleted:
                results.append(record)
        return results

    def delete(self, rid: RID):
        """
        Marca un registro como eliminado dado su RID (Necesario para RecordFile).
        """
        record = self._get_record(rid)
        if record is None or record.deleted:
            return False

        record.deleted = True
        self._set_record(rid, record)
        self.n_deleted += 1
        self.n_records -= 1

        if self._wasted_space_ratio() >= WASTED_RATIO:
            self.reorganize()

        self._write_header()
        return True    

    def delete_by_key(self, key) -> bool:
        """
        Marca como eliminados todos los registros vivos cuya clave coincide
        con key.
        """
        deleted_any = False
        _, current_rid = self._find_neighbors(key)

        for current_rid, record in self._iter_records(current_rid):
            current_key = record.params[self.key_index]

            if current_key > key:
                break

            if current_key == key and not record.deleted:
                record.deleted = True
                self._set_record(current_rid, record)
                self.n_deleted += 1
                self.n_records -= 1
                deleted_any = True

        if deleted_any and self._wasted_space_ratio() >= WASTED_RATIO:
            self.reorganize()

        self._write_header()

        return deleted_any

    def _wasted_space_ratio(self) -> float:
        """
        Calcula la proporcion de slots ocupados por registros eliminados.
        """
        total_slots = 0
        deleted_slots = 0

        for phys_page_id in range(0, self.n_pages + 1):
            page = self._load_page(phys_page_id)

            try:
                for slot_id in range(page.n_records):
                    total_slots += 1
                    record = page.get_record_by_slot_id(slot_id)
                    if record.deleted:
                        deleted_slots += 1

            finally:
                self.buffer_manager.unpin_page(phys_page_id)

        if total_slots == 0:
            return 0.0

        return deleted_slots / total_slots

    def reorganize(self):
        """
        Reconstruye completamente el SequentialFile eliminando registros
        marcados como deleted y vaciando el overflow.
        """
        self.reorganize_count += 1
        records = []

        for _, record in self._iter_records():
            if not record.deleted:
                records.append(Record(record.params))

        records.sort(key=lambda record: record.params[self.key_index])
        required_pages = max(
            1, (len(records) + self.max_records_per_page - 1) // self.max_records_per_page
        )

        while self.n_pages < required_pages:
            self._append_page()

        while self.n_pages > required_pages:
            self.buffer_manager.file_manager.truncate(
                self.file_manager.file_header_size + required_pages * self.page_size
            )
            self.n_pages = required_pages

        for phys_page_id in range(0, self.n_pages + 1):
            page_ba = self.buffer_manager.fetch_page(phys_page_id)
            page_ba[:] = b"\x00" * self.page_size
            page = FixedPage(page_ba, self.page_size, self.serializer)
            page.n_records = 0
            self.buffer_manager.mark_dirty(phys_page_id)
            self.buffer_manager.unpin_page(phys_page_id)

        self.first_rid = None

        for index, record in enumerate(records):
            phys_page_id = index // self.max_records_per_page + 1
            slot_id = index % self.max_records_per_page
            rid = self._make_rid(phys_page_id, slot_id)
            if index + 1 < len(records):
                next_rid = self._make_rid(
                    ((index + 1) // self.max_records_per_page) + 1,
                    (index + 1) % self.max_records_per_page,
                )
            else:
                next_rid = None

            record.next_rid = next_rid
            page = self._load_page(phys_page_id)

            try:
                page.n_records += 1
                page.set_record_in_slot_id(slot_id, record)
                self.buffer_manager.mark_dirty(phys_page_id)
            finally:
                self.buffer_manager.unpin_page(phys_page_id)

            if index == 0:
                self.first_rid = rid

        self.n_records = len(records)
        self.n_deleted = 0
        self._write_header()

    def scan(self):
        """
        Recorre la secuencia lógica y devuelve (RID, valores)
        de todos los registros que no estén eliminados.
        """
        for rid, record in self._iter_records():
            if not record.deleted:
                yield rid, list(record.params)

    def close(self):
        self.buffer_manager.close()