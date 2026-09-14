from indexes.b_tree_base import BPlusTreeBase
from storage.files.heap_file import HeapFile
from heapfile.record import RecordPacker


class BPlusTreeUnclustered(BPlusTreeBase):
    # Índice B+ no agrupado: las hojas guardan RIDs hacia un HeapFile
    # ya abierto, que puede compartirse con otros índices no agrupados
    # sobre la misma tabla (uno por columna indexada, por ejemplo).

    def __init__(self, index_filename: str, heap_file: HeapFile, schema: list[str]):
        super().__init__(index_filename)
        self.heap_file = heap_file
        # HeapFile.add() pide bytes ya empaquetados, a diferencia de
        # SequentialFile que empaqueta solo -- por eso acá hace falta
        # un RecordPacker propio para codificar/decodificar
        self.packer = RecordPacker(schema)

    def _store_record(self, params):
        record_bytes = self.packer.record_encoder(list(params))
        return self.heap_file.add(record_bytes)

    def _fetch_record(self, ref):
        record_bytes = self.heap_file.get(ref)
        return self.packer.record_decoder(record_bytes) if record_bytes else None

    def _delete_record(self, key, ref) -> bool:
        # acá key no hace falta, HeapFile borra directo por RID
        return self.heap_file.remove(ref)

    def close(self):
        self.file.close()
        # el HeapFile no se cierra acá: puede estar compartido con
        # otros índices, lo cierra quien lo creó
