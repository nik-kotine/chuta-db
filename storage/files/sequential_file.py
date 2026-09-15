import struct
from storage.buffer_manager import BufferManager
from storage.rid import RID, RID_SIZE, DELETED_SIZE
from storage.seq_record import Record
from storage.pages.fixed_page import FixedPage
from storage.pages.variable_page import VariablePage
from storage.formats.data_types import return_format
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer
from storage.record_file import RecordFile

SLOT_ID_BITS = 16

FILE_HEADER_FORMAT = ">iiii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

WASTED_RATIO = 0.5

class SequentialFile(RecordFile):
    def __init__(
        self,
        buffer_manager: BufferManager,
        page_size: int,
        record_format: list[str],
    ):
        self.buffer_manager = buffer_manager
        self.file_manager = buffer_manager.file_manager
        self.page_size = page_size
        self.key_index = 0

        # Determinación precisa de tipos de longitud variable
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

        header = self.file_manager.read_header()
        if len(header) > 0:
            self._load_header()

    def _load_header(self):
        header = self.file_manager.read_header()
        if len(header) != FILE_HEADER_SIZE:
            raise RuntimeError("invalid or corrupt header file")

        self.n_pages, first_rid, self.n_records, self.n_deleted = \
            struct.unpack(FILE_HEADER_FORMAT, header)

        self.first_rid = None if first_rid == -1 else self._int_to_rid(first_rid)

    def _write_header(self):
        first_rid_int = self._rid_to_int(self.first_rid)
        header = struct.pack(
            FILE_HEADER_FORMAT,
            self.n_pages,
            first_rid_int,
            self.n_records,
            self.n_deleted
        )
        self.file_manager.write_header(header)

    def _rid_to_int(self, rid: RID | None) -> int:
        if rid is None:
            return -1
        page_id, slot_id = rid
        if page_id == -1:
            return -1
        return (page_id << SLOT_ID_BITS) | slot_id

    def _int_to_rid(self, value: int) -> RID | None:
        if value == -1:
            return None
        return RID(value >> SLOT_ID_BITS, value % (1 << SLOT_ID_BITS))

    def _make_rid(self, phys_page_id: int, slot_id: int) -> RID:
        return RID(phys_page_id, slot_id)

    def _load_page(self, phys_page_id: int):
        page_ba = self.buffer_manager.fetch_page(phys_page_id)
        if len(page_ba) < self.page_size:
            page_ba.extend(b"\x00" * (self.page_size - len(page_ba)))
        return self.page_class(page_ba, self.page_size, self.serializer)

    def _get_record(self, rid: RID) -> Record | None:
        # Guarda de seguridad contra RIDs nulos o inválidos
        if rid is None or rid == (-1, -1) or getattr(rid, "page_id", -1) == -1:
            return None

        phys_page_id, slot_id = rid 
        page = self._load_page(phys_page_id)
        try:
            return page.get_record(slot_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _set_record(self, rid: RID, record: Record):
        if rid is None or rid == (-1, -1) or getattr(rid, "page_id", -1) == -1:
            return
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)
        try:
            page.set_record(slot_id, record)
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _first_live_in_page(self, phys_page_id: int):
        page = self._load_page(phys_page_id)
        try:
            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)
                if record and not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _last_live_in_page(self, phys_page_id: int):
        page = self._load_page(phys_page_id)
        try:
            for slot_id in range(page.n_records - 1, -1, -1):
                record = page.get_record(slot_id)
                if record and not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

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
                self.buffer_manager.unpin_page(mid)

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
                self.buffer_manager.unpin_page(hi)

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
                    self.buffer_manager.unpin_page(next_page)

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

    def _overflow_neighbors(self, key, duplicates_after: bool):
        page = self._load_page(0)
        try:
            prev_rid, prev_key = None, None
            next_rid, next_key = None, None

            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)
                if record is None or record.deleted:
                    continue

                current_key = record.params[self.key_index]
                rid = self._make_rid(0, slot_id)

                if duplicates_after:
                    if current_key <= key:
                        if prev_key is None or current_key >= prev_key:
                            prev_rid, prev_key = rid, current_key
                    else:
                        if next_key is None or current_key < next_key:
                            next_rid, next_key = rid, current_key
                else:
                    if current_key < key:
                        if prev_key is None or current_key > prev_key:
                            prev_rid, prev_key = rid, current_key
                    else:
                        if next_key is None or current_key < next_key:
                            next_rid, next_key = rid, current_key

            return prev_rid, prev_key, next_rid, next_key
        finally:
            self.buffer_manager.unpin_page(0)

    def _find_neighbors(self, key, duplicates_after: bool = True):
        if self.first_rid is None:
            return None, None

        main_prev_rid, main_prev_key, main_next_rid, main_next_key = (
            self._main_neighbors(key, duplicates_after=duplicates_after)
        )
        ov_prev_rid, ov_prev_key, ov_next_rid, ov_next_key = (
            self._overflow_neighbors(key, duplicates_after=duplicates_after)
        )

        if ov_next_rid is not None and (main_next_rid is None or ov_next_key < main_next_key):
            next_rid = ov_next_rid
        else:
            next_rid = main_next_rid

        if main_prev_rid is not None and (ov_prev_rid is None or main_prev_key > ov_prev_key):
            prev_rid = main_prev_rid
        else:
            prev_rid = ov_prev_rid

        return prev_rid, next_rid

    def _append_page(self) -> int:
        phys_page_id = self.file_manager.allocate_page()
        self.n_pages += 1
        page = self._load_page(phys_page_id)
        try:
            page.reset()
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)
        return phys_page_id

    def _insert_into_overflow(self, record: Record) -> RID | None:
        total_slot_size = self.serializer.get_size_of(record.params) + RID_SIZE + DELETED_SIZE
        page = self._load_page(0)

        try:
            if page.ensure_initialized():
                self.buffer_manager.mark_dirty(0)

            if not page.has_space(total_slot_size):
                if page.n_records == 0:
                    raise RuntimeError("record is too big for insertion")
                return None

            slot_id = page.insert(record)
            self.buffer_manager.mark_dirty(0)

            return self._make_rid(0, slot_id)
        finally:
            self.buffer_manager.unpin_page(0)

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
                self._append_page()

            page = self._load_page(1)

            try:
                if page.ensure_initialized():
                    self.buffer_manager.mark_dirty(1)
                if not page.has_space(total_slot_size):
                    raise RuntimeError("Record is too big for insertion")

                rid = self._make_rid(1, page.insert(record))
                self.first_rid = rid
                self.n_records = 1
                self.buffer_manager.mark_dirty(1)
            finally:
                self.buffer_manager.unpin_page(1)

            self._write_header()
            return rid

        previous_rid, next_rid = self._find_neighbors(
            params[self.key_index], duplicates_after=True
        )

        record.next_rid = next_rid
        new_rid = self._insert_into_overflow(record)

        if new_rid is None:
            self.reorganize()
            return self.insert(params)

        if previous_rid is None:
            self.first_rid = new_rid
        else:
            previous_record = self._get_record(previous_rid)
            if previous_record is not None:
                previous_record.next_rid = new_rid
                self._set_record(previous_rid, previous_record)

        self.n_records += 1
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
                phys_page_id >= 1 and self._first_live_in_page(phys_page_id) is None
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

    def reorganize(self):
        self.reorganize_count += 1
        records = [
            Record(record.params)
            for _, record in self._iter_records()
            if not record.deleted
        ]

        if len(records) == 0:
            self.first_rid = None
            self.n_records = 0
            self.n_deleted = 0
            self._truncate(0)
            self._write_header()
            return

        records.sort(key=lambda r: r.params[self.key_index])

        page = self._load_page(0)
        try:
            page.reset()
            self.buffer_manager.mark_dirty(0)
        finally:
            self.buffer_manager.unpin_page(0)

        rids = []
        pageindex = 1
        page = None

        for record in records:
            record_size = self.serializer.get_size_of(record.params) + RID_SIZE + DELETED_SIZE

            if page is not None and not page.has_space(record_size):
                self.buffer_manager.mark_dirty(pageindex)
                self.buffer_manager.unpin_page(pageindex)
                pageindex += 1
                page = None

            if page is None:
                if self.n_pages < pageindex:
                    self._append_page()
                page = self._load_page(pageindex)
                page.reset()

                if not page.has_space(record_size):
                    raise RuntimeError("Record is too big for insertion")

            slot_id = page.insert(record)
            rids.append(self._make_rid(pageindex, slot_id))

        self.buffer_manager.mark_dirty(pageindex)
        self.buffer_manager.unpin_page(pageindex)

        self.first_rid = rids[0]
        for index in range(len(rids) - 1):
            rec = self._get_record(rids[index])
            if rec is not None:
                rec.next_rid = rids[index + 1]
                self._set_record(rids[index], rec)

        self.n_pages = pageindex
        self.n_records = len(rids)
        self.n_deleted = 0
        self._truncate(pageindex)
        self._write_header()

    def scan(self):
        if self.first_rid is None:
            return

        for rid, record in self._iter_records():
            if not record.deleted:
                yield rid, list(record.params)

    def _truncate(self, n_main_pages: int):
        self.buffer_manager.flush_all()
        self.file_manager.truncate(
            self.file_manager.file_header_size + (n_main_pages + 1) * self.page_size
        )