import os
import random

from indexes.r_tree_base import RTreeBase
from indexes.b_tree_leaf_page import RID
from indexes.r_tree_node import LEAF_M
from spatial.geometry import Point, Rectangle, euclidean, haversine, point_in_polygon

TEST_INDEX_FILE = "test_r_tree_index.bin"


class FakeRTree(RTreeBase):
    # mismo truco que FakeBPlusTree en test_b_tree_base.py: guarda los
    # "registros reales" en un dict en memoria, para probar la logica del
    # arbol (split, condense, busquedas) sin depender de un HeapFile real.

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

    def _delete_record(self, point, ref) -> bool:
        return self.fake_storage.pop(ref, None) is not None


def limpiar():
    if os.path.exists(TEST_INDEX_FILE):
        os.remove(TEST_INDEX_FILE)


def test_insert_y_search_simple():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    puntos = [Point(1, 1), Point(5, 5), Point(10, 2), Point(3, 8)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    for i, p in enumerate(puntos):
        ref = tree.search(p)
        assert ref is not None
        assert tree._fetch_record(ref) == i

    assert tree.search(Point(99, 99)) is None
    print("OK: insert y search simples, sin forzar split")

    tree.buffer_manager.close()
    limpiar()


def test_forzar_split():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    random.seed(1)
    n = LEAF_M * 6
    puntos = [Point(random.uniform(0, 1000), random.uniform(0, 1000)) for _ in range(n)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    assert tree.height > 0  # tuvo que crecer mas alla de una sola hoja

    for i, p in enumerate(puntos):
        ref = tree.search(p)
        assert ref is not None
        assert tree._fetch_record(ref) == i

    print(f"OK: split de hojas y creacion de raiz nueva, altura final = {tree.height}")

    tree.buffer_manager.close()
    limpiar()


def test_range_search():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    random.seed(2)
    n = LEAF_M * 4
    puntos = [Point(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(n)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    query = Rectangle(20, 20, 60, 60)
    esperado = {i for i, p in enumerate(puntos) if query.contains_point(p)}

    resultado = {tree._fetch_record(ref) for _, ref in tree.range_search(query)}

    assert resultado == esperado
    print(f"OK: range_search coincide con fuerza bruta ({len(esperado)} puntos)")

    tree.buffer_manager.close()
    limpiar()


def test_radius_search_euclidiana():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    random.seed(3)
    n = LEAF_M * 4
    puntos = [Point(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(n)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    centro = Point(50, 50)
    radio = 15
    esperado = {i for i, p in enumerate(puntos) if euclidean(centro, p) <= radio}

    resultado = {tree._fetch_record(ref) for _, ref in tree.radius_search(centro, radio)}

    assert resultado == esperado
    print(f"OK: radius_search euclidiana coincide con fuerza bruta ({len(esperado)} puntos)")

    tree.buffer_manager.close()
    limpiar()


def test_radius_search_haversine():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    # puntos alrededor de Lima (x=lon, y=lat)
    random.seed(4)
    n = LEAF_M * 4
    puntos = [Point(-77.03 + random.uniform(-0.1, 0.1), -12.05 + random.uniform(-0.1, 0.1)) for _ in range(n)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    centro = Point(-77.03, -12.05)
    radio_m = 5000
    esperado = {i for i, p in enumerate(puntos) if haversine(centro, p) <= radio_m}

    resultado = {tree._fetch_record(ref) for _, ref in tree.radius_search(centro, radio_m, metric="haversine")}

    assert resultado == esperado
    print(f"OK: radius_search haversine coincide con fuerza bruta ({len(esperado)} puntos)")

    tree.buffer_manager.close()
    limpiar()


def test_knn():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    random.seed(5)
    n = LEAF_M * 4
    puntos = [Point(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(n)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    consulta = Point(50, 50)
    k = 10
    por_distancia = sorted(range(n), key=lambda i: euclidean(consulta, puntos[i]))
    esperado = set(por_distancia[:k])

    resultado = {tree._fetch_record(ref) for _, ref in tree.knn(consulta, k)}

    assert resultado == esperado
    print(f"OK: knn (k={k}) coincide con fuerza bruta")

    tree.buffer_manager.close()
    limpiar()


def test_polygon_search():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    random.seed(6)
    n = LEAF_M * 4
    puntos = [Point(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(n)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    # triangulo
    poligono = [Point(10, 10), Point(90, 20), Point(50, 90)]
    esperado = {i for i, p in enumerate(puntos) if point_in_polygon(p, poligono)}

    resultado = {tree._fetch_record(ref) for _, ref in tree.polygon_search(poligono)}

    assert resultado == esperado
    print(f"OK: polygon_search coincide con fuerza bruta ({len(esperado)} puntos)")

    tree.buffer_manager.close()
    limpiar()


def test_delete_simple():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    puntos = [Point(1, 1), Point(5, 5), Point(10, 2)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    assert tree.delete(Point(5, 5))
    assert tree.search(Point(5, 5)) is None
    assert tree.search(Point(1, 1)) is not None
    assert tree.search(Point(10, 2)) is not None
    assert not tree.delete(Point(99, 99))  # no existe

    print("OK: delete simple")

    tree.buffer_manager.close()
    limpiar()


def test_delete_forzando_condense():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    random.seed(7)
    n = LEAF_M * 6
    puntos = [Point(random.uniform(0, 1000), random.uniform(0, 1000)) for _ in range(n)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    assert tree.height > 0

    # borra la mayoria -- fuerza underflow y CondenseTree en cascada
    random.shuffle(puntos)
    borrados, quedan = puntos[: n * 9 // 10], puntos[n * 9 // 10:]

    for p in borrados:
        assert tree.delete(p)

    for p in borrados:
        assert tree.search(p) is None
    for p in quedan:
        assert tree.search(p) is not None

    print(f"OK: delete forzando condense/reinsercion, quedaron {len(quedan)} puntos consistentes")

    tree.buffer_manager.close()
    limpiar()


def test_persistencia():
    limpiar()
    tree = FakeRTree(TEST_INDEX_FILE)

    random.seed(8)
    puntos = [Point(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    altura_antes = tree.height
    tree.buffer_manager.close()

    # reabrimos el mismo archivo de indice desde cero, sin la instancia
    # vieja -- la "fake_storage" en RAM de tree2 arranca vacia (es un stand-in
    # de HeapFile, no persiste), pero la estructura del arbol en disco si, asi
    # que search() debe seguir encontrando el RID de cada punto
    tree2 = FakeRTree(TEST_INDEX_FILE)
    assert tree2.height == altura_antes
    for p in puntos:
        assert tree2.search(p) is not None

    print("OK: persistencia -- cerrar y reabrir conserva el arbol")

    tree2.buffer_manager.close()
    limpiar()


tests = [
    test_insert_y_search_simple,
    test_forzar_split,
    test_range_search,
    test_radius_search_euclidiana,
    test_radius_search_haversine,
    test_knn,
    test_polygon_search,
    test_delete_simple,
    test_delete_forzando_condense,
    test_persistencia,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")
