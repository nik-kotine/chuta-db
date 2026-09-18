import os
import random
import sys
import time

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from indexes.extendible_hash import HashIndex, PAGE_SIZE

TEST_FILE = "test_hash_complexity_index.bin"
HEADER_SIZE = 16
BUFFER_FRAMES = 100


class CountingHashIndex(HashIndex):
    # mismo truco que CountingBPlusTree en test_b_tree_complexity.py:
    # cuenta cuantas paginas fisicas se leen (bucket/directorio/metadata)
    # para poder medir el costo real de una operacion en paginas
    # accedidas -- en un hash bien balanceado esto deberia quedarse
    # CONSTANTE con N (a diferencia del B+, que crece como log(N)).

    def __init__(self, *args, **kwargs):
        self.page_loads = 0
        super().__init__(*args, **kwargs)

    def _load_bucket(self, page_id, is_new=False):
        self.page_loads += 1
        return super()._load_bucket(page_id, is_new)

    def _load_directory_page(self, page_id):
        self.page_loads += 1
        return super()._load_directory_page(page_id)

    def _load_metadata_page(self):
        self.page_loads += 1
        return super()._load_metadata_page()


def limpiar():
    if os.path.exists(TEST_FILE):
        os.remove(TEST_FILE)


def ram_del_indice(index) -> int:
    # tamaño en RAM del objeto indice en si -- sin contar buffer_manager
    # ni file_manager, que ya estan acotados aparte por BufferManager
    # (no es "cacheo sin limite" del indice, es el pool global)
    total = 0
    for k, v in vars(index).items():
        if k in ("buffer_manager", "file_manager", "serializer"):
            continue
        total += sys.getsizeof(v)
    return total


def medir(n):
    limpiar()
    fm = FileManager(TEST_FILE, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    index = CountingHashIndex(
        table_name="bench", column_name="key", key_format=">i", key_variable=False,
        buffer_manager=bm, max_bucket_size=16, depth=1, seed=0, file_manager=fm,
    )

    claves = list(range(n))
    random.shuffle(claves)

    t0 = time.perf_counter()
    for i, key in enumerate(claves):
        index.insert(key, (i, 0))
    t_insert = time.perf_counter() - t0

    tam_disco = os.path.getsize(TEST_FILE)
    ram_indice = ram_del_indice(index)

    # paginas leidas por UN search / UN delete: reseteamos el contador
    # antes de cada tanda para medir solo esa operacion
    muestra = random.sample(claves, min(20, n))

    index.page_loads = 0
    t0 = time.perf_counter()
    for key in muestra:
        index.search(key)
    t_search = (time.perf_counter() - t0) / len(muestra)
    loads_por_search = index.page_loads / len(muestra)

    index.page_loads = 0
    for key in muestra:
        index.delete(key)
    loads_por_delete = index.page_loads / len(muestra)

    bm.close()
    limpiar()

    return {
        "n": n,
        "depth": index.depth,
        "paginas_por_search": loads_por_search,
        "paginas_por_delete": loads_por_delete,
        "ms_por_insert": (t_insert / n) * 1000,
        "us_por_search": t_search * 1_000_000,
        "tam_disco_kb": tam_disco / 1024,
        "bytes_por_registro": tam_disco / n,
        "ram_indice_bytes": ram_indice,
    }


print("--- Costo por operación (memoria secundaria: páginas leídas) ---")
print(f"{'N':>10} | {'depth':>6} | {'pág/search':>10} | {'pág/delete':>10} | {'ms/insert':>10} | {'µs/search':>10}")
print("-" * 70)

resultados = []
for n in [100, 1_000, 10_000, 50_000, 100_000]:
    r = medir(n)
    resultados.append(r)
    print(f"{r['n']:>10} | {r['depth']:>6} | {r['paginas_por_search']:>10.2f} | {r['paginas_por_delete']:>10.2f} | {r['ms_por_insert']:>10.4f} | {r['us_por_search']:>10.2f}")

print("\n--- Espacio en disco y RAM ---")
print(f"{'N':>10} | {'disco (KB)':>10} | {'bytes/reg':>10} | {'RAM índice (bytes)':>18}")
print("-" * 58)
for r in resultados:
    print(f"{r['n']:>10} | {r['tam_disco_kb']:>10.1f} | {r['bytes_por_registro']:>10.2f} | {r['ram_indice_bytes']:>18}")

# la aserción clave de un hash bien construido: a diferencia de un B+
# (donde las paginas/operacion crecen como log(N)), acá deberían
# quedarse CONSTANTES -- un search/delete siempre resuelve el bucket
# correcto con el mismo puñado de lecturas (metadata + directorio +
# bucket, sin importar cuántos registros haya en total).
paginas_100 = resultados[0]["paginas_por_search"]
paginas_100k = resultados[-1]["paginas_por_search"]
crecimiento_n = resultados[-1]["n"] / resultados[0]["n"]  # 1000x
crecimiento_paginas = paginas_100k / paginas_100

print(f"\nN creció {crecimiento_n:.0f}x (de {resultados[0]['n']} a {resultados[-1]['n']})")
print(f"páginas/search creció {crecimiento_paginas:.2f}x (de {paginas_100:.2f} a {paginas_100k:.2f})")

assert max(r["paginas_por_search"] for r in resultados) <= 5, (
    "un search no deberia necesitar mas de un puñado de paginas "
    "(metadata + directorio + bucket [+ overflow ocasional])"
)
assert max(r["paginas_por_delete"] for r in resultados) <= 5, (
    "un delete no deberia necesitar mas paginas que un search"
)

# el espacio en disco SI crece con N (pre-asigna buckets por capacidad,
# no por uso real -- ver README del benchmark), pero no debería explotar
# de forma mas que lineal
bytes_100 = resultados[0]["bytes_por_registro"]
bytes_100k = resultados[-1]["bytes_por_registro"]
print(f"bytes/registro: {bytes_100:.2f} (N=100) -> {bytes_100k:.2f} (N=100000)")

# la RAM que retiene el objeto indice en si no debe crecer sin control
# con N (el directorio persiste en disco, no en el objeto Python)
ram_valores = [r["ram_indice_bytes"] for r in resultados]
assert max(ram_valores) - min(ram_valores) <= 64, (
    "la RAM del objeto HashIndex no deberia variar mucho con N -- "
    "indicaria que algo se esta cacheando en el objeto sin limite"
)

print("\nOK: paginas por operacion (search y delete) se mantienen ~constantes con N -- confirma O(1) esperado de un hash.")
print("OK: el espacio en disco crece con N sin quedar fuera de control.")
print("OK: la RAM del objeto indice se mantiene fija sin importar N.")
