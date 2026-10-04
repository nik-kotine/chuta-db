import json
import os
import tempfile
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "parser"))

from executor import ExecuteVisitor
from parser import Parser
from scanner import Scanner
from storage.storage_manager import StorageManager


def execute(visitor, sql):
    return visitor.ejecutar(Parser(Scanner(sql)).parse_p())


def test_recovery_rolls_back_uncommitted_insert_after_reopen():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            wal_path = os.path.join(directory, "wal.log")
            sm = StorageManager(wal_path=wal_path)
            visitor = ExecuteVisitor(sm)
            execute(visitor, "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;")
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (1, 'Ana');")
            # Simula un crash: el WAL queda durable, pero no se ejecuta el
            # cierre normal que haria rollback antes de cerrar el manager.
            sm.log_manager.force()
            for table in sm.tables.values():
                table.close()
            sm.catalog.close()
            sm.index_manager.close()
            sm.log_manager.close()

            reopened = StorageManager(wal_path=wal_path)
            reader = ExecuteVisitor(reopened)
            assert execute(reader, "SELECT * FROM ventas;")[0].filas == []
            assert reopened.recovered_transactions
            reopened.close()
        finally:
            os.chdir(old_directory)


def test_recovery_preserves_committed_insert():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            wal_path = os.path.join(directory, "wal.log")
            sm = StorageManager(wal_path=wal_path)
            visitor = ExecuteVisitor(sm)
            execute(visitor, "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;")
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (2, 'Beto'); END TRANSACTION;")
            sm.close()

            reopened = StorageManager(wal_path=wal_path)
            reader = ExecuteVisitor(reopened)
            assert execute(reader, "SELECT * FROM ventas;")[0].filas == [[2, "Beto"]]
            reopened.close()
        finally:
            os.chdir(old_directory)


def test_recovery_rolls_back_uncommitted_update_after_reopen():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            wal_path = os.path.join(directory, "wal.log")
            sm = StorageManager(wal_path=wal_path)
            visitor = ExecuteVisitor(sm)
            execute(visitor, "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;")
            execute(visitor, "INSERT INTO ventas VALUES (3, 'Ana');")
            execute(visitor, "BEGIN TRANSACTION; UPDATE ventas SET nombre = 'Beto' WHERE id = 3;")
            sm.log_manager.force()
            for table in sm.tables.values():
                table.close()
            sm.catalog.close()
            sm.index_manager.close()
            sm.log_manager.close()

            reopened = StorageManager(wal_path=wal_path)
            reader = ExecuteVisitor(reopened)
            assert execute(reader, "SELECT * FROM ventas;")[0].filas == [[3, "Ana"]]
            reopened.close()
        finally:
            os.chdir(old_directory)


def test_recovery_redoes_committed_insert_if_data_page_is_missing():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            wal_path = os.path.join(directory, "wal.log")
            sm = StorageManager(wal_path=wal_path)
            visitor = ExecuteVisitor(sm)
            execute(visitor, "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;")
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (4, 'Dina'); END TRANSACTION;")
            sm.tables["ventas"].delete_by_key(4)
            sm.tables["ventas"].close()
            sm.catalog.close()
            sm.index_manager.close()
            sm.log_manager.close()

            reopened = StorageManager(wal_path=wal_path)
            reader = ExecuteVisitor(reopened)
            assert execute(reader, "SELECT * FROM ventas;")[0].filas == [[4, "Dina"]]
            reopened.close()
        finally:
            os.chdir(old_directory)


if __name__ == "__main__":
    test_recovery_rolls_back_uncommitted_insert_after_reopen()
    test_recovery_preserves_committed_insert()
    test_recovery_rolls_back_uncommitted_update_after_reopen()
    test_recovery_redoes_committed_insert_if_data_page_is_missing()
    print("test_recovery_manager: OK")