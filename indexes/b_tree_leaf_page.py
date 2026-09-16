import struct
from collections import namedtuple
from indexes.b_tree_key_codec import encode_key, decode_key, MAX_KEY_SIZE
# Una hoja de B+ Tree guarda entradas (key, ref) en un arreglo denso y
# siempre ordenado por key. La key es de largo VARIABLE (soporta int
# y str) hasta MAX_KEY_SIZE bytes ya codificada -- ver
# b_tree_key_codec.py. Por eso, a diferencia de un arreglo de tamaño
# fijo, cada entrada del directorio solo guarda offset+largo hacia
# donde estan los bytes reales de la key, que se almacenan aparte en
# un area que crece desde el final de la pagina (mismo esquema que
# SlottedPage en heapfile/page.py, pero el directorio se mantiene
# siempre ORDENADO por key en vez de por orden de insercion, y sin
# tombstones -- cada mutacion reescribe la pagina entera compactada).

PAGE_SIZE = 4096

# Estructura de la cabecera: int, short, int, short
#  int     page_id
# short   n_entries: cantidad de entradas (key, ref) almacenadas
# int     next_leaf_id: id de la siguiente hoja en la lista enlazada,
# NULL_LEAF si es la ultima hoja
# short   free_space_high: offset donde empieza el area de bytes de
# las keys (crece hacia atras a medida que se ocupa la pagina)

HEADER_FORMAT = ">IHIH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
NULL_LEAF = 0

# Directorio por entrada: donde estan los bytes de la key (offset,
# largo) y la referencia al registro real (fija, no varia de tamaño)
# int     key
# int     ref.page_id
# int     ref.slot_id
DIR_FORMAT = ">HHii"
DIR_SIZE = struct.calcsize(DIR_FORMAT)

# tamaño de la entrada mas grande posible: su slot de directorio mas
# una key al tope de MAX_KEY_SIZE
MAX_ENTRY_SIZE = DIR_SIZE + MAX_KEY_SIZE

# cuantas entradas entran en una pagina en el PEOR caso (todas las
# keys al tope de MAX_KEY_SIZE) -- con keys mas chicas entran muchas
# mas. Es solo un piso informativo, no la capacidad real de la pagina.
MAX_ENTRIES = (PAGE_SIZE - HEADER_SIZE) // MAX_ENTRY_SIZE

# umbral de ocupacion (en bytes) por debajo del cual una hoja esta en
# underflow. Se usa 25% en vez del 50% clasico de arreglos de tamaño
# fijo para dejar margen de sobra: como el tamaño de cada entrada
# varia, no hay una cota exacta tipo "(MIN-1)+MIN <= MAX_ENTRIES" --
# con 25% dos hojas en underflow fusionandose ocupan como mucho ~50%
# de una pagina mas una entrada, muy lejos de desbordar PAGE_SIZE.
CAPACITY_BYTES = PAGE_SIZE - HEADER_SIZE
MIN_USED_BYTES = CAPACITY_BYTES // 4

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
            self.free_space_high = PAGE_SIZE
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.n_entries, self.next_leaf_id, self.free_space_high)

    def load_header(self):
        self.page_id, self.n_entries, self.next_leaf_id, self.free_space_high = struct.unpack_from(HEADER_FORMAT, self.data, 0)

    def _dir_offset(self, index: int) -> int:
        return HEADER_SIZE + index * DIR_SIZE

    def _read_dir(self, index: int):
        return struct.unpack_from(DIR_FORMAT, self.data, self._dir_offset(index))

    def _read_entry(self, index: int):
        key_offset, key_len, ref_page_id, ref_slot_id = self._read_dir(index)
        key = decode_key(self.data[key_offset:key_offset + key_len])
        return key, RID(ref_page_id, ref_slot_id)


    # Busca la posicion de key en el arreglo ordenado mediante busqueda
    # binaria. Si key no esta presente, retorna la posicion donde
    # deberia insertarse para mantener el orden.
    def _find_index(self, key) -> int:
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

    def find(self, key) -> RID | None:
        index = self._find_index(key)
        if index < self.n_entries:
            found_key, ref = self._read_entry(index)
            if found_key == key:
                return ref
        return None

    def _all_entries(self) -> list:
        return [self._read_entry(i) for i in range(self.n_entries)]

    def used_bytes(self) -> int:
        return self.n_entries * DIR_SIZE + (PAGE_SIZE - self.free_space_high)

    def has_space(self, key) -> bool:
        needed = DIR_SIZE + len(encode_key(key))
        free = self.free_space_high - self._dir_offset(self.n_entries)
        return free >= needed

    # Reescribe la pagina entera a partir de una lista (key, ref) ya
    # ordenada: el directorio queda compacto justo despues del header,
    # y los bytes de las keys quedan compactados desde el final de la
    # pagina. Toda mutacion (insert/delete/split/borrow/merge) pasa
    # por aca en vez de desplazar bytes in-place, porque el tamaño de
    # cada entrada varia.
    def _rewrite(self, entries: list):
        cursor = PAGE_SIZE
        for index, (key, ref) in enumerate(entries):
            encoded = encode_key(key)
            cursor -= len(encoded)
            self.data[cursor:cursor + len(encoded)] = encoded
            struct.pack_into(DIR_FORMAT, self.data, self._dir_offset(index), cursor, len(encoded), ref.page_id, ref.slot_id)

        self.n_entries = len(entries)
        self.free_space_high = cursor
        self.save_header()


    # Inserta (key, ref) en la posicion que mantiene el arreglo
    # ordenado. Retorna False si la pagina ya esta llena, el caller
    # debe hacer split() y reintentar.

    def insert(self, key, ref: RID) -> bool:
        if not self.has_space(key):
            return False

        entries = self._all_entries()
        entries.insert(self._find_index(key), (key, ref))
        self._rewrite(entries)
        return True


    # Elimina la entrada con esa key. Retorna False si key no estaba
    # presente.

    def delete(self, key) -> bool:
        index = self._find_index(key)
        if index >= self.n_entries:
            return False

        found_key, _ = self._read_entry(index)
        if found_key != key:
            return False

        entries = self._all_entries()
        del entries[index]
        self._rewrite(entries)
        return True


    # Divide la pagina en dos mitades cuando no entra una insercion.
    # La segunda mitad se muda a la pagina nueva, se actualiza
    # el encadenamiento next_leaf_id para no romper la lista
    # enlazada de hojas, y se retorna la clave mediana (la que
    # el nodo padre necesita para ubicar hacia la nueva hoja).

    def split(self, new_page_id: int):
        entries = self._all_entries()
        mid = len(entries) // 2

        new_page = BTreeLeafPage(new_page_id)
        new_page.next_leaf_id = self.next_leaf_id
        new_page._rewrite(entries[mid:])

        self.next_leaf_id = new_page_id
        self._rewrite(entries[:mid])

        split_key, _ = new_page._read_entry(0)
        return split_key, new_page

    # true si quedo por debajo del minimo despues de un delete
    def is_underflow(self) -> bool:
        return self.used_bytes() < MIN_USED_BYTES

    # true si tiene de sobra como para prestarle una entrada a un
    # hermano sin quedar ella misma en underflow. Se usa el peor caso
    # (la entrada mas grande posible) porque no se sabe de antemano
    # cual entrada especifica se va a prestar.
    def can_lend(self) -> bool:
        if self.n_entries <= 1:
            return False
        return self.used_bytes() - MAX_ENTRY_SIZE >= MIN_USED_BYTES

    # se lleva la ULTIMA entrada del hermano izquierdo y la pone
    # primera aca (redistribucion). retorna la nueva clave separadora
    # que el padre tiene que guardar entre ambas hojas
    def borrow_from_left(self, left_sibling: "BTreeLeafPage"):
        left_entries = left_sibling._all_entries()
        borrowed = left_entries.pop()
        left_sibling._rewrite(left_entries)

        entries = self._all_entries()
        entries.insert(0, borrowed)
        self._rewrite(entries)

        return borrowed[0]

    # se lleva la PRIMERA entrada del hermano derecho y la agrega al
    # final aca. retorna la nueva clave separadora (la que quedo
    # primera en el hermano despues de sacarle una)
    def borrow_from_right(self, right_sibling: "BTreeLeafPage"):
        right_entries = right_sibling._all_entries()
        borrowed = right_entries.pop(0)
        right_sibling._rewrite(right_entries)

        entries = self._all_entries()
        entries.append(borrowed)
        self._rewrite(entries)

        return right_entries[0][0]

    # fusiona el hermano derecho entero dentro de esta hoja (cuando
    # ninguna de las dos tiene de sobra para redistribuir). el
    # hermano derecho queda huerfano, lo saca de la cadena quien
    # llame a esto (b_tree_base.py, sacando su clave del padre)
    def merge_with_right(self, right_sibling: "BTreeLeafPage"):
        entries = self._all_entries() + right_sibling._all_entries()
        self.next_leaf_id = right_sibling.next_leaf_id
        self._rewrite(entries)
