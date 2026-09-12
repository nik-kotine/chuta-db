import os

from b_tree_base import BPlusTreeBase
from b_tree_leaf_page import RID

TEST_INDEX_FILE = "test_index.bin"


class FakeBPlusTree(BPlusTreeBase):
    # subclase de prueba: en vez de HeapFile o SequentialFile, guarda
    # los "registros reales" en un dict en memoria. Sirve para probar
    # la lógica del árbol (split, propagación, range_search) sola,
    # sin depender de que b_plus_clustered/unclustered ya existan.
    # El ref tiene que ser un RID (con .page_id/.slot_id) porque
    # b_tree_leaf_page.py asume esos atributos al guardarlo.

    def __init__(self, index_filename):
        super().__init__(index_filename)
        self.fake_storage = {}
        self.next_ref = 0

    def _store_record(self, params):
        ref = RID(self.next_ref, 0)
        self.fake_storage[ref] = params
        self.next_ref += 1
        return ref

    def _fetch_record(self, ref):
        return self.fake_storage.get(ref)

    def _delete_record(self, key, ref) -> bool:
        return self.fake_storage.pop(ref, None) is not None


def limpiar():
    if os.path.exists(TEST_INDEX_FILE):
        os.remove(TEST_INDEX_FILE)


def test_insert_y_search_simple():
    limpiar()
    tree = FakeBPlusTree(TEST_INDEX_FILE)

    for key in [10, 20, 30, 40, 50]:
        tree.insert(key, f"valor{key}")

    for key in [10, 20, 30, 40, 50]:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == f"valor{key}"

    assert tree.search(999) is None
    print("OK: insert y search simples andan bien, sin forzar ningún split")

    tree.file.close()
    limpiar()


def test_forzar_split_de_hoja():
    limpiar()
    tree = FakeBPlusTree(TEST_INDEX_FILE)

    # más claves de las que entran en una sola hoja (MAX_ENTRIES ≈ 340)
    claves = list(range(1, 500))
    for key in claves:
        tree.insert(key, f"valor{key}")

    assert tree.height > 0  # tuvo que crecer más allá de una sola hoja

    for key in claves:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == f"valor{key}"

    print(f"OK: split de hojas y creación de raíz nueva anduvieron, altura final = {tree.height}")

    tree.file.close()
    limpiar()


def test_range_search():
    limpiar()
    tree = FakeBPlusTree(TEST_INDEX_FILE)

    for key in range(1, 500):
        tree.insert(key, f"valor{key}")

    resultados = tree.range_search(100, 120)
    claves_encontradas = [key for key, _ in resultados]

    assert claves_encontradas == list(range(100, 121))
    print("OK: range_search devuelve las claves en orden, cruzando varias hojas")

    tree.file.close()
    limpiar()


def test_persistencia():
    limpiar()
    tree = FakeBPlusTree(TEST_INDEX_FILE)

    for key in range(1, 500):
        tree.insert(key, f"valor{key}")

    altura_previa = tree.height
    tree.file.close()

    # reabrimos el mismo archivo de índice desde cero, sin la
    # instancia vieja -- si algo dependiera de estado en RAM no
    # persistido, esto fallaría
    tree2 = FakeBPlusTree(TEST_INDEX_FILE)
    assert tree2.height == altura_previa
    assert tree2.search(250) is not None

    print("OK: la raíz y la altura se recuperan bien tras cerrar y reabrir el índice")

    tree2.file.close()
    limpiar()


tests = [
    test_insert_y_search_simple,
    test_forzar_split_de_hoja,
    test_range_search,
    test_persistencia,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")
