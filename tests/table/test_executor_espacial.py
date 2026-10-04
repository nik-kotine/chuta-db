"""
Pruebas de las extensiones de la Parte 2 del parser:
  - DROP TABLE
  - tipo de columna POINT y literales POINT(x, y)
  - distancia(a, b) en WHERE y en ORDER BY
  - EXPLAIN y EXPLAIN ANALYZE

Las distancias se contrastan contra el mismo calculo hecho en Python,
para que el test falle si el motor devuelve otro conjunto o en otro orden.
"""

import math
import os
import random
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RAIZ, "parser"))

from scanner import Scanner
from parser import Parser
from storage.storage_manager import StorageManager
from executor import ExecuteVisitor, ExecutionError, _haversine


ARCHIVOS = ["sys_tables.dat", "sys_columns.dat", "sys_indexes.dat",
            "tiendas.dat", "rutas.dat", "seqp.dat"]

# Lima en orden PostGIS: (longitud, latitud)
LIMA = (-77.0428, -12.0464)


def limpiar():
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)


def correr(sm, sql):
    return ExecuteVisitor(sm).ejecutar(Parser(Scanner(sql)).parse_p())


def _sembrar(sm, n=120, semilla=7):
    """
    Crea 'tiendas' con n puntos alrededor de Lima.
    Devuelve {id: (longitud, latitud)}.
    """
    correr(sm, "CREATE TABLE tiendas (id INT PRIMARY KEY, nombre VARCHAR(20), "
               "ubicacion POINT) USING HEAP;")
    random.seed(semilla)
    puntos = {}
    for i in range(n):
        x = round(LIMA[0] + random.uniform(-0.2, 0.2), 6)   # longitud
        y = round(LIMA[1] + random.uniform(-0.2, 0.2), 6)   # latitud
        puntos[i] = (x, y)
        correr(sm, f"INSERT INTO tiendas VALUES ({i}, 'T{i}', POINT({x}, {y}));")
    return puntos


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def test_point_round_trip():
    """Un POINT debe volver del disco exactamente como se guardo."""
    limpiar()
    sm = StorageManager()
    puntos = _sembrar(sm, n=40)

    filas = correr(sm, "SELECT id, ubicacion FROM tiendas;")[0].filas
    guardado = {f[0]: f[1] for f in filas}

    assert len(guardado) == len(puntos)
    for i, esperado in puntos.items():
        obtenido = guardado[i]
        assert isinstance(obtenido, tuple) and len(obtenido) == 2, obtenido
        assert abs(obtenido[0] - esperado[0]) < 1e-9
        assert abs(obtenido[1] - esperado[1]) < 1e-9

    sm.close()
    limpiar()
    print("test_point_round_trip: OK")


def test_filtro_por_distancia():
    """WHERE distancia(...) < r debe dar el mismo conjunto que el calculo directo."""
    limpiar()
    sm = StorageManager()
    puntos = _sembrar(sm)

    radio = 0.05
    esperado = {i for i, p in puntos.items() if _dist(p, LIMA) < radio}
    sql = (f"SELECT id FROM tiendas WHERE "
           f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) < {radio};")
    obtenido = {f[0] for f in correr(sm, sql)[0].filas}

    assert esperado, "el conjunto de prueba no deberia quedar vacio"
    assert obtenido == esperado, f"sobran {obtenido - esperado}, faltan {esperado - obtenido}"

    sm.close()
    limpiar()
    print("test_filtro_por_distancia: OK")


def test_orden_por_distancia():
    """ORDER BY distancia(...) LIMIT k: los k mas cercanos, en orden."""
    limpiar()
    sm = StorageManager()
    puntos = _sembrar(sm)

    k = 10
    esperado = [i for i, _ in sorted(puntos.items(), key=lambda kv: _dist(kv[1], LIMA))][:k]
    sql = (f"SELECT id FROM tiendas ORDER BY "
           f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) LIMIT {k};")
    obtenido = [f[0] for f in correr(sm, sql)[0].filas]
    assert obtenido == esperado, f"{obtenido} != {esperado}"

    # DESC: los mas lejanos
    esperado_desc = [i for i, _ in sorted(puntos.items(),
                                          key=lambda kv: -_dist(kv[1], LIMA))][:3]
    sql = (f"SELECT id FROM tiendas ORDER BY "
           f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) DESC LIMIT 3;")
    obtenido_desc = [f[0] for f in correr(sm, sql)[0].filas]
    assert obtenido_desc == esperado_desc, f"{obtenido_desc} != {esperado_desc}"

    sm.close()
    limpiar()
    print("test_orden_por_distancia: OK")


def test_distancia_entre_columnas():
    """distancia(a, b) tambien acepta dos columnas, no solo un literal."""
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE rutas (id INT PRIMARY KEY, origen POINT, "
               "destino POINT) USING HEAP;")
    correr(sm, "INSERT INTO rutas VALUES (1, POINT(0.0, 0.0), POINT(3.0, 4.0));")
    correr(sm, "INSERT INTO rutas VALUES (2, POINT(0.0, 0.0), POINT(1.0, 0.0));")

    # la ruta 1 mide 5 (triangulo 3-4-5), la ruta 2 mide 1
    filas = correr(sm, "SELECT id FROM rutas WHERE distancia(origen, destino) > 2;")[0].filas
    assert [f[0] for f in filas] == [1]

    filas = correr(sm, "SELECT id FROM rutas WHERE distancia(origen, destino) < 2;")[0].filas
    assert [f[0] for f in filas] == [2]

    sm.close()
    limpiar()
    print("test_distancia_entre_columnas: OK")


def test_point_en_sequential_y_persistencia():
    """POINT funciona en archivo secuencial y sobrevive al cierre de la base."""
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE seqp (id INT PRIMARY KEY, u POINT) USING SEQUENTIAL;")
    correr(sm, "INSERT INTO seqp VALUES (1, POINT(3.0, 4.0));")
    correr(sm, "INSERT INTO seqp VALUES (2, POINT(1.0, 1.0));")
    correr(sm, "UPDATE seqp SET u = POINT(9.0, 9.0) WHERE id = 2;")
    sm.close()

    sm2 = StorageManager()
    assert sm2.catalog.get_table_info("seqp")["schema"] == ["integer", "point"]
    filas = correr(sm2, "SELECT id, u FROM seqp;")[0].filas
    assert sorted(filas) == [[1, (3.0, 4.0)], [2, (9.0, 9.0)]], filas
    sm2.close()
    limpiar()
    print("test_point_en_sequential_y_persistencia: OK")


def test_distancia_geodesica():
    """
    Haversine en metros, contrastado con distancias reales conocidas y
    con la metrica euclidiana.
    """
    # ciudades en orden PostGIS (longitud, latitud)
    lima = (-77.0428, -12.0464)
    cusco = (-71.9675, -13.5319)
    arequipa = (-71.5375, -16.4090)

    # tolerancia del 1%: la Tierra no es una esfera perfecta
    assert abs(_haversine(*lima, *cusco) / 1000 - 574) < 6, _haversine(*lima, *cusco)
    assert abs(_haversine(*lima, *arequipa) / 1000 - 766) < 8
    assert _haversine(*lima, *lima) == 0.0

    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE tiendas (id INT PRIMARY KEY, nombre VARCHAR(12), "
               "ubicacion POINT) USING HEAP;")
    filas = [(1, "Centro", -77.0428, -12.0464),
             (2, "Miraflores", -77.0300, -12.1200),
             (3, "Callao", -77.1200, -12.0600),
             (4, "Cusco", -71.9675, -13.5319)]
    for i, nombre, x, y in filas:
        correr(sm, f"INSERT INTO tiendas VALUES ({i}, '{nombre}', POINT({x}, {y}));")

    # el radio del enunciado esta en metros: solo entra el punto exacto
    sql = ("SELECT id FROM tiendas WHERE distancia_geodesica"
           "(ubicacion, POINT(-77.0428, -12.0464)) < 5000;")
    assert [f[0] for f in correr(sm, sql)[0].filas] == [1]

    # con 20 km entran los tres de Lima, pero no Cusco
    sql = ("SELECT id FROM tiendas WHERE distancia_geodesica"
           "(ubicacion, POINT(-77.0428, -12.0464)) < 20000;")
    assert sorted(f[0] for f in correr(sm, sql)[0].filas) == [1, 2, 3]

    # el orden por cercania es el mismo con las dos metricas
    base = "SELECT id FROM tiendas ORDER BY {}(ubicacion, POINT(-77.0428, -12.0464));"
    geo = [f[0] for f in correr(sm, base.format("distancia_geodesica"))[0].filas]
    euc = [f[0] for f in correr(sm, base.format("distancia"))[0].filas]
    assert geo == euc == [1, 2, 3, 4], (geo, euc)

    # distancia_euclidiana es un alias de distancia
    sql = ("SELECT id FROM tiendas WHERE distancia_euclidiana"
           "(ubicacion, POINT(-77.0428, -12.0464)) < 0.045;")
    assert [f[0] for f in correr(sm, sql)[0].filas] == [1]

    # el plan nombra la metrica que se uso
    sql = ("EXPLAIN SELECT id FROM tiendas WHERE distancia_geodesica"
           "(ubicacion, POINT(-77.0428, -12.0464)) < 5000;")
    assert "distancia_geodesica(" in correr(sm, sql)[0].explain

    sm.close()
    limpiar()
    print("test_distancia_geodesica: OK")


def test_drop_table():
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE tiendas (id INT PRIMARY KEY, ubicacion POINT) USING HEAP;")
    correr(sm, "INSERT INTO tiendas VALUES (1, POINT(0.0, 0.0));")

    correr(sm, "DROP TABLE tiendas;")
    assert not os.path.exists("tiendas.dat"), "el archivo de datos deberia borrarse"

    try:
        correr(sm, "SELECT * FROM tiendas;")
        assert False, "la tabla no deberia existir"
    except ExecutionError as e:
        assert "no existe" in str(e)

    try:
        correr(sm, "DROP TABLE fantasma;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "no existe" in str(e)

    sm.close()
    limpiar()
    print("test_drop_table: OK")


def test_explain():
    """EXPLAIN describe el plan sin ejecutar; ANALYZE agrega filas y tiempos."""
    limpiar()
    sm = StorageManager()
    _sembrar(sm, n=60)

    # sin ANALYZE: hay costo estimado, no hay tiempos reales
    plan = correr(sm, "EXPLAIN SELECT * FROM tiendas WHERE id = 5;")[0].explain
    assert "cost=" in plan
    assert "actual time" not in plan
    assert "Planning Time:" in plan
    assert "Execution Time:" not in plan

    # con ANALYZE: aparecen los tiempos y las filas reales
    plan = correr(sm, "EXPLAIN ANALYZE SELECT * FROM tiendas WHERE id = 5;")[0].explain
    assert "actual time=" in plan
    assert "loops=1" in plan
    assert "Execution Time:" in plan

    # el filtro espacial sin indice es un barrido secuencial
    sql = (f"EXPLAIN ANALYZE SELECT id FROM tiendas WHERE "
           f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) < 0.05;")
    plan = correr(sm, sql)[0].explain
    assert "Seq Scan on tiendas" in plan
    assert "Filter:" in plan
    assert "distancia(ubicacion, POINT" in plan
    assert "Rows Removed by Filter:" in plan

    # ORDER BY distancia obliga a ordenar, y el LIMIT queda por encima
    sql = (f"EXPLAIN ANALYZE SELECT id FROM tiendas ORDER BY "
           f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) LIMIT 5;")
    plan = correr(sm, sql)[0].explain
    assert plan.startswith("Limit")
    assert "->  Sort" in plan
    assert "Sort Key: distancia(" in plan
    assert "->  Seq Scan on tiendas" in plan

    # un indice cambia el nodo de acceso
    correr(sm, "CREATE INDEX ON tiendas (id) USING BTREE;")
    plan = correr(sm, "EXPLAIN ANALYZE SELECT * FROM tiendas WHERE id = 5;")[0].explain
    assert "Index Scan using" in plan
    assert "Index Cond:" in plan

    # EXPLAIN tambien acepta sentencias que no son SELECT
    plan = correr(sm, "EXPLAIN INSERT INTO tiendas VALUES (999, 'X', POINT(0.0, 0.0));")[0].explain
    assert plan.startswith("Insert on tiendas")

    sm.close()
    limpiar()
    print("test_explain: OK")


def test_errores_espaciales():
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE tiendas (id INT PRIMARY KEY, ubicacion POINT) USING HEAP;")
    correr(sm, "INSERT INTO tiendas VALUES (1, POINT(0.0, 0.0));")

    # un POINT no se puede reemplazar por un escalar
    try:
        correr(sm, "INSERT INTO tiendas VALUES (2, 5);")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "POINT(x, y)" in str(e)

    # distancia sobre una columna que no es espacial
    try:
        correr(sm, "SELECT id FROM tiendas WHERE distancia(id, POINT(0.0, 0.0)) < 1;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "no es de tipo POINT" in str(e)

    sm.close()
    limpiar()
    print("test_errores_espaciales: OK")


if __name__ == "__main__":
    print("=== PARTE 2: DROP TABLE, CONSULTAS ESPACIALES Y EXPLAIN ===")
    test_point_round_trip()
    test_filtro_por_distancia()
    test_orden_por_distancia()
    test_distancia_entre_columnas()
    test_point_en_sequential_y_persistencia()
    test_distancia_geodesica()
    test_drop_table()
    test_explain()
    test_errores_espaciales()
    print("¡Todas las pruebas espaciales pasaron exitosamente!")
