import os
import struct
from collections import namedtuple

from heapfile.page import SlottedPage, PAGE_SIZE, HEADER_SIZE, SLOT_SIZE

RID = namedtuple("RID", ["page_id", "slot_id"])

# Pagina 0 del archivo: directorio persistente. Guarda cuantas paginas de
# datos hay y, para cada una, su free_space_bytes actual. Evita re-escanear
# todas las paginas cada vez que se abre el archivo.
DIR_HEADER_FORMAT = ">I"  # page_count
DIR_HEADER_SIZE = struct.calcsize(DIR_HEADER_FORMAT)
DIR_ENTRY_FORMAT = ">H"  # free_space_bytes de una pagina
DIR_ENTRY_SIZE = struct.calcsize(DIR_ENTRY_FORMAT)
MAX_DATA_PAGES = (PAGE_SIZE - DIR_HEADER_SIZE) // DIR_ENTRY_SIZE

# Mayor registro que puede llegar a caber en una pagina recien creada
# (sin fragmentacion, sin otros slots).
MAX_RECORD_SIZE = PAGE_SIZE - HEADER_SIZE - SLOT_SIZE


class HeapFile:
    def __init__(self, filename: str):
        # Si el archivo no existe: crearlo e inicializar la pagina 0
        # (page_count = 0) vacia.
        # Si ya existe: abrirlo y cargar la pagina 0 completa a memoria
        # (self._dir_data) para no tener que leerla de nuevo en cada
        # operacion.
        self.filename=filename
        is_new=not os.path.exists(filename)
        self.file=open(filename,"w+b" if is_new else "r+b")

        if is_new:
            self.page_count=0
            self._dir_data=bytearray(PAGE_SIZE)
            self._save_directory()
        else:
            self.file.seek(0)
            self._dir_data=bytearray(self.file.read(PAGE_SIZE))
            self.page_count = struct.unpack_from(DIR_HEADER_FORMAT, self._dir_data, 0)[0]

    # ---------- directorio de espacio libre (pagina 0) ----------

    def _save_directory(self):
        # Empaqueta self.page_count en self._dir_data y escribe esos 
        # 4096 bytes al inicio del archivo.
        struct.pack_into(DIR_HEADER_FORMAT, self._dir_data,0,self.page_count)
        self.file.seek(0)
        self.file.write(self._dir_data)
        self.file.flush()

    def _get_free_space(self, page_id: int) -> int:
        # Lee de self._dir_data el free_space_bytes guardado para page_id.
        offset = DIR_HEADER_SIZE + (page_id-1)*DIR_ENTRY_SIZE
        return struct.unpack_from(DIR_ENTRY_FORMAT, self._dir_data, offset)[0]

    def _set_free_space(self, page_id: int, free_bytes: int):
        # Escribe en self._dir_data el free_space_bytes de page_id
        # (todavia no persiste a disco, eso lo hace _save_directory).
        offset=DIR_HEADER_SIZE+(page_id-1)*DIR_ENTRY_SIZE
        struct.pack_into(DIR_ENTRY_FORMAT, self._dir_data, offset, free_bytes)

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

    def _new_page(self) -> SlottedPage:
        # Crea una pagina vacia nueva al final del archivo, incrementa
        # page_count, la sincroniza a disco y la devuelve.
        # Ojo: si page_count ya llego a MAX_DATA_PAGES no hay mas espacio
        # en el directorio para trackearla.
        if self.page_count >= MAX_DATA_PAGES:
            raise ValueError("HeapFile alcanzo el maximo de paginas soportado por el directorio")
        page = SlottedPage(self.page_count + 1)
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
        for page_id in range(1,self.page_count+1):
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
        if page_id < 1 or page_id > self.page_count:
            return None
        page=self._load(page_id)
        return page.get_record(slot_id)

    def remove(self, rid: RID) -> bool:
        # Borra el registro en rid (delegado en SlottedPage.delete_record)
        # y sincroniza la pagina/directorio si el borrado fue efectivo.
        page_id, slot_id=rid
        if page_id<1 or page_id>self.page_count:
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
        if page_id<1 or page_id>self.page_count:
            return
        page=self._load(page_id)
        page.defragment()
        self._sync_page(page)

    def vacuum(self):
        # compact() sobre todas las paginas de datos del archivo.
        for page_id in range(1,self.page_count+1):
            self.compact(page_id)

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
