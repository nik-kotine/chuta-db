import json
import os
import tempfile
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "parser"))

from executor import ExecuteVisitor
from parser import Parser
from scanner import Scanner
from storage.storage_manager import StorageManager
from storage.file_manager import FileManager
from storage.log_manager import LogManager, LogRecordType


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


def test_recovery_redoes_committed_physical_page_change():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            wal_path = os.path.join(directory, "wal.log")
            filename = "physical.dat"
            file_manager = FileManager(filename, 4096, 24)
            page = bytearray(4096)
            page[:3] = b"old"
            file_manager.allocate_page()
            file_manager.write_page(0, page)
            file_manager.force()
            file_manager.close()

            log_manager = LogManager(wal_path)
            begin_lsn = log_manager.append(LogRecordType.BEGIN, 1)
            update_lsn = log_manager.append(
                LogRecordType.UPDATE,
                1,
                prev_lsn=begin_lsn,
                operation="physical_page",
                file_name=filename,
                resource_type="page",
                page_id=0,
                offset=0,
                before=b"old",
                after=b"new",
            )
            log_manager.append(
                LogRecordType.COMMIT,
                1,
                prev_lsn=update_lsn,
            )
            log_manager.force()
            log_manager.close()

            reopened = StorageManager(wal_path=wal_path)
            physical = FileManager(filename, 4096, 24)
            assert physical.read_page(0)[:3] == b"new"
            physical.close()
            reopened.close()
        finally:
            os.chdir(old_directory)


def test_recovery_rolls_back_uncommitted_create_table():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            wal_path = os.path.join(directory, "wal.log")
            sm = StorageManager(wal_path=wal_path)
            visitor = ExecuteVisitor(sm)
            execute(visitor, "BEGIN TRANSACTION; CREATE TABLE temporal (id INT PRIMARY KEY) USING HEAP;")
            sm.log_manager.force()
            for table in sm.tables.values():
                table.close()
            sm.catalog.close()
            sm.index_manager.close()
            sm.log_manager.close()

            reopened = StorageManager(wal_path=wal_path)
            assert reopened.catalog.get_table_info("temporal") is None
            reopened.close()
        finally:
            os.chdir(old_directory)


if __name__ == "__main__":
    test_recovery_rolls_back_uncommitted_insert_after_reopen()
    test_recovery_preserves_committed_insert()
    test_recovery_rolls_back_uncommitted_update_after_reopen()
    test_recovery_redoes_committed_insert_if_data_page_is_missing()
    test_recovery_redoes_committed_physical_page_change()
    test_recovery_rolls_back_uncommitted_create_table()
    print("test_recovery_manager: OK")