import heapq
import itertools
import struct

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager

from indexes.r_tree_node import RTreeNode, LeafEntry, InternalEntry, PAGE_SIZE
from spatial.geometry import Point, Rectangle, mindist, point_in_polygon, DISTANCE_FUNCS

ROOT_HEADER_FORMAT = ">I?B"
ROOT_HEADER_SIZE = struct.calcsize(ROOT_HEADER_FORMAT)


class RTreeBase:
    # Logica pura del R-Tree (Guttman 1984): ChooseLeaf + split cuadratico
    # para insertar, CondenseTree para borrar, RangeSearch / radius_search
    # (busqueda por rango con metrica) / knn (best-first con heapq) para
    # consultar. No sabe nada de HeapFile -- eso lo llenan las subclases via
    # los hooks _store_record / _fetch_record / _delete_record, mismo
    # esquema que BPlusTreeBase.

    def __init__(self, index_filename: str, buffer_frames: int = 50):
        self.index_filename = index_filename
        self.file_manager = FileManager(index_filename, PAGE_SIZE, ROOT_HEADER_SIZE)
        self.buffer_manager = BufferManager(self.file_manager, buffer_frames)

        is_new = len(self.file_manager.read_header()) < ROOT_HEADER_SIZE
        if is_new:
            self._init_empty_index()
        else:
            self._load_root_header()

    # --- persistencia ---------------------------------------------------

    def _init_empty_index(self):
        self.buffer_manager.invalidate_all(self.file_manager)
        self.file_manager.truncate(self.file_manager.file_header_size)
        root_page_id = self.file_manager.allocate_page()
        root = RTreeNode(root_page_id, is_leaf=True)
        self._save_node(root)
        self.root_page_id = root_page_id
        self.root_is_leaf = True
        self.height = 0
        self._save_root_header()

    def _load_root_header(self):
        header = self.file_manager.read_header()
        self.root_page_id, self.root_is_leaf, self.height = struct.unpack(ROOT_HEADER_FORMAT, header)

    def _save_root_header(self):
        header = struct.pack(ROOT_HEADER_FORMAT, self.root_page_id, self.root_is_leaf, self.height)
        self.file_manager.write_header(header)

    def _load_node(self, page_id: int) -> RTreeNode:
        data = self.buffer_manager.fetch_page(page_id, self.file_manager)
        self.buffer_manager.unpin_page(page_id, self.file_manager)
        return RTreeNode(page_id, data=data)

    def _save_node(self, node: RTreeNode) -> None:
        node.flush()
        buf = self.buffer_manager.fetch_page(node.page_id, self.file_manager)
        buf[:] = node.data
        self.buffer_manager.mark_dirty(node.page_id, self.file_manager)
        self.buffer_manager.unpin_page(node.page_id, self.file_manager)

    def _allocate_page_id(self) -> int:
        return self.file_manager.allocate_page()

    # --- hooks de storage (los llenan las subclases) ---------------------

    def _store_record(self, params):
        raise NotImplementedError

    def _fetch_record(self, ref):
        raise NotImplementedError

    def _delete_record(self, point, ref) -> bool:
        raise NotImplementedError

    # --- insercion: ChooseLeaf + AdjustTree ------------------------------

    def _choose_leaf(self, target_mbr: Rectangle) -> list:
        path = []
        page_id = self.root_page_id
        while True:
            node = self._load_node(page_id)
            path.append(node)
            if node.is_leaf:
                return path
            best_idx, best_enlargement, best_area = 0, None, None
            for i, entry in enumerate(node.entries):
                enlargement = entry.mbr.enlargement(target_mbr)
                area = entry.mbr.area()
                if best_enlargement is None or enlargement < best_enlargement or (
                    enlargement == best_enlargement and area < best_area
                ):
                    best_idx, best_enlargement, best_area = i, enlargement, area
            page_id = node.entries[best_idx].ref

    def _insert_entry_into_tree(self, point: Point, ref):
        path = self._choose_leaf(Rectangle.from_point(point))
        leaf = path[-1]
        leaf.insert_entry(LeafEntry(point, ref))

        split_node = leaf.quadratic_split(self._allocate_page_id()) if leaf.is_full() else None
        self._save_node(leaf)
        if split_node is not None:
            self._save_node(split_node)

        child_node, child_split = leaf, split_node
        for level in range(len(path) - 2, -1, -1):
            parent = path[level]
            idx = next(i for i, e in enumerate(parent.entries) if e.ref == child_node.page_id)
            parent.replace_entry(idx, InternalEntry(child_node.mbr(), child_node.page_id))

            new_split = None
            if child_split is not None:
                parent.insert_entry(InternalEntry(child_split.mbr(), child_split.page_id))
                if parent.is_full():
                    new_split = parent.quadratic_split(self._allocate_page_id())

            self._save_node(parent)
            if new_split is not None:
                self._save_node(new_split)

            child_node, child_split = parent, new_split

        if child_split is not None:
            new_root_id = self._allocate_page_id()
            new_root = RTreeNode(new_root_id, is_leaf=False)
            new_root.insert_entry(InternalEntry(child_node.mbr(), child_node.page_id))
            new_root.insert_entry(InternalEntry(child_split.mbr(), child_split.page_id))
            self._save_node(new_root)
            self.root_page_id = new_root_id
            self.root_is_leaf = False
            self.height += 1
            self._save_root_header()

    def insert(self, point: Point, params):
        ref = self._store_record(params)
        self._insert_entry_into_tree(point, ref)
        return ref

    # --- busqueda exacta --------------------------------------------------

    def search(self, point: Point):
        path = self._find_leaf_path(point)
        if path is None:
            return None
        leaf = path[-1]
        for entry in leaf.entries:
            if entry.point == point:
                return entry.ref
        return None

    def _find_leaf_path(self, point: Point):
        root = self._load_node(self.root_page_id)
        return self._search_leaf_for_point([root], point)

    def _search_leaf_for_point(self, path: list, point: Point):
        node = path[-1]
        if node.is_leaf:
            for entry in node.entries:
                if entry.point == point:
                    return path
            return None
        for entry in node.entries:
            if entry.mbr.contains_point(point):
                child = self._load_node(entry.ref)
                found = self._search_leaf_for_point(path + [child], point)
                if found is not None:
                    return found
        return None

    # --- borrado: FindLeaf + CondenseTree ---------------------------------

    def delete(self, point: Point) -> bool:
        path = self._find_leaf_path(point)
        if path is None:
            return False

        leaf = path[-1]
        idx = next(i for i, e in enumerate(leaf.entries) if e.point == point)
        ref = leaf.entries[idx].ref
        leaf.delete_at(idx)
        self._delete_record(point, ref)
        self._save_node(leaf)

        self._condense_tree(path)
        return True

    def _flatten_entries(self, node: RTreeNode):
        if node.is_leaf:
            return [(entry.point, entry.ref) for entry in node.entries]
        flattened = []
        for entry in node.entries:
            flattened.extend(self._flatten_entries(self._load_node(entry.ref)))
        return flattened

    def _condense_tree(self, path: list):
        # CondenseTree de Guttman: los nodos que quedan por debajo de `m` se
        # sacan enteros del arbol (no hay borrow/merge con hermanos como en
        # un B-Tree -- los hermanos de un R-Tree no tienen un orden que
        # permita redistribuir de forma significativa) y sus entradas se
        # reinsertan desde la raiz. Simplificacion deliberada respecto al
        # Guttman original: en vez de reinsertar subarboles completos
        # preservando su altura, aplano todo hasta el nivel hoja
        # (_flatten_entries) y reinserto punto por punto -- mas trabajo que
        # el optimo, pero mucho mas simple y sigue siendo correcto.
        orphans = []
        for level in range(len(path) - 1, 0, -1):
            node = path[level]
            parent = path[level - 1]
            idx = next(i for i, e in enumerate(parent.entries) if e.ref == node.page_id)
            if node.is_underflow():
                parent.delete_at(idx)
                orphans.extend(self._flatten_entries(node))
            else:
                parent.replace_entry(idx, InternalEntry(node.mbr(), node.page_id))
            self._save_node(parent)

        root = path[0]
        if not root.is_leaf and len(root.entries) == 1:
            only_child_id = root.entries[0].ref
            only_child = self._load_node(only_child_id)
            self.root_page_id = only_child_id
            self.root_is_leaf = only_child.is_leaf
            self.height -= 1
            self._save_root_header()

        for point, ref in orphans:
            self._insert_entry_into_tree(point, ref)

    # --- consultas espaciales ----------------------------------------------

    def range_search(self, query_rect: Rectangle):
        results = []
        self._range_search_node(self.root_page_id, query_rect, results)
        return results

    def _range_search_node(self, page_id, query_rect, results):
        node = self._load_node(page_id)
        if node.is_leaf:
            for entry in node.entries:
                if query_rect.contains_point(entry.point):
                    results.append((entry.point, entry.ref))
        else:
            for entry in node.entries:
                if query_rect.intersects(entry.mbr):
                    self._range_search_node(entry.ref, query_rect, results)

    def radius_search(self, point: Point, radius: float, metric: str = "euclidean"):
        results = []
        self._radius_search_node(self.root_page_id, point, radius, metric, results)
        return results

    def _radius_search_node(self, page_id, point, radius, metric, results):
        node = self._load_node(page_id)
        distance = DISTANCE_FUNCS[metric]
        if node.is_leaf:
            for entry in node.entries:
                if distance(point, entry.point) <= radius:
                    results.append((entry.point, entry.ref))
        else:
            for entry in node.entries:
                if mindist(point, entry.mbr, metric) <= radius:
                    self._radius_search_node(entry.ref, point, radius, metric, results)

    def knn(self, point: Point, k: int, metric: str = "euclidean"):
        # Best-first search con cola de prioridad (heapq) en vez de ordenar
        # los hijos en cada nivel -- evita ese sort repetido en cada paso del
        # recorrido. El tie-breaker (next(counter)) evita que heapq
        # intente comparar Point/tuplas cuando dos prioridades empatan.
        distance = DISTANCE_FUNCS[metric]
        counter = itertools.count()
        heap = [(0.0, next(counter), ("node", self.root_page_id))]
        found = []
        while heap and len(found) < k:
            dist, _, (kind, payload) = heapq.heappop(heap)
            if kind == "point":
                found.append(payload)
                continue
            node = self._load_node(payload)
            if node.is_leaf:
                for entry in node.entries:
                    d = distance(point, entry.point)
                    heapq.heappush(heap, (d, next(counter), ("point", (entry.point, entry.ref))))
            else:
                for entry in node.entries:
                    d = mindist(point, entry.mbr, metric)
                    heapq.heappush(heap, (d, next(counter), ("node", entry.ref)))
        return found

    def polygon_search(self, polygon: list):
        bbox = Rectangle.from_points(polygon)
        candidates = self.range_search(bbox)
        return [(point, ref) for point, ref in candidates if point_in_polygon(point, polygon)]
