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
        """Claves que tuvieron duplicados durante la vida de este índice."""
        self._keys_with_duplicates = set()
        # HeapFile.insert() pide bytes ya empaquetados, a diferencia de
        # SequentialFile que empaqueta solo -- por eso acá hace falta
        # un RecordPacker propio para codificar/decodificar

    def _store_record(self, params):
        return self.heap_file.insert(list(params))

    def _fetch_record(self, ref):
        return self.heap_file.fetch(ref)

    def _insert_ref(self, key, ref):
        """Registra una referencia y recuerda si la clave ya existía."""
        if super().search(key) is not None:
            self._keys_with_duplicates.add(key)
        return super()._insert_ref(key, ref)

    def search(self, key):
        """
        Busca todas las referencias asociadas a una clave.

        El B+ tree base devuelve una sola referencia. Este índice recorre la
        cadena de hojas para conservar los duplicados; mantiene el retorno de
        un RID para claves que siempre fueron únicas por compatibilidad.
        """
        page_id = self.root_page_id
        depth = self.height

        while depth > 0:
            node = self._load_internal(page_id)
            page_id = node._read_child(0)
            depth -= 1

        matches = []
        while True:
            leaf = self._load_leaf(page_id)
            for index in range(leaf.n_entries):
                entry_key, ref = leaf._read_entry(index)
                if entry_key == key:
                    matches.append(ref)

            if leaf.next_leaf_id == 0:
                break
            page_id = leaf.next_leaf_id

        if not matches:
            return None
        if len(matches) == 1 and key not in self._keys_with_duplicates:
            return matches[0]
        return matches

    def delete_ref(self, key, ref):
        """
        Elimina una referencia específica sin borrar otra fila duplicada.

        Se recorre la cadena de hojas porque varias referencias pueden tener
        la misma clave y el método delete(key) del árbol base solo identifica
        la primera coincidencia.
        """
        page_id = self.root_page_id
        depth = self.height

        while depth > 0:
            node = self._load_internal(page_id)
            page_id = node._read_child(0)
            depth -= 1

        while True:
            leaf = self._load_leaf(page_id)
            for index in range(leaf.n_entries):
                entry_key, entry_ref = leaf._read_entry(index)
                if entry_key == key and entry_ref == ref:
                    for shifted in range(index, leaf.n_entries - 1):
                        next_key, next_ref = leaf._read_entry(shifted + 1)
                        leaf._write_entry(shifted, next_key, next_ref)
                    leaf.n_entries -= 1
                    leaf.save_header()
                    self._save_page(leaf)
                    return True

            if leaf.next_leaf_id == 0:
                return False
            page_id = leaf.next_leaf_id

    def _delete_record(self, key, ref) -> bool:
        # acá key no hace falta, HeapFile borra directo por RID
        return self.heap_file.delete(ref)

    def close(self):
        self.file.close()
        # el HeapFile no se cierra acá: puede estar compartido con
        # otros índices, lo cierra quien lo creó
