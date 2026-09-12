import struct
from collections import namedtuple
# Una hoja de B+ Tree guarda entradas de tamaño FIJO (key, ref) 
# en un arreglo denso y siempre ordenado por key. Esto es necesario 
# para poder hacer busqueda binaria dentro de la página y 
# para que split pueda dividir el arreglo por la mitad.

PAGE_SIZE = 4096

# Estructura de la cabecera: int, short, int
#  int     page_id
# short   n_entries: cantidad de entradas (key, ref) almacenadas
# int     next_leaf_id: id de la siguiente hoja en la lista enlazada,
# NULL_LEAF si es la ultima hoja

HEADER_FORMAT = ">IHI" 
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
NULL_LEAF = 0

# Estructura de cada entrada: int, int, int
# int     key
# int     ref.page_id
# int     ref.slot_id

ENTRY_FORMAT = ">iii"
ENTRY_SIZE = struct.calcsize(ENTRY_FORMAT)
MAX_ENTRIES = (PAGE_SIZE - HEADER_SIZE) // ENTRY_SIZE 

RID = namedtuple("RID", ["page_id", "slot_id"])

# El page de btree sigue el mismo formato que un slotted page, 
# pero con entradas de tamaño fijo y sin tombstones.
class BTreeLeafPage:

    def __init__(self, page_id: int, data: bytearray = None):
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self.page_id = page_id
            self.n_entries = 0
            self.next_leaf_id = NULL_LEAF
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.n_entries, self.next_leaf_id)

    def load_header(self):
        self.page_id, self.n_entries, self.next_leaf_id = struct.unpack_from(HEADER_FORMAT, self.data, 0)

    def _entry_offset(self, index: int) -> int:
        return HEADER_SIZE + index * ENTRY_SIZE

    def _read_entry(self, index: int):
        offset = self._entry_offset(index)
        key, ref_page_id, ref_slot_id = struct.unpack_from(ENTRY_FORMAT, self.data, offset)
        return key, RID(ref_page_id, ref_slot_id)

    def _write_entry(self, index: int, key: int, ref: RID):
        offset = self._entry_offset(index)
        struct.pack_into(ENTRY_FORMAT, self.data, offset, key, ref.page_id, ref.slot_id)

    
    # Busca la posicion de key en el arreglo ordenado mediante busqueda
    # binaria. Si key no esta presente, retorna la posicion donde
    # deberia insertarse para mantener el orden.
    def _find_index(self, key: int) -> int:
        lo, hi = 0, self.n_entries
        while lo < hi:
            mid = (lo + hi) // 2
            mid_key, _ = self._read_entry(mid)
            if mid_key < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    
    # Retorna el ref asociado a key, o None si key no esta presente.
    
    def find(self, key: int) -> RID | None:
        index = self._find_index(key)
        if index < self.n_entries:
            found_key, ref = self._read_entry(index)
            if found_key == key:
                return ref
        return None

    def has_space(self) -> bool:
        return self.n_entries < MAX_ENTRIES

    
    # Inserta (key, ref) en la posicion que mantiene el arreglo
    # ordenado, desplazando las entradas mayores una posicion a la
    # derecha. Retorna False si la pagina ya esta llena, el caller
    # debe hacer split() y reintentar.

    def insert(self, key: int, ref: RID) -> bool:
        if not self.has_space():
            return False

        index = self._find_index(key)
        for i in range(self.n_entries, index, -1):
            shifted_key, shifted_ref = self._read_entry(i - 1)
            self._write_entry(i, shifted_key, shifted_ref)

        self._write_entry(index, key, ref)
        self.n_entries += 1
        self.save_header()
        return True

    
    # Elimina desplazando el resto del arreglo una posicion a la izquierda. 
    # Retorna False si key no estaba presente. 
    
    def delete(self, key: int) -> bool:
        index = self._find_index(key)
        if index >= self.n_entries:
            return False

        found_key, _ = self._read_entry(index)
        if found_key != key:
            return False

        for i in range(index, self.n_entries - 1):
            next_key, next_ref = self._read_entry(i + 1)
            self._write_entry(i, next_key, next_ref)

        self.n_entries -= 1
        self.save_header()
        return True

    
    # Divide la pagina en dos mitades cuando no entra una insercion.
    # La segunda mitad se muda a la pagina nueva, se actualiza 
    # el encadenamiento next_leaf_id para no romper la lista 
    # enlazada de hojas, y se retorna la clave mediana (la que
    # el nodo padre necesita para ubicar hacia la nueva hoja).
    
    def split(self, new_page_id: int):
        mid = self.n_entries // 2
        new_page = BTreeLeafPage(new_page_id)

        for i in range(mid, self.n_entries):
            entry_key, entry_ref = self._read_entry(i)
            new_page.insert(entry_key, entry_ref)

        self.n_entries = mid
        new_page.next_leaf_id = self.next_leaf_id
        self.next_leaf_id = new_page_id
        self.save_header()
        new_page.save_header()

        split_key, _ = new_page._read_entry(0)
        return split_key, new_page
