import struct
from BufferManager import BufferManager

"""
rid: (page_id, slot_id)
page_id representa en que pagina se encuentra (0+, 0 siendo overflow) y
slot_id el numero de registro dentro de esa pagina. (-1, -1) es un registro
nulo
"""
RID_FORMAT = "ii"
RID_SIZE = struct.calcsize(RID_FORMAT)

"""
deleted: bool
Representa si el registro fue marcado como eliminado
"""
DELETED_FORMAT = "?"
DELETED_SIZE = struct.calcsize(DELETED_FORMAT)

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


class FixedLengthRecordSerializer:
    def __init__(self, record_format: str):
        self.record_format = record_format
        self.record_size = struct.calcsize(record_format)
        self.slot_size = self.record_size + RID_SIZE + DELETED_SIZE

    def serialize(self, params) -> bytes:
        """
        Serializa los datos proporcionados por el usuario.
        """
        return struct.pack(self.record_format, *params)

    def deserialize(self, data: bytes):
        """
        Deserializa los datos proporcionados por el usuario.
        """
        return struct.unpack(self.record_format, data)


class Record:
    def __init__(self, params, next_rid=None, deleted=False):
        self.params = params
        self.next_rid = next_rid
        self.deleted = deleted


class FixedPage:
    def __init__(
        self,
        page_ba: bytearray,
        page_size: int,
        serializer: FixedLengthRecordSerializer,
    ):
        self.page_ba = page_ba
        self.page_size = page_size
        self.serializer = serializer
        self.max_records = (page_size - PAGE_HEADER_SIZE) // serializer.slot_size

    @property
    def n_records(self) -> int:
        """
        Retorna la cantidad de registros actualmente almacenados.
        """
        return struct.unpack_from(PAGE_HEADER_FORMAT, self.page_ba, 0)[0]

    @n_records.setter
    def n_records(self, value: int):
        struct.pack_into(PAGE_HEADER_FORMAT, self.page_ba, 0, value)

    @property
    def free_slots(self) -> int:
        """
        Retorna la cantidad de slots libres.
        """
        return self.max_records - self.n_records

    def has_space(self) -> bool:
        """
        Indica si la pagina tiene al menos un slot libre.
        """
        return self.n_records < self.max_records

    def _slot_offset(self, slot_id: int) -> int:
        """
        Calcula el offset de un slot dentro de la pagina.
        """
        if slot_id < 0 or slot_id >= self.max_records:
            raise RuntimeError("index out of range")

        return PAGE_HEADER_SIZE + slot_id * self.serializer.slot_size

    def get_by_slot_id(self, slot_id: int) -> Record | None:
        """
        Retorna el registro ubicado en slot_id.
        """
        if slot_id < 0 or slot_id >= self.n_records:
            return None

        offset = self._slot_offset(slot_id)
        record_data = bytes(
            self.page_ba[offset : (offset + self.serializer.record_size)]
        )

        offset += self.serializer.record_size
        params = self.serializer.deserialize(record_data)
        next_rid = struct.unpack_from(RID_FORMAT, self.page_ba, offset)

        if next_rid == (-1, -1):
            next_rid = None

        offset += RID_SIZE
        deleted = struct.unpack_from(DELETED_FORMAT, self.page_ba, offset)[0]

        return Record(params, next_rid, deleted)

    def set_by_slot_id(self, slot_id: int, record: Record):
        """
        Sobreescribe completamente un slot existente.
        """
        if slot_id < 0 or slot_id >= self.n_records:
            raise RuntimeError("slot_id is out of range")

        offset = self._slot_offset(slot_id)
        record_data = self.serializer.serialize(record.params)
        self.page_ba[offset : (offset + self.serializer.record_size)] = record_data
        offset += self.serializer.record_size
        next_rid = record.next_rid

        if next_rid is None:
            next_rid = (-1, -1)

        struct.pack_into(RID_FORMAT, self.page_ba, offset, *next_rid)
        offset += RID_SIZE
        struct.pack_into(DELETED_FORMAT, self.page_ba, offset, record.deleted)

    def insert(self, record: Record) -> int:
        """
        Inserta un registro en el primer slot libre al final de la
        pagina y retorna su slot_id.
        """
        if not self.has_space():
            return -1

        slot_id = self.n_records
        self.n_records += 1
        self.set_by_slot_id(slot_id, record)

        return slot_id

    def delete_slot(self, slot_id: int) -> bool:
        """
        Marca un registro como eliminado sin liberar fisicamente su slot.
        """
        record = self.get_by_slot_id(slot_id)

        if record is None or record.deleted:
            return False

        record.deleted = True
        self.set_by_slot_id(slot_id, record)

        return True


class SequentialFile:
    def __init__(
        self, buffer_manager: BufferManager, page_size: int, record_format: str
    ):
        self.buffer_manager = buffer_manager
        self.file_manager = buffer_manager.file_manager
        self.page_size = page_size
        self.key_index = 0
        self.serializer = FixedLengthRecordSerializer(record_format)
        self.records_per_page = (
            page_size - PAGE_HEADER_SIZE
        ) // self.serializer.slot_size
        self.first_rid = None
        self.n_pages = 0
        self.n_records = 0
        self.n_deleted = 0
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
        return phys_page_id * self.records_per_page + slot_id

    def _int_to_rid(self, value):
        """
        Convierte una representacion entera a un RID.
        """
        if value == -1:
            return None

        return (value // self.records_per_page, value % self.records_per_page)

    def _make_rid(self, phys_page_id: int, slot_id: int):
        """
        Construye un RID a partir de pagina fisica y slot.
        """
        return phys_page_id, slot_id

    def _load_page(self, phys_page_id: int) -> FixedPage:
        """
        Obtiene una pagina del BufferManager y crea una interfaz
        FixedPage sobre el bytearray almacenado en el frame.
        """
        page_ba = self.buffer_manager.fetch_page(phys_page_id)

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
            return page.get_by_slot_id(slot_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _set_record(self, rid, record: Record):
        """
        Sobreescribe un registro identificado por su RID.
        """
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            page.set_by_slot_id(slot_id, record)
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

                first_record = page.get_by_slot_id(0)
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

        try:
            page.n_records = 0
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

        return phys_page_id

    def _insert_into_overflow(self, record: Record) -> tuple:
        """
        Inserta un registro en la pagina de overflow.
        """
        page = self._load_page(0)

        try:
            if not page.has_space():
                return None

            slot_id = page.insert(record)
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

    def insert(self, params):
        """
        Inserta un registro nuevo en overflow y actualiza la cadena
        logica para conservar el orden por clave.
        """
        record = Record(params)

        if self.first_rid is None:
            if self.n_pages == 0:
                self._append_page()

            page = self._load_page(1)

            try:
                rid = self._make_rid(1, page.insert(record))
                self.first_rid = rid
                self.n_records = 1
                self.buffer_manager.mark_dirty(1)
            finally:
                self.buffer_manager.unpin_page(1)

            self._write_header()
            return rid

        previous_rid, next_rid = self._find_neighbors(params[self.key_index])

        record.next_rid = next_rid

        new_rid = self._insert_into_overflow(record)

        if new_rid is None:
            self.reorganize()
            return self.insert(params)

        if previous_rid is None:
            self.first_rid = new_rid
        else:
            previous_record = self._get_record(previous_rid)
            previous_record.next_rid = new_rid
            self._set_record(previous_rid, previous_record)

        self.n_records += 1
        self._write_header()

        return new_rid

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

    def delete(self, key) -> bool:
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
                    record = page.get_by_slot_id(slot_id)
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
        records = []

        for _, record in self._iter_records():
            if not record.deleted:
                records.append(Record(record.params))

        records.sort(key=lambda record: record.params[self.key_index])
        required_pages = max(
            1, (len(records) + self.records_per_page - 1) // self.records_per_page
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
            phys_page_id = index // self.records_per_page + 1
            slot_id = index % self.records_per_page
            rid = self._make_rid(phys_page_id, slot_id)
            if index + 1 < len(records):
                next_rid = self._make_rid(
                    ((index + 1) // self.records_per_page) + 1,
                    (index + 1) % self.records_per_page,
                )
            else:
                next_rid = None

            record.next_rid = next_rid
            page = self._load_page(phys_page_id)

            try:
                page.n_records += 1
                page.set_by_slot_id(slot_id, record)
                self.buffer_manager.mark_dirty(phys_page_id)
            finally:
                self.buffer_manager.unpin_page(phys_page_id)

            if index == 0:
                self.first_rid = rid

        self.n_records = len(records)
        self.n_deleted = 0
        self._write_header()
