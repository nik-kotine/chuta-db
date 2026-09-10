import struct
from os import supports_dir_fd

from BufferManager import BufferManager

SLOT_ID_BITS = 16

SLOT_FORMAT = ">ii"
SLOT_SIZE = struct.calcsize(SLOT_FORMAT)
"""
rid: (page_id, slot_id)
page_id representa en que pagina se encuentra (0+, 0 siendo overflow) y
slot_id el numero de registro dentro de esa pagina. (-1, -1) es un registro
nulo
"""
RID_FORMAT = ">ii"
RID_SIZE = struct.calcsize(RID_FORMAT)

"""
deleted: bool
Representa si el registro fue marcado como eliminado
"""
DELETED_FORMAT = ">?"
DELETED_SIZE = struct.calcsize(DELETED_FORMAT)

"""
La cabecera del archivo contiene:
    n_pages: cantidad de paginas principales
    first_rid: RID del primer registro de la secuencia logica
    n_records: cantidad de registros vivos
    n_deleted: cantidad de registros marcados como eliminados
"""
FILE_HEADER_FORMAT = ">iiii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

"""
La cabecera de cada pagina contiene:
    n_records: cantidad de registros almacenados fisicamente en la pagina
"""
PAGE_HEADER_FORMAT = ">ii"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)
WASTED_RATIO = 0.5

FIXED_DATA_TYPES = {
    "smallint": [">h", 2],
    "int2": [">h", 2],
    "integer": [">i", 4],
    "int4": [">i", 4],
    "bigint": [">q", 8],
    "int8": [">q", 8],
    "real": [">f", 4],
    "float4": [">f", 4],
    "double precision": [">d", 8],
    "float8": [">d", 8],
    "boolean": [">?", 1],
    "char": [">b", 1],  # o ">B",
    "oid": [">I", 4],
    "xid": [">I", 4],
    "cid": [">I", 4],
    "date": [">i", 4],
    "timestamp": [">q", 8],
    "timestampz": [">q", 8],
    "time": [">q", 8],
    "timetz": [">ql", 12],
    "interval": [">qii", 16],
    "money": [">q", 8],
    "uuid": ["16s", 16],
    "name": ["64s", 64],
}

STRING_DATA_TYPE_STARTS = ["bit", "char", "varchar"]  # FALTA NUMERIC

STRING_DATA_TYPES = ["text", "bytea", "varbit", "json", "jsonb", "xml"]


# inet, cidr, numeric
# enum, array, composite, range


class VariableLengthRecordSerializer:
    def __init__(self, record_format: list[str]):
        self.record_format = record_format

    def get_size_of(self, params):
        record_bytes = self.serialize(params)
        return len(record_bytes) + RID_SIZE + DELETED_SIZE

    @staticmethod
    def return_format(primitive_type: str) -> list:
        # por cada tipo, retorna [{tipo de dato para struct o s si es un string}, -1 para unlimited o el tamaño del tipo]
        if primitive_type is None or primitive_type == "":
            raise TypeError("Type is null")
        if primitive_type in FIXED_DATA_TYPES.keys():
            return [FIXED_DATA_TYPES[primitive_type][0], FIXED_DATA_TYPES[primitive_type][1]]
        if primitive_type in STRING_DATA_TYPES:
            return ["s", -1]
        textlist = primitive_type.split("(")
        if (primitive_type[-1] == ")" and len(textlist) == 2 and textlist[0] in STRING_DATA_TYPE_STARTS and textlist[1][
            :-1].isnumeric() and int(textlist[1][:-1]) > 0):
            if textlist[0] == "char" or textlist[0] == "varchar":
                return [f"{textlist[1][:-1]}s", int(textlist[1][:-1])]
            if textlist[0] == "bit":
                return [f"{(int(textlist[1][:-1]) - 1) // 8 + 1}s", (int(textlist[1][:-1]) - 1) // 8 + 1]
        raise TypeError("Non-existing type")

    def serialize(self, params) -> bytes:
        output = bytearray()
        for index in range(0, len(params)):
            struct_format_tuple = VariableLengthRecordSerializer.return_format(self.record_format[index])
            if struct_format_tuple[1] == -1:
                if struct_format_tuple[0] == "s":
                    attencoded = params[index].encode("utf-8")
                    output += struct.pack(">i", len(attencoded)) + attencoded

                # else:
                # nada hasta que no haya numeric
            elif struct_format_tuple[0][-1] == "s" and struct_format_tuple[0][:-1].isnumeric():
                length = struct_format_tuple[1]
                attencoded = params[index].encode("utf-8")
                if len(attencoded) > length:
                    attencoded2 = attencoded[0:length]
                else:
                    attencoded2 = bytearray(length)
                    attencoded2[0:len(attencoded)] = attencoded
                output += attencoded2
            else:
                output += struct.pack(struct_format_tuple[0], params[index])
        return output

    def deserialize(self, data: bytes):
        unpacking_index = 0
        output = []
        for index in range(0, len(self.record_format)):
            struct_format_tuple = VariableLengthRecordSerializer.return_format(self.record_format[index])
            if struct_format_tuple[1] == -1:
                field_length = struct.unpack(">i", data[unpacking_index:unpacking_index + 4])[0]
                unpacking_index += 4
                if struct_format_tuple[0] == "s":
                    output.append(
                        data[unpacking_index:unpacking_index + field_length].decode("utf-8"))
                # else:
                # nada hasta que no haya numeric
                unpacking_index += field_length
            elif struct_format_tuple[0][-1] == "s" and struct_format_tuple[0][:-1].isnumeric():
                length = struct_format_tuple[1]
                output.append(
                    data[unpacking_index:unpacking_index + length].decode("utf-8"))
                unpacking_index += length
            else:
                record_size = struct_format_tuple[1]
                record_value = struct.unpack(struct_format_tuple[0], data[unpacking_index:unpacking_index + record_size])[0]
                output.append(record_value)
                unpacking_index += record_size
        return output


class Record:
    def __init__(self, params, next_rid=None, deleted=False):
        self.params = params
        self.next_rid = next_rid
        self.deleted = deleted


class VariablePage:
    offset: int
    size: int
    slots: list[tuple[int, int]]
    page_ba: bytearray
    page_size: int

    def __init__(self, offset: int, size: int, slots: list[tuple[int, int]], page_ba: bytearray, page_size: int,
                 serializer: VariableLengthRecordSerializer) -> None:
        self.offset = offset
        self.size = size
        self.slots = slots
        self.page_ba = page_ba
        self.serializer = serializer

    def __init__(self, page_ba: bytearray, page_size: int, serializer: VariableLengthRecordSerializer) -> None:
        self.page_size = page_size
        self.page_ba = page_ba
        self.serializer = serializer
        self.slots = []
        base = PAGE_HEADER_SIZE
        for i in range(self.size):
            data0, data1 = struct.unpack_from(">ii", self.page_ba, base)
            self.slots.append(tuple([data0, data1]))
            base += 8

    @property
    def size(self) -> int:
        return struct.unpack_from(">i", self.page_ba, 4)[0]

    @size.setter
    def size(self, size: int):
        struct.pack_into(">i", self.page_ba, 4, size)

    @property
    def offset(self) -> int:
        return struct.unpack_from(">i", self.page_ba, 0)[0]

    @offset.setter
    def offset(self, offset: int):
        struct.pack_into(">i", self.page_ba, 0, offset)

    #@property
    #def n_records(self) -> int:
    #    return struct.unpack_from(PAGE_HEADER_FORMAT, self.page_ba, 0)[0]

    #@n_records.setter
    #def n_records(self, value: int):
    #    struct.pack_into(PAGE_HEADER_FORMAT, self.page_ba, 0, value)

    def has_space_int(self, size: int) -> bool:
        return self.offset - (PAGE_HEADER_SIZE + self.size * SLOT_SIZE) >= size + SLOT_SIZE

    def has_space_two_int(self, size1: int, size2: int) -> bool:
        return self.offset - (PAGE_HEADER_SIZE + self.size * SLOT_SIZE) >= size1 + size2 + SLOT_SIZE * 2

    def _slot_offset(self, slot_id: int) -> int:
        if slot_id < 0 or slot_id >= len(self.slots):
            raise RuntimeError(
                "index out of range")  # else if deleted TODO IMPORTANTE!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        return PAGE_HEADER_SIZE + slot_id * SLOT_SIZE

    def get_by_slot_id(self, slot_id: int) -> Record | None:  #
        if slot_id < 0 or slot_id >= len(
                self.slots):  # else if deleted TODO IMPORTANTE!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            return None
        slot_offset = self._slot_offset(slot_id)
        offset, size = struct.unpack_from(SLOT_FORMAT, self.page_ba, slot_offset)
        record_data = bytes(self.page_ba[offset: offset + size])
        params = self.serializer.deserialize(record_data)
        offset += size - RID_SIZE - DELETED_SIZE  # no incluye el puntero al siguiente record ni bool de deleted

        next_rid = struct.unpack_from(RID_FORMAT, self.page_ba, offset)

        if next_rid == (-1, -1):
            next_rid = None

        offset += RID_SIZE
        deleted = struct.unpack_from(DELETED_FORMAT, self.page_ba, offset)[0]

        return Record(params, next_rid, deleted)

    def set_by_slot_id_same_size(self, slot_id: int,
                                 record: Record):  # podremos usar esto? solo para registros con la misma longitud, método interno
        if slot_id < 0 or slot_id >= len(
                self.slots):  # else if deleted TODO IMPORTANTE!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            raise RuntimeError("slot_id is out of range")
        slot_offset = self._slot_offset(slot_id)
        offset, size = struct.unpack_from(SLOT_FORMAT, self.page_ba, slot_offset)
       # record_data = bytes(self.page_ba[offset: offset + size])
        record_data = self.serializer.serialize(record.params)
        if len(record_data) != size-RID_SIZE-DELETED_SIZE:
            raise RuntimeError("Record calculated size and record size stored on slot do not match")
        self.page_ba[offset: (offset + size-RID_SIZE-DELETED_SIZE)] = record_data
        offset += size - RID_SIZE - DELETED_SIZE
        next_rid = record.next_rid

        if next_rid is None:
            next_rid = (-1, -1)

        struct.pack_into(RID_FORMAT, self.page_ba, offset, *next_rid)
        offset += RID_SIZE
        struct.pack_into(DELETED_FORMAT, self.page_ba, offset, record.deleted)

    def insert(self,
               record: Record) -> int:  # notar que siempre se inserta al final ya que los registros no se borran físicamente
        """
        Inserta un registro en el primer slot libre al final de la
        pagina y retorna su slot_id.
        """
        record_bytes = self.serializer.serialize(
            record.params)  # No uso get_size_of para no tener que recalcular record_bytes
        record_size = len(record_bytes) + RID_SIZE + DELETED_SIZE
        if not self.has_space_int(record_size):
            return -1

        slot_id = self.size
        self.size += 1

        self.offset -= record_size

        struct.pack_into(SLOT_FORMAT, self.page_ba, PAGE_HEADER_SIZE+len(self.slots)*SLOT_SIZE, self.offset, record_size)
        self.slots.append((self.offset, record_size))

        self.page_ba[self.offset: self.offset + len(record_bytes)] = record_bytes
        next_rid = record.next_rid
        if next_rid is None:
            next_rid = (-1, -1)
        struct.pack_into(RID_FORMAT, self.page_ba, self.offset + len(record_bytes), *next_rid)
        struct.pack_into(DELETED_FORMAT, self.page_ba, self.offset + len(record_bytes) + RID_SIZE, record.deleted)
        return slot_id

    def delete_slot(self,
                    slot_id: int) -> bool:
        record = self.get_by_slot_id(slot_id)

        if record is None or record.deleted:
            return False

        record.deleted = True
        self.set_by_slot_id_same_size(slot_id, record)

        return True


class VariableSequentialFile:
    def __init__(
            self, buffer_manager: BufferManager, page_size: int, record_format: list[str]
            # diferente a la implementación del de longitud fija
    ):
        self.buffer_manager = buffer_manager
        self.file_manager = buffer_manager.file_manager
        self.page_size = page_size
        self.key_index = 0
        self.serializer = VariableLengthRecordSerializer(record_format)
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
        # return phys_page_id * self.records_per_page + slot_id
        return (
                    phys_page_id << SLOT_ID_BITS) | slot_id  # Me parece mejor hacerlo así, tal vez si tenemos que juntar ambos es mejor tener el id así

    def _int_to_rid(self, value):
        """
        Convierte una representacion entera a un RID.
        """
        if value == -1:
            return None

        return value >> SLOT_ID_BITS, value % (1 << SLOT_ID_BITS)  # confirmar que este y el de arriba estén bien

    def _make_rid(self, phys_page_id: int, slot_id: int):  # por si se modifica en algún momento
        """
        Construye un RID a partir de pagina fisica y slot.
        """
        return phys_page_id, slot_id

    def _load_page(self, phys_page_id: int) -> VariablePage:
        """
        Obtiene una pagina del BufferManager y crea una interfaz
        VariablePage sobre el bytearray almacenado en el frame.
        """
        page_ba = self.buffer_manager.fetch_page(phys_page_id)

        return VariablePage(page_ba, self.page_size, self.serializer)

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

    def _set_record_same_size(self, rid, record: Record):
        """
        Sobreescribe un registro identificado por su RID.
        """
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            page.set_by_slot_id_same_size(slot_id, record)
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
                if page.size == 0:
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




    def _find_neighbors_duplicates_after(self, key):
        """
        Busca los registros inmediatamente anterior y posterior a una clave
        recorriendo la secuencia logica desde first_rid. Para claves duplicadas
        (en caso se necesiten), los registros se colocan en orden de llegada.
        """
        if self.first_rid is None:
            return None, None

        previous_rid = None

        for current_rid, record in self._iter_records(self.first_rid):
            if record.deleted:
                continue

            current_key = record.params[self.key_index]

            if current_key > key:
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
            page.size = 0
            page.offset = self.page_size
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

        return phys_page_id

    def _insert_into_overflow(self, record: Record) -> tuple:
        """
        Inserta un registro en la pagina de overflow.
        """
        size = self.serializer.get_size_of(record.params)
        page = self._load_page(0)
        if page.size == 0 and page.offset == 0:
            page.offset = self.page_size
            page.size = 0
            self.buffer_manager.mark_dirty(0)
        if (not page.has_space_int(size)) and page.size == 0:
            raise RuntimeError("Record is too big for insertion")

        try:
            if not page.has_space_int(size):
                return None

            slot_id = page.insert(record)
            self.buffer_manager.mark_dirty(0)

            return self._make_rid(0, slot_id)

        finally:
            self.buffer_manager.unpin_page(0)

    def _iter_records(self, start_rid=None):
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

        size = self.serializer.get_size_of(record.params)

        if self.first_rid is None:
            if self.n_pages == 0:
                self._append_page()

            page = self._load_page(1)
            if not page.has_space_int(size):
                raise RuntimeError("Record is too big for insertion")

            try:
                rid = self._make_rid(1, page.insert(record))
                self.first_rid = rid
                self.n_records = 1
                self.buffer_manager.mark_dirty(1)
            finally:
                self.buffer_manager.unpin_page(1)

            self._write_header()
            return rid

        previous_rid, next_rid = self._find_neighbors_duplicates_after(params[self.key_index])

        record.next_rid = next_rid

        new_rid = self._insert_into_overflow(record)

        if new_rid is None:
            self.reorganize_variable()
            return self.insert(params)

        if previous_rid is None:
            self.first_rid = new_rid
        else:
            previous_record = self._get_record(previous_rid)
            previous_record.next_rid = new_rid
            self._set_record_same_size(previous_rid, previous_record)

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
                self._set_record_same_size(current_rid, record)
                self.n_deleted += 1
                self.n_records -= 1
                deleted_any = True

        if deleted_any and self._wasted_space_ratio() >= WASTED_RATIO:
            self.reorganize_variable()

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

            try:  # no deberíamos usar self.n_deleted?
                for slot_id in range(page.size):
                    total_slots += 1
                    record = page.get_by_slot_id(slot_id)
                    if record.deleted:
                        deleted_slots += 1

            finally:
                self.buffer_manager.unpin_page(phys_page_id)

        if total_slots == 0:
            return 0.0

        return deleted_slots / total_slots

    def reorganize_variable(self):
        if self.n_records + self.n_deleted == 0:
            raise RuntimeError("File being reorganized with no records")
        records = []
        for _, record in self._iter_records():
            if not record.deleted:
                records.append(Record(record.params))

        if len(records) == 0:
            self.n_pages = 0
            self.n_records = 0
            self.first_rid = None
            self.buffer_manager.file_manager.truncate(FILE_HEADER_SIZE + self.page_size)
            self.n_deleted = 0
            self._write_header()
            return

        records.sort(key=lambda record: record.params[self.key_index])
        records.reverse() #no pongo reverse=True ya que aparentemente reversed mantiene estable el orden de duplicados como si estuviera no reversed

        self.n_records = len(records)

        # Limpia página de overflow
        page_ba = self.buffer_manager.fetch_page(0)
        page_ba[:] = b"\x00" * self.page_size
        page = VariablePage(page_ba, self.page_size, self.serializer)
        page.size = 0
        page.offset = self.page_size
        self.buffer_manager.mark_dirty(0)
        self.buffer_manager.unpin_page(0)
        pageindex = 1
        while len(records) > 0:

            if self.n_pages < pageindex:
                self._append_page()
            page_ba = self.buffer_manager.fetch_page(pageindex)
            page_ba[:] = b"\x00" * self.page_size
            page = VariablePage(page_ba, self.page_size, self.serializer)
            page.size = 0
            page.offset = self.page_size

            record = records[-1]
            record_size = self.serializer.get_size_of(record.params)
            next_record = records[-2] if len(records) >= 2 else None
            next_record_size = -1 if next_record is None else self.serializer.get_size_of(next_record.params)

            if not page.has_space_int(record_size): #la página actualmente está vacía, se asume el mismo tamaño para todas las páginas no overflow
                raise RuntimeError("Record is too big for insertion")
            while page.has_space_int(record_size):
                next_rid = (
                    None if next_record is None else (
                        self._make_rid(pageindex, page.size + 1)
                        if page.has_space_two_int(record_size, next_record_size)
                        else self._make_rid(pageindex + 1, 0)
                    ))
                record.next_rid = next_rid
                page.insert(record)
                records.pop()
                if len(records) == 0:
                    break
                record = records[-1]
                record_size = self.serializer.get_size_of(record.params)
                next_record = records[-2] if len(records) >= 2 else None
                next_record_size = -1 if next_record is None else self.serializer.get_size_of(next_record.params)

            self.buffer_manager.mark_dirty(pageindex)
            self.buffer_manager.unpin_page(pageindex)
            pageindex += 1

        self.n_pages = pageindex - 1
        self.first_rid = self._make_rid(1, 0)
        self.n_deleted = 0
        self.buffer_manager.file_manager.truncate(FILE_HEADER_SIZE + (self.n_pages + 1) * self.page_size)
        self._write_header()


"""
    def reorganize(self):

        #Reconstruye completamente el SequentialFile eliminando registros
        #marcados como deleted y vaciando el overflow.

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
"""