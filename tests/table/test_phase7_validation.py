import os
import tempfile
import threading

from parser import Parser
from parser.executor import ExecuteVisitor, ExecutionError
from parser.scanner import Scanner
from storage.lock_manager import LockManager, LockMode, LockTimeoutError
from storage.storage_manager import StorageManager


def execute(visitor, sql):
    return visitor.ejecutar(Parser(Scanner(sql)).parse_p())


def test_error_aborts_transaction_and_restores_previous_state():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            sm = StorageManager(wal_path=os.path.join(directory, "wal.log"))
            visitor = ExecuteVisitor(sm)
            execute(visitor, "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;")
            execute(visitor, "INSERT INTO ventas VALUES (1, 'Ana');")

            try:
                execute(
                    visitor,
                    "BEGIN TRANSACTION; INSERT INTO ventas VALUES (2, 'Beto'); "
                    "INSERT INTO ventas VALUES (1, 'duplicado');",
                )
                assert False, "la clave duplicada debia abortar la transaccion"
            except Exception:
                pass

            assert visitor.transaction_id is None
            assert execute(visitor, "SELECT * FROM ventas;")[0].filas == [[1, "Ana"]]
            assert not sm.lock_manager.held_resources(visitor.session_id)
            sm.close()
        finally:
            os.chdir(old_directory)


def test_writer_competes_deterministically_with_reader():
    manager = LockManager()
    resource = ("table", "ventas")
    reader_ready = threading.Event()
    release_reader = threading.Event()
    writer_acquired = threading.Event()

    def reader():
        manager.acquire(resource, 1, LockMode.SHARED)
        reader_ready.set()
        release_reader.wait(1)
        manager.release(resource, 1)

    def writer():
        reader_ready.wait(1)
        manager.acquire(resource, 2, LockMode.EXCLUSIVE, timeout=1)
        writer_acquired.set()
        manager.release(resource, 2)

    reader_thread = threading.Thread(target=reader)
    writer_thread = threading.Thread(target=writer)
    reader_thread.start()
    writer_thread.start()
    assert reader_ready.wait(1)
    assert not writer_acquired.wait(0.05)
    release_reader.set()
    assert writer_acquired.wait(1)
    reader_thread.join()
    writer_thread.join()


def test_timeout_is_reported_and_does_not_leave_waiter_state():
    manager = LockManager()
    resource = ("table", "ventas")
    manager.acquire(resource, 10, LockMode.EXCLUSIVE)

    try:
        manager.acquire(resource, 11, LockMode.EXCLUSIVE, timeout=0.01)
        assert False, "se esperaba un timeout"
    except LockTimeoutError:
        pass

    assert not manager.held_resources(11)
    manager.release(resource, 10)


def test_recovery_is_idempotent_for_uncommitted_transaction():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            wal_path = os.path.join(directory, "wal.log")
            sm = StorageManager(wal_path=wal_path)
            visitor = ExecuteVisitor(sm)
            execute(visitor, "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;")
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (3, 'Cris');")
            sm.log_manager.force()
            for table in sm.tables.values():
                table.close()
            sm.catalog.close()
            sm.index_manager.close()
            sm.log_manager.close()

            first = StorageManager(wal_path=wal_path)
            assert execute(ExecuteVisitor(first), "SELECT * FROM ventas;")[0].filas == []
            first.close()

            second = StorageManager(wal_path=wal_path)
            assert execute(ExecuteVisitor(second), "SELECT * FROM ventas;")[0].filas == []
            assert second.recovered_transactions == []
            second.close()
        finally:
            os.chdir(old_directory)


if __name__ == "__main__":
    test_error_aborts_transaction_and_restores_previous_state()
    test_writer_competes_deterministically_with_reader()
    test_timeout_is_reported_and_does_not_leave_waiter_state()
    test_recovery_is_idempotent_for_uncommitted_transaction()
    print("test_phase7_validation: OK")