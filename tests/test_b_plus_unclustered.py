import os

from indexes.b_plus_unclustered import BPlusTreeUnclustered
from storage.files.heap_file import HeapFile

INDEX_FILE = "test_unclustered_index.bin"
HEAP_FILE = "test_unclustered_heap.bin"


def limpiar():
    for f in (INDEX_FILE, HEAP_FILE):
        if os.path.exists(f):
            os.remove(f)


def crear_arbol():
    heap = HeapFile(HEAP_FILE)
    tree = BPlusTreeUnclustered(INDEX_FILE, heap, schema=["int", "int"])
    return tree, heap


def test_insert_y_search():
    limpiar()
    tree, heap = crear_arbol()

    for key in [10, 20, 30, 40, 50]:
        tree.insert(key, [key, key * 100])

    for key in [10, 20, 30, 40, 50]:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == [key, key * 100]

    assert tree.search(999) is None
    print("OK: insert/search contra HeapFile real")

    tree.close()
    heap.close()
    limpiar()


def test_muchos_registros_y_delete():
    limpiar()
    tree, heap = crear_arbol()

    claves = list(range(1, 1000))
    for key in claves:
        tree.insert(key, [key, key * 2])

    assert tree.height > 0

    for key in claves:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == [key, key * 2]

    a_borrar = claves[::3]
    for key in a_borrar:
        assert tree.delete(key) is True

    sobrevivientes = [k for k in claves if k not in a_borrar]
    for key in sobrevivientes:
        ref = tree.search(key)
        assert ref is not None
        assert tree._fetch_record(ref) == [key, key * 2]

    for key in a_borrar:
        assert tree.search(key) is None

    print(f"OK: {len(claves)} inserts + {len(a_borrar)} deletes contra HeapFile real")

    tree.close()
    heap.close()
    limpiar()


def test_no_depende_del_orden_fisico_del_heap():
    # lo que define a un indice NO agrupado: el heap guarda los
    # registros en orden de llegada (desordenado), pero el arbol
    # igual tiene que poder recorrerlos ordenados por clave
    limpiar()
    tree, heap = crear_arbol()

    claves = [50, 10, 80, 30, 90, 20, 70, 40, 60]
    for key in claves:
        tree.insert(key, [key, key])

    resultados = tree.range_search(20, 70)
    claves_en_rango = [key for key, _ in resultados]
    assert claves_en_rango == [20, 30, 40, 50, 60, 70]

    print("OK: range_search devuelve orden correcto aunque el heap esté desordenado")

    tree.close()
    heap.close()
    limpiar()


def test_dos_indices_comparten_el_mismo_heap():
    # la razon de que el constructor reciba un HeapFile ya abierto:
    # permitir varios indices no agrupados sobre la misma tabla
    limpiar()
    index_2 = "test_unclustered_index_2.bin"
    if os.path.exists(index_2):
        os.remove(index_2)

    heap = HeapFile(HEAP_FILE)
    indice_por_id = BPlusTreeUnclustered(INDEX_FILE, heap, schema=["int", "int"])
    indice_por_valor = BPlusTreeUnclustered(index_2, heap, schema=["int", "int"])

    refs = {}
    for key in range(1, 20):
        ref = indice_por_id.insert(key, [key, key * 10])
        refs[key] = ref
        # el segundo indice apunta al MISMO registro del heap, pero
        # usando el otro campo (key*10) como clave de busqueda
        indice_por_valor._insert_ref(key * 10, ref)

    # buscar por cualquiera de los dos indices trae el mismo registro
    ref_por_id = indice_por_id.search(5)
    ref_por_valor = indice_por_valor.search(50)
    assert ref_por_id == ref_por_valor == refs[5]
    assert indice_por_id._fetch_record(ref_por_id) == [5, 50]

    print("OK: dos índices no agrupados comparten el mismo HeapFile sin pisarse")

    indice_por_id.close()
    indice_por_valor.close()
    heap.close()
    limpiar()
    os.remove(index_2)


tests = [
    test_insert_y_search,
    test_muchos_registros_y_delete,
    test_no_depende_del_orden_fisico_del_heap,
    test_dos_indices_comparten_el_mismo_heap,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")
