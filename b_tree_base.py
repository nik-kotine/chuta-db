import struct
import os

from b_tree_leaf_page import PAGE_SIZE, NULL_LEAF, BTreeLeafPage
from b_tree_internal_page import BTreeInternalPage

# Página 0 del archivo de índice, guarda dónde está la raíz y su altura
ROOT_HEADER_FORMAT = ">I?B"  # root_page_id, root_is_leaf, height
ROOT_HEADER_SIZE = struct.calcsize(ROOT_HEADER_FORMAT)


class BPlusTreeBase:
    # Lógica común del árbol (búsqueda, inserción, borrado). No sabe
    # de HeapFile ni SequentialFile, eso lo llenan las subclases.

    def __init__(self, index_filename: str):
      
        self.index_filename=index_filename
        is_new=not os.path.exists(index_filename)
        self.file=open(index_filename,"w+b" if is_new else "r+b")

        if is_new:
            # pag 0 es header
            # pag 1 es hoja vacía
            self.file.write(b"\x00"*PAGE_SIZE)
            root_leaf=BTreeLeafPage(1)
            self.file.write(root_leaf.data)
            self.root_page_id=1
            self.root_is_leaf=True
            self.height=0
            self._save_root_header()  # sin esto la pagina 0 se queda en ceros
        else:
            self._load_root_header()

    # -------- página 0: persistencia de la raiz ---------

    def _load_root_header(self):
        self.file.seek(0)
        header=self.file.read(ROOT_HEADER_SIZE)
        self.root_page_id, self.root_is_leaf, self.height=struct.unpack(ROOT_HEADER_FORMAT, header)

    def _save_root_header(self):
        self.file.seek(0)
        self.file.write(struct.pack(ROOT_HEADER_FORMAT,self.root_page_id, self.root_is_leaf, self.height))
        self.file.flush()

    # ---------- I/O de paginas del arbol (hoja o nodo interno) ----------

    def _load_leaf(self, page_id: int) -> BTreeLeafPage:
        self.file.seek(page_id*PAGE_SIZE)
        data=bytearray(self.file.read(PAGE_SIZE))
        return BTreeLeafPage(page_id, data=data)

    def _load_internal(self, page_id: int) -> BTreeInternalPage:
        self.file.seek(page_id*PAGE_SIZE)
        data=bytearray(self.file.read(PAGE_SIZE))
        return BTreeInternalPage(page_id, data=data)

    def _save_page(self, page) -> None:
        self.file.seek(page.page_id*PAGE_SIZE)
        self.file.write(page.data)

    def _allocate_page_id(self) -> int: # igual q en filemanager
        self.file.seek(0,2)
        file_size=self.file.tell()
        page_id=file_size//PAGE_SIZE
        self.file.write(b"\x00"*PAGE_SIZE)
        return page_id

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
        # BLOQUEADO: falta el rebalanceo (redistribuir/fusionar)
        # todavía no hay métodos para eso ni en la hoja ni en el nodo
        # interno. Por ahora solo borra sin reequilibrar el árbol.

        # mismo descenso que search()
        page_id = self.root_page_id
        depth = self.height
        while depth > 0:
            node = self._load_internal(page_id)
            page_id = node.find_child(key)
            depth -= 1

        leaf = self._load_leaf(page_id)
        ref = leaf.find(key)
        if ref is None:
            return False

        leaf.delete(key)
        self._save_page(leaf)
        self._delete_record(key, ref)
        return True

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
