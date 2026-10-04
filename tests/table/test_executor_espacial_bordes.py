"""
Casos borde de las consultas espaciales.

Los tests de test_executor_knn_poligono.py verifican que los algoritmos
sean correctos sobre datos normales. Estos cubren lo degenerado: tablas
vacias, k=0, puntos repetidos, poligonos sin area, coordenadas en los
extremos del planeta y valores fuera de rango.

Varios documentan una decision de diseno mas que un resultado "obvio";
en esos casos el comentario dice cual es la decision y por que.
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RAIZ, "parser"))

from scanner import Scanner
from parser import Parser
from storage.storage_manager import StorageManager
from executor import ExecuteVisitor, ExecutionError
from spatial.geometry import Point, haversine

CUADRADO = ("POLYGON(POINT(-1.0, -1.0), POINT(1.0, -1.0), "
            "POINT(1.0, 1.0), POINT(-1.0, 1.0))")


def limpiar():
    for f in os.listdir("."):
        if f.endswith((".dat", ".idx", ".log")):
            os.remove(f)


def correr(sm, sql):
    return ExecuteVisitor(sm).ejecutar(Parser(Scanner(sql)).parse_p())


def ids(resultado):
    return sorted(f[0] for f in resultado.filas)


def _tabla(sm, filas, con_indice=True, nombre="t"):
    correr(sm, f"CREATE TABLE {nombre} (id INT PRIMARY KEY, u POINT) USING HEAP;")
    for i, x, y in filas:
        correr(sm, f"INSERT INTO {nombre} VALUES ({i}, POINT({x}, {y}));")
    if con_indice:
        correr(sm, f"CREATE INDEX ON {nombre} (u) USING RTREE;")


# ---------------------------------------------------------------------

def test_tabla_vacia():
    """Ninguna consulta espacial debe romperse sobre una tabla sin filas."""
    limpiar()
    sm = StorageManager()
    _tabla(sm, [])

    assert correr(sm, "SELECT id FROM t ORDER BY distancia(u, POINT(0.0, 0.0)) "
                      "LIMIT 5;")[0].filas == []
    assert correr(sm, f"SELECT id FROM t WHERE dentro_de(u, {CUADRADO});")[0].filas == []
    assert correr(sm, "SELECT id FROM t WHERE distancia(u, POINT(0.0, 0.0)) "
                      "< 1;")[0].filas == []
    assert correr(sm, "SELECT id FROM t WHERE distancia_geodesica"
                      "(u, POINT(0.0, 0.0)) < 1000;")[0].filas == []

    # y el plan tiene que seguir siendo valido
    plan = correr(sm, "EXPLAIN ANALYZE SELECT id FROM t ORDER BY "
                      "distancia(u, POINT(0.0, 0.0)) LIMIT 5;")[0].explain
    assert "rows=0" in plan

    sm.close()
    limpiar()
    print("test_tabla_vacia: OK")


def test_limite_cero_y_mayor_que_la_tabla():
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(0, 0.0, 0.0), (1, 1.0, 0.0), (2, 2.0, 0.0)])

    base = "SELECT id FROM t ORDER BY distancia(u, POINT(0.0, 0.0)) LIMIT {};"
    assert correr(sm, base.format(0))[0].filas == []
    assert ids(correr(sm, base.format(1))[0]) == [0]
    # pedir mas vecinos que filas hay devuelve las que existen, sin error
    assert ids(correr(sm, base.format(999))[0]) == [0, 1, 2]

    sm.close()
    limpiar()
    print("test_limite_cero_y_mayor_que_la_tabla: OK")


def test_puntos_duplicados_con_delete():
    """
    Tres filas con coordenadas identicas: borrar una no puede arrastrar
    a las otras.

    Es el caso que rompia antes: RTreeBase.delete busca por punto y borra
    la primera entrada que coincide, ademas de eliminar el registro del
    heap. Como indice secundario eso borraba una fila ajena.
    """
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(0, 5.0, 5.0), (1, 5.0, 5.0), (2, 5.0, 5.0), (9, 9.0, 9.0)])

    assert ids(correr(sm, "SELECT id FROM t;")[0]) == [0, 1, 2, 9]
    # el k-NN ve las tres repetidas antes que la lejana
    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(5.0, 5.0)) LIMIT 3;")[0]) == [0, 1, 2]

    correr(sm, "DELETE FROM t WHERE id = 1;")
    assert ids(correr(sm, "SELECT id FROM t;")[0]) == [0, 2, 9], "se borro una fila de mas"
    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(5.0, 5.0)) LIMIT 3;")[0]) == [0, 2, 9]

    correr(sm, "DELETE FROM t WHERE id = 0;")
    assert ids(correr(sm, "SELECT id FROM t;")[0]) == [2, 9]
    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(5.0, 5.0)) LIMIT 2;")[0]) == [2, 9]

    # y el ultimo duplicado tambien se puede borrar
    correr(sm, "DELETE FROM t WHERE id = 2;")
    assert ids(correr(sm, "SELECT id FROM t;")[0]) == [9]

    sm.close()
    limpiar()
    print("test_puntos_duplicados_con_delete: OK")


def test_update_de_punto_duplicado():
    """UPDATE pasa por delete_ref + _insert_ref: mismo riesgo que DELETE."""
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(0, 5.0, 5.0), (1, 5.0, 5.0)])

    correr(sm, "UPDATE t SET u = POINT(100.0, 100.0) WHERE id = 1;")
    assert ids(correr(sm, "SELECT id FROM t;")[0]) == [0, 1]

    # el que se movio ya no esta cerca del original, el otro si
    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(5.0, 5.0)) LIMIT 1;")[0]) == [0]
    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(100.0, 100.0)) LIMIT 1;")[0]) == [1]

    sm.close()
    limpiar()
    print("test_update_de_punto_duplicado: OK")


def test_poligonos_degenerados():
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(0, 0.0, 0.0), (1, 2.0, 2.0), (2, 0.5, 0.5)], con_indice=False)

    # tres vertices colineales: area cero, no contiene a nadie
    colineal = "POLYGON(POINT(0.0, 0.0), POINT(1.0, 1.0), POINT(2.0, 2.0))"
    assert correr(sm, f"SELECT id FROM t WHERE dentro_de(u, {colineal});")[0].filas == []

    # auto-intersectante (mono): el ray casting le da una interpretacion
    # consistente (regla par-impar). No se valida la geometria, igual que
    # PostGIS, que acepta poligonos invalidos y deja ST_IsValid aparte.
    mono = ("POLYGON(POINT(-1.0, -1.0), POINT(1.0, 1.0), "
            "POINT(1.0, -1.0), POINT(-1.0, 1.0))")
    resultado = ids(correr(sm, f"SELECT id FROM t WHERE dentro_de(u, {mono});")[0])
    assert isinstance(resultado, list)   # lo que importa es que no reviente

    # el minimo son 3 vertices
    try:
        correr(sm, "SELECT id FROM t WHERE dentro_de(u, "
                   "POLYGON(POINT(0.0, 0.0), POINT(1.0, 1.0)));")
        assert False, "deberia fallar"
    except Exception as e:
        assert "3 vertices" in str(e)

    sm.close()
    limpiar()
    print("test_poligonos_degenerados: OK")


def test_coordenadas_extremas():
    """Antimeridiano y polos: Haversine los maneja, el MBR del R-Tree no."""
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(0, 179.9, 0.0), (1, -179.9, 0.0), (2, 0.0, 90.0), (3, 0.0, -90.0)],
           con_indice=False)

    # 179.9 y -179.9 estan a 0.2 grados reales (~22 km) cruzando el
    # antimeridiano, no a 359.8 grados: Haversine lo resuelve bien porque
    # trabaja con el seno de la diferencia de longitud.
    d = haversine(Point(179.9, 0.0), Point(-179.9, 0.0))
    assert 20_000 < d < 25_000, d

    filas = ids(correr(sm, "SELECT id FROM t WHERE distancia_geodesica"
                           "(u, POINT(179.95, 0.0)) < 50000;")[0])
    assert filas == [0, 1], filas

    # los polos son puntos validos
    assert ids(correr(sm, "SELECT id FROM t WHERE distancia_geodesica"
                          "(u, POINT(0.0, 90.0)) < 1000;")[0]) == [2]

    # OJO: la euclidiana NO entiende el antimeridiano, ahi 179.9 y -179.9
    # quedan en extremos opuestos del plano. Es el limite conocido de la
    # metrica plana, no un error.
    assert ids(correr(sm, "SELECT id FROM t WHERE "
                          "distancia(u, POINT(179.95, 0.0)) < 1;")[0]) == [0]

    sm.close()
    limpiar()
    print("test_coordenadas_extremas: OK")


def test_coordenadas_fuera_de_rango():
    """
    La metrica geodesica exige grados validos; la euclidiana no.

    Un POINT puede representar cualquier par del plano (coordenadas de un
    plano cartesiano, por ejemplo), asi que validar siempre romperia ese
    uso. Solo la geodesica los interpreta como grados sobre la esfera.
    """
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(1, 0.0, 200.0), (2, 10.0, 20.0)], con_indice=False)

    # la euclidiana los acepta: son solo numeros
    assert ids(correr(sm, "SELECT id FROM t WHERE "
                          "distancia(u, POINT(0.0, 0.0)) < 1000;")[0]) == [1, 2]

    # la geodesica los rechaza con un mensaje que recuerda el orden
    try:
        correr(sm, "SELECT id FROM t WHERE distancia_geodesica"
                   "(u, POINT(0.0, 0.0)) < 1000;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "latitud" in str(e) and "-90" in str(e)

    # tambien si el literal es el invalido
    try:
        correr(sm, "SELECT id FROM t WHERE distancia_geodesica"
                   "(u, POINT(500.0, 0.0)) < 1000;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "longitud" in str(e)

    sm.close()
    limpiar()
    print("test_coordenadas_fuera_de_rango: OK")


def test_empates_exactos_en_knn():
    """
    Cuatro puntos equidistantes: el orden entre ellos es indefinido, pero
    el conjunto devuelto no.
    """
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(0, 1.0, 0.0), (1, -1.0, 0.0), (2, 0.0, 1.0), (3, 0.0, -1.0)])

    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(0.0, 0.0)) LIMIT 4;")[0]) == [0, 1, 2, 3]

    # pidiendo 2 de 4 empatados, cualesquiera dos son validos
    dos = ids(correr(sm, "SELECT id FROM t ORDER BY "
                         "distancia(u, POINT(0.0, 0.0)) LIMIT 2;")[0])
    assert len(dos) == 2 and set(dos) <= {0, 1, 2, 3}

    sm.close()
    limpiar()
    print("test_empates_exactos_en_knn: OK")


def test_rollback_con_indice_espacial():
    """Un ROLLBACK no debe dejar el R-Tree apuntando a filas inexistentes."""
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(1, 1.0, 1.0)])

    correr(sm, "BEGIN TRANSACTION; INSERT INTO t VALUES (2, POINT(2.0, 2.0)); ROLLBACK;")

    assert ids(correr(sm, "SELECT id FROM t;")[0]) == [1]
    # el indice no puede devolver la fila revertida
    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(2.0, 2.0)) LIMIT 2;")[0]) == [1]

    sm.close()
    limpiar()
    print("test_rollback_con_indice_espacial: OK")


def test_un_solo_punto():
    """El caso mas chico que no es vacio."""
    limpiar()
    sm = StorageManager()
    _tabla(sm, [(7, 3.0, 4.0)])

    assert ids(correr(sm, "SELECT id FROM t ORDER BY "
                          "distancia(u, POINT(0.0, 0.0)) LIMIT 5;")[0]) == [7]
    # distancia a si mismo = 0, asi que entra con cualquier radio positivo
    assert ids(correr(sm, "SELECT id FROM t WHERE "
                          "distancia(u, POINT(3.0, 4.0)) < 0.001;")[0]) == [7]
    assert correr(sm, "SELECT id FROM t WHERE "
                      "distancia(u, POINT(3.0, 4.0)) < 0;")[0].filas == []

    sm.close()
    limpiar()
    print("test_un_solo_punto: OK")


if __name__ == "__main__":
    print("=== CASOS BORDE DE LAS CONSULTAS ESPACIALES ===")
    test_tabla_vacia()
    test_limite_cero_y_mayor_que_la_tabla()
    test_puntos_duplicados_con_delete()
    test_update_de_punto_duplicado()
    test_poligonos_degenerados()
    test_coordenadas_extremas()
    test_coordenadas_fuera_de_rango()
    test_empates_exactos_en_knn()
    test_rollback_con_indice_espacial()
    test_un_solo_punto()
    print("¡Todos los casos borde pasaron exitosamente!")
