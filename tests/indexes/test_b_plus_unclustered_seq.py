"""
Pruebas del indice B+ no agrupado sobre una tabla SequentialFile
(BPlusTreeUnclusteredSequential).

Un SequentialFile reubica sus filas al reorganizarse (cuando el overflow o
el desperdicio superan el 30%), asi que los RID guardados en las hojas dejan
de ser validos. La clase se defiende reconstruyendose cuando detecta que el
archivo se reorganizo. Estos tests fuerzan esos reorganizes (insertando los
suficientes registros para cruzar el umbral de overflow) y, despues de cada
operacion, verifican el indice CONTRA UN SCAN de
la tabla: nunca contra valores escritos a mano. Asi, si el indice devuelve un
RID viejo que apunta a otra fila, la comparacion falla.

PYTHONPATH=. python3 tests/indexes/test_b_plus_unclustered_seq.py  (o run_all_tests.py)
"""

import os

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.files.sequential_file import FILE_HEADER_SIZE
from storage.table import Table
from indexes.b_plus_unclustered_sequential import BPlusTreeUnclusteredSequential
from indexes.b_plus_clustered import BPlusTreeClustered

DATA_FILE = "test_uncseq_data.dat"
INDEX_FILE = "test_uncseq_index.idx"
INDEX_FILE_2 = "test_uncseq_index2.idx"
CLUSTERED_FILE = "test_uncseq_clustered.idx"

# esquema (id, categoria, monto): el SequentialFile ordena por id (col 0),
# el indice secundario indexa otra columna, que es lo que lo hace NO agrupado.
SCHEMA = ["integer", "integer", "integer"]


def limpiar():
    for f in (DATA_FILE, INDEX_FILE, INDEX_FILE_2, CLUSTERED_FILE):
        if os.path.exists(f):
            os.remove(f)


def nueva_tabla(page_size=4096, frames=200, schema=SCHEMA, key_index=0):
    """Crea una tabla sequential vacia sobre DATA_FILE, igual que hace
    StorageManager.open_table: un FileManager fresco basta, SequentialFile
    arranca con el archivo recien creado.

    Se usa page_size=4096 (el real del motor): SequentialFile dispara
    reorganize por la PROPORCION de registros en overflow (>=30% tras 20
    registros), no por el llenado de la pagina, asi que los reorganizes se
    disparan igual sin necesidad de paginas chicas (que ademas tienen un
    bug de perdida de datos preexistente en el reorganize, ajeno a este
    indice)."""
    fm = FileManager(DATA_FILE, page_size, FILE_HEADER_SIZE)
    bm = BufferManager(fm, frames)
    table = Table(
        "t", schema, bm, file_type="sequential",
        key_index=key_index, file_manager=fm,
    )
    return table, bm


def nuevo_indice(table, col, index_file=INDEX_FILE):
    idx = BPlusTreeUnclusteredSequential(index_file, table.data_file, table.schema, col)
    table.attach_index(col, idx)
    return idx


def _refs(idx, key):
    r = idx.search(key)
    if r is None:
        return []
    return r if isinstance(r, list) else [r]


def _filas_por_scan(table, col):
    d = {}
    for _rid, params in table.scan():
        d.setdefault(params[col], []).append(tuple(params))
    return d


def verificar(table, idx, col):
    """El indice tiene que coincidir, clave por clave, con lo que ve un scan.

    Cada clave secundaria presente debe devolver EXACTAMENTE las filas vivas
    que tienen ese valor (comparadas como multiconjunto, para cubrir
    duplicados), y una clave inexistente no debe devolver nada."""
    esperado = _filas_por_scan(table, col)

    for key, filas in esperado.items():
        obtenidas = [table.data_file.fetch(ref) for ref in _refs(idx, key)]
        obtenidas = [tuple(f) for f in obtenidas if f is not None]
        assert sorted(obtenidas) == sorted(filas), (
            f"clave {key}: el indice devolvio {sorted(obtenidas)} "
            f"pero el scan dice {sorted(filas)}"
        )

    assert _refs(idx, 10**9) == [], "una clave inexistente no debe devolver refs"


def verificar_rango(table, idx, col, lo, hi):
    esperado = []
    for _rid, params in table.scan():
        if lo <= params[col] <= hi:
            esperado.append(tuple(params))

    obtenidas = []
    for _key, ref in idx.range_search(lo, hi):
        fila = table.data_file.fetch(ref)
        if fila is not None:
            obtenidas.append(tuple(fila))

    assert sorted(obtenidas) == sorted(esperado), (
        f"rango [{lo},{hi}]: indice {sorted(obtenidas)} vs scan {sorted(esperado)}"
    )


def test_insert_y_search_basico():
    limpiar()
    table, bm = nueva_tabla()
    idx = nuevo_indice(table, col=1)

    for id_, cat in [(3, 30), (1, 10), (2, 20), (5, 50), (4, 40)]:
        table.insert([id_, cat, id_ * 100])

    verificar(table, idx, col=1)
    assert table.data_file.reorganize_count == 0, "con paginas grandes no deberia reorganizar"
    print("test_insert_y_search_basico: OK")

    idx.close()
    bm.close()
    limpiar()


def test_reindex_tras_reorganize_en_insert():
    # al pasar de 20 registros el overflow supera el 30% y
    # SequentialFile.reorganize() se dispara varias veces durante los
    # inserts, invalidando los RID guardados en las hojas.
    limpiar()
    table, bm = nueva_tabla()
    idx = nuevo_indice(table, col=1)

    n = 250
    for id_ in range(1, n + 1):
        # categoria = id_ % 50 -> hay duplicados en la clave secundaria
        table.insert([id_, id_ % 50, id_ * 2])
        if id_ % 25 == 0:
            verificar(table, idx, col=1)

    assert table.data_file.reorganize_count > 0, "el test no logro forzar ningun reorganize"
    verificar(table, idx, col=1)
    print(
        f"test_reindex_tras_reorganize_en_insert: OK "
        f"({table.data_file.reorganize_count} reorganizes, el indice se reconstruyo solo)"
    )

    idx.close()
    bm.close()
    limpiar()


def test_duplicados():
    limpiar()
    table, bm = nueva_tabla()
    idx = nuevo_indice(table, col=1)

    # muchas filas con la MISMA categoria (clave secundaria repetida)
    for id_ in range(1, 21):
        table.insert([id_, 7, id_])
    # y algunas con otra
    for id_ in range(21, 26):
        table.insert([id_, 9, id_])

    refs7 = _refs(idx, 7)
    assert len(refs7) == 20, f"se esperaban 20 filas con categoria 7, hubo {len(refs7)}"
    verificar(table, idx, col=1)
    print("test_duplicados: OK")

    idx.close()
    bm.close()
    limpiar()


def test_range_search():
    limpiar()
    table, bm = nueva_tabla()
    idx = nuevo_indice(table, col=1)

    for id_ in range(1, 121):
        table.insert([id_, (id_ * 7) % 100, id_])

    assert table.data_file.reorganize_count > 0
    verificar_rango(table, idx, col=1, lo=20, hi=60)
    verificar_rango(table, idx, col=1, lo=0, hi=99)
    verificar_rango(table, idx, col=1, lo=95, hi=99)
    print("test_range_search: OK")

    idx.close()
    bm.close()
    limpiar()


def test_delete_dispara_reorganize():
    # inserta, despues borra ~40% a traves de Table.delete(rid): ese borrado
    # cruza WASTED_RATIO y dispara reorganize mientras el indice secundario
    # esta enlazado. El indice tiene que quedar consistente igual.
    limpiar()
    table, bm = nueva_tabla()
    idx = nuevo_indice(table, col=1)

    n = 120
    for id_ in range(1, n + 1):
        table.insert([id_, id_ % 30, id_])
    verificar(table, idx, col=1)

    reorg_antes = table.data_file.reorganize_count

    # borra los id multiplos de 2 (60 filas). Se re-escanea en cada paso
    # porque un reorganize invalida los RID del scan anterior.
    objetivo = {id_ for id_ in range(1, n + 1) if id_ % 2 == 0}
    borrados = 0
    while objetivo:
        progreso = False
        for rid, params in list(table.scan()):
            if params[0] in objetivo:
                assert table.delete(rid) is True
                objetivo.discard(params[0])
                borrados += 1
                progreso = True
                break  # el scan puede haber quedado obsoleto por un reorganize
        assert progreso, "no se pudo borrar; el scan no encontro la fila objetivo"

    assert borrados == 60
    assert table.data_file.reorganize_count > reorg_antes, "el borrado no disparo reorganize"
    verificar(table, idx, col=1)

    # las categorias de los id pares ya no deben aparecer si no quedo ninguna
    for _rid, params in table.scan():
        assert params[0] % 2 == 1, "quedo una fila que debio borrarse"

    print(
        f"test_delete_dispara_reorganize: OK "
        f"({table.data_file.reorganize_count - reorg_antes} reorganizes durante el borrado)"
    )

    idx.close()
    bm.close()
    limpiar()


def test_persistencia():
    limpiar()
    table, bm = nueva_tabla()
    idx = nuevo_indice(table, col=1)

    for id_ in range(1, 101):
        table.insert([id_, id_ % 20, id_])
    verificar(table, idx, col=1)

    # snapshot de lo que deberia seguir viendose tras reabrir
    esperado = _filas_por_scan(table, col=1)

    idx.close()
    bm.close()

    # reabrir: nuevo FileManager/BufferManager, el indice recarga su root
    # header y el SequentialFile relee su header. reorganize_count arranca
    # en 0 en ambos, asi que no hay reindex espurio.
    table2, bm2 = nueva_tabla()
    idx2 = nuevo_indice(table2, col=1)

    for key, filas in esperado.items():
        obtenidas = [table2.data_file.fetch(ref) for ref in _refs(idx2, key)]
        obtenidas = [tuple(f) for f in obtenidas if f is not None]
        assert sorted(obtenidas) == sorted(filas), (key, obtenidas, filas)

    verificar(table2, idx2, col=1)
    print("test_persistencia: OK")

    idx2.close()
    bm2.close()
    limpiar()


def test_dos_indices_misma_tabla():
    # dos indices secundarios sobre columnas distintas del MISMO
    # SequentialFile: cada uno se reconstruye por su cuenta tras un reorganize.
    limpiar()
    table, bm = nueva_tabla()
    idx_cat = nuevo_indice(table, col=1, index_file=INDEX_FILE)
    idx_monto = nuevo_indice(table, col=2, index_file=INDEX_FILE_2)

    for id_ in range(1, 151):
        table.insert([id_, id_ % 25, id_ % 40])

    assert table.data_file.reorganize_count > 0
    verificar(table, idx_cat, col=1)
    verificar(table, idx_monto, col=2)
    print("test_dos_indices_misma_tabla: OK")

    idx_cat.close()
    idx_monto.close()
    bm.close()
    limpiar()


def test_convive_con_indice_clustered():
    # una tabla sequential con indice CLUSTERED en la PK (col 0) y uno
    # NO agrupado en otra columna (col 1). El insert pasa por el clustered,
    # que puede disparar reorganize; el secundario debe seguir consistente.
    limpiar()
    table, bm = nueva_tabla()
    clustered = BPlusTreeClustered(CLUSTERED_FILE, table.data_file, buffer_frames=200)
    clustered._reindex()
    table.set_clustered_index(clustered)
    idx = nuevo_indice(table, col=1)

    # inserta desordenado a proposito
    orden = list(range(1, 141))
    orden = orden[::2] + orden[1::2]
    for id_ in orden:
        table.insert([id_, id_ % 30, id_])

    assert table.data_file.reorganize_count > 0
    verificar(table, idx, col=1)

    # y el clustered sigue sirviendo la PK
    for id_ in [1, 70, 140]:
        ref = clustered.search(id_)
        assert ref is not None
        assert clustered._fetch_record(ref) == (id_, id_ % 30, id_)

    print("test_convive_con_indice_clustered: OK")

    idx.close()
    clustered.close()
    bm.close()
    limpiar()


def test_iter_ordered_asc_y_desc_tras_reorganize():
    # iter_ordered() es lo que usa el ORDER BY por indice (ASC y DESC). Tras
    # varios reorganizes, los RID de las hojas quedan viejos; el override de
    # iter_ordered debe reindexar antes de recorrer, si no _fetch_record
    # devolveria filas equivocadas. Se verifica contra un scan de la tabla.
    limpiar()
    table, bm = nueva_tabla()
    idx = nuevo_indice(table, col=1)

    # categoria = (id*7)%100 -> hay duplicados y el orden por categoria NO
    # coincide con el orden de insercion
    for id_ in range(1, 151):
        table.insert([id_, (id_ * 7) % 100, id_])

    assert table.data_file.reorganize_count > 0, "el test no forzo ningun reorganize"

    esperado = sorted(tuple(params) for _rid, params in table.scan())

    # ascendente: las claves (col 1) no decrecen, y las filas resueltas por RID
    # coinciden (como multiconjunto) con las de la tabla
    asc = [(key, table.data_file.fetch(ref)) for key, ref in idx.iter_ordered()]
    claves_asc = [key for key, _ in asc]
    assert claves_asc == sorted(claves_asc), "iter_ordered() no vino ascendente por clave"
    filas_asc = sorted(tuple(fila) for _key, fila in asc if fila is not None)
    assert filas_asc == esperado, "iter_ordered() ascendente no resolvio las filas correctas"

    # descendente: mismas entradas al reves (claves no crecientes)
    desc = [(key, table.data_file.fetch(ref)) for key, ref in idx.iter_ordered(reverse=True)]
    claves_desc = [key for key, _ in desc]
    assert claves_desc == sorted(claves_desc, reverse=True), "iter_ordered(reverse=True) no vino descendente"
    filas_desc = sorted(tuple(fila) for _key, fila in desc if fila is not None)
    assert filas_desc == esperado, "iter_ordered() descendente no resolvio las filas correctas"

    # asc y desc tienen que ser exactamente el mismo conjunto, invertido
    assert claves_desc == claves_asc[::-1], "asc y desc no son espejos"

    print("test_iter_ordered_asc_y_desc_tras_reorganize: OK")

    idx.close()
    bm.close()
    limpiar()


tests = [
    test_insert_y_search_basico,
    test_reindex_tras_reorganize_en_insert,
    test_duplicados,
    test_range_search,
    test_delete_dispara_reorganize,
    test_persistencia,
    test_dos_indices_misma_tabla,
    test_convive_con_indice_clustered,
    test_iter_ordered_asc_y_desc_tras_reorganize,
]

for test in tests:
    print(f"Corriendo {test.__name__}...")
    test()

print(f"\n{len(tests)} tests pasaron.")
