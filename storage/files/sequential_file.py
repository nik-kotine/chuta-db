import struct
from storage.buffer_manager import BufferManager
from storage.file_manager import FileManager
from storage.rid import RID, RID_SIZE, DELETED_SIZE
from storage.seq_record import Record
from storage.pages.fixed_page import FixedPage
from storage.pages.variable_page import VariablePage
from storage.formats.data_types import return_format
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer
from storage.record_file import RecordFile

SLOT_ID_BITS = 16

# n_pages, first_rid, n_records, n_deleted, n_overflow_pages, n_overflow_records
FILE_HEADER_FORMAT = ">iiiiii"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

WASTED_RATIO = 0.3

# Umbral de la cadena de overflow relativo al tamaño del archivo (no una
# cantidad fija de paginas): mismo principio que WASTED_RATIO pero para
# inserts. Con un umbral fijo en paginas, el reorganize dispara cada K
# inserts SIEMPRE sin importar N -> O(N^2) total (K reorganizes de costo
# O(N) cada uno). Con un umbral en %, el archivo necesita cada vez MAS
# inserts nuevos para volver a cruzarlo a medida que crece (como el
# growth factor de un array dinamico) -> O(N) amortizado.
OVERFLOW_RATIO = 0.3

# con N chico, un solo insert a overflow ya cruza el % (ej. n_records=2,
# 1 en overflow -> 50%) y reorganizaria en cada insert -- mismo problema
# que un array dinamico sin capacidad inicial minima. No cambia la
# complejidad asintotica (esa la da el % arriba), solo evita el
# desperdicio en archivos chicos.
MIN_RECORDS_FOR_OVERFLOW_CHECK = 20

class SequentialFile(RecordFile):
    def __init__(
        self,
        buffer_manager: BufferManager,
        page_size: int,
        record_format: list[str],
        file_manager: FileManager = None,
    ):
        self.buffer_manager = buffer_manager
        self.file_manager = file_manager or getattr(buffer_manager, "active_file", None)
        if self.file_manager is None:
            raise ValueError("SequentialFile necesita un FileManager para operar")
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
        # cuantas paginas fisicas componen la cadena de overflow ahora
        # mismo (siempre >= 1: la pagina 0 es la primera) y cuantos
        # inserts fueron a parar ahi desde el ultimo reorganize -- ver
        # OVERFLOW_RATIO arriba.
        self.n_overflow_pages = 1
        self.n_overflow_records = 0

        header = self.file_manager.read_header()
        if len(header) > 0:
            self._load_header()

    def _load_header(self):
        header = self.file_manager.read_header()
        if len(header) != FILE_HEADER_SIZE:
            raise RuntimeError("invalid or corrupt header file")

        (
            self.n_pages, first_rid, self.n_records, self.n_deleted,
            self.n_overflow_pages, self.n_overflow_records,
        ) = struct.unpack(FILE_HEADER_FORMAT, header)

        self.first_rid = None if first_rid == -1 else self._int_to_rid(first_rid)

    def _write_header(self):
        first_rid_int = self._rid_to_int(self.first_rid)
        header = struct.pack(
            FILE_HEADER_FORMAT,
            self.n_pages,
            first_rid_int,
            self.n_records,
            self.n_deleted,
            self.n_overflow_pages,
            self.n_overflow_records,
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
        page_ba = self.buffer_manager.fetch_page(phys_page_id, self.file_manager)
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
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _set_record(self, rid: RID, record: Record):
        if rid is None or rid == (-1, -1) or getattr(rid, "page_id", -1) == -1:
            return
        phys_page_id, slot_id = rid
        page = self._load_page(phys_page_id)
        try:
            page.set_record(slot_id, record)
            self.buffer_manager.mark_dirty(phys_page_id, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _first_live_in_page(self, phys_page_id: int):
        page = self._load_page(phys_page_id)
        try:
            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)
                if record and not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

    def _last_live_in_page(self, phys_page_id: int):
        page = self._load_page(phys_page_id)
        try:
            for slot_id in range(page.n_records - 1, -1, -1):
                record = page.get_record(slot_id)
                if record and not record.deleted:
                    return slot_id, record
            return None
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)

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
                self.buffer_manager.unpin_page(mid, self.file_manager)

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
                self.buffer_manager.unpin_page(hi, self.file_manager)

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
                    self.buffer_manager.unpin_page(next_page, self.file_manager)

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

    def _find_neighbors(self, key, duplicates_after: bool = True):
        if self.first_rid is None:
            return None, None

        # _main_neighbors ya da el par mas ajustado considerando SOLO
        # paginas principales (binary search + scan de una sola pagina,
        # barato). Cualquier registro de overflow mas cercano a key que
        # ese par tiene que estar, en la cadena logica (next_rid), en
        # algun punto ENTRE main_prev_rid y main_next_rid -- caminamos
        # solo ese tramo en vez de escanear el overflow completo, que es
        # lo que hacia _overflow_neighbors (y por que un archivo con
        # mucho overflow acumulado volvia cada insert mas caro).
        main_prev_rid, main_prev_key, main_next_rid, main_next_key = (
            self._main_neighbors(key, duplicates_after=duplicates_after)
        )

        prev_rid, prev_key = main_prev_rid, main_prev_key
        next_rid, next_key = main_next_rid, main_next_key

        if main_prev_rid is not None:
            current_rid = self._get_record(main_prev_rid).next_rid
        else:
            current_rid = self.first_rid

        while current_rid is not None and current_rid != main_next_rid:
            record = self._get_record(current_rid)
            if record is None:
                break
            if record.deleted:
                current_rid = record.next_rid
                continue

            current_key = record.params[self.key_index]
            is_prev_candidate = current_key <= key if duplicates_after else current_key < key

            if is_prev_candidate:
                prev_rid, prev_key = current_rid, current_key
                current_rid = record.next_rid
            else:
                # nos pasamos de la clave: todo lo que sigue en la
                # cadena es >= esto, asi que ya no puede haber un "next"
                # mas ajustado -- cortamos
                next_rid, next_key = current_rid, current_key
                break

        return prev_rid, next_rid

    def _append_page(self) -> int:
        phys_page_id = self.file_manager.allocate_page()
        self.n_pages += 1
        page = self._load_page(phys_page_id)
        try:
            page.reset()
            self.buffer_manager.mark_dirty(phys_page_id, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(phys_page_id, self.file_manager)
        return phys_page_id

    def _overflow_page_ids(self) -> list[int]:
        # pagina 0 siempre es la primera de la cadena; las que siguen
        # (si las hay) fueron asignadas justo despues de las paginas
        # principales actuales, en orden, la primera vez que hicieron
        # falta (ver _insert_into_overflow)
        return [0] + list(range(self.n_pages + 1, self.n_pages + self.n_overflow_pages))

    def _insert_into_overflow(self, record: Record) -> RID:
        # a diferencia de la version vieja, esto SIEMPRE encuentra
        # lugar: si la pagina actual de la cadena esta llena, se agrega
        # una pagina nueva a la cadena en vez de forzar un reorganize.
        # El reorganize lo decide insert() aparte, por proporcion
        # (OVERFLOW_RATIO), no por "se lleno la pagina fisica".
        total_slot_size = self.serializer.get_size_of(record.params) + RID_SIZE + DELETED_SIZE
        current_page_id = self._overflow_page_ids()[-1]
        page = self._load_page(current_page_id)

        try:
            if page.ensure_initialized():
                self.buffer_manager.mark_dirty(current_page_id, self.file_manager)

            if not page.has_space(total_slot_size):
                if page.n_records == 0:
                    raise RuntimeError("record is too big for insertion")

                # la pagina actual de la cadena esta llena: se agrega
                # una nueva y se sigue ahi
                self.buffer_manager.unpin_page(current_page_id, self.file_manager)
                current_page_id = self.file_manager.allocate_page()
                self.n_overflow_pages += 1
                page = self._load_page(current_page_id)
                page.reset()

                if not page.has_space(total_slot_size):
                    raise RuntimeError("record is too big for insertion")

            slot_id = page.insert(record)
            self.buffer_manager.mark_dirty(current_page_id, self.file_manager)

            return self._make_rid(current_page_id, slot_id)
        finally:
            self.buffer_manager.unpin_page(current_page_id, self.file_manager)

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
                    self.buffer_manager.mark_dirty(1, self.file_manager)
                if not page.has_space(total_slot_size):
                    raise RuntimeError("Record is too big for insertion")

                rid = self._make_rid(1, page.insert(record))
                self.first_rid = rid
                self.n_records = 1
                self.buffer_manager.mark_dirty(1, self.file_manager)
            finally:
                self.buffer_manager.unpin_page(1, self.file_manager)

            self._write_header()
            return rid

        previous_rid, next_rid = self._find_neighbors(
            params[self.key_index], duplicates_after=True
        )

        record.next_rid = next_rid
        new_rid = self._insert_into_overflow(record)
        self.n_overflow_records += 1

        if previous_rid is None:
            self.first_rid = new_rid
        else:
            previous_record = self._get_record(previous_rid)
            if previous_record is not None:
                previous_record.next_rid = new_rid
                self._set_record(previous_rid, previous_record)

        self.n_records += 1

        # se reorganiza por PROPORCION de overflow sobre el archivo, no
        # porque una pagina fisica se llene -- ver OVERFLOW_RATIO arriba.
        # El registro que acabamos de insertar tambien se reubica en ese
        # reorganize, asi que hay que rastrear su RID nuevo (ver
        # reorganize(track_rid=...)) en vez de devolver el viejo, ya
        # invalido.
        if (
            self.n_records >= MIN_RECORDS_FOR_OVERFLOW_CHECK
            and self.n_overflow_records / self.n_records >= OVERFLOW_RATIO
        ):
            new_rid = self.reorganize(track_rid=new_rid)
        else:
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
            # solo interesan paginas PRINCIPALES vacias (1..n_pages) --
            # antes "phys_page_id >= 1" alcanzaba porque el overflow era
            # nada mas la pagina 0, pero ahora puede ocupar paginas con
            # id > n_pages tambien, y esas NO cuentan como "se vacio una
            # pagina principal" (vaciarse ahi es normal y no amerita
            # reorganizar)
            page_emptied = any(
                1 <= phys_page_id <= self.n_pages and self._first_live_in_page(phys_page_id) is None
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

    def reorganize(self, track_rid: RID | None = None) -> RID | None:
        # track_rid: reorganize() reasigna el RID de TODOS los registros
        # vivos, asi que un RID que devolvio insert() justo antes de
        # disparar este reorganize quedaria apuntando a cualquier cosa.
        # Si el llamador necesita saber donde termino un registro
        # puntual (identificado por su RID actual, todavia valido en
        # este momento), lo pasa acá y se lo devolvemos ya reubicado.
        self.reorganize_count += 1
        entries = [
            (old_rid, Record(record.params))
            for old_rid, record in self._iter_records()
            if not record.deleted
        ]

        if len(entries) == 0:
            self.first_rid = None
            self.n_records = 0
            self.n_deleted = 0
            self.n_overflow_pages = 1
            self.n_overflow_records = 0
            self._truncate(0)
            self._write_header()
            return None

        entries.sort(key=lambda entry: entry[1].params[self.key_index])

        page = self._load_page(0)
        try:
            page.reset()
            self.buffer_manager.mark_dirty(0, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(0, self.file_manager)

        rids = []
        new_rid_for_tracked = None
        pageindex = 1
        page = None

        for old_rid, record in entries:
            record_size = self.serializer.get_size_of(record.params) + RID_SIZE + DELETED_SIZE

            if page is not None and not page.has_space(record_size):
                self.buffer_manager.mark_dirty(pageindex, self.file_manager)
                self.buffer_manager.unpin_page(pageindex, self.file_manager)
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
            new_rid = self._make_rid(pageindex, slot_id)
            rids.append(new_rid)
            if track_rid is not None and old_rid == track_rid:
                new_rid_for_tracked = new_rid

        self.buffer_manager.mark_dirty(pageindex, self.file_manager)
        self.buffer_manager.unpin_page(pageindex, self.file_manager)

        self.first_rid = rids[0]
        for index in range(len(rids) - 1):
            rec = self._get_record(rids[index])
            if rec is not None:
                rec.next_rid = rids[index + 1]
                self._set_record(rids[index], rec)

        self.n_pages = pageindex
        self.n_records = len(rids)
        self.n_deleted = 0
        self.n_overflow_pages = 1
        self.n_overflow_records = 0
        self._truncate(pageindex)
        self._write_header()

        return new_rid_for_tracked

    def scan(self):
        if self.first_rid is None:
            return

        for rid, record in self._iter_records():
            if not record.deleted:
                yield rid, list(record.params)

    def close(self):
        self.buffer_manager.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _truncate(self, n_main_pages: int):
        self.buffer_manager.flush_file(self.file_manager)
        self.file_manager.truncate(
            self.file_manager.file_header_size + (n_main_pages + 1) * self.page_size
        )