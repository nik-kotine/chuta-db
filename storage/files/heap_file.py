import os
import struct
from storage.record_file import RecordFile
from storage.formats.data_types import return_format
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer
from storage.rid import RID, RID_SIZE, DELETED_SIZE
from storage.seq_record import Record
from storage.pages.variable_page import VariablePage, SLOT_SIZE
from storage.buffer_manager import BufferManager
from storage.file_manager import FileManager

PAGE_SIZE = 4096
HEADER_SIZE = VariablePage.PAGE_HEADER_SIZE

DIR_HEADER_FORMAT = ">II"
DIR_HEADER_SIZE = struct.calcsize(DIR_HEADER_FORMAT)
DIR_ENTRY_FORMAT = ">H"
DIR_ENTRY_SIZE = struct.calcsize(DIR_ENTRY_FORMAT)
ENTRIES_PER_DIR_PAGE = (PAGE_SIZE - DIR_HEADER_SIZE) // DIR_ENTRY_SIZE
NULL_DIR_PAGE = 0

MAX_RECORD_SIZE = PAGE_SIZE - HEADER_SIZE - SLOT_SIZE - RID_SIZE - DELETED_SIZE

class HeapFile(RecordFile):
    def __init__(self, filename: str, buffer_manager: BufferManager, record_format: list[str] | str,
                 file_manager: FileManager = None):
        self.filename=filename
        self.buffer_manager = buffer_manager
        self.file_manager = file_manager or getattr(buffer_manager, "active_file", None)
        if self.file_manager is None:
            raise ValueError("HeapFile necesita un FileManager para operar")
        self.record_format = record_format
        if isinstance(record_format, str):
            self.record_format = [record_format]

        self.variable_length = any(return_format(t)[1] == -1 for t in self.record_format)

        if self.variable_length:
            self.serializer = VariableLengthRecordSerializer(self.record_format)
        else:
            self.serializer = FixedLengthRecordSerializer(self.record_format)

        file_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        is_new = file_size <= self.file_manager.file_header_size
        self._dir_page_ids=[]

        if is_new:
            page_id = self.file_manager.allocate_page()
            if page_id != 0:
                raise RuntimeError(f"Expected page_id 0 but got {page_id}")

            self.page_count=0
            self._dir_page_ids=[0]

            dir_data = self.buffer_manager.fetch_page(0, self.file_manager)
            struct.pack_into(DIR_HEADER_FORMAT, dir_data, 0, self.page_count, NULL_DIR_PAGE)
            self.buffer_manager.mark_dirty(0, self.file_manager)
            self.buffer_manager.unpin_page(0, self.file_manager)
        else:
            page_id=0
            while True:
                self._dir_page_ids.append(page_id)
                dir_data = self.buffer_manager.fetch_page(page_id, self.file_manager)
                next_dir_page_id = struct.unpack_from(DIR_HEADER_FORMAT, dir_data, 0)[1]

                if page_id == 0:
                    self.page_count = struct.unpack_from(DIR_HEADER_FORMAT, dir_data, 0)[0]

                self.buffer_manager.unpin_page(page_id, self.file_manager)

                if next_dir_page_id==NULL_DIR_PAGE:
                    break
                page_id=next_dir_page_id

    def _entry_location(self, page_id: int):
        flat_index = page_id - 1
        dir_index = flat_index // ENTRIES_PER_DIR_PAGE
        entry_index = flat_index % ENTRIES_PER_DIR_PAGE
        offset = DIR_HEADER_SIZE + entry_index*DIR_ENTRY_SIZE
        return dir_index, offset

    def _get_free_space(self, page_id: int) -> int:
        dir_index, offset = self._entry_location(page_id)
        dir_page_id = self._dir_page_ids[dir_index]

        dir_data = self.buffer_manager.fetch_page(dir_page_id, self.file_manager)
        free_space = struct.unpack_from(DIR_ENTRY_FORMAT, dir_data, offset)[0]
        self.buffer_manager.unpin_page(dir_page_id, self.file_manager)
        return free_space

    def _set_free_space(self, page_id: int, free_bytes: int):
        dir_index, offset = self._entry_location(page_id)
        dir_page_id = self._dir_page_ids[dir_index]

        dir_data = self.buffer_manager.fetch_page(dir_page_id, self.file_manager)
        struct.pack_into(DIR_ENTRY_FORMAT, dir_data, offset, free_bytes)

        self.buffer_manager.mark_dirty(dir_page_id, self.file_manager)
        self.buffer_manager.unpin_page(dir_page_id, self.file_manager)

    def _update_page_count(self):
        dir_data = self.buffer_manager.fetch_page(0, self.file_manager)
        struct.pack_into(">I", dir_data, 0, self.page_count)
        self.buffer_manager.mark_dirty(0, self.file_manager)
        self.buffer_manager.unpin_page(0, self.file_manager)

    def _page_offset(self, page_id: int) -> int:
        return page_id * PAGE_SIZE

    def _load(self, page_id: int) -> VariablePage:
        raw = self.buffer_manager.fetch_page(page_id, self.file_manager)
        page = VariablePage(raw, PAGE_SIZE, self.serializer)
        page.page_id = page_id
        return page

    def _sync_page(self, page: VariablePage):
        self.buffer_manager.mark_dirty(page.page_id, self.file_manager)
        self.buffer_manager.unpin_page(page.page_id, self.file_manager)
        self._set_free_space(page.page_id, page.free_space_bytes)

    @property
    def next_page_id(self) -> int:
        return len(self._dir_page_ids) + self.page_count

    def _needs_new_dir_page(self) -> bool:
        dir_index = (self.next_page_id - 1) // ENTRIES_PER_DIR_PAGE
        return dir_index >= len(self._dir_page_ids)

    def _add_dir_page(self):
        new_dir_id = self.next_page_id
        last_dir_id = self._dir_page_ids[-1]

        last_data = self.buffer_manager.fetch_page(last_dir_id, self.file_manager)
        struct.pack_into(">I", last_data, 4, new_dir_id)
        self.buffer_manager.mark_dirty(last_dir_id, self.file_manager)
        self.buffer_manager.unpin_page(last_dir_id, self.file_manager)

        allocated_id = self.file_manager.allocate_page()
        if allocated_id != new_dir_id:
            new_dir_id = allocated_id

        new_data =self.buffer_manager.fetch_page(new_dir_id, self.file_manager)
        struct.pack_into(DIR_HEADER_FORMAT, new_data, 0, 0, NULL_DIR_PAGE)
        self.buffer_manager.mark_dirty(new_dir_id, self.file_manager)
        self.buffer_manager.unpin_page(new_dir_id, self.file_manager)

        self._dir_page_ids.append(new_dir_id)

    def _is_data_page(self, page_id: int) -> bool:
        return 1 <= page_id < self.next_page_id and page_id not in self._dir_page_ids

    def _new_page(self) -> VariablePage:
        if self._needs_new_dir_page():
            self._add_dir_page()

        new_page_id = self.file_manager.allocate_page()
        raw_data = self.buffer_manager.fetch_page(new_page_id, self.file_manager)
        page = VariablePage(raw_data, PAGE_SIZE, self.serializer)

        page.page_id = new_page_id
        page.reset()

        self.page_count += 1
        self._update_page_count()
        self._set_free_space(new_page_id, page.free_space_bytes)

        return page
    
    def insert(self, values) -> RID:
        record = Record(values)
        record_data_size = self.serializer.get_size_of(values)

        if record_data_size > MAX_RECORD_SIZE:
            raise ValueError(f"Record's length exceeds maximum: {record_data_size} bytes, maximum {MAX_RECORD_SIZE}")

        needed = record_data_size + RID_SIZE + DELETED_SIZE + SLOT_SIZE
        for page_id in range(1, self.next_page_id):
            if page_id in self._dir_page_ids:
                continue
            if self._get_free_space(page_id) < needed:
                continue
            page = self._load(page_id)
            slot_id = page.insert(record)
            self._sync_page(page)
            return RID(page_id, slot_id)

        page = self._new_page()
        slot_id = page.insert(record)
        self._sync_page(page)
        return RID(page.page_id, slot_id)

    def fetch(self, rid: RID):
        page_id, slot_id = rid
        if not self._is_data_page(page_id):
            return None
        page = self._load(page_id)
        record = page.get_record(slot_id)
        self.buffer_manager.unpin_page(page_id, self.file_manager)
        if record is None:
            return None
        return list(record.params)

    def delete(self, rid: RID) -> bool:
        page_id, slot_id = rid
        if not self._is_data_page(page_id):
            return False
        page = self._load(page_id)
        ok = page.delete_record(slot_id)
        if ok:
            self._sync_page(page)
        else:
            self.buffer_manager.unpin_page(page_id, self.file_manager)
        return ok

    def compact(self, page_id: int):
        if not self._is_data_page(page_id):
            return
        page=self._load(page_id)
        page.defragment()
        self._sync_page(page)

    def reorganize(self):
        for page_id in range(1,self.next_page_id):
            if page_id in self._dir_page_ids:
                continue
            self.compact(page_id)

    def scan(self):
        for page_id in range(1, self.next_page_id):
            if page_id in self._dir_page_ids:
                continue

            page = self._load(page_id)

            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)
                if record is not None:
                    yield RID(page_id, slot_id), list(record.params)

            self.buffer_manager.unpin_page(page_id, self.file_manager)

    def close(self):
        self.buffer_manager.close(self.file_manager)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
