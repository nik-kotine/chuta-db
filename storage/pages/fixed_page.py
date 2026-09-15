import struct
from storage.pages.seq_page import Page
from storage.seq_record import Record
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.rid import (
    RID, NULL_RID, 
    RID_FORMAT, RID_SIZE, 
    DELETED_FORMAT, DELETED_SIZE
)

PAGE_HEADER_FORMAT = ">i"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)


class FixedPage(Page):
    PAGE_HEADER_FORMAT = PAGE_HEADER_FORMAT
    PAGE_HEADER_SIZE = PAGE_HEADER_SIZE

    def __init__(
        self,
        page_ba: bytearray,
        page_size: int,
        serializer: FixedLengthRecordSerializer,
    ):
        super().__init__(page_ba, page_size, serializer)
        self.max_records = (page_size - self.PAGE_HEADER_SIZE) // serializer.slot_size

    @property
    def n_records(self) -> int:
        return struct.unpack_from(self.PAGE_HEADER_FORMAT, self.page_ba, 0)[0]

    @n_records.setter
    def n_records(self, value: int):
        struct.pack_into(self.PAGE_HEADER_FORMAT, self.page_ba, 0, value)

    @property
    def free_slots(self) -> int:
        return self.max_records - self.n_records

    def has_space(self, size=None) -> bool:
        return self.n_records < self.max_records

    def _slot_offset(self, slot_id: int) -> int:
        if slot_id < 0 or slot_id >= self.max_records:
            raise RuntimeError("index out of range")
        return self.PAGE_HEADER_SIZE + slot_id * self.serializer.slot_size

    def get_record(self, slot_id: int) -> Record | None:
        if slot_id < 0 or slot_id >= self.n_records:
            return None

        offset = self._slot_offset(slot_id)
        
        # 1. Leer datos del usuario
        record_data = bytes(self.page_ba[offset : offset + self.serializer.record_size])
        params = self.serializer.deserialize(record_data)
        
        # 2. Leer siguiente RID
        offset += self.serializer.record_size
        next_rid_tuple = struct.unpack_from(RID_FORMAT, self.page_ba, offset)
        next_rid = None if next_rid_tuple == (-1, -1) else RID(*next_rid_tuple)
        
        # 3. Leer bandera deleted
        offset += RID_SIZE
        deleted = struct.unpack_from(DELETED_FORMAT, self.page_ba, offset)[0]

        return Record(list(params), next_rid, deleted)

    def set_record(self, slot_id: int, record: Record):
        if slot_id < 0 or slot_id >= self.n_records:
            raise RuntimeError("index out of range")

        offset = self._slot_offset(slot_id)
        
        # 1. Guardar datos serializados
        record_bytes = self.serializer.serialize(record.params)
        self.page_ba[offset : offset + self.serializer.record_size] = record_bytes
        
        # 2. Guardar next_rid
        offset += self.serializer.record_size
        next_rid = record.next_rid if record.next_rid is not None else NULL_RID
        struct.pack_into(RID_FORMAT, self.page_ba, offset, *next_rid)
        
        # 3. Guardar deleted
        offset += RID_SIZE
        struct.pack_into(DELETED_FORMAT, self.page_ba, offset, record.deleted)

    def insert(self, record: Record) -> int:
        if not self.has_space():
            return -1

        slot_id = self.n_records
        self.n_records += 1
        self.set_record(slot_id, record)
        return slot_id

    def delete_slot(self, slot_id: int) -> bool:
        record = self.get_record(slot_id)
        if record is None or record.deleted:
            return False

        record.deleted = True
        self.set_record(slot_id, record)
        return True

    def reset(self):
        self.n_records = 0