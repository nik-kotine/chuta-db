import os
import struct

from b_tree_base import BPlusTreeBase
from b_tree_leaf_page import RID
from FileManager import FileManager
from BufferManager import BufferManager
from SequentialFile import SequentialFile, FILE_HEADER_FORMAT, FILE_HEADER_SIZE


class BPlusTreeClustered(BPlusTreeBase):
    # Índice B+ agrupado: las hojas guardan RIDs hacia SequentialFile,
    # que mantiene los registros ordenados físicamente por la misma
    # clave (por eso "agrupado", orden del índice = orden del dato).

    def __init__(self, index_filename: str, data_filename: str, page_size: int, record_format: str, buffer_frames: int = 50):
        super().__init__(index_filename)

        # el archivo de datos necesita su propio header + página de
        # overflow antes de abrirlo con FileManager, igual que hace
        # create_sequential() en TestSequentialFile.py
        if not os.path.exists(data_filename):
            with open(data_filename, "wb") as f:
                f.write(struct.pack(FILE_HEADER_FORMAT, 0, -1, 0, 0))
                f.write(b"\x00" * page_size)

        file_manager = FileManager(data_filename, page_size, FILE_HEADER_SIZE)
        buffer_manager = BufferManager(file_manager, buffer_frames)
        self.sequential_file = SequentialFile(buffer_manager, page_size, record_format)

    def _store_record(self, params):
        # SequentialFile devuelve una tupla plana (page_id, slot_id),
        # sin .page_id/.slot_id -- la envolvemos en RID para que la
        # hoja la pueda guardar bien
        page_id, slot_id = self.sequential_file.insert(params)
        return RID(page_id, slot_id)

    def _fetch_record(self, ref):
        record = self.sequential_file._get_record(ref)
        return record.params if record else None

    def _delete_record(self, key, ref) -> bool:
        # SequentialFile borra por key, no por RID (borrado lógico,
        # ya dispara reorganize sola cuando hace falta)
        return self.sequential_file.delete(key)

    def close(self):
        self.sequential_file.buffer_manager.close()
        self.file.close()
