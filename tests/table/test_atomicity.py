import os
import tempfile

from parser.executor import ExecuteVisitor
from parser import Parser
from parser.scanner import Scanner
from storage.storage_manager import StorageManager


def execute(visitor, sql):
    return visitor.ejecutar(Parser(Scanner(sql)).parse_p())


def setup_table(directory, indexed=False):
    old_directory = os.getcwd()
    os.chdir(directory)
    sm = StorageManager(wal_path=os.path.join(directory, "wal.log"))
    visitor = ExecuteVisitor(sm)
    execute(visitor, "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;")
    if indexed:
        execute(visitor, "CREATE INDEX ON ventas (nombre) USING BTREE;")
    return old_directory, sm, visitor


def close_table(old_directory, sm):
    sm.close()
    os.chdir(old_directory)


def test_insert_rollback_removes_row():
    with tempfile.TemporaryDirectory() as directory:
        old, sm, visitor = setup_table(directory)
        try:
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (1, 'Ana'); ROLLBACK;")
            assert execute(visitor, "SELECT * FROM ventas;")[0].filas == []
        finally:
            close_table(old, sm)


def test_delete_rollback_restores_row():
    with tempfile.TemporaryDirectory() as directory:
        old, sm, visitor = setup_table(directory)
        try:
            execute(visitor, "INSERT INTO ventas VALUES (2, 'Beto');")
            execute(visitor, "BEGIN TRANSACTION; DELETE FROM ventas WHERE id = 2; ROLLBACK;")
            assert execute(visitor, "SELECT * FROM ventas;")[0].filas == [[2, "Beto"]]
        finally:
            close_table(old, sm)


def test_multiple_mutations_rollback_in_reverse_order():
    with tempfile.TemporaryDirectory() as directory:
        old, sm, visitor = setup_table(directory)
        try:
            execute(visitor, "INSERT INTO ventas VALUES (1, 'Ana');")
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (2, 'Beto'); DELETE FROM ventas WHERE id = 1; ROLLBACK;")
            assert execute(visitor, "SELECT * FROM ventas;")[0].filas == [[1, "Ana"]]
        finally:
            close_table(old, sm)


def test_insert_commit_preserves_row():
    with tempfile.TemporaryDirectory() as directory:
        old, sm, visitor = setup_table(directory)
        try:
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (3, 'Cris'); END TRANSACTION;")
            assert execute(visitor, "SELECT * FROM ventas;")[0].filas == [[3, "Cris"]]
        finally:
            close_table(old, sm)


def test_rollback_keeps_secondary_index_consistent():
    with tempfile.TemporaryDirectory() as directory:
        old, sm, visitor = setup_table(directory, indexed=True)
        try:
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (4, 'Dina'); ROLLBACK;")
            assert execute(visitor, "SELECT * FROM ventas WHERE nombre = 'Dina';")[0].filas == []
        finally:
            close_table(old, sm)


if __name__ == "__main__":
    test_insert_rollback_removes_row()
    test_delete_rollback_restores_row()
    test_multiple_mutations_rollback_in_reverse_order()
    test_insert_commit_preserves_row()
    test_rollback_keeps_secondary_index_consistent()
    print("test_phase5_atomicity: OK")