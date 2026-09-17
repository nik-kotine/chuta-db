import struct

from indexes.b_tree_leaf_page import PAGE_SIZE, NULL_LEAF, BTreeLeafPage
from indexes.b_tree_internal_page import BTreeInternalPage
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager

# Header del archivo de índice, guarda dónde está la raíz y su altura
ROOT_HEADER_FORMAT = ">I?B"  # root_page_id, root_is_leaf, height
ROOT_HEADER_SIZE = struct.calcsize(ROOT_HEADER_FORMAT)


class BPlusTreeBase:
    # Lógica común del árbol (búsqueda, inserción, borrado). No sabe
    # de HeapFile ni SequentialFile, eso lo llenan las subclases.

    def __init__(self, index_filename: str, buffer_frames: int = 50):

        self.index_filename = index_filename
        self.file_manager = FileManager(index_filename, PAGE_SIZE, ROOT_HEADER_SIZE)
        self.buffer_manager = BufferManager(self.file_manager, buffer_frames)

        is_new = len(self.file_manager.read_header()) < ROOT_HEADER_SIZE

        if is_new:
            self._init_empty_index()
        else:
            self._load_root_header()

    # crea (o recrea) un indice vacio: la pagina 0 es la hoja vacia
    # que arranca como raiz (el header de la raiz vive aparte, en el
    # header del archivo). Separado de __init__ para que _reindex() en
    # las subclases lo pueda reusar cuando haga falta reconstruir el
    # indice desde cero (por ejemplo, si SequentialFile.reorganize()
    # invalido los RID guardados)
    def _init_empty_index(self):
        # el archivo subyacente se reescribe entero -- cualquier pagina
        # que el buffer pool tuviera cacheada de antes queda invalida
        self.buffer_manager.invalidate_all()
        self.file_manager.truncate(self.file_manager.file_header_size)

        root_page_id = self.file_manager.allocate_page()
        root_leaf = BTreeLeafPage(root_page_id)
        self._save_page(root_leaf)

        self.root_page_id = root_page_id
        self.root_is_leaf = True
        self.height = 0
        self._save_root_header()  # sin esto el header se queda en ceros

    # -------- header del archivo: persistencia de la raiz ---------

    def _load_root_header(self):
        header = self.file_manager.read_header()
        self.root_page_id, self.root_is_leaf, self.height = struct.unpack(ROOT_HEADER_FORMAT, header)

    def _save_root_header(self):
        header = struct.pack(ROOT_HEADER_FORMAT, self.root_page_id, self.root_is_leaf, self.height)
        self.file_manager.write_header(header)

    # ---------- I/O de paginas del arbol (hoja o nodo interno) ----------
    # cada load/save es autocontenido (pin+unpin en el mismo metodo, sin
    # quedarse con una pagina "prestada" del pool) para no arriesgar
    # pin leaks cuando una operacion sostiene varias paginas a la vez
    # (el camino descendido, hermanos durante rebalanceo, etc).

    def _load_leaf(self, page_id: int) -> BTreeLeafPage:
        data = self.buffer_manager.fetch_page(page_id)
        self.buffer_manager.unpin_page(page_id)
        return BTreeLeafPage(page_id, data=data)

    def _load_internal(self, page_id: int) -> BTreeInternalPage:
        data = self.buffer_manager.fetch_page(page_id)
        self.buffer_manager.unpin_page(page_id)
        return BTreeInternalPage(page_id, data=data)

    def _save_page(self, page) -> None:
        buf = self.buffer_manager.fetch_page(page.page_id)
        buf[:] = page.data
        self.buffer_manager.mark_dirty(page.page_id)
        self.buffer_manager.unpin_page(page.page_id)

    def _allocate_page_id(self) -> int:
        return self.file_manager.allocate_page()

    # ---------- hooks de almacenamiento ----------
    # Implementación en subclases clusterd y unclustered, acá es donde se tiene
    # contacto con el almacenamiento real

    def _store_record(self, params):
        raise NotImplementedError

    def _fetch_record(self, ref):
        raise NotImplementedError

    def _delete_record(self, key, ref) -> bool:
        raise NotImplementedError

    # ---------- API publica del arbol ----------

    def search(self, key):
        # baja "height" niveles por nodos internos, al llegar a
        # depth == 0, la página siguiente es siempre una hoja
        page_id = self.root_page_id
        depth = self.height
        while depth > 0:
            node = self._load_internal(page_id)
            page_id = node.find_child(key)
            depth -= 1

        leaf = self._load_leaf(page_id)
        return leaf.find(key)

    def insert(self, key, params):
        # se persiste el dato real primero, para tener el ref listo
        ref = self._store_record(params)
        return self._insert_ref(key, ref)

    # hace todo lo de insert() menos persistir el dato real -- separado
    # para que las subclases puedan reindexar un ref que ya existe
    # (por ejemplo, si hay que reconstruir el indice entero porque
    # SequentialFile.reorganize() invalido los RID de todos)
    def _insert_ref(self, key, ref):
        # mismo descenso que search(), guardando el camino recorrido
        # para poder propagar un split hacia arriba si hace falta
        path = []
        page_id = self.root_page_id
        depth = self.height
        while depth > 0:
            node = self._load_internal(page_id)
            path.append(node)
            page_id = node.find_child(key)
            depth -= 1

        leaf = self._load_leaf(page_id)

        if leaf.insert(key, ref):
            self._save_page(leaf)
            return ref

        # la hoja esta llena: la dividimos y recien ahi insertamos,
        # en la mitad que le corresponda segun split_key
        new_leaf_id = self._allocate_page_id()
        split_key, new_leaf = leaf.split(new_leaf_id)

        if key < split_key:
            leaf.insert(key, ref)
        else:
            new_leaf.insert(key, ref)

        self._save_page(leaf)
        self._save_page(new_leaf)

        self._propagate_split(path, split_key, new_leaf_id)
        return ref

    def _propagate_split(self, path, split_key, right_page_id):
        # empuja la clave mediana hacia el padre, dividiendo en
        # cascada si también se llena, hasta llegar a la raíz
        if not path:
            # la cascada llego hasta la raiz actual, creamos una raiz
            # nueva con exactamente dos hijos (la raiz vieja y la
            # mitad que acaba de salir de su split) 
            new_root_id = self._allocate_page_id()
            new_root = BTreeInternalPage(new_root_id)
            new_root.init_as_root(self.root_page_id, split_key, right_page_id)
            self._save_page(new_root)

            self.root_page_id = new_root_id
            self.root_is_leaf = False
            self.height += 1
            self._save_root_header()
            return

        parent = path.pop()  # el padre mas cercano a la hoja primero
        if parent.insert_key(split_key, right_page_id):
            self._save_page(parent)
            return

        # el padre también está lleno, lo dividimos y seguimos
        # propagando la clave que empuja hacia arriba un nivel mas
        new_parent_id = self._allocate_page_id()
        pushed_up_key, new_parent = parent.split(new_parent_id)

        if split_key < pushed_up_key:
            parent.insert_key(split_key, right_page_id)
        else:
            new_parent.insert_key(split_key, right_page_id)

        self._save_page(parent)
        self._save_page(new_parent)

        self._propagate_split(path, pushed_up_key, new_parent_id)

    def delete(self, key) -> bool:
        # mismo descenso que search(), pero guardando (padre,
        # indice_del_hijo) en cada nivel -- hace falta para saber
        # cuales son los hermanos adyacentes si hay que rebalancear
        path = []
        page_id = self.root_page_id
        depth = self.height
        while depth > 0:
            node = self._load_internal(page_id)
            child_index = node.find_child_index(key)
            path.append((node, child_index))
            page_id = node._read_child(child_index)
            depth -= 1

        leaf = self._load_leaf(page_id)
        ref = leaf.find(key)
        if ref is None:
            return False

        leaf.delete(key)
        self._delete_record(key, ref)

        if not path or not leaf.is_underflow():
            # si la hoja ES la raiz no hay con quien rebalancear, y
            # si no quedo en underflow no hace falta nada mas
            self._save_page(leaf)
            return True

        self._save_page(leaf)
        self._rebalance_leaf(leaf, path)
        return True

    def _rebalance_leaf(self, leaf, path):
        # redistribuye o fusiona una hoja en underflow con un hermano
        # adyacente (mismo padre). path[-1] es (padre, indice_de_leaf)
        parent, child_index = path[-1]

        if child_index > 0:
            left_sibling = self._load_leaf(parent._read_child(child_index - 1))
            if left_sibling.can_lend():
                new_separator = leaf.borrow_from_left(left_sibling)
                parent._write_key(child_index - 1, new_separator)
                self._save_page(left_sibling)
                self._save_page(leaf)
                self._save_page(parent)
                return

        if child_index < parent.n_keys:
            right_sibling = self._load_leaf(parent._read_child(child_index + 1))
            if right_sibling.can_lend():
                new_separator = leaf.borrow_from_right(right_sibling)
                parent._write_key(child_index, new_separator)
                self._save_page(leaf)
                self._save_page(right_sibling)
                self._save_page(parent)
                return

        # ningun hermano tiene de sobra: hay que fusionar
        if child_index > 0:
            left_sibling = self._load_leaf(parent._read_child(child_index - 1))
            left_sibling.merge_with_right(leaf)
            self._save_page(left_sibling)
            parent.delete_key_at(child_index - 1)
        else:
            right_sibling = self._load_leaf(parent._read_child(child_index + 1))
            leaf.merge_with_right(right_sibling)
            self._save_page(leaf)
            parent.delete_key_at(child_index)

        self._save_page(parent)

        if parent.is_underflow():
            self._rebalance_internal(parent, path[:-1])

    def _rebalance_internal(self, node, path):
        # mismo problema que _rebalance_leaf pero un nivel arriba,
        # con la diferencia de que ademas hay que mover la clave
        # separadora del padre (no solo el hijo) en cada operacion
        if not path:
            # node es la raiz: si se quedo sin claves, su unico hijo
            # pasa a ser la raiz nueva y el arbol pierde un nivel
            if node.n_keys == 0:
                self.root_is_leaf = (self.height == 1)
                self.root_page_id = node._read_child(0)
                self.height -= 1
                self._save_root_header()
            else:
                self._save_page(node)
            return

        parent, child_index = path[-1]

        if child_index > 0:
            left_sibling = self._load_internal(parent._read_child(child_index - 1))
            if left_sibling.can_lend():
                separator = parent._read_key(child_index - 1)
                new_separator = node.borrow_from_left(left_sibling, separator)
                parent._write_key(child_index - 1, new_separator)
                self._save_page(left_sibling)
                self._save_page(node)
                self._save_page(parent)
                return

        if child_index < parent.n_keys:
            right_sibling = self._load_internal(parent._read_child(child_index + 1))
            if right_sibling.can_lend():
                separator = parent._read_key(child_index)
                new_separator = node.borrow_from_right(right_sibling, separator)
                parent._write_key(child_index, new_separator)
                self._save_page(node)
                self._save_page(right_sibling)
                self._save_page(parent)
                return

        if child_index > 0:
            left_sibling = self._load_internal(parent._read_child(child_index - 1))
            separator = parent._read_key(child_index - 1)
            left_sibling.merge_with_right(node, separator)
            self._save_page(left_sibling)
            parent.delete_key_at(child_index - 1)
        else:
            right_sibling = self._load_internal(parent._read_child(child_index + 1))
            separator = parent._read_key(child_index)
            node.merge_with_right(right_sibling, separator)
            self._save_page(node)
            parent.delete_key_at(child_index)

        self._save_page(parent)

        if parent.is_underflow():
            self._rebalance_internal(parent, path[:-1])

    def range_search(self, start_key, end_key):
        # un solo descenso hasta la hoja donde arrancaría start_key
        page_id = self.root_page_id
        depth = self.height
        while depth > 0:
            node = self._load_internal(page_id)
            page_id = node.find_child(start_key)
            depth -= 1

        leaf = self._load_leaf(page_id)
        results = []

        # de ahí en más solo se sigue next_leaf_id, sin volver a subir
        while leaf is not None:
            for i in range(leaf.n_entries):
                entry_key, ref = leaf._read_entry(i)
                if entry_key > end_key:
                    return results
                if entry_key >= start_key:
                    results.append((entry_key, ref))

            leaf = self._load_leaf(leaf.next_leaf_id) if leaf.next_leaf_id != NULL_LEAF else None

        return results
