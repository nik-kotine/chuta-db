import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "parser"))

from parser.executor import ExecuteVisitor, ExecutionError
from parser import Parser
from parser.scanner import Scanner
from storage.storage_manager import StorageManager


def execute(visitor, sql):
    program = Parser(Scanner(sql)).parse_p()
    return visitor.ejecutar(program)


def test_begin_end_and_rollback_update_session_state():
    with tempfile.TemporaryDirectory() as directory:
        sm = StorageManager(wal_path=os.path.join(directory, "wal.log"))
        visitor = ExecuteVisitor(sm)

        execute(visitor, "BEGIN TRANSACTION;")
        assert visitor.transaction_id is not None
        execute(visitor, "END TRANSACTION;")
        assert visitor.transaction_id is None

        execute(visitor, "BEGIN TRANSACTION; ROLLBACK;")
        assert visitor.transaction_id is None
        sm.close()


def test_invalid_transaction_commands_fail():
    with tempfile.TemporaryDirectory() as directory:
        sm = StorageManager(wal_path=os.path.join(directory, "wal.log"))
        visitor = ExecuteVisitor(sm)

        for sql in ("END TRANSACTION;", "ROLLBACK;"):
            try:
                execute(visitor, sql)
                assert False, "el comando debia requerir una transaccion"
            except ExecutionError:
                pass

        execute(visitor, "BEGIN TRANSACTION;")
        try:
            execute(visitor, "BEGIN TRANSACTION;")
            assert False, "BEGIN duplicado debia fallar"
        except ExecutionError:
            pass
        assert visitor.transaction_id is None
        sm.close()


def test_autocommit_releases_lock_after_insert():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            sm = StorageManager(wal_path=os.path.join(directory, "wal.log"))
            visitor = ExecuteVisitor(sm)
            execute(
                visitor,
                "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;",
            )
            execute(visitor, "INSERT INTO ventas VALUES (1, 'Ana');")

            assert not sm.lock_manager.held_resources(visitor.session_id)
            sm.close()
        finally:
            os.chdir(old_directory)


def test_explicit_transaction_keeps_lock_until_end():
    with tempfile.TemporaryDirectory() as directory:
        old_directory = os.getcwd()
        os.chdir(directory)
        try:
            sm = StorageManager(wal_path=os.path.join(directory, "wal.log"))
            visitor = ExecuteVisitor(sm)
            execute(
                visitor,
                "CREATE TABLE ventas (id INT PRIMARY KEY, nombre VARCHAR(20)) USING HEAP;",
            )
            execute(visitor, "BEGIN TRANSACTION; INSERT INTO ventas VALUES (1, 'Ana');")

            assert sm.lock_manager.held_resources(visitor.transaction_id)
            execute(visitor, "END TRANSACTION;")
            assert visitor.transaction_id is None
            sm.close()
        finally:
            os.chdir(old_directory)


if __name__ == "__main__":
    test_begin_end_and_rollback_update_session_state()
    test_invalid_transaction_commands_fail()
    test_autocommit_releases_lock_after_insert()
    test_explicit_transaction_keeps_lock_until_end()
    print("test_phase4_transactions: OK")