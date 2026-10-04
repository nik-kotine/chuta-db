from indexes.r_tree_base import RTreeBase
from storage.files.heap_file import HeapFile


class RTree(RTreeBase):
    # Conecta RTreeBase con un HeapFile ya abierto, para poder compartir el
    # HeapFile entre varios indices de la misma tabla. El R-Tree no tiene
    # variante "clustered" como el B+ (esa distincion es propia de un indice
    # sobre una clave totalmente ordenable; no aplica a datos espaciales).

    def __init__(self, index_filename: str, heap_file: HeapFile, buffer_frames: int = 50):
        super().__init__(index_filename, buffer_frames)
        self.heap_file = heap_file

    def _store_record(self, params):
        return self.heap_file.insert(list(params))

    def _fetch_record(self, ref):
        return self.heap_file.fetch(ref)

    def _delete_record(self, point, ref) -> bool:
        return self.heap_file.delete(ref)

    def close(self):
        self.buffer_manager.close(self.file_manager)
