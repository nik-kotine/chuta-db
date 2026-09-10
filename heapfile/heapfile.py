import os
import struct
from collections import namedtuple

from heapfile.page import SlottedPage, PAGE_SIZE, HEADER_SIZE, SLOT_SIZE

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


class HeapFile:
    def __init__(self, filename: str):
        # Si el archivo no existe: crearlo e inicializar la pagina 0
        # (page_count = 0) vacia.
        # Si ya existe: abrirlo y cargar TODA la cadena de paginas de
        # directorio a memoria (self._dir_pages, una por cada pagina de
        # directorio que exista) siguiendo next_dir_page_id, para no tener
        # que releerlas de disco en cada operacion.
        self.filename=filename
        is_new=not os.path.exists(filename)
        self.file=open(filename,"w+b" if is_new else "r+b")

        if is_new:
            self.page_count=0
            self._dir_pages=[bytearray(PAGE_SIZE)]
            self._dir_page_ids=[0]
            self._save_directory()
        else:
            self._dir_pages=[]
            self._dir_page_ids=[]
            page_id=0
            while True:
                self.file.seek(page_id*PAGE_SIZE)
                block=bytearray(self.file.read(PAGE_SIZE))
                self._dir_pages.append(block)
                self._dir_page_ids.append(page_id)
                next_dir_page_id=struct.unpack_from(DIR_HEADER_FORMAT, block, 0)[1]
                if next_dir_page_id==NULL_DIR_PAGE:
                    break
                page_id=next_dir_page_id
            self.page_count = struct.unpack_from(DIR_HEADER_FORMAT, self._dir_pages[0], 0)[0]

    # ---------- directorio de espacio libre (pagina 0) ----------

    def _save_directory(self):
        # El page_count global solo vive en la primera pagina de directorio
        # (el next_dir_page_id de cada pagina no cambia aca, eso lo pisa
        # _add_dir_page una sola vez cuando encadena una pagina nueva).
        # Reescribe TODAS las paginas de directorio a disco -- son pocas
        # (una cada ENTRIES_PER_DIR_PAGE paginas de datos), asi que es barato.
        struct.pack_into(">I", self._dir_pages[0], 0, self.page_count)
        for dir_page_id, block in zip(self._dir_page_ids, self._dir_pages):
            self.file.seek(dir_page_id*PAGE_SIZE)
            self.file.write(block)
        self.file.flush()

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
        return struct.unpack_from(DIR_ENTRY_FORMAT, self._dir_pages[dir_index], offset)[0]

    def _set_free_space(self, page_id: int, free_bytes: int):
        # Escribe en la pagina de directorio correspondiente el
        # free_space_bytes de page_id (todavia no persiste a disco,
        # eso lo hace _save_directory).
        dir_index, offset = self._entry_location(page_id)
        struct.pack_into(DIR_ENTRY_FORMAT, self._dir_pages[dir_index], offset, free_bytes)

    # ---------- I/O de paginas de datos ----------

    def _page_offset(self, page_id: int) -> int:
        return page_id * PAGE_SIZE  # page_id 0 = directorio, 1..N = datos

    def _load(self, page_id: int) -> SlottedPage:
        # Lee PAGE_SIZE bytes del archivo en el offset de page_id y
        # arma un SlottedPage a partir de ese bytearray.
        self.file.seek(self._page_offset(page_id))
        raw = bytearray(self.file.read(PAGE_SIZE))
        return SlottedPage(page_id, data=raw)

    def _write_page(self, page: SlottedPage):
        # Escribe page.data de vuelta en su offset dentro del archivo.
        self.file.seek(self._page_offset(page.page_id))
        self.file.write(page.data)

    def _sync_page(self, page: SlottedPage):
        # Persiste una pagina modificada y actualiza su entrada en el directorio
        self._write_page(page)
        self._set_free_space(page.page_id, page.free_space_bytes)
        self._save_directory()

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
        return dir_index >= len(self._dir_pages)

    def _add_dir_page(self):
        # Encadena una pagina de directorio nueva al final de la cadena:
        # le pone el next_dir_page_id a la ultima pagina de la cadena y
        # reserva la pagina nueva (vacia, con next_dir_page_id=0/NULL).
        new_dir_id = self.next_page_id
        last_block = self._dir_pages[-1]
        last_id = self._dir_page_ids[-1]
        struct.pack_into(">I", last_block, 4, new_dir_id)  # 2do campo del header = next_dir_page_id
        self.file.seek(last_id*PAGE_SIZE)
        self.file.write(last_block)

        new_block = bytearray(PAGE_SIZE)  # ya nace con next_dir_page_id=0 (NULL)
        self._dir_pages.append(new_block)
        self._dir_page_ids.append(new_dir_id)
        self.file.seek(new_dir_id*PAGE_SIZE)
        self.file.write(new_block)
        self.file.flush()

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
        page = SlottedPage(self.next_page_id)
        self.page_count += 1
        self._sync_page(page)
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
        if len(record_data)>MAX_RECORD_SIZE:
            raise ValueError(f"Registro demasiado grande: {len(record_data)} bytes, maximo {MAX_RECORD_SIZE}")

        needed=len(record_data)+SLOT_SIZE
        for page_id in range(1,self.next_page_id):
            if page_id in self._dir_page_ids: # esta es una pagina de directorio, no de datos
                continue
            if self._get_free_space(page_id)<needed:
                continue
            page=self._load(page_id)
            slot_id=page.insert(record_data)
            self._sync_page(page)
            return RID(page_id,slot_id)
        page=self._new_page()
        slot_id=page.insert(record_data)
        self._sync_page(page)
        return RID(page.page_id,slot_id)
    def get(self, rid: RID):
        # Devuelve los bytes del registro en rid, o None si no existe
        # o esta borrado. Delegado en SlottedPage.get_record.
        page_id, slot_id = rid
        if not self._is_data_page(page_id):
            return None
        page=self._load(page_id)
        return page.get_record(slot_id)

    def remove(self, rid: RID) -> bool:
        # Borra el registro en rid (delegado en SlottedPage.delete_record)
        # y sincroniza la pagina/directorio si el borrado fue efectivo.
        page_id, slot_id=rid
        if not self._is_data_page(page_id):
            return False
        page=self._load(page_id)
        ok=page.delete_record(slot_id)
        if ok:
            self._sync_page(page)
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
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
