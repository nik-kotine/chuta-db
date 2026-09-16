import os
import struct

from indexes.b_tree_base import BPlusTreeBase
from indexes.b_tree_leaf_page import RID
from storage.files.sequential_file import SequentialFile, FILE_HEADER_FORMAT, FILE_HEADER_SIZE


# señal interna: se usa para cortar insert()/delete() a mitad de
# camino cuando SequentialFile se reorganizo sola (ver _check_reindex
# mas abajo). No es un error real, por eso no hereda de Exception
# generico para no confundirla con un bug -- solo la atrapa esta
# misma clase.
class _ReindexNeeded(Exception):
    pass


class BPlusTreeClustered(BPlusTreeBase):
    # Índice B+ agrupado: las hojas guardan RIDs hacia SequentialFile,
    # que mantiene los registros ordenados físicamente por la misma
    # clave (por eso "agrupado", orden del índice = orden del dato).

    @staticmethod
    def _normalize_record_format(record_format):
        if isinstance(record_format, (list, tuple)):
            return list(record_format)

        struct_types = {
            "i": "integer",
            "q": "bigint",
            "h": "smallint",
            "f": "real",
            "d": "double precision",
            "?": "boolean",
        }
        if record_format in struct_types:
            return [struct_types[record_format]]
        if record_format and all(token in struct_types for token in record_format):
            return [struct_types[token] for token in record_format]
        return [record_format]

    def __init__(self, index_filename: str, sequential_file:SequentialFile):
        super().__init__(index_filename)

        # el archivo de datos necesita su propio header + página de
        # overflow antes de abrirlo con FileManager, igual que hace
        # create_sequential() en TestSequentialFile.py
        self.sequential_file = sequential_file
        self._reorganize_count = self.sequential_file.reorganize_count

    def _store_record(self, params):
        # SequentialFile devuelve una tupla plana (page_id, slot_id),
        # sin .page_id/.slot_id -- la envolvemos en RID para que la
        # hoja la pueda guardar bien
        page_id, slot_id = self.sequential_file.insert(params)
        self._check_reindex()
        return RID(page_id, slot_id)

    def _fetch_record(self, ref):
        record = self.sequential_file._get_record(ref)
        return record.params if record else None

    def _delete_record(self, key, ref) -> bool:
        # SequentialFile borra por key, no por RID (borrado lógico)
        ok = self.sequential_file.delete_by_key(key)
        self._check_reindex()
        return ok

    # SequentialFile puede reorganizarse sola tanto en insert()
    # (overflow lleno) como en delete() (espacio desperdiciado), y
    # reorganize() reasigna el RID de TODOS los registros vivos. Si
    # eso pasa a mitad de un insert()/delete() de este arbol, seguir
    # con la operacion como si nada (split, rebalanceo, etc.) estaria
    # trabajando sobre RIDs y paginas ya invalidas. Por eso cortamos
    # con una excepcion en vez de devolver un valor: insert()/delete()
    # de esta clase la atrapan y reconstruyen el indice entero.
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
            # si llegamos hasta _delete_record() es porque la key
            # existia en el indice (delete() ya la encontro antes de
            # llamar al hook), asi que el borrado en si fue exitoso
            self._reindex()
            return True

    # reconstruye el indice entero desde cero, releyendo el estado
    # actual (ya reorganizado) de SequentialFile
    def _reindex(self):
        self._init_empty_index()
        for rid, record in self.sequential_file._iter_records():
            if record.deleted:
                continue
            key = record.params[self.sequential_file.key_index]
            self._insert_ref(key, RID(rid[0], rid[1]))
        self._reorganize_count = self.sequential_file.reorganize_count

    def close(self):
        self.file.close()
