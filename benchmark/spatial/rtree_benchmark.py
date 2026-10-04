# Benchmark de R-Tree vs busqueda secuencial (fuerza bruta) sobre datos
# geograficos (lat/lon). Vive fuera de tests/ por la misma razon que
# benchmark/indices/: es rendimiento, no correccion -- la correccion ya la
# prueban tests/indexes/test_r_tree_base.py (compara cada resultado del
# arbol contra fuerza bruta antes de medir nada de tiempo).
import json
import os
import random
import sys
import time
from statistics import mean

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from indexes.r_tree_base import RTreeBase
from indexes.b_tree_leaf_page import RID
from spatial.geometry import Point, haversine

TEST_FILE = "bench_rtree_geo_index.bin"
REPEATS_QUERY = 100
SIZES = [int(a) for a in sys.argv[1:]] or [1_000, 10_000, 100_000]
RADII_KM = [1, 5, 10]
KS = [10, 50, 100]

# Bbox de ~55km x 55km (0.5 grados de lado a baja latitud, donde 1 grado =
# aprox 111km) -- lo bastante grande para que radios de 1/5/10km cubran
# selectividades bien distintas (desde <1% hasta una porcion grande del area).
LON_RANGE = (-0.25, 0.25)
LAT_RANGE = (-0.25, 0.25)


class FakeRTree(RTreeBase):
    # mismo storage falso que tests/indexes/test_r_tree_base.py -- aisla el
    # costo del indice en si del costo de un HeapFile real debajo.
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

    def _delete_record(self, point, ref):
        return self.fake_storage.pop(ref, None) is not None


def limpiar():
    if os.path.exists(TEST_FILE):
        os.remove(TEST_FILE)


def brute_force_radius(points, center, radius_m):
    return [i for i, p in enumerate(points) if haversine(center, p) <= radius_m]


def brute_force_knn(points, query, k):
    return sorted(range(len(points)), key=lambda i: haversine(query, points[i]))[:k]


def construir(n, seed):
    limpiar()
    tree = FakeRTree(TEST_FILE)
    random.seed(seed)
    points = [
        Point(random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE))
        for _ in range(n)
    ]

    t0 = time.perf_counter()
    for i, p in enumerate(points):
        tree.insert(p, i)
    t_construccion = time.perf_counter() - t0

    return tree, points, t_construccion


def medir_escalamiento(n):
    tree, points, t_construccion = construir(n, seed=42)
    disco_kb = os.path.getsize(TEST_FILE) / 1024
    altura = tree.height
    return tree, points, {
        "n": n,
        "altura": altura,
        "ms_construccion": t_construccion * 1000,
        "disco_kb": disco_kb,
    }


def medir_range_queries(tree, points, n):
    resultados = []
    for radio_km in RADII_KM:
        radio_m = radio_km * 1000
        t_rtree, t_bruto, tamanos = [], [], []
        for _ in range(REPEATS_QUERY):
            center = Point(random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE))

            t0 = time.perf_counter()
            r1 = tree.radius_search(center, radio_m, metric="haversine")
            t_rtree.append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            r2 = brute_force_radius(points, center, radio_m)
            t_bruto.append(time.perf_counter() - t0)

            assert {tree._fetch_record(ref) for _, ref in r1} == set(r2)
            tamanos.append(len(r2))

        resultados.append({
            "n": n,
            "radio_km": radio_km,
            "selectividad_pct": mean(tamanos) / n * 100,
            "us_rtree": mean(t_rtree) * 1e6,
            "us_secuencial": mean(t_bruto) * 1e6,
        })
    return resultados


def medir_knn_queries(tree, points, n):
    resultados = []
    for k in KS:
        t_rtree, t_bruto = [], []
        for _ in range(REPEATS_QUERY):
            q = Point(random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE))

            t0 = time.perf_counter()
            r1 = tree.knn(q, k, metric="haversine")
            t_rtree.append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            r2 = brute_force_knn(points, q, k)
            t_bruto.append(time.perf_counter() - t0)

            assert {tree._fetch_record(ref) for _, ref in r1} == set(r2)

        resultados.append({
            "n": n,
            "k": k,
            "us_rtree": mean(t_rtree) * 1e6,
            "us_secuencial": mean(t_bruto) * 1e6,
        })
    return resultados


resultados_escalamiento = []
resultados_range = []
resultados_knn = []

print(f"{'N':>8} | {'altura':>6} | {'constr (ms)':>11} | {'indice (KB)':>11}")
print("-" * 46)
for n in SIZES:
    tree, points, esc = medir_escalamiento(n)
    resultados_escalamiento.append(esc)
    print(f"{esc['n']:>8} | {esc['altura']:>6} | {esc['ms_construccion']:>11.2f} | {esc['disco_kb']:>11.1f}")

    resultados_range.extend(medir_range_queries(tree, points, n))
    resultados_knn.extend(medir_knn_queries(tree, points, n))

    tree.buffer_manager.close()
    limpiar()

print(f"\n{'N':>8} | {'radio (km)':>10} | {'% dataset':>9} | {'us/rtree':>10} | {'us/secuencial':>13}")
print("-" * 62)
for r in resultados_range:
    print(f"{r['n']:>8} | {r['radio_km']:>10} | {r['selectividad_pct']:>9.3f} | {r['us_rtree']:>10.2f} | {r['us_secuencial']:>13.2f}")

print(f"\n{'N':>8} | {'k':>5} | {'us/rtree':>10} | {'us/secuencial':>13}")
print("-" * 45)
for r in resultados_knn:
    print(f"{r['n']:>8} | {r['k']:>5} | {r['us_rtree']:>10.2f} | {r['us_secuencial']:>13.2f}")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados.json")
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({
        "escalamiento": resultados_escalamiento,
        "range_queries": resultados_range,
        "knn_queries": resultados_knn,
    }, f, indent=2)

print(f"\nOK: resultados guardados en {OUT}")
