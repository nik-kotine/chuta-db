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

# minimo de claves para un nodo no-raiz. Misma cuenta que en la hoja:
# floor div asegura que un merge de dos nodos en el minimo, mas la
# clave que baja del padre, siempre entra en una sola pagina
MIN_KEYS = MAX_KEYS // 2


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

    # Retorna el indice del hijo por el que hay que bajar para buscar
    # key: primer indice cuya clave es estrictamente mayor a key (o
    # n_keys si key es mayor o igual a todas). Con esto se cumple la
    # invariante: child[0] cubre < key[0], child[i] cubre
    # [key[i-1], key[i]) para 0 < i < n_keys, y child[n_keys] cubre
    # >= key[n_keys-1]. Separado de find_child() porque
    # b_tree_base.py necesita el indice (no solo el page_id) para
    # saber que hermanos son adyacentes durante el rebalanceo.
    def find_child_index(self, key: int) -> int:
        lo, hi = 0, self.n_keys
        while lo < hi:
            mid = (lo + hi) // 2
            if key < self._read_key(mid):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def find_child(self, key: int) -> int:
        return self._read_child(self.find_child_index(key))

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

    # true si quedo por debajo del minimo despues de sacarle una clave
    def is_underflow(self) -> bool:
        return self.n_keys < MIN_KEYS

    # true si tiene de sobra como para prestarle una clave a un
    # hermano sin quedar el mismo en underflow
    def can_lend(self) -> bool:
        return self.n_keys > MIN_KEYS

    # quita keys[index] y children[index+1] (el par que queda huerfano
    # tras una fusion), desplazando el resto del arreglo
    def delete_key_at(self, index: int):
        for i in range(index, self.n_keys - 1):
            self._write_key(i, self._read_key(i + 1))
        for i in range(index + 1, self.n_keys):
            self._write_child(i, self._read_child(i + 1))
        self.n_keys -= 1
        self.save_header()

    # pide prestada la ULTIMA clave+hijo del hermano izquierdo. La
    # clave separadora del padre baja como primera clave aca, y la
    # ultima clave del hermano sube a ser la nueva separadora
    def borrow_from_left(self, left_sibling: "BTreeInternalPage", separator_key: int) -> int:
        borrowed_key = left_sibling._read_key(left_sibling.n_keys - 1)
        borrowed_child = left_sibling._read_child(left_sibling.n_keys)
        left_sibling.n_keys -= 1
        left_sibling.save_header()

        for i in range(self.n_keys, 0, -1):
            self._write_key(i, self._read_key(i - 1))
        for i in range(self.n_keys + 1, 0, -1):
            self._write_child(i, self._read_child(i - 1))

        self._write_key(0, separator_key)
        self._write_child(0, borrowed_child)
        self.n_keys += 1
        self.save_header()

        return borrowed_key

    # pide prestada la PRIMERA clave+hijo del hermano derecho. La
    # separadora del padre baja como ultima clave aca, y la primera
    # clave del hermano sube a ser la nueva separadora
    def borrow_from_right(self, right_sibling: "BTreeInternalPage", separator_key: int) -> int:
        borrowed_key = right_sibling._read_key(0)
        borrowed_child = right_sibling._read_child(0)

        right_sibling.delete_key_at(0)

        self._write_key(self.n_keys, separator_key)
        self._write_child(self.n_keys + 1, borrowed_child)
        self.n_keys += 1
        self.save_header()

        return borrowed_key

    # fusiona el hermano derecho entero dentro de este nodo, bajando
    # en el medio la clave separadora del padre (a diferencia de la
    # hoja, acá SI hace falta esa clave porque los nodos internos no
    # guardan directamente ningun dato, solo separadores)
    def merge_with_right(self, right_sibling: "BTreeInternalPage", separator_key: int):
        self._write_key(self.n_keys, separator_key)
        base = self.n_keys + 1

        for i in range(right_sibling.n_keys):
            self._write_key(base + i, right_sibling._read_key(i))
        for i in range(right_sibling.n_keys + 1):
            self._write_child(base + i, right_sibling._read_child(i))

        self.n_keys = base + right_sibling.n_keys
        self.save_header()
