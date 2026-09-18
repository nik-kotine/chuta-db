from indexes.b_tree_base import BPlusTreeBase
from storage.files.heap_file import HeapFile


class BPlusTreeUnclustered(BPlusTreeBase):
    
    def __init__(self, index_filename: str, heap_file: HeapFile, schema: list[str]):
        super().__init__(index_filename)
        self.heap_file = heap_file
        self._keys_with_duplicates = set()

    def _store_record(self, params):
        return self.heap_file.insert(list(params))

    def _fetch_record(self, ref):
        return self.heap_file.fetch(ref)

    def _insert_ref(self, key, ref):
        if super().search(key) is not None:
            self._keys_with_duplicates.add(key)
        return super()._insert_ref(key, ref)

    def search(self, key):
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

    def delete_ref(self, key, ref):
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

    def _delete_record(self, key, ref) -> bool:
        return self.heap_file.delete(ref)

    def close(self):
        self.buffer_manager.close(self.file_manager)