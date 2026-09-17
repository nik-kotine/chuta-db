"""
Script de prueba manual de la conexion parser <-> table <-> catalogo.

    PYTHONPATH=. python3 probar.py

A diferencia de tests/table/test_executor.py (que solo verifica con
asserts), este script imprime todo para poder verlo. Se limpia a si mismo
al empezar, asi que se puede correr las veces que haga falta.
"""

import os
import sys

sys.path.insert(0, "parser")

from scanner import Scanner
from parser import Parser
from storage.storage_manager import StorageManager
from executor import ExecuteVisitor


ARCHIVOS = ["sys_tables.dat", "sys_columns.dat", "sys_indexes.dat",
            "ventas.dat", "empleados.dat", "cursos.dat"]


def limpiar():
    """Borra los .dat para empezar de cero (el catalogo persiste en disco)."""
    for f in ARCHIVOS:
        if os.path.exists(f):
            os.remove(f)


def correr(sm, sql):
    """Parsea y ejecuta un bloque de SQL. Devuelve la lista de resultados."""
    programa = Parser(Scanner(sql)).parse_p()
    return ExecuteVisitor(sm).ejecutar(programa)


def titulo(texto):
    print()
    print("=" * 62)
    print(texto)
    print("=" * 62)


def demo_basica():
    titulo("1. CREATE, INSERT, SELECT, DELETE")

    SQL = """
    CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;
    INSERT INTO ventas VALUES (3, 'Carlos', 300.5);
    INSERT INTO ventas VALUES (1, 'Ana', 100.0);
    INSERT INTO ventas VALUES (2, 'Beto', 250.75);

    SELECT * FROM ventas;
    SELECT cliente, monto FROM ventas WHERE monto > 200 ORDER BY monto DESC;
    SELECT cliente FROM ventas WHERE id BETWEEN 1 AND 2;
    DELETE FROM ventas WHERE id = 3;
    SELECT * FROM ventas;
    """

    sm = StorageManager()
    for resultado in correr(sm, SQL):
        print(resultado)
        print()
    sm.close()


def demo_precedencia():
    titulo("2. Precedencia de AND / OR")

    sm = StorageManager()
    correr(sm, """
    CREATE TABLE empleados (id INT PRIMARY KEY, nombre VARCHAR(15), sueldo FLOAT, activo BOOL) USING HEAP;
    INSERT INTO empleados VALUES (1, 'Ana', 1000.0, TRUE);
    INSERT INTO empleados VALUES (2, 'Beto', 3000.0, FALSE);
    INSERT INTO empleados VALUES (3, 'Carla', 5000.0, TRUE);
    """)

    # Sin parentesis: AND liga mas fuerte -> (sueldo > 2000 AND activo) OR id = 1
    print("WHERE sueldo > 2000 AND activo = TRUE OR id = 1")
    print(correr(sm, "SELECT id, nombre FROM empleados WHERE sueldo > 2000 AND activo = TRUE OR id = 1;")[0])

    # Con parentesis cambia el agrupamiento
    print("\nWHERE sueldo > 2000 AND (activo = TRUE OR id = 1)")
    print(correr(sm, "SELECT id, nombre FROM empleados WHERE sueldo > 2000 AND (activo = TRUE OR id = 1);")[0])

    sm.close()


def demo_primary_key():
    titulo("3. PRIMARY KEY en la segunda columna")

    sm = StorageManager()
    correr(sm, "CREATE TABLE cursos (nombre VARCHAR(15), codigo INT PRIMARY KEY) USING HEAP;")

    meta = sm.catalog.get_table_info("cursos")
    print("catalogo -> columnas:", meta["column_names"])
    print("            tipos   :", meta["schema"])
    print("            key_index:", meta["key_index"], "(apunta a 'codigo', no a 'nombre')")

    correr(sm, "INSERT INTO cursos VALUES ('BD2', 10);")
    print("\ninsertado ('BD2', 10)")

    print("ahora se intenta repetir el codigo 10:")
    try:
        correr(sm, "INSERT INTO cursos VALUES ('Compiladores', 10);")
        print("  >>> no dio error (mal)")
    except Exception as e:
        print("  >>>", type(e).__name__ + ":", e)

    sm.close()


def demo_errores():
    titulo("4. Errores")

    sm = StorageManager()
    correr(sm, "CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20)) USING HEAP;")

    casos = [
        ("columna inexistente",   "SELECT inexistente FROM ventas;"),
        ("tabla inexistente",     "SELECT * FROM fantasma;"),
        ("sin PRIMARY KEY",       "CREATE TABLE malo (a INT, b INT) USING HEAP;"),
        ("faltan valores",        "INSERT INTO ventas VALUES (1);"),
        ("tabla repetida",        "CREATE TABLE ventas (id INT PRIMARY KEY) USING HEAP;"),
        ("error de sintaxis",     "SELECT id cliente FROM ventas;"),
    ]

    for etiqueta, sql in casos:
        print("\n{}:\n  {}".format(etiqueta, sql))
        try:
            correr(sm, sql)
            print("  >>> no dio error (mal)")
        except Exception as e:
            print("  >>>", type(e).__name__ + ":", e)

    sm.close()


def demo_persistencia():
    titulo("5. Persistencia de los nombres de columna")

    sm = StorageManager()
    correr(sm, """
    CREATE TABLE ventas (id INT PRIMARY KEY, cliente VARCHAR(20), monto FLOAT) USING HEAP;
    INSERT INTO ventas VALUES (1, 'Ana', 100.0);
    INSERT INTO ventas VALUES (2, 'Beto', 250.75);
    """)
    print("base creada y cerrada")
    sm.close()

    # Proceso nuevo: solo se lee del disco, nadie vuelve a declarar el esquema
    sm2 = StorageManager()
    meta = sm2.catalog.get_table_info("ventas")
    print("\nleido del catalogo en disco:")
    print("  columnas :", meta["column_names"])
    print("  tipos    :", meta["schema"])
    print("  file_type:", meta["file_type"])

    print("\ny se puede consultar por nombre sin volver a crear nada:")
    print(correr(sm2, "SELECT cliente FROM ventas WHERE id = 2;")[0])
    sm2.close()


if __name__ == "__main__":
    limpiar()
    demo_basica()

    limpiar()
    demo_precedencia()

    limpiar()
    demo_primary_key()

    limpiar()
    demo_errores()

    limpiar()
    demo_persistencia()

    limpiar()
    print("\nListo.")
