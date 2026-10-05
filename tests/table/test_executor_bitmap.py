"""
Pruebas del executor con el indice de BITMAP: creacion por SQL, uso del
scan por bitmap para=, <, BETWEEN y conjunciones AND/OR, ausencia de barrido
de tabla, combinacion con otros indices y persistencia entre sesiones.
PYTHONPATH=. python3 tests/table/test_executor_bitmap.py  (o run_all_tests.py)
"""

import os

from parser.scanner import Scanner
from parser import Parser
from storage.storage_manager import StorageManager
from parser.executor import ExecuteVisitor, ExecutionError
from indexes.bitmap_index import BitmapIndex

ARCHIVOS = [
    "sys_tables.dat", "sys_columns.dat", "sys_indexes.dat",
    "ventas.dat", "emp.dat", "dept.dat",

    "idx_ventas_pais_unclustered.idx",
    "idx_ventas_cliente_unclustered.idx",
    "idx_ventas_id_unclustered.idx",
    "idx_ventas_monto_unclustered.idx",
    "idx_emp_nombre_unclustered.idx",

    "idx_ventas_pais_hash.idx",

    "idx_ventas_pais_bitmap.idx",
    "idx_ventas_cliente_bitmap.idx",
    "idx_ventas_id_bitmap.idx",
]

PAISES = ["AR", "BR", "MX"]
CLIENTES = ["Ana", "Beto", "Caro"]


def limpiar():
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)


def correr(sm, sql):
    programa = Parser(Scanner(sql)).parse_p()
    return ExecuteVisitor(sm).ejecutar(programa)


def _prohibir_scan(tabla):
    """Como en el resto de las pruebas del executor: si una consulta con
    indice llega a barrer la tabla, el test revienta."""
    llamadas = {"n": 0}

    def bomba():
        llamadas["n"] += 1
        raise AssertionError("la consulta barrio la tabla en vez de usar el indice")

    original = tabla.scan
    tabla.scan = bomba
    return llamadas, original


def _nodos(resultado):
    """Nodos del plan sin el de la sentencia, que siempre va primero."""
    return [p["node"] for p in resultado.plan if p["node"] != "SELECT"]


def _nodo_bitmap(resultado):
    return next(p for p in resultado.plan if p["node"] == "BITMAP INDEX SCAN")


def _crear_ventas(sm, filas=40, indices=("pais",), tipos=None):
    """Tabla de prueba: `filas` filas de 4 columnas (id, cliente, monto,
    pais). El pais y el cliente se reparten en 3 valores, asi que son
    buenos candidatos a un indice de bitmap."""
    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), "
               "monto FLOAT, pais VARCHAR(20)) USING HEAP;")
    for i in range(filas):
        res = correr(sm, f"INSERT INTO ventas VALUES ({i + 1}, "
                         f"'{CLIENTES[i % 3]}', {float(i)}, "
                         f"'{PAISES[i % 3]}');")[0]
        assert res.mensaje == "1 fila insertada", res.mensaje
    for columna in indices:
        tipo = (tipos or {}).get(columna, "BITMAP")
        res = correr(sm, f"CREATE INDEX ON ventas ({columna}) USING {tipo};")[0]
        assert f"{tipo} no agrupado" in res.mensaje or tipo == "BTREE", res.mensaje
    return sm.open_table("ventas")


def _ids(paises, n=40):
    """Ids (1..n) que quedaron en alguno de los paises dados."""
    return sorted(i + 1 for i in range(n) if PAISES[i % 3] in paises)


def test_create_index_bitmap_crea_el_indice():
    limpiar()
    sm = StorageManager()
    _crear_ventas(sm, filas=9, indices=())

    res = correr(sm, "CREATE INDEX ON ventas (pais) USING BITMAP;")[0]
    assert "BITMAP" in res.mensaje and "ventas(pais)" in res.mensaje, res.mensaje

    tabla = sm.open_table("ventas")
    pos = tabla.column_index("pais")
    indices = tabla.secondary_indexes.get(pos, [])
    assert len(indices) == 1 and isinstance(indices[0], BitmapIndex), indices
    indice = indices[0]
    # el indice quedo poblado con las filas que ya existian
    assert indice.key_count() == 3, indice.keys()
    for i, pais in enumerate(PAISES):
        assert indice.search(pais).count() == 3, pais
    assert indice.search("ZZ").count() == 0

    sm.close()
    limpiar()
    print("test_create_index_bitmap_crea_el_indice: OK")


def test_select_usa_bitmap_por_punto():
    limpiar()
    sm = StorageManager()
    tabla = _crear_ventas(sm)

    llamadas, original = _prohibir_scan(tabla)
    try:
        res = correr(sm, "SELECT cliente FROM ventas WHERE pais = 'AR';")[0]
        esperado = _ids(["AR"])
        assert sorted(f[0] for f in res.filas) == sorted(
            CLIENTES[(i - 1) % 3] for i in esperado
        ), res.filas
        assert _nodos(res) == ["BITMAP INDEX SCAN", "OUTPUT"], res.plan

        nodo = _nodo_bitmap(res)
        assert nodo["matches"] == len(esperado) == 14, nodo
        assert nodo["columns"] == ["pais"], nodo
        assert nodo["access"] == "EQ", nodo
        assert nodo["heap_pages"] >= 1, nodo

        # valor ausente: el indice responde vacio sin tocar el heap
        res = correr(sm, "SELECT cliente FROM ventas WHERE pais = 'ZZ';")[0]
        assert res.filas == [], res.filas
        assert _nodo_bitmap(res)["matches"] == 0
        assert _nodo_bitmap(res)["heap_pages"] == 0
    finally:
        tabla.scan = original
    assert llamadas["n"] == 0, f"el bitmap debia servir, scan: {llamadas['n']}"

    sm.close()
    limpiar()
    print("test_select_usa_bitmap_por_punto: OK")


def test_select_usa_bitmap_por_rango_y_between():
    limpiar()
    sm = StorageManager()
    tabla = _crear_ventas(sm, indices=("pais", "id"))

    llamadas, original = _prohibir_scan(tabla)
    try:
        res = correr(sm, "SELECT cliente FROM ventas WHERE id BETWEEN 5 AND 8;")[0]
        assert sorted(f[0] for f in res.filas) == ["Ana", "Beto", "Beto", "Caro"], res.filas
        assert _nodos(res) == ["BITMAP INDEX SCAN", "OUTPUT"], res.plan
        assert _nodo_bitmap(res)["access"] == "RANGO"

        res = correr(sm, "SELECT id FROM ventas WHERE id >= 38;")[0]
        assert sorted(f[0] for f in res.filas) == [38, 39, 40], res.filas
        assert "BITMAP INDEX SCAN" in _nodos(res), res.plan

        res = correr(sm, "SELECT id FROM ventas WHERE id < 3;")[0]
        assert sorted(f[0] for f in res.filas) == [1, 2], res.filas

        # un rango que no toca ninguna fila
        res = correr(sm, "SELECT id FROM ventas WHERE id > 1000;")[0]
        assert res.filas == [], res.filas
        assert _nodo_bitmap(res)["matches"] == 0

        # rango sobre el id y a la vez igualdad sobre el pais: se cruzan
        res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR' AND id <= 9;")[0]
        assert sorted(f[0] for f in res.filas) == [1, 4, 7], res.filas
        assert sorted(_nodo_bitmap(res)["columns"]) == ["id", "pais"]
    finally:
        tabla.scan = original
    assert llamadas["n"] == 0, f"scan: {llamadas['n']}"

    sm.close()
    limpiar()
    print("test_select_usa_bitmap_por_rango_y_between: OK")


def test_conjunciones_and_se_combinan():
    limpiar()
    sm = StorageManager()
    # 30 filas donde pais y cliente se reparten en cruz: los dos filtros
    # juntos dejan filas de verdad
    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), "
               "monto FLOAT, pais VARCHAR(20)) USING HEAP;")
    for i in range(30):
        correr(sm, f"INSERT INTO ventas VALUES ({i + 1}, '{CLIENTES[i % 3]}', "
                   f"{float(i)}, '{PAISES[(i // 3) % 3]}');")
    correr(sm, "CREATE INDEX ON ventas (pais) USING BITMAP;")
    correr(sm, "CREATE INDEX ON ventas (cliente) USING BITMAP;")
    correr(sm, "CREATE INDEX ON ventas (id) USING BITMAP;")

    esperado = [
        i + 1 for i in range(30)
        if PAISES[(i // 3) % 3] == "AR" and CLIENTES[i % 3] == "Beto"
    ]
    assert len(esperado) == 4, esperado

    tabla = sm.open_table("ventas")
    llamadas, original = _prohibir_scan(tabla)
    try:
        res = correr(sm, "SELECT id, cliente, pais FROM ventas "
                         "WHERE pais = 'AR' AND cliente = 'Beto';")[0]
        assert sorted(f[0] for f in res.filas) == esperado, (res.filas, esperado)
        # un solo nodo de scan: los dos predicados se cruzaron en memoria
        assert _nodos(res) == ["BITMAP INDEX SCAN", "OUTPUT"], res.plan
        nodo = _nodo_bitmap(res)
        assert nodo["columns"] == ["pais", "cliente"], nodo
        assert nodo["matches"] == len(esperado), nodo

        # tres predicados sobre tres indices bitmap distintos
        res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR' "
                         "AND cliente = 'Beto' AND id >= 5;")[0]
        assert sorted(f[0] for f in res.filas) == [i for i in esperado if i >= 5]
        assert sorted(_nodo_bitmap(res)["columns"]) == ["cliente", "id", "pais"]

        # un AND con un valor ausente: la interseccion queda vacia
        res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR' "
                         "AND cliente = 'ZZZ';")[0]
        assert res.filas == [], res.filas
    finally:
        tabla.scan = original
    assert llamadas["n"] == 0, f"scan: {llamadas['n']}"

    sm.close()
    limpiar()
    print("test_conjacciones_and_se_combinan: OK")


def test_or_se_resuelve_con_union_de_mascaras():
    limpiar()
    sm = StorageManager()
    tabla = _crear_ventas(sm, indices=("pais", "id"))

    llamadas, original = _prohibir_scan(tabla)
    try:
        res = correr(sm, "SELECT id FROM ventas "
                         "WHERE pais = 'AR' OR pais = 'MX';")[0]
        esperado = _ids(["AR", "MX"])
        assert sorted(f[0] for f in res.filas) == esperado, res.filas
        nodo = _nodo_bitmap(res)
        assert nodo["matches"] == len(esperado) == 27, nodo
        # el plan declara las dos ramas que se unieron
        assert nodo["access"] == "EQ", nodo
        assert nodo["columns"] == ["pais", "pais"], nodo

        # OR con un valor ausente: la union se queda con la otra rama
        res = correr(sm, "SELECT id FROM ventas "
                         "WHERE pais = 'ZZ' OR pais = 'BR';")[0]
        assert sorted(f[0] for f in res.filas) == _ids(["BR"]), res.filas

        # OR totalmente ausente
        res = correr(sm, "SELECT id FROM ventas "
                         "WHERE pais = 'ZZ' OR pais = 'XX';")[0]
        assert res.filas == [], res.filas

        # tres ramas: una igualdad y un rango
        res = correr(sm, "SELECT id FROM ventas WHERE pais = 'BR' "
                         "OR id <= 2;")[0]
        esperado = sorted(set(_ids(["BR"])) | {1, 2})
        assert sorted(f[0] for f in res.filas) == esperado, res.filas
        assert _nodo_bitmap(res)["access"] == "EQ+RANGO", _nodo_bitmap(res)
        assert sorted(_nodo_bitmap(res)["columns"]) == ["id", "pais"]
    finally:
        tabla.scan = original
    assert llamadas["n"] == 0, f"scan: {llamadas['n']}"

    sm.close()
    limpiar()
    print("test_or_se_resuelve_con_union_de_mascaras: OK")


def test_cuando_el_bitmap_no_alcanza_cae_al_b_mas_hash():
    limpiar()
    sm = StorageManager()
    # bitmap en pais, B+ en id: los dos indices conviven sobre columnas distintas
    tabla = _crear_ventas(sm, filas=30, indices=("pais", "cliente", "id"),
                          tipos={"id": "BTREE"})

    # != no se puede resolver con una mascara de igualdad: el bitmap cede
    res = correr(sm, "SELECT id FROM ventas WHERE pais != 'AR';")[0]
    assert "BITMAP INDEX SCAN" not in _nodos(res), res.plan
    assert len(res.filas) == 20, len(res.filas)

    # un predicado sobre una columna sin indice tampoco usa bitmap
    res = correr(sm, "SELECT id FROM ventas WHERE monto > 25;")[0]
    assert _nodos(res) == ["SEQUENTIAL SCAN", "OUTPUT"], res.plan
    assert len(res.filas) == 4, len(res.filas)

    # un AND donde solo una parte tiene bitmap: el plan clasico responde con
    # el B+ del id, que es el unico que acota el rango
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR' AND id <= 6;")[0]
    assert "BITMAP INDEX SCAN" not in _nodos(res), res.plan
    assert _nodos(res) == ["INDEX SCAN", "OUTPUT"], res.plan
    assert res.plan[1]["column"] == "id", res.plan
    assert sorted(f[0] for f in res.filas) == [1, 4], res.filas

    # cuando TODO el WHERE lo pueden resolver los bitmaps, mandan ellos.
    # Con el reparto de la fixture, pais = AR implica cliente = Ana
    res = correr(sm, "SELECT id FROM ventas "
                     "WHERE pais = 'AR' AND cliente = 'Ana';")[0]
    nodo = _nodo_bitmap(res)
    assert nodo["matches"] == 10, nodo
    assert sorted(f[0] for f in res.filas) == _ids(["AR"], 30), res.filas
    # y si las dos mascaras no se cruzan, el resultado es vacio sin tocar
    # el heap
    res = correr(sm, "SELECT id FROM ventas "
                     "WHERE pais = 'AR' AND cliente = 'Caro';")[0]
    assert res.filas == [], res.filas
    assert _nodo_bitmap(res)["matches"] == 0

    sm.close()
    limpiar()
    print("test_cuando_el_bitmap_no_alcanza_cae_al_b_mas_hash: OK")


def test_bitmap_convive_con_b_mas_hash_en_la_misma_columna():
    limpiar()
    sm = StorageManager()
    tabla = _crear_ventas(sm, filas=30, indices=("pais",))
    # los tres tipos de indice sobre la MISMA columna
    correr(sm, "CREATE INDEX ON ventas (pais) USING BTREE;")
    correr(sm, "CREATE INDEX ON ventas (pais) USING HASH;")

    pos = tabla.column_index("pais")
    tipos = {type(idx).__name__ for idx in tabla.secondary_indexes[pos]}
    assert tipos == {"BitmapIndex", "BPlusTreeUnclustered", "HashIndex"}, tipos

    indice_bitmap = [i for i in tabla.secondary_indexes[pos]
                     if isinstance(i, BitmapIndex)][0]

    # el bitmap se sigue usando para las consultas por igualdad
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'BR';")[0]
    assert "BITMAP INDEX SCAN" in _nodos(res), res.plan
    assert sorted(f[0] for f in res.filas) == _ids(["BR"], 30), res.filas

    # el DELETE por SQL tiene que bajar el bit de los tres indices
    res = correr(sm, "DELETE FROM ventas WHERE pais = 'BR';")[0]
    assert res.mensaje.startswith(f"{len(_ids(['BR'], 30))} fila(s)"), res.mensaje
    assert indice_bitmap.search("BR").count() == 0, "el bitmap quedo con filas muertas"
    assert indice_bitmap.search("AR").count() == len(_ids(["AR"], 30))

    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'BR';")[0]
    assert res.filas == [], res.filas
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR';")[0]
    assert sorted(f[0] for f in res.filas) == _ids(["AR"], 30), res.filas

    sm.close()
    limpiar()
    print("test_bitmap_convive_con_b_mas_hash_en_la_misma_columna: OK")


def test_crud_a_traves_del_bitmap():
    """INSERT y DELETE hechos por SQL tienen que verse reflejados en el
    bitmap sin tocar el indice a mano."""
    limpiar()
    sm = StorageManager()
    tabla = _crear_ventas(sm)
    pos = tabla.column_index("pais")
    indice = [i for i in tabla.secondary_indexes[pos]
              if isinstance(i, BitmapIndex)][0]
    antes = indice.search("AR").count()
    assert antes == 14, antes

    # alta
    correr(sm, "INSERT INTO ventas VALUES (100, 'Dani', 999.0, 'AR');")
    assert indice.search("AR").count() == antes + 1, "el alta no llego al bitmap"
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR';")[0]
    assert 100 in [f[0] for f in res.filas], res.filas

    # baja filtrando por la OTRA columna: el bit se apaga igual
    res = correr(sm, "DELETE FROM ventas WHERE id = 100;")[0]
    assert res.mensaje == "1 fila(s) eliminada(s)", res.mensaje
    assert indice.search("AR").count() == antes, "la baja no llego al bitmap"

    # cuando se va la ultima fila de un valor, la clave desaparece
    for id_ar in _ids(["AR"]):
        correr(sm, f"DELETE FROM ventas WHERE id = {id_ar};")
    assert indice.search("AR").count() == 0
    assert "AR" not in indice.keys(), indice.keys()
    assert indice.key_count() == 2, indice.keys()

    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR';")[0]
    assert res.filas == [], res.filas

    sm.close()
    limpiar()
    print("test_crud_a_traves_del_bitmap: OK")


def test_el_bitmap_ahorra_paginas_del_heap():
    """El punto del bitmap: para un filtro muy selectivo se tocan muchas
    menos paginas del heap que barriendo la tabla entera."""
    limpiar()
    sm = StorageManager()
    # casi todas las filas son AR: el caso donde el filtro es muy selectivo
    # y el bitmap se gana la comparacion
    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), "
               "monto FLOAT, pais VARCHAR(20)) USING HEAP;")
    for i in range(1500):
        correr(sm, f"INSERT INTO ventas VALUES ({i + 1}, 'C', 1.0, 'AR');")
    for i in range(3):
        correr(sm, f"INSERT INTO ventas VALUES ({9000 + i}, 'X', 1.0, 'MX');")

    # sin bitmap todavia: hay que recorrer el heap entero
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'MX';")[0]
    assert sorted(f[0] for f in res.filas) == [9000, 9001, 9002], res.filas
    assert _nodos(res) == ["SEQUENTIAL SCAN", "OUTPUT"], res.plan
    total_filas = len(correr(sm, "SELECT id FROM ventas;")[0].filas)
    assert total_filas == 1503, total_filas

    correr(sm, "CREATE INDEX ON ventas (pais) USING BITMAP;")
    tabla = sm.open_table("ventas")
    indice = [i for i in tabla.secondary_indexes[tabla.column_index("pais")]
              if isinstance(i, BitmapIndex)][0]
    assert indice.search("MX").count() == 3

    # con bitmap: el indice acota a 3 filas en una sola pagina del heap
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'MX';")[0]
    assert sorted(f[0] for f in res.filas) == [9000, 9001, 9002], res.filas
    nodo = _nodo_bitmap(res)
    assert nodo["matches"] == 3 and nodo["heap_pages"] == 1, nodo
    assert nodo["matches"] < total_filas / 100, (nodo["matches"], total_filas)

    stats = indice.stats()
    assert stats["keys"] == 2, stats
    # 1500 filas de un valor y 3 de otro: el indice entero (que incluye la
    # mascara del valor grande) sigue entrando en una sola pagina
    assert stats["data_pages"] == 1, stats
    assert stats["bytes"] <= 4096 + 128, stats

    # un AND de dos valores que no se cruzan: la interseccion se hace en
    # RAM y la respuesta sale sin tocar el heap
    res = correr(sm, "SELECT id FROM ventas "
                     "WHERE pais = 'AR' AND pais = 'MX';")[0]
    assert res.filas == [], res.filas
    assert _nodo_bitmap(res)["heap_pages"] == 0, res.plan

    # mientras que el valor grande si toca casi todo el heap: el bitmap
    # sirve cuando el filtro es selectivo, no siempre
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR';")[0]
    nodo = _nodo_bitmap(res)
    assert nodo["matches"] == 1500, nodo
    assert nodo["heap_pages"] > 1, nodo

    sm.close()
    limpiar()
    print("test_el_bitmap_ahorra_paginas_del_heap: OK")


def test_create_index_bitmap_rechaza_usos_invalidos():
    limpiar()
    sm = StorageManager()

    # BITMAP CLUSTERED no tiene sentido (el parser lo corta)
    try:
        correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20)) "
                   "USING HEAP;")
        correr(sm, "CREATE INDEX ON ventas (cliente) USING BITMAP CLUSTERED;")
    except Exception as e:
        assert "BITMAP" in str(e) and "CLUSTERED" in str(e), e
    else:
        raise AssertionError("BITMAP CLUSTERED deberia rechazarse")

    # y solo va sobre tablas Heap
    try:
        correr(sm, "CREATE TABLE emp (id INT PRIMARY KEY, nombre VARCHAR(20)) "
                   "USING SEQUENTIAL;")
        correr(sm, "CREATE INDEX ON emp (nombre) USING BITMAP;")
    except ExecutionError as e:
        assert "BITMAP" in str(e) and "HEAP" in str(e), e
    else:
        raise AssertionError("un bitmap sobre una tabla Sequential deberia rechazarse")

    # el hash sigue exigiendo Heap (apunta a filas del heap, no a un
    # archivo ordenado)
    try:
        correr(sm, "CREATE INDEX ON emp (nombre) USING HASH;")
    except ExecutionError as e:
        assert "HEAP" in str(e), e
    else:
        raise AssertionError("un hash sobre Sequential deberia rechazarse")

    # en cambio el B+ no agrupado SI se acepta sobre Sequential
    # (BPlusTreeUnclusteredSequential se reconstruye tras cada reorganize)
    res = correr(sm, "CREATE INDEX ON emp (nombre) USING BTREE;")[0]
    assert "no agrupado" in res.mensaje, res.mensaje

    # un tipo de indice desconocido sigue siendo un error de sintaxis
    try:
        correr(sm, "CREATE INDEX ON ventas (cliente) USING TRIE;")
    except Exception as e:
        assert "BTREE" in str(e), e
    else:
        raise AssertionError("USING TRIE deberia rechazarse")

    sm.close()
    limpiar()
    print("test_create_index_bitmap_rechaza_usos_invalidos: OK")


def test_persistencia_del_bitmap_entre_sesiones():
    limpiar()
    sm = StorageManager()
    _crear_ventas(sm)
    sm.close()

    sm2 = StorageManager()
    tabla = sm2.open_table("ventas")
    pos = tabla.column_index("pais")
    indices = tabla.secondary_indexes.get(pos, [])
    assert len(indices) == 1 and isinstance(indices[0], BitmapIndex), indices
    indice = indices[0]
    assert indice.key_count() == 3, indice.keys()
    assert indice.search("AR").count() == 14

    # el indice se reabre y la consulta sigue yendo por bitmap
    res = correr(sm2, "SELECT id FROM ventas WHERE pais = 'AR';")[0]
    assert sorted(f[0] for f in res.filas) == _ids(["AR"]), res.filas
    assert "BITMAP INDEX SCAN" in _nodos(res), res.plan

    # altas y bajas de la sesion nueva siguen actualizandolo
    correr(sm2, "INSERT INTO ventas VALUES (77, 'NUE', 5.0, 'AR');")
    assert indice.search("AR").count() == 15
    res = correr(sm2, "DELETE FROM ventas WHERE pais = 'AR';")[0]
    assert "15 fila(s)" in res.mensaje, res.mensaje
    assert indice.search("AR").count() == 0
    assert "AR" not in indice.keys(), indice.keys()

    res = correr(sm2, "SELECT id FROM ventas WHERE pais = 'AR';")[0]
    assert res.filas == [], res.filas

    sm2.close()
    limpiar()
    print("test_persistencia_del_bitmap_entre_sesiones: OK")


def test_select_star_y_proyeccion_con_bitmap():
    limpiar()
    sm = StorageManager()
    _crear_ventas(sm)

    res = correr(sm, "SELECT * FROM ventas WHERE pais = 'AR';")[0]
    assert res.columnas == ["id", "cliente", "monto", "pais"], res.columnas
    assert len(res.filas) == 14
    assert all(len(f) == 4 for f in res.filas)

    # el resultado sale ordenado por RID (que es el orden del heap), no por
    # el orden de las claves del indice
    ids = [f[0] for f in res.filas]
    assert ids == sorted(ids), ids

    # LIMIT se aplica sobre el stream del scan con bitmap
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR' LIMIT 5;")[0]
    assert [f[0] for f in res.filas] == ids[:5], res.filas

    # ORDER BY + bitmap: el sort trabaja sobre lo que devuelve la mascara
    res = correr(sm, "SELECT id FROM ventas WHERE pais = 'AR' "
                     "ORDER BY id DESC;")[0]
    assert [f[0] for f in res.filas] == sorted(ids, reverse=True), res.filas

    sm.close()
    limpiar()
    print("test_select_star_y_proyeccion_con_bitmap: OK")


if __name__ == "__main__":
    print("=== PROBANDO EL SCAN CON INDICE DE BITMAP ===")
    test_create_index_bitmap_crea_el_indice()
    test_select_usa_bitmap_por_punto()
    test_select_usa_bitmap_por_rango_y_between()
    test_conjunciones_and_se_combinan()
    test_or_se_resuelve_con_union_de_mascaras()
    test_cuando_el_bitmap_no_alcanza_cae_al_b_mas_hash()
    test_bitmap_convive_con_b_mas_hash_en_la_misma_columna()
    test_crud_a_traves_del_bitmap()
    test_el_bitmap_ahorra_paginas_del_heap()
    test_create_index_bitmap_rechaza_usos_invalidos()
    test_persistencia_del_bitmap_entre_sesiones()
    test_select_star_y_proyeccion_con_bitmap()
    print("Todas las pruebas del executor con bitmap pasaron")
