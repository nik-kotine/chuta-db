from indexes.b_tree_base import BPlusTreeBase
from storage.files.sequential_file import SequentialFile
from storage.rid import RID as SeqRID


class BPlusTreeUnclusteredSequential(BPlusTreeBase):
    """
    Indice B+ secundario (no agrupado) sobre una tabla SequentialFile.

    A diferencia de BPlusTreeUnclustered (que indexa un HeapFile, cuyos RID
    son estables), un SequentialFile reorganiza sus paginas cuando el
    desperdicio o el overflow superan el 30% (WASTED_RATIO/OVERFLOW_RATIO
    en sequential_file.py), y eso reescribe cada registro vivo en una
    posicion fisica nueva. Cualquier RID guardado en una hoja antes de ese
    reorganize queda apuntando a otra fila.

    La solucion es la misma que usa BPlusTreeClustered: en vez de intentar
    actualizar cada entrada cuando el archivo se reorganiza, el indice
    compara su copia de `sequential_file.reorganize_count` antes de cada
    operacion y, si cambio, se reconstruye entero desde un recorrido de
    la tabla (`_reindex`). El costo es un rebuild O(n log n) ocasional;
    a cambio no hace falta cambiar el formato de hoja del B+ (sigue
    guardando (page_id, slot_id), igual que el no agrupado sobre Heap).

    `_insert_ref`/`delete_ref` son el camino de integracion real: Table
    siempre escribe o borra en `sequential_file` antes de avisarle a sus
    indices secundarios, asi que si la sincronizacion de arriba dispara
    un rebuild, la fila en cuestion ya quedo reflejada (o excluida) por
    ese rebuild y no hay que tocar la hoja de nuevo. El `.insert(key,
    params)`/`.delete(key)` genericos heredados de BPlusTreeBase no pasan
    por esa sincronizacion y no son el camino que usan Table/IndexManager;
    no se recomienda usarlos directamente sobre esta clase.
    """

    def __init__(
        self,
        index_filename: str,
        sequential_file: SequentialFile,
        schema: list[str],
        column_index: int,
        buffer_frames: int = 50,
    ):
        super().__init__(index_filename, buffer_frames)
        self.sequential_file = sequential_file
        self.schema = schema
        self.column_index = column_index
        self._keys_with_duplicates = set()
        self._reorganize_count = sequential_file.reorganize_count

    def _store_record(self, params):
        return self.sequential_file.insert(list(params))

    def _fetch_record(self, ref):
        return self.sequential_file.fetch(ref)

    def _delete_record(self, key, ref) -> bool:
        return self.sequential_file.delete(SeqRID(ref.page_id, ref.slot_id))

    def _sync(self) -> bool:
        """Reconstruye el indice si el archivo se reorganizo desde la
        ultima operacion. Devuelve True si hizo falta reconstruir."""
        if self.sequential_file.reorganize_count == self._reorganize_count:
            return False
        self._reindex()
        return True

    def _reindex(self):
        self._init_empty_index()
        self._keys_with_duplicates = set()
        for rid, record in self.sequential_file._iter_records():
            if record.deleted:
                continue
            key = record.params[self.column_index]
            if super().search(key) is not None:
                self._keys_with_duplicates.add(key)
            super()._insert_ref(key, rid)
        self._reorganize_count = self.sequential_file.reorganize_count

    def _insert_ref(self, key, ref):
        if self._sync():
            return ref
        if super().search(key) is not None:
            self._keys_with_duplicates.add(key)
        return super()._insert_ref(key, ref)

    def search(self, key):
        self._sync()

        page_id = self.root_page_id
        depth = self.height
        while depth > 0:
            node = self._load_internal(page_id)
            page_id = node.find_leftmost_child(key)
            depth -= 1

        matches = []
        leaf = self._load_leaf(page_id)
        while True:
            fin_de_rango = False
            for index in range(leaf.n_entries):
                entry_key, ref = leaf._read_entry(index)
                if entry_key > key:
                    fin_de_rango = True
                    break
                if entry_key == key:
                    matches.append(ref)

            if fin_de_rango or leaf.next_leaf_id == 0:
                break
            leaf = self._load_leaf(leaf.next_leaf_id)

        if not matches:
            return None
        if len(matches) == 1 and key not in self._keys_with_duplicates:
            return matches[0]
        return matches

    def delete_ref(self, key, ref) -> bool:
        if self._sync():
            return True

        page_id = self.root_page_id
        depth = self.height
        while depth > 0:
            node = self._load_internal(page_id)
            page_id = node.find_leftmost_child(key)
            depth -= 1

        while True:
            leaf = self._load_leaf(page_id)
            entries = leaf._all_entries()
            for index, (entry_key, entry_ref) in enumerate(entries):
                if entry_key == key and entry_ref == ref:
                    del entries[index]
                    leaf._rewrite(entries)
                    self._save_page(leaf)
                    return True

            if leaf.next_leaf_id == 0:
                return False
            page_id = leaf.next_leaf_id

    def range_search(self, start_key, end_key):
        self._sync()
        return super().range_search(start_key, end_key)

    def iter_ordered(self, reverse=False):
        # Lo usa el ORDER BY por indice (ascendente y descendente). Igual que
        # search/range_search: si el archivo se reorganizo, los RID guardados
        # en las hojas quedaron viejos, asi que hay que reindexar antes de
        # recorrer en orden; si no, _fetch_record traeria filas equivocadas.
        self._sync()
        yield from super().iter_ordered(reverse)

    def delete(self, key) -> bool:
        # El delete generico de BPlusTreeBase desciende por el arbol y borra el
        # dato apuntado por el RID guardado en la hoja, pero no sincroniza
        # primero: si el archivo se reorganizo desde la ultima operacion, esos
        # RIDs quedaron viejos y _delete_record borraria otra fila del
        # SequentialFile. Sincronizamos antes para descender sobre RIDs vigentes.
        # Table nunca usa delete(key) sobre indices secundarios (usa delete_ref),
        # asi que esto solo afecta al uso "standalone" de la clase.
        self._sync()
        return super().delete(key)

    def close(self):
        self.buffer_manager.close(self.file_manager)
