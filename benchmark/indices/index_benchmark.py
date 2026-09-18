# Benchmark de estructuras de indexacion: B+ agrupado (clustered), B+ no
# agrupado (unclustered) y Hash extensible.
import json
import os
import random
import struct
import sys
import time
from statistics import mean

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.files.sequential_file import FILE_HEADER_FORMAT, FILE_HEADER_SIZE, SequentialFile
from storage.files.heap_file import HeapFile
from indexes.b_plus_clustered import BPlusTreeClustered
from indexes.b_plus_unclustered import BPlusTreeUnclustered
from indexes.extendible_hash import HashIndex, PAGE_SIZE as HASH_PAGE_SIZE

PAGE_SIZE = 4096
BUFFER_FRAMES = 50
HEAP_HEADER_SIZE = 10
SCHEMA = ["integer", "integer"]

REPEATS = 3
SAMPLE_SIZE = 30  # claves muestreadas para busqueda exacta / puntos de rango

# Tamaños de N configurables por linea de comandos para pruebas rapidas,

# El clustered escala ~O(N^2) con este SequentialFile: la pagina de
# overflow tiene capacidad fija (no crece con N), asi que dispara un
# reorganize() cada ~250 inserts SIEMPRE, y cada reorganize (mas el
# reindex completo que hace BPlusTreeClustered para seguirlo) cuesta
# O(N_actual). N=10_000 ya tarda minutos y N=100_000 tardaria horas 
#  Por eso el default se mantiene chico; para numeros mas grandes correr 
# a mano:
# python -m benchmark.indices.index_benchmark 10000
SIZES = [int(a) for a in sys.argv[1:]] or [500, 2_000, 5_000]

HASH_FILE_HEADER_SIZE = 16
HASH_DEFAULT_BUCKET_SIZE = 16
HASH_DEFAULT_DEPTH = 1
HASH_DEFAULT_SEED = 0

CLUSTERED_INDEX_FILE = "bench_clustered.idx"
CLUSTERED_DATA_FILE = "bench_clustered_data.bin"
UNCLUSTERED_INDEX_FILE = "bench_unclustered.idx"
UNCLUSTERED_DATA_FILE = "bench_unclustered_heap.bin"
HASH_INDEX_FILE = "bench_hash.idx"
HASH_DATA_FILE = "bench_hash_heap.bin"

ARCHIVOS = [
    CLUSTERED_INDEX_FILE, CLUSTERED_DATA_FILE,
    UNCLUSTERED_INDEX_FILE, UNCLUSTERED_DATA_FILE,
    HASH_INDEX_FILE, HASH_DATA_FILE,
]


def limpiar():
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)


def crear_clustered():
    # mismo setup que Table haria para una tabla "sequential": header +
    # una pagina de overflow antes de que SequentialFile abra el archivo
    with open(CLUSTERED_DATA_FILE, "wb") as f:
        f.write(struct.pack(FILE_HEADER_FORMAT, 0, -1, 0, 0))
        f.write(b"\x00" * PAGE_SIZE)

    fm = FileManager(CLUSTERED_DATA_FILE, PAGE_SIZE, FILE_HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    sf = SequentialFile(bm, PAGE_SIZE, SCHEMA)
    tree = BPlusTreeClustered(CLUSTERED_INDEX_FILE, sf, buffer_frames=BUFFER_FRAMES)
    return tree, bm, CLUSTERED_DATA_FILE


def crear_unclustered():
    fm = FileManager(UNCLUSTERED_DATA_FILE, PAGE_SIZE, HEAP_HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    heap = HeapFile(UNCLUSTERED_DATA_FILE, bm, record_format=SCHEMA)
    tree = BPlusTreeUnclustered(UNCLUSTERED_INDEX_FILE, heap, schema=SCHEMA)
    return tree, bm, UNCLUSTERED_DATA_FILE


class HashIndexAdapter:
    """
    HashIndex no sabe de HeapFile (inserta pares (key, rid) sueltos, el
    caller es quien guarda el dato real) -- este adapter lo envuelve
    junto a un HeapFile propio para exponer la misma interfaz
    (insert/search/delete/range_search/close) que usan los arboles B+ acá,
    y asi poder medirlos con el mismo medir_todo().

    range_search/orden no tienen equivalente nativo en un hash: la unica
    forma correcta de responderlos es un scan completo del heap + sort,
    asi que eso es justamente lo que hace el fallback de abajo (a
    proposito, para que el benchmark muestre ese costo real).
    """

    def __init__(self, index_filename, heap_file):
        self.index_filename = index_filename
        fm = FileManager(index_filename, HASH_PAGE_SIZE, HASH_FILE_HEADER_SIZE)
        self.bm = BufferManager(fm)
        self.index = HashIndex(
            table_name="bench", column_name="key", key_format=">i", key_variable=False,
            buffer_manager=self.bm, max_bucket_size=HASH_DEFAULT_BUCKET_SIZE,
            depth=HASH_DEFAULT_DEPTH, seed=HASH_DEFAULT_SEED, file_manager=fm,
        )
        self.heap = heap_file

    def insert(self, key, params):
        rid = self.heap.insert(params)
        self.index.insert(key, rid)
        return rid

    def search(self, key):
        matches = self.index.search(key)
        if not matches:
            return None
        return matches[0].rid if len(matches) == 1 else [kv.rid for kv in matches]

    def delete(self, key) -> bool:
        matches = self.index.search(key)
        if not matches:
            return False
        for kv in matches:
            self.heap.delete(kv.rid)
        self.index.delete(key)
        return True

    def range_search(self, start_key, end_key):
        vivos = [
            (params[0], rid) for rid, params in self.heap.scan()
            if start_key <= params[0] <= end_key
        ]
        vivos.sort(key=lambda par: par[0])
        return vivos

    def close(self):
        self.index.close()


def crear_hash():
    fm = FileManager(HASH_DATA_FILE, PAGE_SIZE, HEAP_HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    heap = HeapFile(HASH_DATA_FILE, bm, record_format=SCHEMA)
    tree = HashIndexAdapter(HASH_INDEX_FILE, heap)
    return tree, bm, HASH_DATA_FILE


# nombre -> funcion de creacion
INDICES = {
    "B+ clustered": crear_clustered,
    "B+ unclustered": crear_unclustered,
    "Hash extensible": crear_hash,
}


class ConjuntoVivo:
    """Claves actualmente insertadas, con borrado aleatorio O(1) (swap-pop)
    para que el costo de elegir a quien borrar durante el churn no
    distorsione la medicion del propio churn."""

    def __init__(self, claves):
        self._lista = list(claves)
        self._pos = {k: i for i, k in enumerate(self._lista)}

    def agregar(self, k):
        self._pos[k] = len(self._lista)
        self._lista.append(k)

    def quitar_aleatoria(self):
        idx = random.randrange(len(self._lista))
        k = self._lista[idx]
        ultimo = self._lista.pop()
        if idx < len(self._lista):
            self._lista[idx] = ultimo
            self._pos[ultimo] = idx
        del self._pos[k]
        return k

    def muestra(self, n):
        return random.sample(self._lista, min(n, len(self._lista)))

    def __len__(self):
        return len(self._lista)


def medir_construccion(crear_fn, n, repeats=REPEATS):
    tiempos = []
    for _ in range(repeats):
        limpiar()
        tree, bm, _ = crear_fn()
        claves = list(range(n))
        random.shuffle(claves)

        t0 = time.perf_counter()
        for k in claves:
            tree.insert(k, [k, k * 2])
        tiempos.append(time.perf_counter() - t0)

        tree.close()
        bm.close()
    limpiar()
    return mean(tiempos)


def medir_todo(nombre, crear_fn, n):
    tiempo_construccion = medir_construccion(crear_fn, n)

    # se reconstruye una vez mas y se deja vivo para el resto de medidas
    limpiar()
    tree, bm, data_file = crear_fn()
    claves = list(range(n))
    random.shuffle(claves)
    for k in claves:
        tree.insert(k, [k, k * 2])

    vivos = ConjuntoVivo(claves)

    espacio_indice = os.path.getsize(tree.index_filename)
    espacio_datos = os.path.getsize(data_file)

    # --- busqueda exacta ---
    muestra = vivos.muestra(SAMPLE_SIZE)
    tiempos = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for k in muestra:
            tree.search(k)
        tiempos.append((time.perf_counter() - t0) / len(muestra))
    us_exacta = mean(tiempos) * 1_000_000

    # --- busqueda por rango (ancho ~1% de n) ---
    ancho = max(5, n // 100)
    inicios = vivos.muestra(10)
    tiempos = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for inicio in inicios:
            tree.range_search(inicio, inicio + ancho)
        tiempos.append((time.perf_counter() - t0) / len(inicios))
    us_rango = mean(tiempos) * 1_000_000

    # --- ordenamiento: recorrido completo en orden de clave ---
    tiempos = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        en_orden = tree.range_search(-1, n + 1)
        tiempos.append(time.perf_counter() - t0)
    ms_orden = mean(tiempos) * 1000
    assert len(en_orden) == n, f"{nombre}: el recorrido en orden deberia devolver los {n} vivos"
    claves_en_orden = [k for k, _ in en_orden]
    assert claves_en_orden == sorted(claves_en_orden), f"{nombre}: range_search no devolvio las claves ordenadas"

    # --- rendimiento con inserciones/eliminaciones frecuentes (churn) ---
    churn_ops = max(200, min(2000, n // 5))
    siguiente_clave_nueva = n
    t0 = time.perf_counter()
    for _ in range(churn_ops):
        if random.random() < 0.5:
            tree.insert(siguiente_clave_nueva, [siguiente_clave_nueva, siguiente_clave_nueva * 2])
            vivos.agregar(siguiente_clave_nueva)
            siguiente_clave_nueva += 1
        else:
            tree.delete(vivos.quitar_aleatoria())
    t_churn = time.perf_counter() - t0
    ops_por_seg = churn_ops / t_churn

    # busqueda exacta otra vez, ya con el arbol "usado", para ver si el
    # churn degrado el costo de busqueda frente al valor pre-churn
    muestra_post = vivos.muestra(SAMPLE_SIZE)
    t0 = time.perf_counter()
    for k in muestra_post:
        tree.search(k)
    us_exacta_post_churn = (time.perf_counter() - t0) / len(muestra_post) * 1_000_000

    tree.close()
    bm.close()
    limpiar()

    return {
        "nombre": nombre,
        "n": n,
        "tiempo_construccion_ms": tiempo_construccion * 1000,
        "ms_por_insert": tiempo_construccion / n * 1000,
        "espacio_datos_kb": espacio_datos / 1024,
        "espacio_indice_kb": espacio_indice / 1024,
        "us_exacta": us_exacta,
        "us_rango": us_rango,
        "ms_orden": ms_orden,
        "churn_ops": churn_ops,
        "ops_por_seg": ops_por_seg,
        "us_exacta_post_churn": us_exacta_post_churn,
    }


resultados = []
for n in SIZES:
    for nombre, crear_fn in INDICES.items():
        print(f"midiendo {nombre} con N={n}...", end=" ", flush=True)
        r = medir_todo(nombre, crear_fn, n)
        resultados.append(r)
        print("OK")

print("\n--- Construccion y espacio adicional ---")
print(f"{'indice':<16} | {'N':>8} | {'constr. (ms)':>12} | {'ms/insert':>10} | {'datos (KB)':>10} | {'indice (KB)':>11}")
print("-" * 82)
for r in resultados:
    print(f"{r['nombre']:<16} | {r['n']:>8} | {r['tiempo_construccion_ms']:>12.2f} | "
          f"{r['ms_por_insert']:>10.4f} | {r['espacio_datos_kb']:>10.1f} | {r['espacio_indice_kb']:>11.1f}")

print("\n--- Tiempo de consulta ---")
print(f"{'indice':<16} | {'N':>8} | {'us/exacta':>10} | {'us/rango':>10} | {'ms/orden completo':>18}")
print("-" * 78)
for r in resultados:
    print(f"{r['nombre']:<16} | {r['n']:>8} | {r['us_exacta']:>10.2f} | {r['us_rango']:>10.2f} | {r['ms_orden']:>18.2f}")

print("\n--- Rendimiento con inserciones/eliminaciones frecuentes ---")
print(f"{'indice':<16} | {'N':>8} | {'churn ops':>9} | {'ops/seg':>10} | {'us/exacta post-churn':>20}")
print("-" * 82)
for r in resultados:
    print(f"{r['nombre']:<16} | {r['n']:>8} | {r['churn_ops']:>9} | {r['ops_por_seg']:>10.1f} | {r['us_exacta_post_churn']:>20.2f}")

# sanity check: el churn no deberia degradar la busqueda mas de un orden
# de magnitud -- si lo hiciera, indicaria que el rebalanceo (o, en el
# clustered, la reorganizacion de SequentialFile) no esta funcionando
for r in resultados:
    assert r["us_exacta_post_churn"] < r["us_exacta"] * 10 + 500, (
        f"{r['nombre']} (N={r['n']}): la busqueda exacta se degrado demasiado tras el churn "
        f"({r['us_exacta']:.2f}us -> {r['us_exacta_post_churn']:.2f}us)"
    )

# se vuelcan los resultados crudos a JSON para que plot_index_benchmark.py
# grafique exactamente esta corrida, en vez de tener que repetirla entera
# (y arriesgarse a numeros levemente distintos por ruido de medicion)
RESULTADOS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados.json")
with open(RESULTADOS_JSON, "w", encoding="utf-8") as f:
    json.dump(resultados, f, indent=2)

print("\nOK: benchmark de indices completado para N =", SIZES)
