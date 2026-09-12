import os
import random
import time

from b_tree_base import BPlusTreeBase
from b_tree_leaf_page import RID, MAX_ENTRIES
from b_tree_internal_page import MAX_KEYS

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


def medir(n):
    limpiar()
    tree = CountingBPlusTree(TEST_FILE)

    claves = list(range(n))
    random.shuffle(claves)

    t0 = time.perf_counter()
    for key in claves:
        tree.insert(key, key)
    t_insert = time.perf_counter() - t0

    # paginas leidas por UN search, en el peor caso teorico (altura
    # completa + 1 hoja): reseteamos el contador y buscamos una sola
    # clave
    muestra = random.sample(claves, min(20, n))
    tree.page_loads = 0
    t0 = time.perf_counter()
    for key in muestra:
        tree.search(key)
    t_search = (time.perf_counter() - t0) / len(muestra)
    loads_por_search = tree.page_loads / len(muestra)

    tree.file.close()
    limpiar()

    return {
        "n": n,
        "height": tree.height,
        "paginas_por_search": loads_por_search,
        "ms_por_insert": (t_insert / n) * 1000,
        "us_por_search": t_search * 1_000_000,
    }


print(f"MAX_ENTRIES por hoja = {MAX_ENTRIES}, MAX_KEYS por nodo interno = {MAX_KEYS}")
print(f"fanout interno ~= {MAX_KEYS + 1} hijos por nodo\n")

print(f"{'N':>10} | {'altura':>6} | {'páginas/search':>15} | {'ms/insert':>10} | {'µs/search':>10}")
print("-" * 66)

resultados = []
for n in [100, 1_000, 10_000, 50_000, 100_000]:
    r = medir(n)
    resultados.append(r)
    print(f"{r['n']:>10} | {r['height']:>6} | {r['paginas_por_search']:>15.2f} | {r['ms_por_insert']:>10.4f} | {r['us_por_search']:>10.2f}")

# la aserción clave de complejidad logarítmica: las páginas leídas
# por búsqueda tienen que crecer MUCHÍSIMO más lento que N. Si fuera
# lineal (como un heap sin índice), 100_000 registros leerían miles
# de páginas por búsqueda; en un B+ Tree con este fanout, debería
# seguir siendo un puñado de páginas (altura + 1).
paginas_100 = resultados[0]["paginas_por_search"]
paginas_100k = resultados[-1]["paginas_por_search"]
crecimiento_n = resultados[-1]["n"] / resultados[0]["n"]  # 1000x
crecimiento_paginas = paginas_100k / paginas_100

print(f"\nN creció {crecimiento_n:.0f}x (de {resultados[0]['n']} a {resultados[-1]['n']})")
print(f"páginas/search creció {crecimiento_paginas:.2f}x (de {paginas_100:.2f} a {paginas_100k:.2f})")

assert resultados[-1]["height"] <= 3, "la altura no debería superar 2-3 niveles ni con 100k registros, dado el fanout"
assert crecimiento_paginas < 10, "las páginas por búsqueda no deberían crecer casi nada frente a un N 1000x mayor"

print("\nOK: el crecimiento de páginas leídas por operación es logarítmico, no lineal -- confirma D*log_(R/2)(M) de la slide de costos.")
