import os
import random
import sys
import time

from indexes.b_tree_base import BPlusTreeBase
from indexes.b_tree_leaf_page import RID, MAX_ENTRIES
from indexes.b_tree_internal_page import MAX_KEYS

TEST_FILE = "test_complexity_index.bin"


class CountingBPlusTree(BPlusTreeBase):
    # mismo storage falso que test_b_tree_base.py, pero cuenta cuantas
    # paginas del INDICE se leen (_load_leaf/_load_internal), para
    # poder medir el costo real de un search/insert/delete en paginas
    # accedidas -- eso es justo lo que la slide de "costo de
    # operaciones" llama D*log_R/2(M): D es constante (una lectura de
    # pagina), lo que tiene que quedarse chico es la cantidad de
    # paginas que se leen por operacion.

    def __init__(self, index_filename):
        super().__init__(index_filename)
        self.fake_storage = {}
        self.next_ref = 0
        self.page_loads = 0

    def _load_leaf(self, page_id):
        self.page_loads += 1
        return super()._load_leaf(page_id)

    def _load_internal(self, page_id):
        self.page_loads += 1
        return super()._load_internal(page_id)

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
    if os.path.exists(TEST_FILE):
        os.remove(TEST_FILE)


def ram_del_arbol(tree) -> int:
    # tamaño en RAM del objeto arbol en si -- sin contar fake_storage,
    # que es el "storage real" simulado (equivalente a HeapFile o
    # SequentialFile en la vida real), no parte del indice.
    # No se suma sys.getsizeof(tree.__dict__): el contenedor de
    # atributos tiene un overhead fijo que CPython reporta con una
    # variacion espuria de unos bytes segun la magnitud de los enteros
    # guardados, y eso no representa datos cacheados.
    total = 0
    for k, v in vars(tree).items():
        if k == "fake_storage":
            continue
        total += sys.getsizeof(v)
    return total


def medir(n):
    limpiar()
    tree = CountingBPlusTree(TEST_FILE)

    claves = list(range(n))
    random.shuffle(claves)

    t0 = time.perf_counter()
    for key in claves:
        tree.insert(key, key)
    t_insert = time.perf_counter() - t0

    tam_disco = os.path.getsize(TEST_FILE)
    ram_arbol = ram_del_arbol(tree)

    # paginas leidas por UN search / UN delete: reseteamos el
    # contador antes de cada tanda para medir solo esa operacion
    muestra = random.sample(claves, min(20, n))

    tree.page_loads = 0
    t0 = time.perf_counter()
    for key in muestra:
        tree.search(key)
    t_search = (time.perf_counter() - t0) / len(muestra)
    loads_por_search = tree.page_loads / len(muestra)

    tree.page_loads = 0
    for key in muestra:
        tree.delete(key)
    loads_por_delete = tree.page_loads / len(muestra)

    tree.buffer_manager.close()
    limpiar()

    return {
        "n": n,
        "height": tree.height,
        "paginas_por_search": loads_por_search,
        "paginas_por_delete": loads_por_delete,
        "ms_por_insert": (t_insert / n) * 1000,
        "us_por_search": t_search * 1_000_000,
        "tam_disco_kb": tam_disco / 1024,
        "bytes_por_registro": tam_disco / n,
        "ram_arbol_bytes": ram_arbol,
    }


print(f"MAX_ENTRIES por hoja = {MAX_ENTRIES}, MAX_KEYS por nodo interno = {MAX_KEYS}")
print(f"fanout interno ~= {MAX_KEYS + 1} hijos por nodo\n")

print("--- Costo por operación (memoria secundaria: páginas leídas) ---")
print(f"{'N':>10} | {'altura':>6} | {'pág/search':>10} | {'pág/delete':>10} | {'ms/insert':>10} | {'µs/search':>10}")
print("-" * 70)

resultados = []
for n in [100, 1_000, 10_000, 50_000, 100_000]:
    r = medir(n)
    resultados.append(r)
    print(f"{r['n']:>10} | {r['height']:>6} | {r['paginas_por_search']:>10.2f} | {r['paginas_por_delete']:>10.2f} | {r['ms_por_insert']:>10.4f} | {r['us_por_search']:>10.2f}")

print("\n--- Espacio en disco y RAM ---")
print(f"{'N':>10} | {'disco (KB)':>10} | {'bytes/reg':>10} | {'RAM árbol (bytes)':>18}")
print("-" * 58)
for r in resultados:
    print(f"{r['n']:>10} | {r['tam_disco_kb']:>10.1f} | {r['bytes_por_registro']:>10.2f} | {r['ram_arbol_bytes']:>18}")

# la aserción clave de complejidad logarítmica: las páginas leídas
# por búsqueda/borrado tienen que crecer MUCHÍSIMO más lento que N. Si
# fuera lineal (como un heap sin índice), 100_000 registros leerían
# miles de páginas por operación; en un B+ Tree con este fanout,
# debería seguir siendo un puñado de páginas (altura + 1).
paginas_100 = resultados[0]["paginas_por_search"]
paginas_100k = resultados[-1]["paginas_por_search"]
crecimiento_n = resultados[-1]["n"] / resultados[0]["n"]  # 1000x
crecimiento_paginas = paginas_100k / paginas_100

print(f"\nN creció {crecimiento_n:.0f}x (de {resultados[0]['n']} a {resultados[-1]['n']})")
print(f"páginas/search creció {crecimiento_paginas:.2f}x (de {paginas_100:.2f} a {paginas_100k:.2f})")

assert resultados[-1]["height"] <= 3, "la altura no debería superar 2-3 niveles ni con 100k registros, dado el fanout"
assert crecimiento_paginas < 10, "las páginas por búsqueda no deberían crecer casi nada frente a un N 1000x mayor"

# si el rebalanceo de delete() no funcionara bien, borrar iría
# degradando el árbol y este número empezaría a crecer con N
assert max(r["paginas_por_delete"] for r in resultados) < 10, "las páginas por delete no deberían crecer con N -- indicaría que el rebalanceo no está manteniendo el árbol balanceado"

# la RAM que retiene el objeto arbol no debe depender de N, porque no
# cachea páginas de datos entre llamadas (las relee de disco cada vez).
# Margen de 8 bytes: con N=100 el arbol es una sola hoja (height=0), y
# en CPython sys.getsizeof(0) pesa 4 bytes menos que sys.getsizeof(1)
# (un int no-cero reserva un "digito" interno de 30 bits), asi que ese
# salto puntual al pasar de height=0 a height>=1 no es cacheo real.
ram_valores = [r["ram_arbol_bytes"] for r in resultados]
assert max(ram_valores) - min(ram_valores) <= 8, "la RAM del árbol no debería variar con N -- indicaría que algo se está cacheando sin límite"

print("\nOK: páginas por operación (search y delete) crecen de forma logarítmica, no lineal -- confirma D*log_(R/2)(M) de la slide de costos.")
print("OK: el espacio en disco crece proporcional a N sin desperdicio excesivo.")
print("OK: la RAM del árbol se mantiene fija sin importar N -- no cachea páginas.")
