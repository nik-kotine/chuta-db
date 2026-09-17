import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "parser"))

from scanner import Scanner
from parser import Parser
from storage.storage_manager import StorageManager
from executor import ExecuteVisitor, ExecutionError


ARCHIVOS = ["sys_tables.dat", "sys_columns.dat", "sys_indexes.dat", "ventas.dat"]


def limpiar():
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)


def correr(sm, sql):
    programa = Parser(Scanner(sql)).parse_p()
    return ExecuteVisitor(sm).ejecutar(programa)


def test_create_insert_select():
    limpiar()
    sm = StorageManager()

    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;")
    correr(sm, "INSERT INTO ventas VALUES (3, 'Carlos', 300.5);")
    correr(sm, "INSERT INTO ventas VALUES (1, 'Ana', 100.0);")
    correr(sm, "INSERT INTO ventas VALUES (2, 'Beto', 250.75);")

    # SELECT * devuelve los nombres de columna reales
    res = correr(sm, "SELECT * FROM ventas;")[0]
    assert res.columnas == ["id", "cliente", "monto"]
    assert len(res.filas) == 3

    # Proyeccion por nombre
    res = correr(sm, "SELECT cliente FROM ventas WHERE id = 2;")[0]
    assert res.columnas == ["cliente"]
    assert res.filas == [["Beto"]]

    # WHERE con AND / OR y precedencia
    res = correr(sm, "SELECT id FROM ventas WHERE monto > 200 AND id = 3 OR id = 1;")[0]
    assert sorted(f[0] for f in res.filas) == [1, 3]

    # BETWEEN
    res = correr(sm, "SELECT id FROM ventas WHERE id BETWEEN 1 AND 2;")[0]
    assert sorted(f[0] for f in res.filas) == [1, 2]

    # ORDER BY + LIMIT
    res = correr(sm, "SELECT cliente FROM ventas ORDER BY monto DESC LIMIT 2;")[0]
    assert res.filas == [["Carlos"], ["Beto"]]

    # DELETE
    correr(sm, "DELETE FROM ventas WHERE id = 3;")
    res = correr(sm, "SELECT * FROM ventas;")[0]
    assert len(res.filas) == 2

    sm.close()
    limpiar()
    print("test_create_insert_select: OK")


def test_persistencia_de_nombres():
    """Los nombres de columna deben sobrevivir al cierre de la base."""
    limpiar()

    sm = StorageManager()
    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20)) USING HEAP;")
    correr(sm, "INSERT INTO ventas VALUES (1, 'Ana');")
    sm.close()

    sm2 = StorageManager()
    meta = sm2.catalog.get_table_info("ventas")
    assert meta["column_names"] == ["id", "cliente"]
    assert meta["schema"] == ["integer", "varchar(20)"]

    res = correr(sm2, "SELECT cliente FROM ventas WHERE id = 1;")[0]
    assert res.filas == [["Ana"]]
    sm2.close()
    limpiar()
    print("test_persistencia_de_nombres: OK")


def test_primary_key_en_otra_posicion():
    """PRIMARY KEY no tiene que ser la primera columna."""
    limpiar()
    sm = StorageManager()

    correr(sm, "CREATE TABLE ventas (cliente VARCHAR(20), id INT PRIMARY KEY) USING HEAP;")
    meta = sm.catalog.get_table_info("ventas")
    assert meta["key_index"] == 1, f"key_index deberia ser 1, es {meta['key_index']}"

    correr(sm, "INSERT INTO ventas VALUES ('Ana', 1);")
    try:
        correr(sm, "INSERT INTO ventas VALUES ('Beto', 1);")
        assert False, "deberia haber rechazado la clave duplicada"
    except Exception as e:
        assert "clave primaria" in str(e).lower()

    sm.close()
    limpiar()
    print("test_primary_key_en_otra_posicion: OK")


def test_errores():
    limpiar()
    sm = StorageManager()
    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20)) USING HEAP;")

    # Columna inexistente
    try:
        correr(sm, "SELECT inexistente FROM ventas;")
        assert False, "deberia fallar"
    except KeyError as e:
        assert "inexistente" in str(e)

    # Tabla inexistente
    try:
        correr(sm, "SELECT * FROM fantasma;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "no existe" in str(e)

    # Sin PRIMARY KEY
    try:
        correr(sm, "CREATE TABLE malo (a INT, b INT) USING HEAP;")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "PRIMARY KEY" in str(e)

    # Cantidad de valores incorrecta
    try:
        correr(sm, "INSERT INTO ventas VALUES (1);")
        assert False, "deberia fallar"
    except ExecutionError as e:
        assert "espera 2 valores" in str(e)

    sm.close()
    limpiar()
    print("test_errores: OK")


if __name__ == "__main__":
    print("=== PROBANDO CONEXION PARSER <-> TABLE <-> CATALOGO ===")
    test_create_insert_select()
    test_persistencia_de_nombres()
    test_primary_key_en_otra_posicion()
    test_errores()
    print("¡Todas las pruebas de la conexión pasaron exitosamente!")
