"""
Pruebas del executor con indices (B+ agrupado y no agrupado) y algoritmos
externos (External Sort / External Hash) conectados al parser.
PYTHONPATH=. python3 tests/table/test_executor_avanzado.py  (o run_all_tests.py)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "parser"))

from scanner import Scanner
from parser import Parser
from storage.storage_manager import StorageManager
from executor import ExecuteVisitor, ExecutionError


ARCHIVOS = [
    "sys_tables.dat", "sys_columns.dat", "sys_indexes.dat",
    "ventas.dat", "emp.dat", "dept.dat",
    "idx_ventas_monto.idx", "idx_emp_id.idx", "idx_ventas_id.idx",
]


def limpiar():
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)


def correr(sm, sql):
    programa = Parser(Scanner(sql)).parse_p()
    return ExecuteVisitor(sm).ejecutar(programa)


def _prohibir_scan(tabla):
    """Intercambia tabla.scan por una bomba que cuenta las llamadas:
    si una consulta con indice llega a barrer la tabla, el test
    directamente revienta en vez de pasar por alto."""
    llamadas = {"n": 0}

    def bomba():
        llamadas["n"] += 1
        raise AssertionError("la consulta barrio la tabla en vez de usar el indice")

    original = tabla.scan
    tabla.scan = bomba
    return llamadas, original


def test_select_usa_indice_unclustered():
    limpiar()
    sm = StorageManager()

    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;")
    correr(sm, "INSERT INTO ventas VALUES (1, 'Ana', 100.0);")
    correr(sm, "INSERT INTO ventas VALUES (2, 'Beto', 250.0);")
    correr(sm, "INSERT INTO ventas VALUES (3, 'Carlos', 300.0);")
    correr(sm, "CREATE INDEX ON ventas (monto) USING BTREE;")

    tabla = sm.open_table("ventas")
    assert tabla.secondary_indexes.get(2), "el indice no quedo enlazado a la columna monto"
    assert tabla.clustered_index is None

    llamadas, original = _prohibir_scan(tabla)
    try:
        res = correr(sm, "SELECT cliente FROM ventas WHERE monto = 250.0;")[0]
        assert res.filas == [["Beto"]]

        res = correr(sm, "SELECT cliente FROM ventas WHERE monto BETWEEN 100 AND 300;")[0]
        assert sorted(f[0] for f in res.filas) == ["Ana", "Beto", "Carlos"]

        res = correr(sm, "SELECT cliente FROM ventas WHERE monto >= 300;")[0]
        assert res.filas == [["Carlos"]]
    finally:
        tabla.scan = original
    assert llamadas["n"] == 0, f"el indice debia servir la consulta, el scan se llamo {llamadas['n']} veces"

    sm.close()
    limpiar()
    print("test_select_usa_indice_unclustered: OK")


def test_select_usa_indice_clustered():
    limpiar()
    sm = StorageManager()

    correr(sm, "CREATE TABLE emp (id INT PRIMARY KEY, nombre VARCHAR(20)) USING SEQUENTIAL;")
    correr(sm, "INSERT INTO emp VALUES (3, 'Zoe');")
    correr(sm, "INSERT INTO emp VALUES (1, 'Ana');")
    correr(sm, "INSERT INTO emp VALUES (2, 'Bob');")
    correr(sm, "CREATE INDEX ON emp (id) USING BTREE CLUSTERED;")

    tabla = sm.open_table("emp")
    assert tabla.clustered_index is not None, "el indice agrupado no quedo enlazado"

    llamadas, original = _prohibir_scan(tabla)
    try:
        res = correr(sm, "SELECT nombre FROM emp WHERE id = 2;")[0]
        assert res.filas == [["Bob"]]

        res = correr(sm, "SELECT nombre FROM emp WHERE id BETWEEN 1 AND 3;")[0]
        assert sorted(f[0] for f in res.filas) == ["Ana", "Bob", "Zoe"]
    finally:
        tabla.scan = original
    assert llamadas["n"] == 0, f"el indice agrupado debia servir la consulta, scan: {llamadas['n']}"

    sm.close()
    limpiar()
    print("test_select_usa_indice_clustered: OK")


def test_indice_poblado_con_registros_existentes():
    """CREATE INDEX sobre una tabla que ya tiene datos debe indexarlos."""
    limpiar()
    sm = StorageManager()

    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;")
    correr(sm, "INSERT INTO ventas VALUES (1, 'Ana', 100.0);")
    correr(sm, "INSERT INTO ventas VALUES (2, 'Beto', 250.0);")
    correr(sm, "INSERT INTO ventas VALUES (3, 'Carlos', 250.0);")
    correr(sm, "CREATE INDEX ON ventas (monto) USING BTREE;")

    # monto=250.0 tiene dos filas: ambas deben aparecer via el indice
    res = correr(sm, "SELECT cliente FROM ventas WHERE monto = 250.0;")[0]
    assert sorted(f[0] for f in res.filas) == ["Beto", "Carlos"]

    sm.close()
    limpiar()
    print("test_indice_poblado_con_registros_existentes: OK")


def test_indices_persisten_y_mantienen_el_crud():
    """Los indices deben sobrevivir al cierre de la base y seguir al dia
    con INSERT y DELETE hechos por SQL en la sesion siguiente."""
    limpiar()
    sm = StorageManager()

    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;")
    correr(sm, "INSERT INTO ventas VALUES (1, 'Ana', 100.0);")
    correr(sm, "INSERT INTO ventas VALUES (2, 'Beto', 250.0);")
    correr(sm, "CREATE INDEX ON ventas (monto) USING BTREE;")

    correr(sm, "CREATE TABLE emp (id INT PRIMARY KEY, nombre VARCHAR(20)) USING SEQUENTIAL;")
    correr(sm, "INSERT INTO emp VALUES (3, 'Zoe');")
    correr(sm, "INSERT INTO emp VALUES (1, 'Ana');")
    correr(sm, "CREATE INDEX ON emp (id) USING BTREE CLUSTERED;")
    sm.close()

    sm2 = StorageManager()
    tabla = sm2.open_table("ventas")
    assert tabla.secondary_indexes.get(2), "el indice no agrupado no se recargo del catalogo"
    assert sm2.open_table("emp").clustered_index is not None, "el indice agrupado no se recargo del catalogo"

    res = correr(sm2, "SELECT cliente FROM ventas WHERE monto = 250.0;")[0]
    assert res.filas == [["Beto"]]
    res = correr(sm2, "SELECT nombre FROM emp WHERE id = 3;")[0]
    assert res.filas == [["Zoe"]]

    # INSERT post-reapertura: el indice debe ver la fila nueva
    correr(sm2, "INSERT INTO ventas VALUES (4, 'Dani', 250.0);")
    res = correr(sm2, "SELECT cliente FROM ventas WHERE monto = 250.0;")[0]
    assert sorted(f[0] for f in res.filas) == ["Beto", "Dani"]

    # DELETE post-reapertura: el indice debe dejar de ver la fila borrada
    correr(sm2, "DELETE FROM ventas WHERE id = 4;")
    res = correr(sm2, "SELECT cliente FROM ventas WHERE monto = 250.0;")[0]
    assert res.filas == [["Beto"]]

    sm2.close()
    limpiar()
    print("test_indices_persisten_y_mantienen_el_crud: OK")


def test_join_y_groupby_y_orderby_por_sql():
    """JOIN (Grace Hash Join), GROUP BY + agregados (hash aggregate) y
    ORDER BY (k-way merge) deben resolver consultas SQL correctamente."""
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;")
    correr(sm, "INSERT INTO ventas VALUES (1, 'Ana', 100.0);")
    correr(sm, "INSERT INTO ventas VALUES (2, 'Beto', 250.0);")
    correr(sm, "INSERT INTO ventas VALUES (3, 'Carlos', 300.0);")

    correr(sm, "CREATE TABLE dept (id INT PRIMARY KEY, d VARCHAR(20)) USING HEAP;")
    correr(sm, "INSERT INTO dept VALUES (1, 'IA');")
    correr(sm, "INSERT INTO dept VALUES (2, 'DB');")

    # JOIN (equi-join por id)
    res = correr(sm, "SELECT ventas.cliente, dept.d FROM ventas JOIN dept ON ventas.id = dept.id;")[0]
    assert sorted(map(tuple, res.filas)) == [("Ana", "IA"), ("Beto", "DB")]

    # JOIN + WHERE
    res = correr(sm, "SELECT ventas.cliente FROM ventas JOIN dept ON ventas.id = dept.id WHERE ventas.monto > 150.0;")[0]
    assert res.filas == [["Beto"]]

    # JOIN + ORDER BY (sorter externo sobre el registro combinado).
    # El join tiene 2 filas (Ana/IA y Beto/DB); DESC por monto => Beto ante Ana.
    res = correr(sm, "SELECT ventas.cliente FROM ventas JOIN dept ON ventas.id = dept.id ORDER BY ventas.monto DESC;")[0]
    assert res.filas == [["Beto"], ["Ana"]]

    # GROUP BY + agregados contra la clave de grupo
    res = correr(sm, "SELECT id, count(*) FROM dept GROUP BY id;")[0]
    assert sorted(map(tuple, res.filas)) == [(1, 1), (2, 1)]

    res = correr(sm, "SELECT monto, count(*) FROM ventas GROUP BY monto;")[0]
    assert res.columnas == ["monto", "count(*)"]
    assert sorted(map(tuple, res.filas)) == [(100.0, 1), (250.0, 1), (300.0, 1)]

    # agregacion global (sin GROUP BY)
    res = correr(sm, "SELECT count(*), sum(monto) FROM ventas;")[0]
    assert res.filas[0][0] == 3
    assert res.filas[0][1] == 650.0

    # ORDER BY simple (sorter externo, descendente)
    res = correr(sm, "SELECT cliente FROM ventas ORDER BY monto DESC;")[0]
    assert res.filas == [["Carlos"], ["Beto"], ["Ana"]]

    # ORDER BY del resultado de una agregacion (materializado, en RAM)
    res = correr(sm, "SELECT monto, count(*) FROM ventas GROUP BY monto ORDER BY monto DESC;")[0]
    assert [f[0] for f in res.filas] == [300.0, 250.0, 100.0]

    sm.close()
    limpiar()
    print("test_join_y_groupby_y_orderby_por_sql: OK")


def test_create_index_rechaza_usos_invalidos():
    limpiar()
    sm = StorageManager()

    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, monto FLOAT) USING HEAP;")
    correr(sm, "INSERT INTO ventas VALUES (1, 100.0);")
    correr(sm, "CREATE TABLE emp (id INT PRIMARY KEY, nombre VARCHAR(20)) USING SEQUENTIAL;")
    correr(sm, "INSERT INTO emp VALUES (1, 'Ana');")

    try:
        correr(sm, "CREATE INDEX ON ventas (monto) USING HASH;")
        assert False, "un indice HASH no esta implementado y debia fallar"
    except ExecutionError as e:
        assert "HASH" in str(e)

    try:
        correr(sm, "CREATE INDEX ON ventas (id) USING BTREE CLUSTERED;")
        assert False, "CLUSTERED sobre una tabla heap debia fallar"
    except ExecutionError as e:
        assert "SEQUENTIAL" in str(e)

    try:
        correr(sm, "CREATE INDEX ON emp (nombre) USING BTREE;")
        assert False, "un indice no agrupado sobre una tabla sequential debia fallar"
    except ExecutionError as e:
        assert "HEAP" in str(e)

    sm.close()
    limpiar()
    print("test_create_index_rechaza_usos_invalidos: OK")


if __name__ == "__main__":
    print("=== PROBANDO INDICES (B+ AGUPADO / NO AGUPADO) Y EXTERNAL ALGOS ===")
    test_select_usa_indice_unclustered()
    test_select_usa_indice_clustered()
    test_indice_poblado_con_registros_existentes()
    test_indices_persisten_y_mantienen_el_crud()
    test_join_y_groupby_y_orderby_por_sql()
    test_create_index_rechaza_usos_invalidos()
    print("¡Todas las pruebas del executor avanzado pasaron con éxito!")