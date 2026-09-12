import struct

# Pag de nodo interno
# Necesitan directorio de indices
# no tienen records

PAGE_SIZE = 4096
HEADER_FORMAT = ">IH"  # page_id, n_keys
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# int   key (mismo formato que en b_tree_leaf_page.py, para que las
# comparaciones sean consistentes en todo el arbol)
KEY_FORMAT = ">i"
KEY_SIZE = struct.calcsize(KEY_FORMAT)

# int   child_page_id: puntero a una pagina hija, que puede ser otro
# nodo interno o una hoja (esta clase no distingue cual)
CHILD_FORMAT = ">I"
CHILD_SIZE = struct.calcsize(CHILD_FORMAT)

# Siempre hay un hijo mas que claves (N claves separan N+1 hijos), asi
# que reservamos espacio fijo para MAX_KEYS claves y MAX_KEYS+1 hijos.
MAX_KEYS = (PAGE_SIZE - HEADER_SIZE - CHILD_SIZE) // (KEY_SIZE + CHILD_SIZE)


class BTreeInternalPage:

    def __init__(self, page_id: int, data: bytearray = None):
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self.page_id = page_id
            self.n_keys = 0
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.n_keys)

    def load_header(self):
        self.page_id, self.n_keys = struct.unpack_from(HEADER_FORMAT, self.data, 0)

    # Las claves ocupan MAX_KEYS slots fijos justo despues del header,
    # y los hijos ocupan MAX_KEYS+1 slots fijos despues de las claves.
    def _key_offset(self, index: int) -> int:
        return HEADER_SIZE + index * KEY_SIZE

    def _child_offset(self, index: int) -> int:
        return HEADER_SIZE + MAX_KEYS * KEY_SIZE + index * CHILD_SIZE

    def _read_key(self, index: int) -> int:
        return struct.unpack_from(KEY_FORMAT, self.data, self._key_offset(index))[0]

    def _write_key(self, index: int, key: int):
        struct.pack_into(KEY_FORMAT, self.data, self._key_offset(index), key)

    def _read_child(self, index: int) -> int:
        return struct.unpack_from(CHILD_FORMAT, self.data, self._child_offset(index))[0]

    def _write_child(self, index: int, child_page_id: int):
        struct.pack_into(CHILD_FORMAT, self.data, self._child_offset(index), child_page_id)

    # Igual que la búsqueda binaria en la hoja
    def _find_key_index(self, key: int) -> int:
        lo, hi = 0, self.n_keys
        while lo < hi:
            mid = (lo + hi) // 2
            if self._read_key(mid) < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # Retorna el hijo en el primer indice cuya clave
    # es estrictamente mayor a key (o el ultimo hijo si key es mayor o
    # igual a todas las claves). Con esto se cumple la invariante:
    # child[0] cubre < key[0], child[i] cubre [key[i-1], key[i]) para
    # 0 < i < n_keys, y child[n_keys] cubre >= key[n_keys-1].
    def find_child(self, key: int) -> int:
        lo, hi = 0, self.n_keys
        while lo < hi:
            mid = (lo + hi) // 2
            if key < self._read_key(mid):
                hi = mid
            else:
                lo = mid + 1
        return self._read_child(lo)

    def has_space(self) -> bool:
        return self.n_keys < MAX_KEYS

    
    def insert_key(self, key: int, right_child_page_id: int) -> bool:
        if not self.has_space():
            return False

        index = self._find_key_index(key)

        for i in range(self.n_keys, index, -1):
            self._write_key(i, self._read_key(i - 1))
        self._write_key(index, key)

        for i in range(self.n_keys, index, -1):
            self._write_child(i + 1, self._read_child(i))
        self._write_child(index + 1, right_child_page_id)

        self.n_keys += 1
        self.save_header()
        return True

    # Inicializa esta pagina (recien creada y todavia vacia) como una
    # raiz nueva con una sola clave y sus dos hijos. 
    def init_as_root(self, left_child_page_id: int, key: int, right_child_page_id: int):
        self._write_key(0, key)
        self._write_child(0, left_child_page_id)
        self._write_child(1, right_child_page_id)
        self.n_keys = 1
        self.save_header()

    
    def split(self, new_page_id: int):
        mid = self.n_keys // 2
        pushed_up_key = self._read_key(mid)

        new_page = BTreeInternalPage(new_page_id)
        new_n_keys = self.n_keys - mid - 1

        for i in range(new_n_keys):
            new_page._write_key(i, self._read_key(mid + 1 + i))
        for i in range(new_n_keys + 1):
            new_page._write_child(i, self._read_child(mid + 1 + i))

        new_page.n_keys = new_n_keys
        new_page.save_header()

        self.n_keys = mid
        self.save_header()

        return pushed_up_key, new_page
