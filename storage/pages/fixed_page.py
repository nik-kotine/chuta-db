import struct
from storage.formats.fixed_serializer import FixedLengthRecordSerializer, Record
"""
La cabecera de cada pagina contiene:
    n_records: cantidad de registros almacenados fisicamente en la pagina
"""
PAGE_HEADER_FORMAT = "i"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)

class FixedPage:
    """
    Clase usada en el Sequential File
    """
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

    def get_record_by_slot_id(self, slot_id: int) -> Record | None:
        """
        Retorna el registro ubicado en slot_id.
        """
        if slot_id < 0 or slot_id >= self.n_records:
            return None

        offset = self._slot_offset(slot_id)
        slot_data = struct.unpack_from(
            self.serializer.slot_format, self.page_ba, offset
        )
        
        params = slot_data[:-3]
        next_rid = slot_data[-3:-1]
        deleted = slot_data[-1] 

        if next_rid == (-1, -1):
            next_rid = None

        return Record(params, next_rid, deleted)

    def set_record_in_slot_id(self, slot_id: int, record: Record):
        """
        Sobreescribe completamente un slot existente.
        """
        if slot_id < 0 or slot_id >= self.n_records:
            raise RuntimeError("index out of range")

        offset = self._slot_offset(slot_id)
        next_rid = record.next_rid
        
        if next_rid is None:
            next_rid = (-1, -1)
        
        slot_data = tuple(record.params) + next_rid + (record.deleted,)
        
        struct.pack_into(
            self.serializer.slot_format, self.page_ba, offset, *slot_data
        )

    def overflow_insert(self, record: Record) -> int:
        """
        Inserta un registro en el primer slot libre y retorna su slot_id,
        como se haria en un heap file. Se usa exclusivamente para el
        overflow page.
        """
        if not self.has_space():
            return -1

        slot_id = self.n_records
        self.n_records += 1
        self.set_record_in_slot_id(slot_id, record)

        return slot_id

    def delete_slot(self, slot_id: int) -> bool:
        """
        Marca un registro como eliminado (si es que no fue eliminado ya).
        """
        record = self.get_record_by_slot_id(slot_id)

        if record is None or record.deleted:
            return False

        record.deleted = True
        self.set_record_in_slot_id(slot_id, record)

        return True