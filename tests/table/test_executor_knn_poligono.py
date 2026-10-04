"""
Pruebas de las consultas espaciales de la Parte 2 que se apoyan en el
R-Tree:

  - k-NN:        ORDER BY distancia(...) LIMIT k
  - poligonos:   WHERE dentro_de(col, POLYGON(...))
  - radio:       WHERE distancia(...) < r

La propiedad que se verifica en casi todos los casos es la misma: el
resultado con indice tiene que ser identico al resultado sin indice, y
ambos al calculo hecho en Python. Un indice que acelera pero cambia la
respuesta esta roto.
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
from executor import ExecuteVisitor, ExecutionError
from spatial.geometry import Point, haversine, point_in_polygon

ARCHIVOS = ["sys_tables.dat", "sys_columns.dat", "sys_indexes.dat",
            "gasolineras.dat", "sucursales.dat",
            "idx_gasolineras_ubicacion_rtree.idx",
            "idx_sucursales_ubicacion_rtree.idx"]

# Centro de Lima, en orden PostGIS (longitud, latitud)
LIMA = (-77.0428, -12.0464)


def limpiar():
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)
    # los indices quedan con nombre derivado de la tabla
    for f in os.listdir("."):
        if f.endswith(".idx") and f.startswith("idx_"):
            os.remove(f)


def correr(sm, sql):
    return ExecuteVisitor(sm).ejecutar(Parser(Scanner(sql)).parse_p())


def ids(resultado):
    return [f[0] for f in resultado.filas]


def _sembrar(sm, tabla="gasolineras", n=200, semilla=11):
    """Crea la tabla con n puntos al azar. Devuelve {id: (lon, lat)}."""
    correr(sm, f"CREATE TABLE {tabla} (id INT PRIMARY KEY, nombre VARCHAR(16), "
               f"ubicacion POINT) USING HEAP;")
    random.seed(semilla)
    puntos = {}
    for i in range(n):
        x = round(LIMA[0] + random.uniform(-0.3, 0.3), 6)
        y = round(LIMA[1] + random.uniform(-0.3, 0.3), 6)
        puntos[i] = (x, y)
        correr(sm, f"INSERT INTO {tabla} VALUES ({i}, 'G{i}', POINT({x}, {y}));")
    return puntos


def _mas_cercanos(puntos, k, centro=LIMA, filtro=None):
    items = [(i, p) for i, p in puntos.items() if filtro is None or filtro(i)]
    items.sort(key=lambda kv: math.hypot(kv[1][0] - centro[0], kv[1][1] - centro[1]))
    return [i for i, _ in items[:k]]


# ---------------------------------------------------------------------
# k-NN
# ---------------------------------------------------------------------

def test_knn_coincide_con_fuerza_bruta():
    """Las k mas cercanas, con y sin R-Tree, deben ser las mismas."""
    limpiar()
    sm = StorageManager()
    puntos = _sembrar(sm)

    consulta = (f"SELECT id FROM gasolineras ORDER BY "
                f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) LIMIT 10;")

    sin_indice = ids(correr(sm, consulta)[0])
    assert sin_indice == _mas_cercanos(puntos, 10), sin_indice

    correr(sm, "CREATE INDEX ON gasolineras (ubicacion) USING RTREE;")
    con_indice = ids(correr(sm, consulta)[0])
    assert con_indice == sin_indice, f"{con_indice} != {sin_indice}"

    # el plan debe mostrar que ahora pasa por el arbol
    plan = correr(sm, "EXPLAIN ANALYZE " + consulta)[0].explain
    assert "Index Scan using rtree_gasolineras_ubicacion" in plan
    assert "Order By: k-NN (k=10" in plan

    sm.close()
    limpiar()
    print("test_knn_coincide_con_fuerza_bruta: OK")


def test_knn_con_filtro():
    """
    k-NN con WHERE: el arbol devuelve los mas cercanos, pero hay que
    seguir pidiendo hasta juntar k filas que ademas cumplan el filtro.
    """
    limpiar()
    sm = StorageManager()
    puntos = _sembrar(sm)
    correr(sm, "CREATE INDEX ON gasolineras (ubicacion) USING RTREE;")

    consulta = (f"SELECT id FROM gasolineras WHERE id < 50 ORDER BY "
                f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) LIMIT 5;")
    obtenido = ids(correr(sm, consulta)[0])
    assert obtenido == _mas_cercanos(puntos, 5, filtro=lambda i: i < 50), obtenido

    # filtro muy selectivo: el arbol tiene que agrandar k varias veces
    consulta = (f"SELECT id FROM gasolineras WHERE id < 3 ORDER BY "
                f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) LIMIT 3;")
    obtenido = ids(correr(sm, consulta)[0])
    assert obtenido == _mas_cercanos(puntos, 3, filtro=lambda i: i < 3), obtenido

    # si se piden mas vecinos que filas hay, devuelve los que existen
    consulta = (f"SELECT id FROM gasolineras ORDER BY "
                f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) LIMIT 500;")
    assert len(ids(correr(sm, consulta)[0])) == len(puntos)

    sm.close()
    limpiar()
    print("test_knn_con_filtro: OK")


def test_knn_metricas_y_desc():
    limpiar()
    sm = StorageManager()
    puntos = _sembrar(sm, n=120)
    correr(sm, "CREATE INDEX ON gasolineras (ubicacion) USING RTREE;")

    # a esta escala las dos metricas dan el mismo orden de cercania
    base = ("SELECT id FROM gasolineras ORDER BY {}"
            f"(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) LIMIT 8;")
    euclid = ids(correr(sm, base.format("distancia"))[0])
    geo = ids(correr(sm, base.format("distancia_geodesica"))[0])
    assert euclid == geo == _mas_cercanos(puntos, 8), (euclid, geo)

    # DESC pide los MAS LEJANOS: el R-Tree no sabe podar eso, debe ordenar
    consulta = (f"SELECT id FROM gasolineras ORDER BY "
                f"distancia(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) DESC LIMIT 3;")
    lejanos = sorted(puntos.items(),
                     key=lambda kv: -math.hypot(kv[1][0] - LIMA[0], kv[1][1] - LIMA[1]))
    assert ids(correr(sm, consulta)[0]) == [i for i, _ in lejanos[:3]]

    plan = correr(sm, "EXPLAIN " + consulta)[0].explain
    assert "k-NN" not in plan, "DESC no deberia resolverse como k-NN"

    sm.close()
    limpiar()
    print("test_knn_metricas_y_desc: OK")


# ---------------------------------------------------------------------
# Poligonos
# ---------------------------------------------------------------------

def test_poligono_distrito():
    """Sucursales dentro de un distrito, definido como un poligono."""
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE sucursales (id INT PRIMARY KEY, nombre VARCHAR(16), "
               "ubicacion POINT) USING HEAP;")

    filas = [
        (1, "Centro",  -77.050, -12.050),   # dentro
        (2, "Sur",     -77.080, -12.080),   # dentro
        (3, "Norte",   -77.050, -11.950),   # fuera (arriba)
        (4, "Este",    -76.500, -12.050),   # fuera (lejos)
        (5, "Limite",  -77.000, -12.050),   # sobre el borde derecho
    ]
    for i, nombre, x, y in filas:
        correr(sm, f"INSERT INTO sucursales VALUES ({i}, '{nombre}', POINT({x}, {y}));")

    # cuadrado: lon en [-77.1, -77.0], lat en [-12.1, -12.0]
    distrito = ("POLYGON(POINT(-77.1, -12.1), POINT(-77.0, -12.1), "
                "POINT(-77.0, -12.0), POINT(-77.1, -12.0))")
    consulta = f"SELECT id FROM sucursales WHERE dentro_de(ubicacion, {distrito});"

    sin_indice = sorted(ids(correr(sm, consulta)[0]))

    # el mismo resultado que el ray casting directo
    vertices = [Point(-77.1, -12.1), Point(-77.0, -12.1),
                Point(-77.0, -12.0), Point(-77.1, -12.0)]
    esperado = sorted(i for i, _n, x, y in filas
                      if point_in_polygon(Point(x, y), vertices))
    assert sin_indice == esperado, (sin_indice, esperado)
    assert 1 in sin_indice and 2 in sin_indice
    assert 3 not in sin_indice and 4 not in sin_indice

    # con R-Tree: mismo conjunto, distinto plan
    correr(sm, "CREATE INDEX ON sucursales (ubicacion) USING RTREE;")
    con_indice = sorted(ids(correr(sm, consulta)[0]))
    assert con_indice == sin_indice, (con_indice, sin_indice)

    plan = correr(sm, "EXPLAIN ANALYZE " + consulta)[0].explain
    assert "Index Scan using rtree_sucursales_ubicacion" in plan

    sm.close()
    limpiar()
    print("test_poligono_distrito: OK")


def test_poligono_concavo_y_combinado():
    """Un poligono en L: el MBR no alcanza, hace falta el refinamiento."""
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE sucursales (id INT PRIMARY KEY, nombre VARCHAR(16), "
               "ubicacion POINT) USING HEAP;")

    # L que ocupa el cuadrante inferior izquierdo y la franja inferior
    ele = ("POLYGON(POINT(0.0, 0.0), POINT(4.0, 0.0), POINT(4.0, 1.0), "
           "POINT(1.0, 1.0), POINT(1.0, 4.0), POINT(0.0, 4.0))")
    vertices = [Point(0, 0), Point(4, 0), Point(4, 1),
                Point(1, 1), Point(1, 4), Point(0, 4)]

    filas = [(1, 0.5, 0.5), (2, 3.0, 0.5), (3, 0.5, 3.0), (4, 3.0, 3.0), (5, 2.0, 2.0)]
    for i, x, y in filas:
        correr(sm, f"INSERT INTO sucursales VALUES ({i}, 'S{i}', POINT({x}, {y}));")

    consulta = f"SELECT id FROM sucursales WHERE dentro_de(ubicacion, {ele});"
    esperado = sorted(i for i, x, y in filas if point_in_polygon(Point(x, y), vertices))

    sin_indice = sorted(ids(correr(sm, consulta)[0]))
    assert sin_indice == esperado, (sin_indice, esperado)
    # 4 y 5 caen dentro del MBR del poligono pero fuera del poligono
    assert 4 not in sin_indice and 5 not in sin_indice

    correr(sm, "CREATE INDEX ON sucursales (ubicacion) USING RTREE;")
    assert sorted(ids(correr(sm, consulta)[0])) == esperado

    # combinado con un predicado normal
    consulta = (f"SELECT id FROM sucursales WHERE id > 1 AND "
                f"dentro_de(ubicacion, {ele});")
    assert sorted(ids(correr(sm, consulta)[0])) == [i for i in esperado if i > 1]

    sm.close()
    limpiar()
    print("test_poligono_concavo_y_combinado: OK")


# ---------------------------------------------------------------------
# Radio
# ---------------------------------------------------------------------

def test_radio_con_indice():
    limpiar()
    sm = StorageManager()
    puntos = _sembrar(sm, n=150)

    radio_m = 8000
    consulta = (f"SELECT id FROM gasolineras WHERE distancia_geodesica"
                f"(ubicacion, POINT({LIMA[0]}, {LIMA[1]})) < {radio_m};")

    centro = Point(*LIMA)
    esperado = sorted(i for i, (x, y) in puntos.items()
                      if haversine(Point(x, y), centro) < radio_m)

    sin_indice = sorted(ids(correr(sm, consulta)[0]))
    assert sin_indice == esperado, (len(sin_indice), len(esperado))
    assert esperado, "el conjunto de prueba no deberia quedar vacio"

    correr(sm, "CREATE INDEX ON gasolineras (ubicacion) USING RTREE;")
    con_indice = sorted(ids(correr(sm, consulta)[0]))
    assert con_indice == esperado, (con_indice, esperado)

    plan = correr(sm, "EXPLAIN ANALYZE " + consulta)[0].explain
    assert "Index Scan using rtree_gasolineras_ubicacion" in plan

    sm.close()
    limpiar()
    print("test_radio_con_indice: OK")


# ---------------------------------------------------------------------
# Mantenimiento del indice y errores
# ---------------------------------------------------------------------

def test_indice_se_mantiene_y_persiste():
    """El R-Tree se actualiza con INSERT/DELETE y se recarga al reabrir."""
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE gasolineras (id INT PRIMARY KEY, nombre VARCHAR(16), "
               "ubicacion POINT) USING HEAP;")
    for i in range(5):
        correr(sm, f"INSERT INTO gasolineras VALUES ({i}, 'G{i}', "
                   f"POINT({-77.0 - i * 0.01}, {-12.0 - i * 0.01}));")
    correr(sm, "CREATE INDEX ON gasolineras (ubicacion) USING RTREE;")

    # una fila insertada DESPUES de crear el indice tiene que indexarse
    correr(sm, "INSERT INTO gasolineras VALUES (99, 'Nueva', POINT(-77.001, -12.001));")
    consulta = ("SELECT id FROM gasolineras ORDER BY "
                "distancia(ubicacion, POINT(-77.0, -12.0)) LIMIT 2;")
    assert ids(correr(sm, consulta)[0]) == [0, 99]

    # y una borrada debe desaparecer del resultado
    correr(sm, "DELETE FROM gasolineras WHERE id = 0;")
    assert ids(correr(sm, consulta)[0]) == [99, 1]

    sm.close()

    sm2 = StorageManager()
    correr(sm2, "SELECT * FROM gasolineras;")   # fuerza la carga de indices
    assert "idx_gasolineras_ubicacion_rtree" in sm2.index_manager.open_indexes
    assert ids(correr(sm2, consulta)[0]) == [99, 1]
    sm2.close()
    limpiar()
    print("test_indice_se_mantiene_y_persiste: OK")


def test_errores():
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE gasolineras (id INT PRIMARY KEY, nombre VARCHAR(16), "
               "ubicacion POINT) USING HEAP;")

    # R-Tree sobre una columna que no es POINT
    try:
        correr(sm, "CREATE INDEX ON gasolineras (id) USING RTREE;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "POINT" in str(e)

    # un R-Tree no puede ser CLUSTERED
    try:
        correr(sm, "CREATE INDEX ON gasolineras (ubicacion) USING RTREE CLUSTERED;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "CLUSTERED" in str(e)

    # un poligono necesita al menos tres vertices
    try:
        correr(sm, "SELECT id FROM gasolineras WHERE dentro_de(ubicacion, "
                   "POLYGON(POINT(0.0, 0.0), POINT(1.0, 1.0)));")
        assert False, "deberia fallar"
    except Exception as e:
        assert "3 vertices" in str(e), e

    # dentro_de exige un POLYGON como segundo argumento
    try:
        correr(sm, "SELECT id FROM gasolineras WHERE dentro_de(ubicacion, "
                   "POINT(0.0, 0.0));")
        assert False, "deberia fallar"
    except Exception as e:
        assert "POLYGON" in str(e), e

    sm.close()
    limpiar()
    print("test_errores: OK")


if __name__ == "__main__":
    print("=== k-NN E INTERSECCION CON POLIGONOS (R-Tree) ===")
    test_knn_coincide_con_fuerza_bruta()
    test_knn_con_filtro()
    test_knn_metricas_y_desc()
    test_poligono_distrito()
    test_poligono_concavo_y_combinado()
    test_radio_con_indice()
    test_indice_se_mantiene_y_persiste()
    test_errores()
    print("¡Todas las pruebas de k-NN y poligonos pasaron exitosamente!")
