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

NULL_SLOT = -1  # marca "no hay ningun slot muerto que reciclar" en la free list

class VariablePage(Page):
    """
    Página de slots de tamaño variable, usada tanto por HeapFile como por
    SequentialFile (caso variable-length). La tabla de slots crece desde el
    inicio de la página y los datos desde el final; además mantiene una
    free-list de slots muertos (first_free_slot) para que quien la use pueda
    reciclar el espacio de un registro eliminado en un insert posterior
    (HeapFile la usa vía delete_record/insert; SequentialFile no la usa,
    porque su borrado es lógico via delete_slot y reclama espacio a nivel de
    archivo con reorganize()).
    """
    PAGE_HEADER_FORMAT = ">iii"  # (offset, size/n_records, first_free_slot)
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
    def first_free_slot(self) -> int:
        return struct.unpack_from(">i", self.page_ba, 8)[0]

    @first_free_slot.setter
    def first_free_slot(self, slot_id: int):
        struct.pack_into(">i", self.page_ba, 8, slot_id)

    @property
    def n_records(self) -> int:
        return self.size

    @property
    def free_space_low(self) -> int:
        return self.PAGE_HEADER_SIZE + self.size * SLOT_SIZE

    @property
    def free_space_bytes(self) -> int:
        return self.offset - self.free_space_low

    def has_space(self, size=None) -> bool:
        if size is None:
            return False
        return self.free_space_bytes >= size + SLOT_SIZE

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

        if size == 0:  # slot muerto (reciclado en la free list)
            return None

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
            self.defragment()
        if not self.has_space(record_size):
            return -1

        new_offset = self.offset - record_size
        next_rid = record.next_rid if record.next_rid is not None else NULL_RID

        self.page_ba[
            new_offset: new_offset + len(record_bytes)
        ] = record_bytes

        struct.pack_into(
            ">" + RID_FORMAT,
            self.page_ba,
            new_offset + len(record_bytes),
            *next_rid,
        )
        struct.pack_into(
            DELETED_FORMAT,
            self.page_ba,
            new_offset + len(record_bytes) + RID_SIZE, record.deleted
        )

        self.offset = new_offset

        # reciclamos el primero de la free list si hay alguno disponible
        target_slot_id = self.first_free_slot
        if target_slot_id != NULL_SLOT:
            next_free, _ = struct.unpack_from(
                SLOT_FORMAT, self.page_ba, self._slot_offset(target_slot_id)
            )
            self.first_free_slot = next_free
            struct.pack_into(
                SLOT_FORMAT, self.page_ba, self._slot_offset(target_slot_id),
                new_offset, record_size
            )
            return target_slot_id

        slot_id = self.size
        struct.pack_into(
            SLOT_FORMAT,
            self.page_ba,
            self.PAGE_HEADER_SIZE + slot_id * SLOT_SIZE,
            new_offset,
            record_size,
        )
        self.size += 1

        return slot_id

    def delete_slot(self, slot_id: int) -> bool:
        """ Borrado logico (usado por SequentialFile): marca deleted=True pero no recicla el slot. """
        record = self.get_record(slot_id)
        if record is None or record.deleted:
            return False

        record.deleted = True
        self.set_record(slot_id, record)
        return True

    def delete_record(self, slot_id: int) -> bool:
        """ Borrado fisico (usado por HeapFile): libera el slot y lo engancha a la free list. """
        if slot_id < 0 or slot_id >= self.size:
            return False

        slot_offset = self._slot_offset(slot_id)
        offset, size = struct.unpack_from(SLOT_FORMAT, self.page_ba, slot_offset)
        if size == 0:  # ya estaba eliminado
            return False

        # el campo offset (ya no sirve para ubicar datos) pasa a guardar
        # quien era el primer muerto hasta ahora, y este slot se vuelve el
        # nuevo primero
        struct.pack_into(SLOT_FORMAT, self.page_ba, slot_offset, self.first_free_slot, 0)
        self.first_free_slot = slot_id
        return True

    def defragment(self):
        active_slots = []
        for slot_id in range(self.size):
            slot_offset = self._slot_offset(slot_id)
            offset, size = struct.unpack_from(SLOT_FORMAT, self.page_ba, slot_offset)
            if size > 0:
                active_slots.append((slot_id, bytes(self.page_ba[offset: offset + size])))

        self.offset = self.page_size
        for slot_id, raw in active_slots:
            record_len = len(raw)
            new_offset = self.offset - record_len
            self.page_ba[new_offset: self.offset] = raw
            self.offset = new_offset
            struct.pack_into(
                SLOT_FORMAT, self.page_ba, self._slot_offset(slot_id), new_offset, record_len
            )

        low = self.free_space_low
        high = self.offset
        self.page_ba[low:high] = b"\x00" * (high - low)

    def reset(self):
        self.size = 0
        self.offset = self.page_size
        self.first_free_slot = NULL_SLOT

    def ensure_initialized(self) -> bool:
        if self.size == 0 and self.offset == 0:
            self.offset = self.page_size
            self.first_free_slot = NULL_SLOT
            return True
        return False
