import struct
from storage.pages.seq_page import Page
from storage.seq_record import Record
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.rid import RID, RID_FORMAT, RID_SIZE, DELETED_FORMAT, DELETED_SIZE

PAGE_HEADER_FORMAT = ">i"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)


class FixedPage(Page):
    """
    Página de slots de tamaño fijo: la cabecera guarda cuántos registros hay
    y los registros se acomodan a partir del byte del primer slot.
    """
    PAGE_HEADER_FORMAT = PAGE_HEADER_FORMAT
    PAGE_HEADER_SIZE = PAGE_HEADER_SIZE

    def __init__(self, page_ba: bytearray, page_size: int, serializer: FixedLengthRecordSerializer):
        super().__init__(page_ba, page_size, serializer)
        self.max_records = (page_size - self.PAGE_HEADER_SIZE) // serializer.slot_size

    @property
    def n_records(self) -> int:
        """
        Retorna la cantidad de registros actualmente almacenados.
        """
        return struct.unpack_from(self.PAGE_HEADER_FORMAT, self.page_ba, 0)[0]

    @n_records.setter
    def n_records(self, value: int):
        struct.pack_into(self.PAGE_HEADER_FORMAT, self.page_ba, 0, value)

    @property
    def free_slots(self) -> int:
        """
        Retorna la cantidad de slots libres.
        """
        return self.max_records - self.n_records

    def has_space(self, size=None) -> bool:
        """
        Indica si la página tiene al menos un slot libre.
        """
        return self.n_records < self.max_records

    def _slot_offset(self, slot_id: int) -> int:
        """
        Calcula el offset de un slot dentro de la página.
        """
        if slot_id < 0 or slot_id >= self.max_records:
            raise RuntimeError("index out of range")
        return self.PAGE_HEADER_SIZE + slot_id * self.serializer.slot_size

    def get_record(self, slot_id: int) -> Record | None:
        """
        Retorna el registro ubicado en slot_id.
        """
        if slot_id < 0 or slot_id >= self.n_records:
            return None

        offset = self._slot_offset(slot_id)
        record_data = bytes(
            self.page_ba[offset : offset + self.serializer.record_size]
        )

        offset += self.serializer.record_size
        params = self.serializer.deserialize(record_data)

        raw_rid = struct.unpack_from(">" + RID_FORMAT, self.page_ba, offset)
        if raw_rid == (-1, -1):
            next_rid = None
        else:
            next_rid = RID(*raw_rid)

        offset += RID_SIZE
        deleted = struct.unpack_from(DELETED_FORMAT, self.page_ba, offset)[0]

        return Record(params, next_rid, deleted)

    def set_record(self, slot_id: int, record: Record):
        """
        Sobreescribe completamente un slot existente.
        """
        if slot_id < 0 or slot_id >= self.n_records:
            raise RuntimeError("index out of range")

        offset = self._slot_offset(slot_id)
        next_rid = record.next_rid

        if next_rid is None:
            next_rid = (-1, -1)

        slot_data = self.serializer.pack_slot(
            record.params, next_rid, record.deleted
        )

        struct.pack_into(
            self.serializer.slot_format, self.page_ba, offset, *slot_data
        )

    def insert(self, record: Record) -> int:
        """
        Inserta un registro en el primer slot libre y retorna su slot_id.
        """
        if not self.has_space():
            return -1

        slot_id = self.n_records
        self.n_records += 1
        self.set_record(slot_id, record)

        return slot_id

    def delete_slot(self, slot_id: int) -> bool:
        """
        Marca un registro como eliminado (si es que no fue eliminado ya).
        """
        record = self.get_record(slot_id)

        if record is None or record.deleted:
            return False

        record.deleted = True
        self.set_record(slot_id, record)

        return True

    def reset(self):
        """
        Deja la página vacía como si fuera recién creada.
        """
        self.n_records = 0