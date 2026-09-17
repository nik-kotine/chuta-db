import struct
from indexes.b_tree_key_codec import encode_key, decode_key, MAX_KEY_SIZE

# Pag de nodo interno
# Necesitan directorio de indices
# no tienen records
#
# Mismo esquema de largo variable que b_tree_leaf_page.py (ver el
# comentario ahi): las keys de ruteo se guardan aparte, en un area que
# crece desde el final de la pagina, y el directorio de keys se
# mantiene siempre ordenado y compacto justo despues del header. Los
# hijos (child_page_id) SI son de tamaño fijo -- van pegados despues
# del directorio de keys, y se recalculan sus offsets cada vez que
# cambia la cantidad de keys (porque el directorio de keys crece o se
# achica antes que ellos).

PAGE_SIZE = 4096

# int     page_id
# short   n_keys
# short   free_space_high: offset donde empieza el area de bytes de
# las keys
HEADER_FORMAT = ">IHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Directorio por key: donde estan sus bytes (offset, largo)
KEY_DIR_FORMAT = ">HH"
KEY_DIR_SIZE = struct.calcsize(KEY_DIR_FORMAT)

# int   child_page_id: puntero a una pagina hija, que puede ser otro
# nodo interno o una hoja (esta clase no distingue cual)
CHILD_FORMAT = ">I"
CHILD_SIZE = struct.calcsize(CHILD_FORMAT)

# tamaño de la entrada (key + su hijo) mas grande posible
MAX_ENTRY_SIZE = KEY_DIR_SIZE + CHILD_SIZE + MAX_KEY_SIZE

# cuantas keys entran en un nodo en el PEOR caso (todas al tope de
# MAX_KEY_SIZE) -- solo informativo, ver mismo comentario en
# b_tree_leaf_page.py
MAX_KEYS = (PAGE_SIZE - HEADER_SIZE - CHILD_SIZE) // MAX_ENTRY_SIZE

# umbral de ocupacion (en bytes) por debajo del cual un nodo esta en
# underflow -- mismo razonamiento del 25% que en la hoja
CAPACITY_BYTES = PAGE_SIZE - HEADER_SIZE - CHILD_SIZE
MIN_USED_BYTES = CAPACITY_BYTES // 4


class BTreeInternalPage:

    def __init__(self, page_id: int, data: bytearray = None):
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self.page_id = page_id
            self.n_keys = 0
            self.free_space_high = PAGE_SIZE
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.n_keys, self.free_space_high)

    def load_header(self):
        self.page_id, self.n_keys, self.free_space_high = struct.unpack_from(HEADER_FORMAT, self.data, 0)

    # El directorio de keys ocupa n_keys slots justo despues del
    # header, y los hijos (n_keys+1) ocupan slots fijos despues del
    # directorio -- por eso su offset depende de n_keys.
    def _key_dir_offset(self, index: int) -> int:
        return HEADER_SIZE + index * KEY_DIR_SIZE

    def _children_base(self) -> int:
        return HEADER_SIZE + self.n_keys * KEY_DIR_SIZE

    def _child_offset(self, index: int) -> int:
        return self._children_base() + index * CHILD_SIZE

    def _read_key(self, index: int):
        key_offset, key_len = struct.unpack_from(KEY_DIR_FORMAT, self.data, self._key_dir_offset(index))
        return decode_key(self.data[key_offset:key_offset + key_len])

    def _read_child(self, index: int) -> int:
        return struct.unpack_from(CHILD_FORMAT, self.data, self._child_offset(index))[0]

    def _write_child(self, index: int, child_page_id: int):
        # los hijos son de tamaño fijo: sobreescribir en el lugar no
        # afecta a nadie mas, no hace falta reescribir toda la pagina
        struct.pack_into(CHILD_FORMAT, self.data, self._child_offset(index), child_page_id)

    def _all_keys(self) -> list:
        return [self._read_key(i) for i in range(self.n_keys)]

    def _all_children(self) -> list:
        return [self._read_child(i) for i in range(self.n_keys + 1)]

    def used_bytes(self) -> int:
        return self.n_keys * KEY_DIR_SIZE + (self.n_keys + 1) * CHILD_SIZE + (PAGE_SIZE - self.free_space_high)

    def has_space(self, key) -> bool:
        needed = KEY_DIR_SIZE + CHILD_SIZE + len(encode_key(key))
        free = self.free_space_high - (self._children_base() + (self.n_keys + 1) * CHILD_SIZE)
        return free >= needed

    # Reescribe la pagina entera a partir de una lista de keys (ya
    # ordenada, N elementos) y una lista de hijos (N+1 elementos).
    # Igual que en la hoja, toda mutacion pasa por aca porque el
    # tamaño de cada key varia.
    def _rewrite(self, keys: list, children: list):
        assert len(children) == len(keys) + 1

        cursor = PAGE_SIZE
        encoded_keys = []
        for key in keys:
            encoded = encode_key(key)
            cursor -= len(encoded)
            encoded_keys.append((cursor, encoded))

        for index, (offset, encoded) in enumerate(encoded_keys):
            self.data[offset:offset + len(encoded)] = encoded
            # _key_dir_offset(index) no depende de n_keys, asi que es
            # valido escribir el directorio antes de actualizarlo abajo
            struct.pack_into(KEY_DIR_FORMAT, self.data, self._key_dir_offset(index), offset, len(encoded))

        self.n_keys = len(keys)

        children_base = self._children_base()
        for index, child in enumerate(children):
            struct.pack_into(CHILD_FORMAT, self.data, children_base + index * CHILD_SIZE, child)

        self.free_space_high = cursor
        self.save_header()

    # sobreescribe la key en index sin tocar los hijos -- usado por
    # b_tree_base.py para actualizar la clave separadora en el padre
    # despues de un borrow, sin necesidad de saber que hijo va con
    # cada uno
    def _write_key(self, index: int, key):
        keys = self._all_keys()
        children = self._all_children()
        keys[index] = key
        self._rewrite(keys, children)

    # Igual que la búsqueda binaria en la hoja
    def _find_key_index(self, key) -> int:
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
    def find_child_index(self, key) -> int:
        lo, hi = 0, self.n_keys
        while lo < hi:
            mid = (lo + hi) // 2
            if key < self._read_key(mid):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def find_child(self, key) -> int:
        return self._read_child(self.find_child_index(key))


    def insert_key(self, key, right_child_page_id: int) -> bool:
        if not self.has_space(key):
            return False

        keys = self._all_keys()
        children = self._all_children()
        index = self._find_key_index(key)

        keys.insert(index, key)
        children.insert(index + 1, right_child_page_id)
        self._rewrite(keys, children)
        return True

    # Inicializa esta pagina (recien creada y todavia vacia) como una
    # raiz nueva con una sola clave y sus dos hijos. Único caso en que
    # un nodo interno arranca con contenido sin pasar por insert_key
    # (que siempre asume que ya hay un hijo a la izquierda).
    def init_as_root(self, left_child_page_id: int, key, right_child_page_id: int):
        self._rewrite([key], [left_child_page_id, right_child_page_id])


    def split(self, new_page_id: int):
        keys = self._all_keys()
        children = self._all_children()
        mid = len(keys) // 2
        pushed_up_key = keys[mid]

        new_page = BTreeInternalPage(new_page_id)
        new_page._rewrite(keys[mid + 1:], children[mid + 1:])

        self._rewrite(keys[:mid], children[:mid + 1])

        return pushed_up_key, new_page

    # true si quedo por debajo del minimo despues de sacarle una clave
    def is_underflow(self) -> bool:
        return self.used_bytes() < MIN_USED_BYTES

    # true si tiene de sobra como para prestarle una clave a un
    # hermano sin quedar el mismo en underflow (peor caso, igual
    # criterio que en la hoja)
    def can_lend(self) -> bool:
        if self.n_keys <= 1:
            return False
        return self.used_bytes() - MAX_ENTRY_SIZE >= MIN_USED_BYTES

    # quita keys[index] y children[index+1] (el par que queda huerfano
    # tras una fusion), desplazando el resto del arreglo
    def delete_key_at(self, index: int):
        keys = self._all_keys()
        children = self._all_children()
        del keys[index]
        del children[index + 1]
        self._rewrite(keys, children)

    # pide prestada la ULTIMA clave+hijo del hermano izquierdo. La
    # clave separadora del padre baja como primera clave aca, y la
    # ultima clave del hermano sube a ser la nueva separadora
    def borrow_from_left(self, left_sibling: "BTreeInternalPage", separator_key) :
        left_keys = left_sibling._all_keys()
        left_children = left_sibling._all_children()
        borrowed_key = left_keys[-1]
        borrowed_child = left_children[-1]
        left_sibling._rewrite(left_keys[:-1], left_children[:-1])

        keys = self._all_keys()
        children = self._all_children()
        keys.insert(0, separator_key)
        children.insert(0, borrowed_child)
        self._rewrite(keys, children)

        return borrowed_key

    # pide prestada la PRIMERA clave+hijo del hermano derecho. La
    # separadora del padre baja como ultima clave aca, y la primera
    # clave del hermano sube a ser la nueva separadora
    def borrow_from_right(self, right_sibling: "BTreeInternalPage", separator_key):
        borrowed_key = right_sibling._read_key(0)
        borrowed_child = right_sibling._read_child(0)

        right_sibling.delete_key_at(0)

        keys = self._all_keys()
        children = self._all_children()
        keys.append(separator_key)
        children.append(borrowed_child)
        self._rewrite(keys, children)

        return borrowed_key

    # fusiona el hermano derecho entero dentro de este nodo, bajando
    # en el medio la clave separadora del padre (a diferencia de la
    # hoja, acá SI hace falta esa clave porque los nodos internos no
    # guardan directamente ningun dato, solo separadores)
    def merge_with_right(self, right_sibling: "BTreeInternalPage", separator_key):
        keys = self._all_keys() + [separator_key] + right_sibling._all_keys()
        children = self._all_children() + right_sibling._all_children()
        self._rewrite(keys, children)
