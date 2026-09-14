import struct

from BufferManager import BufferManager
from RecordSerializer import (
    DELETED_FORMAT,
    DELETED_SIZE,
    RID_FORMAT,
    RID_SIZE,
    SLOT_FORMAT,
    SLOT_SIZE,
    return_format,
)
from FixedLengthRecordSerializer import FixedLengthRecordSerializer
from VariableLengthRecordSerializer import VariableLengthRecordSerializer

SLOT_ID_BITS = 16

FILE_HEADER_FORMAT = ">iiii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

WASTED_RATIO = 0.5

class Record:

    def __init__(self, params, next_rid=None, deleted=False):
        self.params = params
        self.next_rid = next_rid
        self.deleted = deleted

class Page:
    """
    Página de almacenamiento. Define la interfaz común que comparten
    FixedPage y VariablePage.
    """

    def __init__(self, page_ba: bytearray, page_size: int, serializer):
        self.page_ba = page_ba
        self.page_size = page_size
        self.serializer = serializer

    @property
    def n_records(self) -> int:
        """
        Cantidad de registros almacenados físicamente en la página.
        """
        raise NotImplementedError

    def has_space(self, size=None) -> bool:
        """
        Indica si la página admite un registro (de tamaño `size`).
        """
        raise NotImplementedError

    def get_record(self, slot_id: int) -> Record | None:
        """
        Retorna el registro ubicado en slot_id (None si no existe).
        """
        raise NotImplementedError

    def set_record(self, slot_id: int, record: Record):
        """
        Sobreescribe completamente un slot existente.
        """
        raise NotImplementedError

    def insert(self, record: Record) -> int:
        """
        Inserta un registro y retorna su slot_id (-1 si no hay espacio).
        """
        raise NotImplementedError

    def delete_slot(self, slot_id: int) -> bool:
        """
        Marca un slot como eliminado.
        """
        raise NotImplementedError

    def reset(self):
        """
        Deja la pagina vacía como si fuera recién creada.
        """
        raise NotImplementedError

    def ensure_initialized(self) -> bool:
        """
        Inicializa la pagina si fue leída desde un area en cero de un archivo
        nuevo. Retorna True si modificó el buffer (para marcarla dirty).
        Las páginas de longitud fija nunca lo necesitan.
        """
        return False


class FixedPage(Page):
    """
    Página de slots de tamaño fijo: la cabecera guarda cuántos registros hay
    y los registros se acomodan a partir del byte del primer slot.
    """
    PAGE_HEADER_FORMAT = ">i"
    PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)

    def __init__(self, page_ba, page_size, serializer):
        super().__init__(page_ba, page_size, serializer)
        self.max_records = (page_size - self.PAGE_HEADER_SIZE) // \
            serializer.slot_size

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
        Indica si la pagina tiene al menos un slot libre.
        """
        return self.n_records < self.max_records

    def _slot_offset(self, slot_id: int) -> int:
        """
        Calcula el offset de un slot dentro de la pagina.
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
            self.page_ba[offset: offset + self.serializer.record_size]
        )
        
        offset += self.serializer.record_size
        params = self.serializer.deserialize(record_data)
        next_rid = struct.unpack_from(RID_FORMAT, self.page_ba, offset)

        if next_rid == (-1, -1):
            next_rid = None
            
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
        Inserta un registro en el primer slot libre y retorna su slot_id
        (antes overflow_insert, por conveniencia la renombro a la interfaz
        comun)
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


class VariablePage(Page):
    """
    Página con registros de longitud variable.
    """

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

    def has_space(self, size) -> bool:
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

        next_rid = struct.unpack_from(
            RID_FORMAT, self.page_ba, offset + record_byte_len
        )

        if next_rid == (-1, -1):
            next_rid = None

        deleted = struct.unpack_from(
            DELETED_FORMAT, self.page_ba, offset + record_byte_len + RID_SIZE
        )[0]

        return Record(params, next_rid, deleted)

    def set_record(self, slot_id: int, record: Record):
        """
        Sobreescribe un slot. Solo válido si el nuevo registro ocupa el mismo
        tamaño que el almacenado (los registros nunca se compactan en sitio).
        """
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

        next_rid = record.next_rid
        if next_rid is None:
            next_rid = (-1, -1)

        struct.pack_into(
            RID_FORMAT, self.page_ba, offset + len(record_bytes), *next_rid
        )
        struct.pack_into(
            DELETED_FORMAT,
            self.page_ba,
            offset + len(record_bytes) + RID_SIZE, record.deleted
        )

    def insert(self, record: Record) -> int:
        """
        Inserta un registro en el primer slot libre al final de la pagina.
        """
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

        next_rid = record.next_rid
        if next_rid is None:
            next_rid = (-1, -1)

        struct.pack_into(
            RID_FORMAT, self.page_ba, self.offset + len(record_bytes), *next_rid
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
        """
        Deja la página vacía como si fuera recién creada.
        """
        self.size = 0
        self.offset = self.page_size

    def ensure_initialized(self) -> bool:
        """
        Una página recién asignada es un bloque de ceros (offset == 0).
        En ese caso hay que inicializar el offset al final de la página.
        """
        if self.size == 0 and self.offset == 0:
            self.offset = self.page_size
            return True
        return False

class SequentialFile:
    """
    Archivo secuencial con orden por clave.
    Ahora soporta fija y variable. La forma para elegir cual usar es por
    el formato.
    """
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
        
        self.variable_length = False
        for token in record_format:
            if return_format(token)[1] == -1:
                self.variable_length = True
                break

        if self.variable_length:
            self.serializer = VariableLengthRecordSerializer(record_format)
            self.page_class = VariablePage
        else:
            self.serializer = FixedLengthRecordSerializer(record_format)
            self.page_class = FixedPage

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
        Carga la metadata desde el header del archivo.
        """
        header = self.file_manager.read_header()

        if len(header) != FILE_HEADER_SIZE:
            raise RuntimeError("invalid or corrupt header file")

        self.n_pages, first_rid, self.n_records, self.n_deleted = \
            struct.unpack(FILE_HEADER_FORMAT, header)

        if first_rid == -1:
            self.first_rid = None
        else:
            self.first_rid = self._int_to_rid(first_rid)

    def _write_header(self):
        """
        Persiste la metadata en el header del archivo.
        """
        first_rid = self._rid_to_int(self.first_rid)

        header = struct.pack(
            FILE_HEADER_FORMAT,
            self.n_pages,
            first_rid,
            self.n_records,
            self.n_deleted
        )

        self.file_manager.write_header(header)

    def _rid_to_int(self, rid):
        """
        Convierte un RID a una representacion entera para el header.
        """
        if rid is None:
            return -1

        phys_page_id, slot_id = rid
        return (phys_page_id << SLOT_ID_BITS) | slot_id

    def _int_to_rid(self, value):
        """
        Convierte una representacion entera a un RID.
        """
        if value == -1:
            return None

        return value >> SLOT_ID_BITS, value % (1 << SLOT_ID_BITS)

    def _make_rid(self, phys_page_id: int, slot_id: int):
        """
        Construye un RID a partir de indices de pagina fisica y slot.
        """
        return phys_page_id, slot_id

    def _load_page(self, phys_page_id: int) -> Page:
        """
        Obtiene una pagina del BufferManager y crea la interfaz de página
        concreta sobre el bytearray almacenado en el frame.
        """
        page_ba = self.buffer_manager.fetch_page(phys_page_id)

        return self.page_class(page_ba, self.page_size, self.serializer)

    def _get_record(self, rid):
        """
        Obtiene un registro a partir de su RID.
        """
        if rid is None:
            return None

        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            return page.get_record(slot_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _set_record(self, rid, record: Record):
        """
        Sobreescribe un registro identificado por su RID.
        """
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            page.set_record(slot_id, record)
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _first_live_in_page(self, phys_page_id):
        """
        Retorna (slot_id, record) del primer registro vivo de la pagina.
        Retorna None si la pagina no tiene ningun registro vivo.
        """
        page = self._load_page(phys_page_id)

        try:
            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)
                if not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _last_live_in_page(self, phys_page_id):
        """
        Retorna (slot_id, record) del ultimo registro vivo de la pagina.
        Retorna None si la pagina no tiene ningun registro vivo.
        """
        page = self._load_page(phys_page_id)

        try:
            for slot_id in range(page.n_records - 1, -1, -1):
                record = page.get_record(slot_id)
                if not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _last_live_overall(self):
        """
        Retorna (rid, record) del ultimo registro vivo de todas las paginas
        principales, o (None, None) si no existe.
        """
        for phys_page_id in range(self.n_pages, 0, -1):
            last = self._last_live_in_page(phys_page_id)

            if last is not None:
                slot_id, record = last
                return self._make_rid(phys_page_id, slot_id), record

        return None, None

    def _last_page_lt(self, key, duplicates_after: bool):
        """
        Busca mediante busqueda binaria la ultima pagina principal cuyo primer
        registro (slot 0) tiene clave < key. Si duplicates_after es
        True, entonces tambien incluye clave == key.
        """
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
                        or (
                            duplicates_after
                            and first.params[self.key_index] == key
                        )
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
        """
        Vecinos de key considerando solo los registros vivos de las paginas
        principales con busqueda binaria. Basta acceder a la pagina anterior y
        posterior en casos extremos.
        Retorna (rid_anterior, clave_anterior, rid_siguiente, clave_siguiente).
        Se garantizan paginas no vacias, asi que es tiempo logaritmico.
        """
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
                    if record.deleted:
                        continue
                    
                    if record.params[self.key_index] > key or (
                        (not duplicates_after)
                        and record.params[self.key_index] == key
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
                        if not record.deleted:
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
        """
        Vecinos de key considerando solo los registros vivos de la pagina de
        overflow en tiempo lineal.
        Retorna (rid_anterior, clave_anterior, rid_siguiente, clave_siguiente).
        """
        page = self._load_page(0)

        try:
            prev_rid, prev_key = None, None
            next_rid, next_key = None, None

            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)
                if record.deleted:
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

    def _find_neighbors(self, key, duplicates_after: bool):
        """
        Busca los registros inmediatamente anterior y posterior a una clave
        combinando las paginas principales con la pagina de overflow.
        Retorna (previous_rid, next_rid).
        En caso de empate de claves entre paginas principales y overflow, para
        next gana main y para previous ganan los registros de overflow (los
        duplicados mas nuevos siempre estan en overflow).
        """
        if self.first_rid is None:
            return None, None

        main_prev_rid, main_prev_key, main_next_rid, main_next_key = (
            self._main_neighbors(key, duplicates_after=duplicates_after)
        )
        ov_prev_rid, ov_prev_key, ov_next_rid, ov_next_key = (
            self._overflow_neighbors(key, duplicates_after=duplicates_after)
        )

        if (
            ov_next_rid is not None
            and (main_next_rid is None or ov_next_key < main_next_key)
        ):
            next_rid = ov_next_rid
        else:
            next_rid = main_next_rid

        if (
            main_prev_rid is not None
            and (ov_prev_rid is None or main_prev_key > ov_prev_key)
        ):
            prev_rid = main_prev_rid
        else:
            prev_rid = ov_prev_rid

        return prev_rid, next_rid

    def _append_page(self) -> int:
        """
        Crea una nueva pagina principal al final del archivo.
        """
        phys_page_id = self.file_manager.allocate_page()
        self.n_pages += 1
        page = self._load_page(phys_page_id)

        try:
            page.reset()
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

        return phys_page_id

    def _insert_into_overflow(self, record: Record) -> tuple | None:
        """
        Inserta un registro en la pagina de overflow y retorna su rid, o None
        si la pagina de overflow esta llena.
        """
        size = self.serializer.get_size_of(record.params)
        page = self._load_page(0)

        try:
            if page.ensure_initialized():
                self.buffer_manager.mark_dirty(0)

            if not page.has_space(size):
                if page.n_records == 0:
                    raise RuntimeError("record is too big for insertion")
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
        Inserta un registro nuevo en overflow y actualiza la cadena logica para
        conservar el orden por clave. Los duplicados conservan el orden de
        llegada.
        """
        record = Record(params)
        size = self.serializer.get_size_of(record.params)

        if self.first_rid is None:
            
            if self.n_pages == 0:
                self._append_page()

            page = self._load_page(1)

            try:
                if page.ensure_initialized():
                    self.buffer_manager.mark_dirty(1)
                if not page.has_space(size):
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
        _, current_rid = self._find_neighbors(key, duplicates_after=False)

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
        con key. Si tras el borrado alguna pagina principal se queda sin
        registros vivos (o la proporcion de eliminados supera WASTED_RATIO),
        reorganiza para conservar las invariantes del archivo.
        """
        deleted_any = False
        pages_touched = set()
        _, current_rid = self._find_neighbors(key, duplicates_after=False)

        for current_rid, record in self._iter_records(current_rid):
            current_key = record.params[self.key_index]

            if current_key > key:
                break

            if current_key == key and not record.deleted:
                record.deleted = True
                self._set_record(current_rid, record)
                pages_touched.add(current_rid[0])
                self.n_deleted += 1
                self.n_records -= 1
                deleted_any = True

        if deleted_any:
            page_emptied = any(
                phys_page_id >= 1
                and self._first_live_in_page(phys_page_id) is None
                for phys_page_id in pages_touched
            )
            if page_emptied or self._wasted_space_ratio() >= WASTED_RATIO:
                self.reorganize()

        self._write_header()

        return deleted_any

    def _wasted_space_ratio(self) -> float:
        """
        Calcula la proporcion de slots ocupados por registros eliminados.
        """
        total_slots = self.n_records + self.n_deleted

        if total_slots == 0:
            return 0.0

        return self.n_deleted / total_slots

    def reorganize(self):
        """
        Reconstruye completamente el archivo: conserva solo los registros
        vivos, los acomoda ordenados en las paginas principales y deja la
        pagina de overflow vacia.
        """
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
            record_size = self.serializer.get_size_of(record.params)

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
            record = self._get_record(rids[index])
            record.next_rid = rids[index + 1]
            self._set_record(rids[index], record)

        self.n_pages = pageindex
        self.n_records = len(rids)
        self.n_deleted = 0
        self._truncate(pageindex)
        self._write_header()

    def _truncate(self, n_main_pages: int):
        """
        Deja el archivo con la pagina de overflow y n_main_pages paginas
        principales.
        """
        self.buffer_manager.flush_all()
        self.file_manager.truncate(
            self.file_manager.file_header_size + (n_main_pages + 1) * self.page_size
        )