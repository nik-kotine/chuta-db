# Benchmark de organizaciones de archivo: Heap File vs. Sequential File.
#
# Mide las dos organizaciones en construccion, espacio, consultas
# (clave / rango / recorrido en orden), eliminacion y reorganizacion.
import json
import os
import random
import struct
import sys
import time
from statistics import mean

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.files.heap_file import HeapFile
from storage.files.sequential_file import (FILE_HEADER_FORMAT, FILE_HEADER_SIZE,
                                           SequentialFile)

PAGE_SIZE = 4096
BUFFER_FRAMES = 50
HEAP_HEADER_SIZE = 10
SCHEMA = ["integer", "integer"]

REPEATS = 3
SAMPLE_SIZE = 20   # claves muestreadas para busqueda exacta
RANGE_SAMPLES = 10  # puntos de inicio para la busqueda por rango
DELETE_SAMPLE = 50  # eliminaciones por clave cronometradas una a una
BORRADO_MASIVO = 0.3  # fraccion del archivo que se borra para medir espacio
REINSERT_SAMPLE = 300  # reinserciones cronometradas sobre el archivo ya borrado
APPEND_SAMPLE = 300    # inserciones con clave mayor a todas las existentes

# El heap inserta en O(1) amortizado y el secuencial en ~220 us/registro
# (mantiene el orden y reorganiza cada vez que el overflow pasa el 30%),
# asi que el secuencial domina el tiempo total de la corrida. Con los
# tamanos por defecto tarda ~2-3 minutos. Para una corrida rapida:
#   python -m benchmark.files.file_benchmark 500 2000
SIZES = [int(a) for a in sys.argv[1:]] or [1_000, 5_000, 20_000]

HEAP_FILE = "bench_heap.bin"
SEQ_FILE = "bench_seq.bin"
ARCHIVOS = [HEAP_FILE, SEQ_FILE]


def limpiar():
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)


class HeapAdapter:
    """
    Envuelve HeapFile para exponer la misma interfaz que SeqAdapter y
    poder medir ambos con el mismo codigo.

    El heap no indexa por clave: no tiene search(key) ni forma de acotar
    un rango. La unica manera correcta de responder esas consultas es un
    scan completo, y eso es justamente lo que hace aca -- a proposito,
    para que el benchmark muestre ese costo real frente al secuencial.
    """

    nombre = "Heap File"
    ordenado = False

    def __init__(self):
        fm = FileManager(HEAP_FILE, PAGE_SIZE, HEAP_HEADER_SIZE)
        self.bm = BufferManager(fm, BUFFER_FRAMES)
        self.f = HeapFile(HEAP_FILE, self.bm, record_format=SCHEMA)
        self.filename = HEAP_FILE

    def insert(self, params):
        return self.f.insert(params)

    def search(self, key):
        # sin indice: hay que recorrer todo el archivo
        return [params for _, params in self.f.scan() if params[0] == key]

    def range_search(self, inicio, fin):
        # tampoco se puede cortar antes: los registros no estan ordenados,
        # asi que una clave del rango puede estar en la ultima pagina
        encontrados = [params for _, params in self.f.scan()
                       if inicio <= params[0] <= fin]
        encontrados.sort(key=lambda p: p[0])
        return encontrados

    def scan_ordenado(self):
        # el scan sale en orden fisico (de insercion), hay que ordenarlo
        valores = [params for _, params in self.f.scan()]
        valores.sort(key=lambda p: p[0])
        return valores

    def rid_de(self, key):
        for rid, params in self.f.scan():
            if params[0] == key:
                return rid
        return None

    def delete_por_clave(self, key) -> bool:
        rid = self.rid_de(key)
        if rid is None:
            return False
        return self.f.delete(rid)

    def delete_masivo(self, claves, rids):
        # los RID del heap son estables: nada los mueve entre el insert y
        # el borrado, asi que se pueden reutilizar los guardados
        for k in claves:
            self.f.delete(rids[k])

    def reorganize(self):
        self.f.reorganize()

    def close(self):
        self.f.close()
        self.bm.close()


class SeqAdapter:
    """
    Envuelve SequentialFile con la misma interfaz que HeapAdapter.

    El archivo mantiene los registros ordenados por clave, asi que
    search() ubica la clave sin recorrer todo. Para el rango no hay un
    metodo publico, asi que se arma con los mismos ayudantes internos que
    usa search(): _find_neighbors ubica el arranque y _iter_records
    avanza en orden hasta pasarse del limite superior. Escanear todo el
    archivo tambien daria el resultado correcto, pero desperdiciaria
    justamente la propiedad que distingue a esta organizacion.
    """

    nombre = "Sequential File"
    ordenado = True

    def __init__(self):
        # mismo setup que hace Table para una tabla "sequential": header
        # del archivo + una pagina de overflow inicial
        with open(SEQ_FILE, "wb") as fh:
            fh.write(struct.pack(FILE_HEADER_FORMAT, 0, -1, 0, 0, 1, 0))
            fh.write(b"\x00" * PAGE_SIZE)

        fm = FileManager(SEQ_FILE, PAGE_SIZE, FILE_HEADER_SIZE)
        self.bm = BufferManager(fm, BUFFER_FRAMES)
        self.f = SequentialFile(self.bm, PAGE_SIZE, SCHEMA)
        self.filename = SEQ_FILE

    def insert(self, params):
        return self.f.insert(params)

    def search(self, key):
        return [list(r.params) for r in self.f.search(key)]

    def range_search(self, inicio, fin):
        _, rid = self.f._find_neighbors(inicio, duplicates_after=False)
        if rid is None:
            return []
        encontrados = []
        for _, record in self.f._iter_records(rid):
            clave = record.params[self.f.key_index]
            if clave > fin:
                break                       # ordenado: se puede cortar aca
            if clave >= inicio and not record.deleted:
                encontrados.append(list(record.params))
        return encontrados

    def scan_ordenado(self):
        # el scan ya sale ordenado por clave, no hace falta ordenar nada
        return [params for _, params in self.f.scan()]

    def delete_por_clave(self, key) -> bool:
        return self.f.delete_by_key(key)

    def delete_masivo(self, claves, rids):
        # OJO: aca no se pueden usar los RID guardados al insertar. El
        # archivo reorganiza solo cuando el overflow o el espacio muerto
        # pasan el 30%, y eso reubica los registros: un RID viejo apunta a
        # otra fila. Se borra por clave, que es la via correcta.
        for k in claves:
            self.f.delete_by_key(k)

    def reorganize(self):
        self.f.reorganize()

    def close(self):
        self.f.close()
        self.bm.close()


ORGANIZACIONES = [HeapAdapter, SeqAdapter]


def construir(cls, claves):
    """Crea el archivo e inserta las claves. Devuelve (adapter, rids)."""
    limpiar()
    org = cls()
    rids = {}
    for k in claves:
        rids[k] = org.insert([k, k * 2])
    return org, rids


def medir_construccion(cls, n):
    tiempos = []
    for _ in range(REPEATS):
        claves = list(range(n))
        random.shuffle(claves)
        limpiar()
        org = cls()

        t0 = time.perf_counter()
        for k in claves:
            org.insert([k, k * 2])
        tiempos.append(time.perf_counter() - t0)

        org.close()
    limpiar()
    return mean(tiempos)


def medir_todo(cls, n):
    nombre = cls.nombre
    tiempo_construccion = medir_construccion(cls, n)

    # se construye una vez mas y se deja vivo para el resto de medidas
    claves = list(range(n))
    random.shuffle(claves)
    org, rids = construir(cls, claves)

    espacio_kb = os.path.getsize(org.filename) / 1024
    bytes_por_registro = os.path.getsize(org.filename) / n

    # --- busqueda por clave ---
    muestra = random.sample(range(n), min(SAMPLE_SIZE, n))
    tiempos = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for k in muestra:
            encontrados = org.search(k)
        tiempos.append((time.perf_counter() - t0) / len(muestra))
    us_clave = mean(tiempos) * 1_000_000
    assert len(encontrados) == 1, f"{nombre}: search deberia devolver 1 registro, devolvio {len(encontrados)}"

    # --- busqueda por rango (ancho ~1% de n) ---
    ancho = max(5, n // 100)
    inicios = random.sample(range(max(1, n - ancho)), min(RANGE_SAMPLES, n))
    tiempos = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        for a in inicios:
            del_rango = org.range_search(a, a + ancho)
        tiempos.append((time.perf_counter() - t0) / len(inicios))
    us_rango = mean(tiempos) * 1_000_000
    esperados = [[k, k * 2] for k in range(inicios[-1], min(inicios[-1] + ancho, n - 1) + 1)]
    assert del_rango == esperados, f"{nombre}: el rango devolvio {len(del_rango)} registros, se esperaban {len(esperados)}"

    # --- recorrido completo en orden de clave ---
    tiempos = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        en_orden = org.scan_ordenado()
        tiempos.append(time.perf_counter() - t0)
    ms_orden = mean(tiempos) * 1000
    assert len(en_orden) == n, f"{nombre}: el recorrido deberia devolver los {n} registros, devolvio {len(en_orden)}"
    solo_claves = [p[0] for p in en_orden]
    assert solo_claves == sorted(solo_claves), f"{nombre}: el recorrido no quedo ordenado por clave"

    # --- eliminacion por clave (lo que hace DELETE FROM t WHERE id = k) ---
    a_borrar = random.sample(list(rids.keys()), min(DELETE_SAMPLE, n))
    t0 = time.perf_counter()
    for k in a_borrar:
        assert org.delete_por_clave(k), f"{nombre}: no pudo borrar la clave {k}"
    us_delete = (time.perf_counter() - t0) / len(a_borrar) * 1_000_000
    for k in a_borrar:
        del rids[k]

    # --- borrado masivo: espacio antes/despues y reutilizacion ---
    # esta fase mide espacio, no tiempo, asi que cada organizacion borra
    # por la via que le corresponde (ver delete_masivo en los adapters)
    objetivo = int(n * BORRADO_MASIVO) - len(a_borrar)
    masivo = random.sample(list(rids.keys()), max(0, min(objetivo, len(rids))))
    org.delete_masivo(masivo, rids)
    for k in masivo:
        del rids[k]

    # se compara el conjunto de claves, no solo la cantidad: un borrado
    # que elimina la fila equivocada deja el total bien pero el contenido mal
    vivos = {params[0] for _, params in org.f.scan()}
    esperados = set(rids.keys())
    assert vivos == esperados, (
        f"{nombre}: el contenido tras el borrado no coincide "
        f"(sobran {sorted(vivos - esperados)[:5]}, faltan {sorted(esperados - vivos)[:5]})")
    esperado_vivos = len(esperados)

    kb_tras_borrar = os.path.getsize(org.filename) / 1024

    t0 = time.perf_counter()
    org.reorganize()
    ms_reorganize = (time.perf_counter() - t0) * 1000
    kb_tras_reorganize = os.path.getsize(org.filename) / 1024

    assert {params[0] for _, params in org.f.scan()} == esperados, \
        f"{nombre}: reorganize altero el contenido del archivo"

    # --- reutilizacion: se vuelven a insertar claves que se habian borrado ---
    # Cada organizacion recupera el espacio de forma distinta: el heap
    # recicla los slots muertos en los inserts siguientes (el archivo no
    # crece), el secuencial los compacta al reorganizar (el archivo se
    # achica). Se mide con una muestra fija y no con el 30% completo: en
    # el secuencial, insertar miles de claves seguidas sobre un archivo
    # recien compactado hace crecer la cadena de overflow y el costo por
    # insert sube con ella hasta que el reorganize automatico la vacia
    # -- eso se mide aparte, no conviene que domine esta medida.
    a_reinsertar = masivo[:REINSERT_SAMPLE]
    kb_antes_reinsertar = os.path.getsize(org.filename) / 1024
    t0 = time.perf_counter()
    for k in a_reinsertar:
        org.insert([k, k * 2])
    us_reinsert = (time.perf_counter() - t0) / max(1, len(a_reinsertar)) * 1_000_000
    kb_tras_reinsertar = os.path.getsize(org.filename) / 1024
    crecimiento_kb = kb_tras_reinsertar - kb_antes_reinsertar

    # --- insercion al final: claves mayores que todas las existentes ---
    # Es el patron tipico de una tabla con id autoincremental. Para el heap
    # da igual donde caiga la clave; para el secuencial no: la clave va
    # siempre despues del ultimo registro, las paginas principales estan
    # llenas, asi que cada insert se va a la cadena de overflow y su costo
    # sube con la longitud de esa cadena hasta que el reorganize la vacia.
    t0 = time.perf_counter()
    for i in range(APPEND_SAMPLE):
        org.insert([n * 10 + i, i])
    us_append = (time.perf_counter() - t0) / APPEND_SAMPLE * 1_000_000

    org.close()
    limpiar()

    return {
        "nombre": nombre,
        "n": n,
        "tiempo_construccion_ms": tiempo_construccion * 1000,
        "us_por_insert": tiempo_construccion / n * 1_000_000,
        "espacio_kb": espacio_kb,
        "bytes_por_registro": bytes_por_registro,
        "us_clave": us_clave,
        "us_rango": us_rango,
        "ms_orden": ms_orden,
        "us_delete": us_delete,
        "kb_tras_borrar": kb_tras_borrar,
        "ms_reorganize": ms_reorganize,
        "kb_tras_reorganize": kb_tras_reorganize,
        "us_reinsert": us_reinsert,
        "reinsertados": len(a_reinsertar),
        "kb_tras_reinsertar": kb_tras_reinsertar,
        "crecimiento_kb": crecimiento_kb,
        "us_append": us_append,
    }


resultados = []
for n in SIZES:
    for cls in ORGANIZACIONES:
        print(f"midiendo {cls.nombre} con N={n}...", end=" ", flush=True)
        resultados.append(medir_todo(cls, n))
        print("OK")

print("\n--- Construccion y espacio ---")
print(f"{'organizacion':<17} | {'N':>7} | {'constr. (ms)':>12} | {'us/insert':>9} | {'archivo (KB)':>12} | {'bytes/reg':>9}")
print("-" * 84)
for r in resultados:
    print(f"{r['nombre']:<17} | {r['n']:>7} | {r['tiempo_construccion_ms']:>12.1f} | "
          f"{r['us_por_insert']:>9.1f} | {r['espacio_kb']:>12.1f} | {r['bytes_por_registro']:>9.1f}")

print("\n--- Tiempo de consulta ---")
print(f"{'organizacion':<17} | {'N':>7} | {'us/clave':>10} | {'us/rango 1%':>12} | {'ms/orden completo':>18}")
print("-" * 76)
for r in resultados:
    print(f"{r['nombre']:<17} | {r['n']:>7} | {r['us_clave']:>10.1f} | "
          f"{r['us_rango']:>12.1f} | {r['ms_orden']:>18.2f}")

print("\n--- Eliminacion y reorganizacion ---")
print(f"{'organizacion':<17} | {'N':>7} | {'us/delete':>10} | {'KB tras borrar':>14} | {'reorg (ms)':>10} | {'KB tras reorg':>13}")
print("-" * 92)
for r in resultados:
    print(f"{r['nombre']:<17} | {r['n']:>7} | {r['us_delete']:>10.1f} | "
          f"{r['kb_tras_borrar']:>14.1f} | {r['ms_reorganize']:>10.1f} | {r['kb_tras_reorganize']:>13.1f}")

print("\n--- Reutilizacion del espacio liberado ---")
print(f"(se reinsertan {REINSERT_SAMPLE} claves borradas sobre el archivo ya reorganizado)")
print(f"{'organizacion':<17} | {'N':>7} | {'us/reinsert':>12} | {'KB final':>9} | {'crecio (KB)':>11}")
print("-" * 72)
for r in resultados:
    print(f"{r['nombre']:<17} | {r['n']:>7} | {r['us_reinsert']:>12.1f} | "
          f"{r['kb_tras_reinsertar']:>9.1f} | {r['crecimiento_kb']:>11.1f}")

print("\n--- Insercion al final (clave mayor que todas) ---")
print(f"{'organizacion':<17} | {'N':>7} | {'us/insert intercalado':>21} | {'us/insert al final':>18}")
print("-" * 74)
for r in resultados:
    print(f"{r['nombre']:<17} | {r['n']:>7} | {r['us_reinsert']:>21.1f} | {r['us_append']:>18.1f}")

# --- verificaciones de que los numeros tienen sentido ---
por_nombre = {}
for r in resultados:
    por_nombre.setdefault(r["nombre"], []).append(r)

for nombre, serie in por_nombre.items():
    serie.sort(key=lambda r: r["n"])

    # el espacio debe crecer con N, nunca al reves
    for antes, despues in zip(serie, serie[1:]):
        assert despues["espacio_kb"] > antes["espacio_kb"], (
            f"{nombre}: el archivo no crecio al pasar de N={antes['n']} a N={despues['n']}")

    # reorganizar no puede dejar el archivo mas grande
    for r in serie:
        assert r["kb_tras_reorganize"] <= r["kb_tras_borrar"] + 0.01, (
            f"{nombre} (N={r['n']}): reorganize agrando el archivo "
            f"({r['kb_tras_borrar']:.1f} KB -> {r['kb_tras_reorganize']:.1f} KB)")

    # borrar el 30% y reinsertar esa misma cantidad deja la misma cantidad
    # de registros vivos que al principio, asi que el archivo deberia
    # ocupar mas o menos lo mismo -- si crece mucho, el espacio de los
    # borrados quedo muerto en vez de recuperarse
    # El archivo nunca deberia terminar mas grande que en su pico, dado
    # que se borraron mas registros de los que se reinsertaron.
    for r in serie:
        assert r["kb_tras_reinsertar"] <= r["espacio_kb"] + 0.01, (
            f"{nombre} (N={r['n']}): el archivo quedo en {r['kb_tras_reinsertar']:.1f} KB, "
            f"mas que su pico de {r['espacio_kb']:.1f} KB, con menos registros vivos")

heap = por_nombre["Heap File"]
seq = por_nombre["Sequential File"]

# el heap no indexa: su busqueda por clave tiene que escalar con N,
# mientras que la del secuencial se mantiene mas o menos plana
assert heap[-1]["us_clave"] / heap[0]["us_clave"] > 2, (
    "la busqueda en el heap deberia crecer con N (es un scan completo)")
for h, s in zip(heap, seq):
    assert s["us_clave"] < h["us_clave"], (
        f"N={h['n']}: la busqueda por clave del secuencial ({s['us_clave']:.1f} us) "
        f"deberia ganarle al scan del heap ({h['us_clave']:.1f} us)")
    assert s["us_rango"] < h["us_rango"], (
        f"N={h['n']}: la busqueda por rango del secuencial ({s['us_rango']:.1f} us) "
        f"deberia ganarle a la del heap ({h['us_rango']:.1f} us)")
    assert h["us_por_insert"] < s["us_por_insert"], (
        f"N={h['n']}: el heap deberia insertar mas rapido que el secuencial")

RESULTADOS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados.json")
with open(RESULTADOS_JSON, "w", encoding="utf-8") as f:
    json.dump(resultados, f, indent=2)

print("\nOK: benchmark de organizaciones de archivo completado para N =", SIZES)
