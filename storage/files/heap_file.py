import os
import struct
from collections import namedtuple
from storage.record_file import RecordFile
from storage.pages.slotted_page import SlottedPage, PAGE_SIZE, HEADER_SIZE, SLOT_SIZE, NULL_SLOT
from storage.buffer_manager import BufferManager
RID = namedtuple("RID", ["page_id", "slot_id"])

# Pagina 0 del archivo: directorio persistente. Guarda cuantas paginas de
# datos hay y, para cada una, su free_space_bytes actual. Evita re-escanear
# todas las paginas cada vez que se abre el archivo.
# Una sola pagina de directorio solo tiene espacio para trackear un numero
# limitado de paginas de datos (ENTRIES_PER_DIR_PAGE). Cuando se llena, se
# encadena una pagina de directorio nueva via next_dir_page_id -- asi el
# heap file no tiene techo de paginas, el unico limite real es el disco.
DIR_HEADER_FORMAT = ">II"  # (page_count, next_dir_page_id)
DIR_HEADER_SIZE = struct.calcsize(DIR_HEADER_FORMAT)
DIR_ENTRY_FORMAT = ">H"  # free_space_bytes de una pagina
DIR_ENTRY_SIZE = struct.calcsize(DIR_ENTRY_FORMAT)
ENTRIES_PER_DIR_PAGE = (PAGE_SIZE - DIR_HEADER_SIZE) // DIR_ENTRY_SIZE
NULL_DIR_PAGE = 0  # pagina 0 nunca es "la siguiente" de nadie, sirve de centinela

# Mayor registro que puede llegar a caber en una pagina recien creada
# (sin fragmentacion, sin otros slots).
MAX_RECORD_SIZE = PAGE_SIZE - HEADER_SIZE - SLOT_SIZE


class HeapFile(RecordFile):
    def __init__(self, filename: str, buffer_manager: BufferManager, record_format: str):
        # Si el archivo no existe: crearlo e inicializar la pagina 0
        # (page_count = 0) vacia.
        # Si ya existe: abrirlo y cargar TODA la cadena de paginas de
        # directorio a memoria (self._dir_pages, una por cada pagina de
        # directorio que exista) siguiendo next_dir_page_id, para no tener
        # que releerlas de disco en cada operacion.
        self.filename=filename
        self.buffer_manager = buffer_manager
        self.file_manager = buffer_manager.file_manager
        self.record_format = record_format

        file_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        is_new = file_size <= self.file_manager.file_header_size
        self._dir_page_ids=[]

        if is_new:
            page_id = self.file_manager.allocate_page()
            if page_id != 0:
                raise RuntimeError(f"Expected page_id 0 but got {page_id}")

            self.page_count=0
            self._dir_page_ids=[0]

            dir_data = self.buffer_manager.fetch_page(0)
            struct.pack_into(DIR_HEADER_FORMAT, dir_data, 0, self.page_count, NULL_DIR_PAGE)
            self.buffer_manager.mark_dirty(0)
            self.buffer_manager.unpin_page(0)
        else:
            page_id=0
            while True:
                self._dir_page_ids.append(page_id)
                dir_data = self.buffer_manager.fetch_page(page_id)
                next_dir_page_id = struct.unpack_from(DIR_HEADER_FORMAT, dir_data, 0)[1]

                if page_id == 0:
                    self.page_count = struct.unpack_from(DIR_HEADER_FORMAT, dir_data, 0)[0]

                self.buffer_manager.unpin_page(page_id)

                if next_dir_page_id==NULL_DIR_PAGE:
                    break
                page_id=next_dir_page_id


    # ---------- directorio de espacio libre (pagina 0) ----------

    def _entry_location(self, page_id: int):
        # Ubica en que pagina de directorio (indice dentro de
        # self._dir_pages) y en que offset dentro de ella vive la entrada
        # de free_space_bytes de una pagina de datos dada.
        flat_index = page_id - 1
        dir_index = flat_index // ENTRIES_PER_DIR_PAGE
        entry_index = flat_index % ENTRIES_PER_DIR_PAGE
        offset = DIR_HEADER_SIZE + entry_index*DIR_ENTRY_SIZE
        return dir_index, offset

    def _get_free_space(self, page_id: int) -> int:
        # Lee de la pagina de directorio correspondiente el free_space_bytes
        # guardado para page_id.
        dir_index, offset = self._entry_location(page_id)
        dir_page_id = self._dir_page_ids[dir_index]

        dir_data = self.buffer_manager.fetch_page(dir_page_id)
        free_space = struct.unpack_from(DIR_ENTRY_FORMAT, dir_data, offset)[0]
        self.buffer_manager.unpin_page(dir_page_id)
        return free_space

    def _set_free_space(self, page_id: int, free_bytes: int):
        # Escribe en la pagina de directorio correspondiente el
        # free_space_bytes de page_id
        dir_index, offset = self._entry_location(page_id)
        dir_page_id = self._dir_page_ids[dir_index]

        dir_data = self.buffer_manager.fetch_page(dir_page_id)
        struct.pack_into(DIR_ENTRY_FORMAT, dir_data, offset, free_bytes)

        self.buffer_manager.mark_dirty(dir_page_id)
        self.buffer_manager.unpin_page(dir_page_id)

    def _update_page_count(self):
        dir_data = self.buffer_manager.fetch_page(0)
        struct.pack_into(">I", dir_data, 0, self.page_count)
        self.buffer_manager.mark_dirty(0)
        self.buffer_manager.unpin_page(0)

    # ---------- I/O de paginas de datos ----------

    def _page_offset(self, page_id: int) -> int:
        return page_id * PAGE_SIZE  # page_id 0 = directorio, 1..N = datos

    def _load(self, page_id: int) -> SlottedPage:
        raw = self.buffer_manager.fetch_page(page_id)
        return SlottedPage(page_id, data=raw)

    def _sync_page(self, page: SlottedPage):
        # Marcar la página de datos, actualizar su espacio libre y despinarla
        self.buffer_manager.mark_dirty(page.page_id)
        self.buffer_manager.unpin_page(page.page_id)
        self._set_free_space(page.page_id, page.free_space_bytes)

    @property
    def next_page_id(self) -> int:
        # El proximo id de pagina libre en el archivo (sea de datos o de
        # directorio-overflow). Los ids se reparten secuencialmente entre
        # ambos tipos, asi que es simplemente cuantas paginas de directorio
        # y de datos existen hasta ahora.
        return len(self._dir_page_ids) + self.page_count

    def _needs_new_dir_page(self) -> bool:
        # True si la proxima pagina de datos no entra en ninguna pagina
        # de directorio existente (se les acabaron las entradas).
        dir_index = (self.next_page_id - 1) // ENTRIES_PER_DIR_PAGE
        return dir_index >= len(self._dir_page_ids)

    def _add_dir_page(self):
        # Encadena una pagina de directorio nueva al final de la cadena:
        # le pone el next_dir_page_id a la ultima pagina de la cadena y
        # reserva la pagina nueva (vacia, con next_dir_page_id=0/NULL).
        new_dir_id = self.next_page_id
        last_dir_id = self._dir_page_ids[-1]

        last_data = self.buffer_manager.fetch_page(last_dir_id)
        struct.pack_into(">I", last_data, 4, new_dir_id)  # 2do campo del header = next_dir_page_id
        self.buffer_manager.mark_dirty(last_dir_id)
        self.buffer_manager.unpin_page(last_dir_id)

        allocated_id = self.file_manager.allocate_page()
        if allocated_id != new_dir_id:
            new_dir_id = allocated_id

        new_data =self.buffer_manager.fetch_page(new_dir_id)
        struct.pack_into(DIR_HEADER_FORMAT, new_data, 0, 0, NULL_DIR_PAGE) # (pc: 0, next:NULL=0)
        self.buffer_manager.mark_dirty(new_dir_id)
        self.buffer_manager.unpin_page(new_dir_id)

        self._dir_page_ids.append(new_dir_id)

    def _is_data_page(self, page_id: int) -> bool:
        # Un page_id es valido si cae dentro del rango usado y no es en
        # realidad una pagina de directorio (esas tambien consumen ids).
        return 1 <= page_id < self.next_page_id and page_id not in self._dir_page_ids

    def _new_page(self) -> SlottedPage:
        # Crea una pagina de datos vacia nueva al final del archivo,
        # incrementa page_count, la sincroniza a disco y la devuelve.
        # Si a la pagina de directorio actual ya no le quedan entradas
        # libres, primero encadena una pagina de directorio nueva -- ya
        # no hay un tope duro de paginas, solo el espacio en disco.
        if self._needs_new_dir_page():
            self._add_dir_page()

        new_page_id = self.file_manager.allocate_page()
        raw_data = self.buffer_manager.fetch_page(new_page_id)
        page = SlottedPage(new_page_id, data=raw_data)

        page.page_id = new_page_id
        page.slot_count = 0
        page.free_space_high = PAGE_SIZE
        page.first_free_slot = NULL_SLOT  # Usamos la constante oficial de SlottedPage (0xFFFF)
        
        page.save_header()

        self.page_count += 1
        self._update_page_count()
        self._set_free_space(new_page_id, page.free_space_bytes)

        return page
    
    # ---------- API publica ----------

    def add(self, record_data: bytes) -> RID:
        # record_data puede pesar cualquier cosa <= MAX_RECORD_SIZE
        # (heapfile.py no sabe ni le importa si es de largo fijo o
        # variable, eso ya lo resolvio record.py).
        # 1. Si no entra en ninguna pagina vacia, ValueError.
        # 2. Buscar en el directorio (sin tocar disco) una pagina con
        #    free_space_bytes suficiente; delegar el insert real en
        #    SlottedPage.insert.
        # 3. Si ninguna alcanza, pedir pagina nueva con _new_page.
        # Devuelve el RID (page_id, slot_id) del registro insertado.
        if len(record_data) > MAX_RECORD_SIZE:
            raise ValueError(f"Reg's length exceeds maximum: {len(record_data)} bytes, maximum {MAX_RECORD_SIZE}")

        needed = len(record_data) + SLOT_SIZE
        for page_id in range(1, self.next_page_id):
            if page_id in self._dir_page_ids:
                continue
            if self._get_free_space(page_id) < needed:
                continue
            page = self._load(page_id)
            slot_id = page.insert(record_data)
            self._sync_page(page)
            return RID(page_id, slot_id)
        
        page = self._new_page()
        slot_id = page.insert(record_data)
        self._sync_page(page)
        return RID(page.page_id, slot_id)
    
    def get(self, rid: RID):
        # Devuelve los bytes del registro en rid, o None si no existe
        # o esta borrado. Delegado en SlottedPage.get_record.
        page_id, slot_id = rid
        if not self._is_data_page(page_id):
            return None
        page = self._load(page_id)
        record = page.get_record(slot_id)
        self.buffer_manager.unpin_page(page_id)
        return record

    def remove(self, rid: RID) -> bool:
        # Borra el registro en rid (delegado en SlottedPage.delete_record)
        # y sincroniza la pagina/directorio si el borrado fue efectivo.
        page_id, slot_id = rid
        if not self._is_data_page(page_id):
            return False
        page = self._load(page_id)
        ok = page.delete_record(slot_id)
        if ok:
            self._sync_page(page)
        else:
            self.buffer_manager.unpin_page(page_id)
        return ok

    def compact(self, page_id: int):
        # Fuerza defragment() sobre una pagina puntual y sincroniza.
        # Util para recuperar espacio muerto que quedo tras varios
        # remove() sin un add() posterior que lo reclame.
        if not self._is_data_page(page_id):
            return
        page=self._load(page_id)
        page.defragment()
        self._sync_page(page)

    def vacuum(self):
        # compact() sobre todas las paginas de datos del archivo (saltando
        # las paginas de directorio, que tambien viven en este rango de ids).
        for page_id in range(1,self.next_page_id):
            if page_id in self._dir_page_ids:
                continue
            self.compact(page_id)


    def close(self):
        self.buffer_manager.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
