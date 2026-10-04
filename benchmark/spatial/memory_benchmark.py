# Benchmark de uso de memoria RAM: R-Tree vs busqueda secuencial.
#
# Complementa a rtree_benchmark.py, que mide tiempo y espacio en DISCO.
# Aca se mide lo otro que pide 2.2.4: cuanta RAM necesita cada tecnica.
#
# La diferencia de fondo entre las dos es estructural:
#
#   - La busqueda secuencial mantiene TODOS los puntos en memoria. Su RAM
#     crece linealmente con N; no hay forma de evitarlo, porque para
#     responder una consulta tiene que recorrerlos.
#
#   - El R-Tree vive en disco y solo mantiene en RAM lo que el
#     BufferManager tiene cacheado (50 paginas) mas los metadatos del
#     arbol. Su RAM crece mucho mas despacio que N, asi que el costo por
#     punto CAE a medida que el dataset crece, hasta quedar varias veces
#     por debajo de la secuencial.
#
# Se usa tracemalloc, que contabiliza las asignaciones del heap de Python
# y no el RSS del proceso: asi la medida no se contamina con el
# interprete, los modulos importados ni la fragmentacion del allocator.
#
# PostGIS no aparece en esta comparacion a proposito: corre en otro
# proceso (y hasta puede estar en otra maquina), asi que su RAM depende de
# shared_buffers y work_mem del servidor, no de este programa. Medirla con
# tracemalloc daria solo la memoria del cliente psycopg2, que no dice nada
# sobre el indice.
import json
import os
import random
import sys
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from indexes.r_tree_base import RTreeBase
from indexes.b_tree_leaf_page import RID
from spatial.geometry import Point

TEST_FILE = "bench_rtree_memoria.bin"
SIZES = [int(a) for a in sys.argv[1:]] or [1_000, 10_000, 100_000]

# Misma region y distribucion que rtree_benchmark.py, para que los
# numeros de las dos corridas sean comparables entre si.
LON_RANGE = (-0.25, 0.25)
LAT_RANGE = (-0.25, 0.25)
SEED = 42


class FakeRTree(RTreeBase):
    """
    Variante del FakeRTree de rtree_benchmark.py que NO guarda los
    registros en un dict.

    Alli el dict hace falta para poder leer las filas y validar los
    resultados. Aca estorba: seria memoria del archivo de datos, no del
    indice, y a N grande pesa mas que el propio arbol. Se devuelve un RID
    valido (los nodos lo serializan igual, es parte del indice) pero sin
    retener nada, asi que lo que mide tracemalloc es solo el buffer pool
    y los metadatos del arbol.
    """

    def __init__(self, index_filename):
        super().__init__(index_filename)
        self.next_ref = 0

    def _store_record(self, params):
        ref = RID(self.next_ref, 0)
        self.next_ref += 1
        return ref

    def _fetch_record(self, ref):
        return None

    def _delete_record(self, point, ref):
        return True


def limpiar():
    if os.path.exists(TEST_FILE):
        os.remove(TEST_FILE)


def generar_puntos(n):
    random.seed(SEED)
    return [
        Point(random.uniform(*LON_RANGE), random.uniform(*LAT_RANGE))
        for _ in range(n)
    ]


def medir_secuencial(n):
    """
    RAM de la busqueda secuencial: la lista de puntos en memoria.

    No hay estructura auxiliar, asi que el pico de construccion y lo que
    queda residente son practicamente lo mismo.
    """
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]

    puntos = generar_puntos(n)

    actual, pico = tracemalloc.get_traced_memory()
    residente_kb = (actual - base) / 1024
    pico_kb = (pico - base) / 1024
    tracemalloc.stop()

    del puntos
    return {
        "tecnica": "Secuencial",
        "n": n,
        "pico_kb": pico_kb,
        "residente_kb": residente_kb,
        "bytes_por_punto": residente_kb * 1024 / n,
    }


def medir_rtree(n):
    """
    RAM del R-Tree: lo que ocupan el buffer pool y los metadatos del
    arbol, sin contar los registros.

    Los puntos se generan ANTES de arrancar tracemalloc, porque la lista
    de entrada no es parte del indice: en un motor real los registros
    viven en el archivo de datos y el indice solo guarda las referencias.
    """
    limpiar()
    puntos = generar_puntos(n)      # fuera de la medicion, a proposito

    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]

    tree = FakeRTree(TEST_FILE)
    for i, p in enumerate(puntos):
        tree.insert(p, i)

    actual, pico = tracemalloc.get_traced_memory()
    residente_kb = (actual - base) / 1024
    pico_kb = (pico - base) / 1024
    tracemalloc.stop()

    disco_kb = os.path.getsize(TEST_FILE) / 1024
    altura = tree.height
    paginas_en_buffer = len(getattr(tree.buffer_manager, "frames", []) or [])

    tree.buffer_manager.close(tree.file_manager)
    limpiar()
    del puntos

    return {
        "tecnica": "R-Tree",
        "n": n,
        "pico_kb": max(0.0, pico_kb),
        "residente_kb": max(0.0, residente_kb),
        "bytes_por_punto": max(0.0, residente_kb) * 1024 / n,
        "disco_kb": disco_kb,
        "altura": altura,
        "paginas_en_buffer": paginas_en_buffer,
    }


resultados = []
for n in SIZES:
    print(f"midiendo N={n:,}...", end=" ", flush=True)
    resultados.append(medir_secuencial(n))
    resultados.append(medir_rtree(n))
    print("OK")

print("\n--- Uso de memoria RAM ---")
print(f"{'tecnica':<12} | {'N':>8} | {'pico (KB)':>10} | {'residente (KB)':>14} | {'bytes/punto':>11}")
print("-" * 68)
for r in resultados:
    print(f"{r['tecnica']:<12} | {r['n']:>8,} | {r['pico_kb']:>10.1f} | "
          f"{r['residente_kb']:>14.1f} | {r['bytes_por_punto']:>11.1f}")

print("\n--- R-Tree: RAM vs disco ---")
print(f"{'N':>8} | {'RAM (KB)':>9} | {'disco (KB)':>10} | {'altura':>6} | {'paginas en buffer':>18}")
print("-" * 62)
for r in resultados:
    if r["tecnica"] == "R-Tree":
        print(f"{r['n']:>8,} | {r['residente_kb']:>9.1f} | {r['disco_kb']:>10.1f} | "
              f"{r['altura']:>6} | {r['paginas_en_buffer']:>18}")

# --- verificaciones de que los numeros tienen sentido ---
secuencial = sorted([r for r in resultados if r["tecnica"] == "Secuencial"],
                    key=lambda r: r["n"])
rtree = sorted([r for r in resultados if r["tecnica"] == "R-Tree"],
               key=lambda r: r["n"])

# la secuencial tiene que crecer con N: guarda todos los puntos
for antes, despues in zip(secuencial, secuencial[1:]):
    assert despues["residente_kb"] > antes["residente_kb"], (
        f"la secuencial no crecio al pasar de N={antes['n']} a N={despues['n']}")

# y su costo por punto tiene que quedar mas o menos constante
for r in secuencial:
    assert 20 < r["bytes_por_punto"] < 500, (
        f"Secuencial N={r['n']}: {r['bytes_por_punto']:.1f} bytes/punto "
        f"esta fuera de lo razonable para un objeto Point")

# el R-Tree amortiza: su RAM crece mas despacio que N (el buffer pool
# es fijo), asi que el costo por punto tiene que CAER al crecer N
if len(rtree) > 1:
    for antes, despues in zip(rtree, rtree[1:]):
        assert despues["bytes_por_punto"] < antes["bytes_por_punto"], (
            f"el R-Tree deberia amortizar su RAM al crecer N "
            f"({antes['n']}: {antes['bytes_por_punto']:.1f} -> "
            f"{despues['n']}: {despues['bytes_por_punto']:.1f} bytes/punto)")

# con el dataset mas grande el arbol tiene que usar menos RAM que la lista
if rtree and secuencial:
    assert rtree[-1]["residente_kb"] < secuencial[-1]["residente_kb"], (
        f"con N={rtree[-1]['n']} el R-Tree usa {rtree[-1]['residente_kb']:.1f} KB "
        f"y la secuencial {secuencial[-1]['residente_kb']:.1f} KB")

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados_memoria.json")
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(resultados, f, indent=2)

print("\nOK: benchmark de memoria completado para N =", SIZES)
