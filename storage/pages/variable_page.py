import struct
from storage.pages.seq_page import Page
from storage.seq_record import Record
from storage.rid import (
    RID, NULL_RID, 
    RID_FORMAT, RID_SIZE, 
    DELETED_FORMAT, DELETED_SIZE
)

SLOT_FORMAT = ">ii"
SLOT_SIZE = struct.calcsize(SLOT_FORMAT)

class VariablePage(Page):
    PAGE_HEADER_FORMAT = ">ii"
    PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)

    @property
    def offset(self) -> int:
        return struct.unpack_from(">i", self.page_ba, 0)[0]

    @offset.setter
    def offset(self, offset: int):
        struct.pack_into(">i", self.page_ba, 0, offset)

    @property
    def size(self) -> int:
        return struct.unpack_from(">i", self.page_ba, 4)[0]

    @size.setter
    def size(self, size: int):
        struct.pack_into(">i", self.page_ba, 4, size)

    @property
    def n_records(self) -> int:
        return self.size

    def has_space(self, size=None) -> bool:
        if size is None:
            return False
        return self.offset - (self.PAGE_HEADER_SIZE + self.size * SLOT_SIZE) \
            >= size + SLOT_SIZE

    def _slot_offset(self, slot_id: int) -> int:
        if slot_id < 0 or slot_id >= self.size:
            raise RuntimeError("index out of range")
        return self.PAGE_HEADER_SIZE + slot_id * SLOT_SIZE

    def get_record(self, slot_id: int) -> Record | None:
        if slot_id < 0 or slot_id >= self.size:
            return None

        slot_offset = self._slot_offset(slot_id)
        offset, size = struct.unpack_from(
            SLOT_FORMAT, self.page_ba, slot_offset
        )

        record_byte_len = size - RID_SIZE - DELETED_SIZE
        record_data = bytes(self.page_ba[offset: offset + record_byte_len])
        params = self.serializer.deserialize(record_data)

        next_rid_tuple = struct.unpack_from(
            ">" + RID_FORMAT, self.page_ba, offset + record_byte_len
        )

        if next_rid_tuple == (-1, -1):
            next_rid = None
        else:
            next_rid = RID(*next_rid_tuple)

        deleted = struct.unpack_from(
            DELETED_FORMAT, self.page_ba, offset + record_byte_len + RID_SIZE
        )[0]

        return Record(params, next_rid, deleted)

    def set_record(self, slot_id: int, record: Record):
        if slot_id < 0 or slot_id >= self.size:
            raise RuntimeError("index out of range")

        slot_offset = self._slot_offset(slot_id)
        offset, size = struct.unpack_from(
            SLOT_FORMAT, self.page_ba, slot_offset
        )

        record_bytes = self.serializer.serialize(record.params)

        if len(record_bytes) != size - RID_SIZE - DELETED_SIZE:
            raise RuntimeError("record size does not match the stored size")

        self.page_ba[offset: offset + len(record_bytes)] = record_bytes

        next_rid = record.next_rid if record.next_rid is not None else NULL_RID

        struct.pack_into(
            ">" + RID_FORMAT,
            self.page_ba,
            offset + len(record_bytes),
            *next_rid,
        )
        struct.pack_into(
            DELETED_FORMAT,
            self.page_ba,
            offset + len(record_bytes) + RID_SIZE, record.deleted
        )

    def insert(self, record: Record) -> int:
        record_bytes = self.serializer.serialize(record.params)
        record_size = len(record_bytes) + RID_SIZE + DELETED_SIZE

        if not self.has_space(record_size):
            return -1

        slot_id = self.size
        self.size += 1
        self.offset -= record_size

        struct.pack_into(
            SLOT_FORMAT,
            self.page_ba,
            self.PAGE_HEADER_SIZE + slot_id * SLOT_SIZE,
            self.offset,
            record_size,
        )

        self.page_ba[
            self.offset: self.offset + len(record_bytes)
        ] = record_bytes

        next_rid = record.next_rid if record.next_rid is not None else NULL_RID

        struct.pack_into(
            ">" + RID_FORMAT,
            self.page_ba,
            self.offset + len(record_bytes),
            *next_rid,
        )
        struct.pack_into(
            DELETED_FORMAT,
            self.page_ba,
            self.offset + len(record_bytes) + RID_SIZE, record.deleted
        )

        return slot_id

    def delete_slot(self, slot_id: int) -> bool:
        record = self.get_record(slot_id)
        if record is None or record.deleted:
            return False

        record.deleted = True
        self.set_record(slot_id, record)
        return True

    def reset(self):
        self.size = 0
        self.offset = self.page_size

    def ensure_initialized(self) -> bool:
        if self.size == 0 and self.offset == 0:
            self.offset = self.page_size
            return True
        return False