from indexes.b_tree_base import BPlusTreeBase
from indexes.b_tree_leaf_page import RID
from storage.files.sequential_file import SequentialFile

class _ReindexNeeded(Exception):
    pass

class BPlusTreeClustered(BPlusTreeBase):

    def __init__(
        self,
        index_filename: str,
        sequential_file: SequentialFile,
        buffer_frames: int = 50
    ):
        super().__init__(index_filename, buffer_frames)
        self.sequential_file = sequential_file
        self._reorganize_count = self.sequential_file.reorganize_count

    def _store_record(self, params):
        page_id, slot_id = self.sequential_file.insert(params)
        self._check_reindex()
        return RID(page_id, slot_id)

    def _fetch_record(self, ref):
        record = self.sequential_file._get_record(ref)
        return record.params if record else None

    def _delete_record(self, key, ref) -> bool:
        ok = self.sequential_file.delete_by_key(key)
        self._check_reindex()
        return ok

    def _check_reindex(self):
        if self.sequential_file.reorganize_count != self._reorganize_count:
            raise _ReindexNeeded()

    def insert(self, key, params):
        try:
            return super().insert(key, params)
        except _ReindexNeeded:
            self._reindex()
            return self.search(key)

    def delete(self, key) -> bool:
        try:
            return super().delete(key)
        except _ReindexNeeded:
            self._reindex()
            return True

    def _reindex(self):
        self._init_empty_index()
        for rid, record in self.sequential_file._iter_records():
            if record.deleted:
                continue
            key = record.params[self.sequential_file.key_index]
            self._insert_ref(key, RID(rid[0], rid[1]))
        self._reorganize_count = self.sequential_file.reorganize_count

    def close(self):
        self.buffer_manager.close(self.file_manager)
