from indexes.b_tree_base import BPlusTreeBase
from storage.files.heap_file import HeapFile
from storage.formats.record_packer import RecordPacker


class BPlusTreeUnclustered(BPlusTreeBase):
    # Índice B+ no agrupado: las hojas guardan RIDs hacia un HeapFile
    # ya abierto, que puede compartirse con otros índices no agrupados
    # sobre la misma tabla (uno por columna indexada, por ejemplo).

    def __init__(self, index_filename: str, heap_file: HeapFile, schema: list[str]):
        super().__init__(index_filename)
        self.heap_file = heap_file
        # HeapFile.insert() pide bytes ya empaquetados, a diferencia de
        # SequentialFile que empaqueta solo -- por eso acá hace falta
        # un RecordPacker propio para codificar/decodificar

    def _store_record(self, params):
        return self.heap_file.insert(list(params))

    def _fetch_record(self, ref):
        return self.heap_file.fetch(ref)

    def _delete_record(self, key, ref) -> bool:
        # acá key no hace falta, HeapFile borra directo por RID
        return self.heap_file.delete(ref)

    def close(self):
        self.file.close()
        # el HeapFile no se cierra acá: puede estar compartido con
        # otros índices, lo cierra quien lo creó
