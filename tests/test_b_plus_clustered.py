import os

from indexes.b_plus_clustered import BPlusTreeClustered

INDEX_FILE = "test_clustered_index.bin"
DATA_FILE = "test_clustered_data.bin"


def limpiar():
    for f in (INDEX_FILE, DATA_FILE):
        if os.path.exists(f):
            os.remove(f)


def crear_arbol():
    return BPlusTreeClustered(INDEX_FILE, DATA_FILE, page_size=4096, record_format="ii", buffer_frames=20)


def test_insert_y_search():
    limpiar()
    tree = crear_arbol()

    for key in [10, 20, 30, 40, 50]:
        tree.insert(key, (key, key * 100))

    for key in [10, 20, 30, 40, 50]:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == (key, key * 100)

    assert tree.search(999) is None
    print("OK: insert/search contra SequentialFile real")

    tree.close()
    limpiar()


def test_muchos_registros_y_delete():
    limpiar()
    tree = crear_arbol()

    claves = list(range(1, 1000))
    for key in claves:
        tree.insert(key, (key, key * 2))

    assert tree.height > 0

    for key in claves:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == (key, key * 2)

    a_borrar = claves[::3]
    for key in a_borrar:
        assert tree.delete(key) is True

    sobrevivientes = [k for k in claves if k not in a_borrar]
    for key in sobrevivientes:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == (key, key * 2)

    for key in a_borrar:
        assert tree.search(key) is None

    print(f"OK: {len(claves)} inserts + {len(a_borrar)} deletes contra SequentialFile real")

    tree.close()
    limpiar()


def test_orden_fisico_igual_al_indice():
    # lo que define a un indice CLUSTERED: el orden logico del
    # SequentialFile (su cadena de next_rid) tiene que coincidir con
    # el orden de las claves del arbol
    limpiar()
    tree = crear_arbol()

    claves = [50, 10, 80, 30, 90, 20, 70, 40, 60]
    for key in claves:
        tree.insert(key, (key, key))

    orden_fisico = []
    current_rid = tree.sequential_file.first_rid
    while current_rid is not None:
        record = tree.sequential_file._get_record(current_rid)
        orden_fisico.append(record.params[0])
        current_rid = record.next_rid

    assert orden_fisico == sorted(claves)
    print("OK: el orden fisico del SequentialFile coincide con el orden del indice")

    tree.close()
    limpiar()


def test_persistencia():
    limpiar()
    tree = crear_arbol()

    for key in range(1, 500):
        tree.insert(key, (key, key))

    tree.close()

    tree2 = crear_arbol()
    for key in [1, 250, 499]:
        ref = tree2.search(key)
        assert ref is not None
        assert tree2._fetch_record(ref) == (key, key)

    print("OK: persiste bien tras cerrar y reabrir (indice + datos)")

    tree2.close()
    limpiar()


def test_reindex_automatico_tras_reorganize():
    # page_size chico para que el overflow se llene rapido y dispare
    # SequentialFile.reorganize() varias veces durante los inserts
    limpiar()
    tree = BPlusTreeClustered(INDEX_FILE, DATA_FILE, page_size=128, record_format="ii", buffer_frames=20)

    veces_antes = tree.sequential_file.reorganize_count
    claves = list(range(1, 300))
    for key in claves:
        tree.insert(key, (key, key * 2))

    assert tree.sequential_file.reorganize_count > veces_antes  # confirmamos que sí se disparó

    for key in claves:
        ref = tree.search(key)
        assert ref is not None, f"la clave {key} se perdió tras un reorganize"
        assert tree._fetch_record(ref) == (key, key * 2)

    print(f"OK: reorganize se disparó {tree.sequential_file.reorganize_count} veces, el índice se reconstruyó solo y sigue consistente")

    tree.close()
    limpiar()


tests = [
    test_insert_y_search,
    test_muchos_registros_y_delete,
    test_orden_fisico_igual_al_indice,
    test_persistencia,
    test_reindex_automatico_tras_reorganize,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")
