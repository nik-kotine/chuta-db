import struct
from storage.buffer_manager import BufferManager
from storage.file_manager import FileManager
from storage.rid import RID, RID_FORMAT, RID_SIZE, DELETED_SIZE, NULL_RID
from storage.seq_record import Record
from storage.pages.fixed_page import FixedPage
from storage.pages.variable_page import VariablePage
from storage.formats.data_types import return_format
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer
from storage.record_file import RecordFile
from indexes.external_sort import ExternalSorter

PAGE_SIZE = 4096

# n_pages, first_rid (page_id, slot_id), n_records, n_deleted,
# n_overflow_pages, n_overflow_records, overflow_tail_page
FILE_HEADER_FORMAT = ">i" + RID_FORMAT + "iiiii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

WASTED_RATIO = 0.3

OVERFLOW_RATIO = 0.3

MIN_RECORDS_FOR_OVERFLOW_CHECK = 20

SORT_BUDGET = 1000

SORTABLE_KEY_TYPES = (bool, int, float, str)

_TRACK_RID_FORMAT = ">ii"
_TRACK_RID_SIZE = struct.calcsize(_TRACK_RID_FORMAT)

class SequentialFile(RecordFile):
    def __init__(
        self,
        buffer_manager: BufferManager,
        record_format: list[str],
        file_manager: FileManager = None,
    ):
        self.buffer_manager = buffer_manager
        self.file_manager = file_manager or getattr(buffer_manager, "active_file", None)
        if self.file_manager is None:
            raise ValueError("SequentialFile necesita un FileManager para operar")
        if self.file_manager.page_size != PAGE_SIZE:
            raise ValueError(
                f"SequentialFile trabaja con paginas de {PAGE_SIZE} bytes y el "
                f"FileManager de '{self.file_manager.filename}' usa "
                f"{self.file_manager.page_size}"
            )
        self.page_size = PAGE_SIZE
        self.key_index = 0

        self.variable_length = any(return_format(t)[1] == -1 for t in record_format)

        if self.variable_length:
            self.serializer = VariableLengthRecordSerializer(record_format)
            self.page_class = VariablePage
        else:
            self.serializer = FixedLengthRecordSerializer(record_format)
            self.page_class = FixedPage

        self.first_rid: RID | None = None
        self.n_pages = 0
        self.n_records = 0
        self.n_deleted = 0
        self.reorganize_count = 0
        self.n_overflow_pages = 1
        self.n_overflow_records = 0
        self.overflow_tail = 0

        header = self.file_manager.read_header()
        if len(header) > 0:
            self._load_header()

    def _load_header(self):
        header = self.file_manager.read_header()
        if len(header) != FILE_HEADER_SIZE:
            raise RuntimeError("invalid or corrupt header file")

        (
            self.n_pages,
            first_rid_page, first_rid_slot,
            self.n_records, self.n_deleted,
            self.n_overflow_pages, self.n_overflow_records,
            self.overflow_tail,
        ) = struct.unpack(FILE_HEADER_FORMAT, header)

        if (first_rid_page, first_rid_slot) == (-1, -1):
            self.first_rid = None
        else:
            self.first_rid = self._make_rid(first_rid_page, first_rid_slot)

    def _write_header(self):
        first_rid = self.first_rid if self.first_rid is not None else NULL_RID
        header = struct.pack(
            FILE_HEADER_FORMAT,
            self.n_pages,
            first_rid.page_id,
            first_rid.slot_id,
            self.n_records,
            self.n_deleted,
            self.n_overflow_pages,
            self.n_overflow_records,
            self.overflow_tail,
        )
        self.file_manager.write_header(header)

    def _make_rid(self, phys_page_id: int, slot_id: int) -> RID:
        return RID(phys_page_id, slot_id)

    def _load_page(self, phys_page_id: int):
        page_ba = self.buffer_manager.fetch_page(phys_page_id, self.file_manager)
        if len(page_ba) < self.page_size:
            page_ba.extend(b"\x00" * (self.page_size - len(page_ba)))
        return self.page_class(page_ba, self.page_size, self.serializer)

    def _get_record(self, rid: RID) -> Record | None:
        if rid is None or rid == (-1, -1) or getattr(rid, "page_id", -1) == -1:
            return None

        phys_page_id, slot_id = rid 
        page = self._load_page(phys_page_id)
        try:
            return page.get_record(slot_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _set_record(self, rid: RID, record: Record):
        if rid is None or rid == (-1, -1) or getattr(rid, "page_id", -1) == -1:
            return
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)
        try:
            page.set_record(slot_id, record)
            self.buffer_manager.mark_dirty(phys_page_id, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _first_live_in_page(self, phys_page_id: int):
        page = self._load_page(phys_page_id)
        try:
            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)
                if record and not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _last_live_in_page(self, phys_page_id: int):
        page = self._load_page(phys_page_id)
        try:
            for slot_id in range(page.n_records - 1, -1, -1):
                record = page.get_record(slot_id)
                if record and not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _last_live_overall(self):
        for phys_page_id in range(self.n_pages, 0, -1):
            last = self._last_live_in_page(phys_page_id)
            if last is not None:
                slot_id, record = last
                return self._make_rid(phys_page_id, slot_id), record
        return None, None

    def _last_page_lt(self, key, duplicates_after: bool) -> int:
        if self.n_pages == 0:
            return 0

        low, high = 1, self.n_pages
        result = 0

        while low <= high:
            mid = (low + high) // 2
            page = self._load_page(mid)
            try:
                first = page.get_record(0)
                if (
                    first is not None
                    and (
                        first.params[self.key_index] < key
                        or (duplicates_after and first.params[self.key_index] == key)
                    )
                ):
                    result = mid
                    low = mid + 1
                else:
                    high = mid - 1
            finally:
                self.buffer_manager.unpin_page(mid, self.file_manager)

        return result

    def _main_neighbors(self, key, duplicates_after: bool):
        if self.n_pages == 0:
            return None, None, None, None

        hi = self._last_page_lt(key, duplicates_after=duplicates_after)
        next_rid, next_key = None, None
        next_page, next_slot = None, None

        if hi >= 1:
            page = self._load_page(hi)
            try:
                for slot_id in range(page.n_records):
                    record = page.get_record(slot_id)
                    if record is None or record.deleted:
                        continue
                    if record.params[self.key_index] > key or (
                        (not duplicates_after) and record.params[self.key_index] == key
                    ):
                        next_rid = self._make_rid(hi, slot_id)
                        next_key = record.params[self.key_index]
                        next_page, next_slot = hi, slot_id
                        break
            finally:
                self.buffer_manager.unpin_page(hi, self.file_manager)

        if next_rid is None:
            start = hi + 1 if hi < self.n_pages else self.n_pages + 1
            for page_id in range(start, self.n_pages + 1):
                first = self._first_live_in_page(page_id)
                if first is not None:
                    slot_id, record = first
                    next_rid = self._make_rid(page_id, slot_id)
                    next_key = record.params[self.key_index]
                    next_page, next_slot = page_id, slot_id
                    break

        prev_rid, prev_key = None, None
        if next_rid is not None:
            if next_page == hi:
                page = self._load_page(next_page)
                try:
                    for slot_id in range(next_slot - 1, -1, -1):
                        record = page.get_record(slot_id)
                        if record and not record.deleted:
                            prev_rid = self._make_rid(next_page, slot_id)
                            prev_key = record.params[self.key_index]
                            break
                finally:
                    self.buffer_manager.unpin_page(next_page, self.file_manager)

            if prev_rid is None:
                for page_id in range(next_page - 1, 0, -1):
                    last = self._last_live_in_page(page_id)
                    if last is not None:
                        slot_id, record = last
                        prev_rid = self._make_rid(page_id, slot_id)
                        prev_key = record.params[self.key_index]
                        break
        else:
            prev_rid, prev_record = self._last_live_overall()
            if prev_rid is not None:
                prev_key = prev_record.params[self.key_index]

        return prev_rid, prev_key, next_rid, next_key

    def _find_neighbors(self, key, duplicates_after: bool = True):
        if self.first_rid is None:
            return None, None

        main_prev_rid, main_prev_key, main_next_rid, main_next_key = (
            self._main_neighbors(key, duplicates_after=duplicates_after)
        )

        prev_rid, prev_key = main_prev_rid, main_prev_key
        next_rid, next_key = main_next_rid, main_next_key

        if main_prev_rid is not None:
            current_rid = self._get_record(main_prev_rid).next_rid
        else:
            current_rid = self.first_rid

        while current_rid is not None and current_rid != main_next_rid:
            record = self._get_record(current_rid)
            if record is None:
                break
            if record.deleted:
                current_rid = record.next_rid
                continue

            current_key = record.params[self.key_index]
            is_prev_candidate = current_key <= key if duplicates_after else current_key < key

            if is_prev_candidate:
                prev_rid, prev_key = current_rid, current_key
                current_rid = record.next_rid
            else:
                next_rid, next_key = current_rid, current_key
                break

        return prev_rid, next_rid

    def _ensure_page(self, phys_page_id: int):
        """
        Se asegura de que la pagina exista en el archivo y este vacia. Las paginas
        no son necesariamente contiguas en disco (las de overflow se reservan al
        final del archivo), asi que hay que poder completar el hueco.
        """
        self.file_manager.grow_to_page(phys_page_id)
        page = self._load_page(phys_page_id)
        try:
            page.reset()
            self.buffer_manager.mark_dirty(phys_page_id, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _append_page(self) -> int:
        """
        Agrega una pagina de datos al final del area de paginas principales
        (1..n_pages) y retorna su indice fisico.
        """
        phys_page_id = self.n_pages + 1
        self._ensure_page(phys_page_id)
        self.n_pages = phys_page_id
        return phys_page_id

    def _reset_overflow_page(self):
        """Deja la pagina 0 (la de overflow) limpia para volver a empezar."""
        self._ensure_page(0)

    def _insert_into_overflow(self, record: Record) -> RID:
        total_slot_size = self.serializer.get_size_of(record.params) + RID_SIZE + DELETED_SIZE

        current_page_id = self.overflow_tail
        if not 0 <= current_page_id <= self.n_pages:
            # puntero inconsistente (header editado a mano): reservamos pagina nueva
            # en vez de pisar una que puede tener registros
            current_page_id = self.file_manager.allocate_page()
            self.n_overflow_pages += 1
            self.overflow_tail = current_page_id
        # la pagina del overflow mas reciente todavia puede no existir
        self.file_manager.grow_to_page(current_page_id)

        page = self._load_page(current_page_id)
        try:
            if page.ensure_initialized():
                self.buffer_manager.mark_dirty(current_page_id, self.file_manager)

            if not page.has_space(total_slot_size):
                if page.n_records == 0:
                    raise RuntimeError("record is too big for insertion")

                self.buffer_manager.unpin_page(current_page_id, self.file_manager)
                current_page_id = self.file_manager.allocate_page()
                self.n_overflow_pages += 1
                self.overflow_tail = current_page_id
                page = self._load_page(current_page_id)
                page.reset()
                self.buffer_manager.mark_dirty(current_page_id, self.file_manager)

                if not page.has_space(total_slot_size):
                    raise RuntimeError("record is too big for insertion")

            slot_id = page.insert(record)
            self.buffer_manager.mark_dirty(current_page_id, self.file_manager)

            return self._make_rid(current_page_id, slot_id)
        finally:
            self.buffer_manager.unpin_page(current_page_id, self.file_manager)

    def _iter_records(self, start_rid: RID | None = None):
        current_rid = self.first_rid if start_rid is None else start_rid
        while current_rid is not None:
            record = self._get_record(current_rid)
            if record is None:
                break
            yield current_rid, record
            current_rid = record.next_rid

    def insert(self, params):
        record = Record(params)
        total_slot_size = self.serializer.get_size_of(record.params) + RID_SIZE + DELETED_SIZE

        if self.first_rid is None:
            if self.n_pages == 0:
                page_id = self._append_page()
            else:
                page_id = 1
                self._ensure_page(page_id)
            page = self._load_page(page_id)

            try:
                if page.ensure_initialized():
                    self.buffer_manager.mark_dirty(page_id, self.file_manager)
                if not page.has_space(total_slot_size):
                    raise RuntimeError("Record is too big for insertion")

                rid = self._make_rid(page_id, page.insert(record))
                self.first_rid = rid
                self.n_records = 1
                self.buffer_manager.mark_dirty(page_id, self.file_manager)
            finally:
                self.buffer_manager.unpin_page(page_id, self.file_manager)

            self._write_header()
            return rid

        previous_rid, next_rid = self._find_neighbors(
            params[self.key_index], duplicates_after=True
        )

        record.next_rid = next_rid
        new_rid = self._insert_into_overflow(record)
        self.n_overflow_records += 1

        if previous_rid is None:
            self.first_rid = new_rid
        else:
            previous_record = self._get_record(previous_rid)
            if previous_record is not None:
                previous_record.next_rid = new_rid
                self._set_record(previous_rid, previous_record)

        self.n_records += 1

        if (
            self.n_records >= MIN_RECORDS_FOR_OVERFLOW_CHECK
            and self.n_overflow_records / self.n_records >= OVERFLOW_RATIO
        ):
            new_rid = self.reorganize(track_rid=new_rid)
        else:
            self._write_header()

        return new_rid

    def fetch(self, rid: RID) -> list | None:
        record = self._get_record(rid)
        if record is None or record.deleted:
            return None
        return list(record.params)

    def search(self, key):
        results = []
        _, current_rid = self._find_neighbors(key, duplicates_after=False)
        if current_rid is None:
            return results

        for _, record in self._iter_records(current_rid):
            current_key = record.params[self.key_index]
            if current_key > key:
                break
            if current_key == key and not record.deleted:
                results.append(record)
        return results

    def delete(self, rid: RID) -> bool:
        if not isinstance(rid, RID):
            return self.delete_by_key(rid)

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
        deleted_any = False
        pages_touched = set()
        _, current_rid = self._find_neighbors(key, duplicates_after=False)
        if current_rid is None:
            return False

        for current_rid, record in self._iter_records(current_rid):
            current_key = record.params[self.key_index]
            if current_key > key:
                break

            if current_key == key and not record.deleted:
                record.deleted = True
                self._set_record(current_rid, record)
                pages_touched.add(current_rid.page_id)
                self.n_deleted += 1
                self.n_records -= 1
                deleted_any = True

        if deleted_any:
            page_emptied = any(
                1 <= phys_page_id <= self.n_pages and self._first_live_in_page(phys_page_id) is None
                for phys_page_id in pages_touched
            )
            if page_emptied or self._wasted_space_ratio() >= WASTED_RATIO:
                self.reorganize()

        self._write_header()
        return deleted_any

    def _wasted_space_ratio(self) -> float:
        total_slots = self.n_records + self.n_deleted
        if total_slots == 0:
            return 0.0
        return self.n_deleted / total_slots

    def _sort_key(self, record: Record):
        """Devuelve la clave de ordenamiento del registro, validando el tipo."""
        key = record.params[self.key_index]
        if not isinstance(key, SORTABLE_KEY_TYPES):
            raise RuntimeError(
                f"el ordenamiento externo de SequentialFile no soporta claves de tipo "
                f"{type(key).__name__} (soportadas: int, float, bool, str)"
            )
        return key

    def _pack_sort_value(self, record: Record, old_rid: RID) -> bytes:
        """Serializa el registro y le pega su RID actual para poder rastrearlo."""
        return (
            self.serializer.serialize(record.params)
            + struct.pack(_TRACK_RID_FORMAT, old_rid.page_id, old_rid.slot_id)
        )

    def _unpack_sort_value(self, value: bytes):
        """Deshace `_pack_sort_value`: devuelve (params, rid viejo)."""
        params = self.serializer.deserialize(value[:-_TRACK_RID_SIZE])
        old_rid = struct.unpack(_TRACK_RID_FORMAT, value[-_TRACK_RID_SIZE:])
        return list(params), self._make_rid(old_rid[0], old_rid[1])

    def reorganize(self, track_rid: RID | None = None) -> RID | None:
        """
        Reescribe el archivo ordenado por clave usando ExternalSorter, asi la
        memoria usada es O(SORT_BUDGET) y no O(cantidad de registros).

        La lectura termina antes de la escritura: primero se recorre la lista
        enlazada y se vuelcan runs, recien despues se pisan las paginas 1..m.
        """
        self.reorganize_count += 1
        sorter = ExternalSorter(budget=SORT_BUDGET)

        def items():
            for old_rid, record in self._iter_records():
                if record.deleted:
                    continue
                yield self._sort_key(record), self._pack_sort_value(record, old_rid)

        try:
            ordenados = sorter.spill(items())

            page_id = 0
            page = None
            first_rid = None
            prev_rid = None
            prev_slot = None
            prev_page_id = None
            prev_record = None
            n_records = 0
            new_rid_for_tracked = None

            for _, value in ordenados:
                params, old_rid = self._unpack_sort_value(value)
                record = Record(params)
                record_size = (
                    self.serializer.get_size_of(record.params) + RID_SIZE + DELETED_SIZE
                )

                if page is not None and not page.has_space(record_size):
                    self.buffer_manager.mark_dirty(page_id, self.file_manager)
                    self.buffer_manager.unpin_page(page_id, self.file_manager)
                    page = None

                if page is None:
                    # se reutilizan las paginas 1..n_pages y solo se agrega una
                    # nueva al final del area de datos si hacen falta mas
                    page_id = page_id + 1 if page_id > 0 else 1
                    self.file_manager.grow_to_page(page_id)
                    page = self._load_page(page_id)
                    page.reset()
                    self.buffer_manager.mark_dirty(page_id, self.file_manager)

                    if not page.has_space(record_size):
                        raise RuntimeError("Record is too big for insertion")

                slot_id = page.insert(record)
                new_rid = self._make_rid(page_id, slot_id)

                # el next_rid del registro anterior se completa en el acto, asi no
                # hace falta guardar todos los RIDs en memoria. Si el anterior esta
                # en la pagina que todavia tenemos abierta se parchea sobre ella
                # (perder el pin a mitad de camino dejaria la pagina en manos del
                # buffer pool); si ya la cerramos, se resuelve por su RID.
                if prev_record is not None:
                    prev_record.next_rid = new_rid
                    if prev_page_id == page_id:
                        page.set_record(prev_slot, prev_record)
                    else:
                        self._set_record(prev_rid, prev_record)

                if first_rid is None:
                    first_rid = new_rid
                if track_rid is not None and old_rid == track_rid:
                    new_rid_for_tracked = new_rid

                prev_rid = new_rid
                prev_slot = slot_id
                prev_page_id = page_id
                prev_record = record
                n_records += 1

            if page is not None:
                self.buffer_manager.mark_dirty(page_id, self.file_manager)
                self.buffer_manager.unpin_page(page_id, self.file_manager)

            self.first_rid = first_rid
            self.n_pages = page_id
            self.n_records = n_records
            self.n_deleted = 0
            self.n_overflow_pages = 1
            self.n_overflow_records = 0
            self.overflow_tail = 0
            self._reset_overflow_page()
            self._truncate(page_id)
            self._write_header()

            return new_rid_for_tracked
        finally:
            sorter.cleanup()

    def scan(self):
        if self.first_rid is None:
            return

        for rid, record in self._iter_records():
            if not record.deleted:
                yield rid, list(record.params)

    def close(self):
        self.buffer_manager.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _truncate(self, n_main_pages: int):
        self.buffer_manager.flush_file(self.file_manager)
        self.file_manager.truncate(
            self.file_manager.file_header_size + (n_main_pages + 1) * self.page_size
        )