import struct
from storage.buffer_manager import BufferManager
from storage.record_file import RecordFile
from storage.rid import RID
from storage.pages.fixed_page import FixedPage, FixedLengthRecordSerializer, Record

"""
La cabecera del archivo contiene:
    n_pages: cantidad de paginas principales
    first_rid: RID del primer registro de la secuencia logica
    n_records: cantidad de registros vivos
    n_deleted: cantidad de registros marcados como eliminados
"""
FILE_HEADER_FORMAT = "iiii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

"""
La cabecera de cada pagina contiene:
    n_records: cantidad de registros almacenados fisicamente en la pagina
"""
PAGE_HEADER_FORMAT = "i"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)
WASTED_RATIO = 0.5

class SequentialFile(RecordFile):
    def __init__(
        self, buffer_manager: BufferManager, page_size: int, record_format: str
    ):
        self.buffer_manager = buffer_manager
        self.file_manager = buffer_manager.file_manager
        self.page_size = page_size
        self.key_index = 0
        self.serializer = FixedLengthRecordSerializer(record_format)
        self.max_records_per_page = (
            page_size - PAGE_HEADER_SIZE
        ) // self.serializer.slot_size
        self.first_rid = None
        self.n_pages = 0
        self.n_records = 0
        self.n_deleted = 0
        # contador en RAM (no persiste) que se incrementa cada vez que
        # reorganize() corre. Sirve para que quien mantenga una
        # estructura externa basada en RID (como un indice B+
        # agrupado) pueda detectar cuando sus RID guardados quedaron
        # invalidos porque reorganize() reasigno todo.
        self.reorganize_count = 0
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
        return phys_page_id * self.max_records_per_page + slot_id

    def _int_to_rid(self, value):
        """
        Convierte una representacion entera a un RID.
        """
        if value == -1:
            return None

        return (value // self.max_records_per_page, value % self.max_records_per_page)

    def _make_rid(self, phys_page_id: int, slot_id: int):
        """
        Construye un RID a partir de indices de pagina fisica y slot.
        """
        return phys_page_id, slot_id

    def _load_page(self, phys_page_id: int) -> FixedPage:
        """
        Obtiene una pagina del BufferManager y crea una interfaz
        FixedPage sobre el bytearray almacenado en el frame.
        """
        page_ba = self.buffer_manager.fetch_page(phys_page_id)

        if len(page_ba) == 0:
            page_ba.extend(b"\x00" * self.page_size)
        elif len(page_ba) < self.page_size:
            page_ba[:] = page_ba + b"\x00" * (self.page_size - len(page_ba))

        return FixedPage(page_ba, self.page_size, self.serializer)

    def _get_record(self, rid):
        """
        Obtiene un registro a partir de su RID.
        """
        if rid is None:
            return None

        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            return page.get_record_by_slot_id(slot_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

    def _set_record(self, rid, record: Record):
        """
        Sobreescribe un registro identificado por su RID.
        """
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)

        try:
            page.set_record_in_slot_id(slot_id, record)
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
                record = page.get_record_by_slot_id(slot_id)
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
                record = page.get_record_by_slot_id(slot_id)
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

    def _last_page_lt(self, key):
        """
        Busca mediante busqueda binaria (O(log n_pages) lecturas de pagina) la
        ultima pagina principal cuyo primer registro (slot 0) tiene clave <
        key. Las paginas estan ordenadas por clave, asi que el primer registro
        vivo con clave >= key esta dentro de esa pagina (si llega a contenerla)
        o en la pagina siguiente.
        Retorna 0 si ninguna pagina principal empieza con clave < key.
        """
        if self.n_pages == 0:
            return 0

        low, high = 1, self.n_pages
        result = 0

        while low <= high:
            mid = (low + high) // 2
            page = self._load_page(mid)

            try:
                first = page.get_record_by_slot_id(0)

                # una pagina vacia se trata como si su clave fuera +infinito
                if first is not None and first.params[self.key_index] < key:
                    result = mid
                    low = mid + 1
                else:
                    high = mid - 1
            finally:
                self.buffer_manager.unpin_page(mid)

        return result

    def _main_neighbors(self, key):
        """
        Vecinos de key considerando solo los registros vivos de las paginas
        principales (1..n_pages). Como su orden fisico coincide con el de la
        cadena, basta ubicar con busqueda binaria la pagina de la frontera y
        revisarla a ella y, como mucho, a paginas adyacentes.
        Retorna (rid_anterior, clave_anterior, rid_siguiente, clave_siguiente).
        """
        if self.n_pages == 0:
            return None, None, None, None

        hi = self._last_page_lt(key)
        next_rid, next_key = None, None
        next_page, next_slot = None, None

        if hi >= 1:
            page = self._load_page(hi)

            try:
                for slot_id in range(page.n_records):
                    record = page.get_record_by_slot_id(slot_id)
                    if record.deleted:
                        continue
                    if record.params[self.key_index] >= key:
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
                        record = page.get_record_by_slot_id(slot_id)
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

    def _overflow_neighbors(self, key):
        """
        Vecinos de key considerando solo los registros vivos de la pagina de
        overflow (pagina 0). Su orden fisico es el de insercion (no el de la
        cadena), por lo que se recorre completa; a lo sumo hay
        max_records_per_page registros.
        Retorna (rid_anterior, clave_anterior, rid_siguiente, clave_siguiente).
        """
        page = self._load_page(0)

        try:
            prev_rid, prev_key = None, None
            next_rid, next_key = None, None

            for slot_id in range(page.n_records):
                record = page.get_record_by_slot_id(slot_id)
                if record.deleted:
                    continue

                current_key = record.params[self.key_index]
                rid = self._make_rid(0, slot_id)

                if current_key < key:
                    if prev_key is None or current_key > prev_key:
                        prev_rid, prev_key = rid, current_key

                else:
                    if next_key is None or current_key < next_key:
                        next_rid, next_key = rid, current_key
                    elif current_key == next_key:
                        next_rid, next_key = rid, current_key

            return prev_rid, prev_key, next_rid, next_key
        finally:
            self.buffer_manager.unpin_page(0)

    def _find_neighbors(self, key):
        """
        Busca los registros inmediatamente anterior y posterior a una clave
        combinando las paginas principales (ordenadas) con la pagina de
        overflow. Retorna (previous_rid, next_rid).
        """
        if self.first_rid is None:
            return None, None

        main_prev_rid, main_prev_key, main_next_rid, main_next_key = (
            self._main_neighbors(key)
        )
        ov_prev_rid, ov_prev_key, ov_next_rid, ov_next_key = (
            self._overflow_neighbors(key)
        )

        if (
            ov_next_rid is not None
            and (main_next_rid is None or ov_next_key <= main_next_key)
        ):
            next_rid = ov_next_rid
        else:
            next_rid = main_next_rid

        if (
            main_prev_rid is not None
            and (ov_prev_rid is None or main_prev_key >= ov_prev_key)
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

        page_ba = self.buffer_manager.fetch_page(phys_page_id)
        if len(page_ba) == 0:
            page_ba.extend(b"\x00" * self.page_size)
        elif len(page_ba) < self.page_size:
            page_ba[:] = page_ba + b"\x00" * (self.page_size - len(page_ba))

        page = FixedPage(page_ba, self.page_size, self.serializer)
        
        try:
            page.n_records = 0
            self.buffer_manager.mark_dirty(phys_page_id)
        finally:
            self.buffer_manager.unpin_page(phys_page_id)

        return phys_page_id

    def _insert_into_overflow(self, record: Record) -> tuple:
        """
        Inserta un registro en la pagina de overflow y retorna su rid.
        """
        page = self._load_page(0)

        try:
            if not page.has_space():
                return None

            slot_id = page.overflow_insert(record)
            self.buffer_manager.mark_dirty(0)

            return self._make_rid(0, slot_id)

        finally:
            self.buffer_manager.unpin_page(0)
            
    def _iter_records(self, start_rid = None):
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

    def insert(self, values) -> RID:
        """
        Inserta un registro nuevo en overflow y actualiza la cadena
        logica para conservar el orden por clave.
        """
        record = Record(values)

        if self.first_rid is None:
            if self.n_pages == 0:
                self._append_page()

            page = self._load_page(1)

            try:
                rid = self._make_rid(1, page.overflow_insert(record))
                self.first_rid = rid
                self.n_records = 1
                self.buffer_manager.mark_dirty(1)
            finally:
                self.buffer_manager.unpin_page(1)

            self._write_header()
            return rid

        previous_rid, next_rid = self._find_neighbors(values[self.key_index])

        record.next_rid = next_rid

        new_rid = self._insert_into_overflow(record)

        if new_rid is None:
            self.reorganize()
            return self.insert(values)

        if previous_rid is None:
            self.first_rid = new_rid
        else:
            previous_record = self._get_record(previous_rid)
            previous_record.next_rid = new_rid
            self._set_record(previous_rid, previous_record)

        self.n_records += 1
        self._write_header()

        return new_rid

    def fetch(self, rid: RID):
        """
        Recupera los valores de un registro dado su RID
        """
        record = self._get_record(rid)
        if record is None or record.deleted:
            return None
        return list(record.params)

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

    def delete(self, rid: RID):
        """
        Marca un registro como eliminado dado su RID (Necesario para RecordFile).
        """
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
        """
        Marca como eliminados todos los registros vivos cuya clave coincide
        con key. Si tras el borrado alguna pagina principal se queda sin
        registros vivos, llama a reorganize para conservar la invariante de
        que toda pagina principal tiene al menos un registro vivo (o que el
        archivo queda vacio).
        """
        deleted_any = False
        pages_touched = set()
        _, current_rid = self._find_neighbors(key)

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
        Es O(1) porque el header ya cuenta cuantos registros hay vivos
        (n_records) y cuantos marcados como eliminados (n_deleted); cada slot
        ocupado es de uno u otro tipo, asi que el total es la suma de ambos.
        """
        total_slots = self.n_records + self.n_deleted

        if total_slots == 0:
            return 0.0

        return self.n_deleted / total_slots

    def reorganize(self):
        """
        Reconstruye completamente el SequentialFile: conserva solo los
        registros vivos, los acomoda ordenados en las paginas principales y
        deja la pagina de overflow vacia. No hace falta reordenar: la cadena
        logica ya se mantiene ordenada por clave en todo momento, asi que
        _iter_records() ya la recorre de menor a mayor.
        """
        self.reorganize_count += 1
        records = [
            Record(record.params)
            for _, record in self._iter_records()
            if not record.deleted
        ]
        n_records = len(records)
        required_pages = max(
            1,
            (n_records + self.max_records_per_page - 1) //
            self.max_records_per_page
        )

        while self.n_pages < required_pages:
            self._append_page()

        if self.n_pages > required_pages:
            self.buffer_manager.flush_all()
            self.file_manager.truncate(
                self.file_manager.file_header_size
                + (required_pages + 1) * self.page_size
            )
            self.n_pages = required_pages

        for phys_page_id in range(0, self.n_pages + 1):
            page = self._load_page(phys_page_id)

            try:
                page.page_ba[:] = b"\x00" * self.page_size
                page.n_records = 0
                self.buffer_manager.mark_dirty(phys_page_id)
            finally:
                self.buffer_manager.unpin_page(phys_page_id)

        self.first_rid = None

        for index, record in enumerate(records):
            phys_page_id = index // self.max_records_per_page + 1
            slot_id = index % self.max_records_per_page

            if index + 1 < n_records:
                record.next_rid = self._make_rid(
                    ((index + 1) // self.max_records_per_page) + 1,
                    (index + 1) % self.max_records_per_page,
                )
            else:
                record.next_rid = None

            if slot_id == 0:
                page = self._load_page(phys_page_id)

            page.n_records += 1
            page.set_record_in_slot_id(slot_id, record)
            self.buffer_manager.mark_dirty(phys_page_id)

            if slot_id == self.max_records_per_page - 1 or index + 1 == n_records:
                self.buffer_manager.unpin_page(phys_page_id)

            if index == 0:
                self.first_rid = self._make_rid(phys_page_id, slot_id)

        self.n_records = n_records
        self.n_deleted = 0
        self._write_header()

    def scan(self):
        """
        Recorre la secuencia lógica y devuelve (RID, valores)
        de todos los registros que no estén eliminados.
        """
        for rid, record in self._iter_records():
            if not record.deleted:
                yield rid, list(record.params)

    def close(self):
        self.buffer_manager.close()