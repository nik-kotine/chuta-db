import os
import random

from indexes.r_tree import RTree
from storage.files.heap_file import HeapFile
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from spatial.geometry import Point

INDEX_FILE = "test_r_tree_index.bin"
HEAP_FILE = "test_r_tree_heap.bin"
PAGE_SIZE = 4096
HEADER_SIZE = 10
BUFFER_FRAMES = 10


def limpiar():
    for f in (INDEX_FILE, HEAP_FILE):
        if os.path.exists(f):
            os.remove(f)


def crear_arbol():
    fm = FileManager(HEAP_FILE, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    heap = HeapFile(HEAP_FILE, bm, record_format=["double precision", "double precision", "integer"])

    tree = RTree(INDEX_FILE, heap)
    return tree, heap


def test_insert_y_search():
    limpiar()
    tree, heap = crear_arbol()

    puntos = [Point(1.0, 1.0), Point(5.0, 5.0), Point(10.0, 2.0)]
    for i, p in enumerate(puntos):
        tree.insert(p, [p.x, p.y, i])

    for i, p in enumerate(puntos):
        ref = tree.search(p)
        assert ref is not None
        assert tree._fetch_record(ref) == [p.x, p.y, i]

    assert tree.search(Point(99.0, 99.0)) is None
    print("OK: insert/search contra HeapFile real")

    tree.close()
    heap.close()
    limpiar()


def test_range_knn_y_delete():
    limpiar()
    tree, heap = crear_arbol()

    random.seed(10)
    puntos = [Point(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(200)]
    for i, p in enumerate(puntos):
        tree.insert(p, [p.x, p.y, i])

    assert tree.height > 0

    from spatial.geometry import Rectangle, euclidean
    query = Rectangle(20, 20, 60, 60)
    esperado = {i for i, p in enumerate(puntos) if query.contains_point(p)}
    resultado = {tree._fetch_record(ref)[2] for _, ref in tree.range_search(query)}
    assert resultado == esperado

    centro = Point(50, 50)
    k = 5
    por_distancia = sorted(range(len(puntos)), key=lambda i: euclidean(centro, puntos[i]))
    esperado_knn = set(por_distancia[:k])
    resultado_knn = {tree._fetch_record(ref)[2] for _, ref in tree.knn(centro, k)}
    assert resultado_knn == esperado_knn

    borrado = puntos[0]
    assert tree.delete(borrado)
    assert tree.search(borrado) is None

    print("OK: range_search/knn/delete contra HeapFile real")

    tree.close()
    heap.close()
    limpiar()


tests = [
    test_insert_y_search,
    test_range_knn_y_delete,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")
