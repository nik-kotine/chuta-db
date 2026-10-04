import os
import tempfile
import threading

from storage.log_manager import LogManager, LogRecordType
from storage.transaction_manager import (
    TransactionError,
    TransactionManager,
    TransactionStatus,
)


def test_begin_and_commit_are_durable():
    with tempfile.TemporaryDirectory() as directory:
        log = LogManager(os.path.join(directory, "wal.log"))
        manager = TransactionManager(log)
        transaction = manager.begin()
        manager.commit(transaction.transaction_id)

        assert manager.get(transaction.transaction_id).status == TransactionStatus.COMMITTED
        assert [record.record_type for record in log.read_all().records] == [
            LogRecordType.BEGIN,
            LogRecordType.COMMIT,
        ]
        manager.close()


def test_rollback_undoes_in_reverse_and_writes_clrs():
    with tempfile.TemporaryDirectory() as directory:
        log = LogManager(os.path.join(directory, "wal.log"))
        manager = TransactionManager(log)
        transaction = manager.begin()
        manager.log_update(transaction.transaction_id, operation="first", before=b"1", after=b"2")
        manager.log_update(transaction.transaction_id, operation="second", before=b"2", after=b"3")
        undone = []

        manager.rollback(transaction.transaction_id, undone.append)

        assert [record.operation for record in undone] == ["second", "first"]
        assert manager.get(transaction.transaction_id).status == TransactionStatus.ABORTED
        assert sum(record.record_type == LogRecordType.CLR for record in log.read_all().records) == 2
        manager.close()


def test_rollback_requires_undo_handler_for_updates():
    with tempfile.TemporaryDirectory() as directory:
        log = LogManager(os.path.join(directory, "wal.log"))
        manager = TransactionManager(log)
        transaction = manager.begin()
        manager.log_update(transaction.transaction_id, before=b"old", after=b"new")

        try:
            manager.rollback(transaction.transaction_id)
            assert False, "rollback debia exigir undo_handler"
        except TransactionError:
            pass

        assert manager.get(transaction.transaction_id).status == TransactionStatus.ACTIVE
        manager.close(lambda record: None)


def test_reopen_recovers_state_and_next_id():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "wal.log")
        log = LogManager(path)
        manager = TransactionManager(log)
        transaction = manager.begin()
        manager.commit(transaction.transaction_id)
        manager.close()

        reopened_log = LogManager(path)
        reopened_manager = TransactionManager(reopened_log)
        next_transaction = reopened_manager.begin()

        assert next_transaction.transaction_id > transaction.transaction_id
        assert reopened_manager.get(transaction.transaction_id).status == TransactionStatus.COMMITTED
        reopened_manager.close()


def test_concurrent_begin_uses_unique_ids():
    with tempfile.TemporaryDirectory() as directory:
        log = LogManager(os.path.join(directory, "wal.log"))
        manager = TransactionManager(log)
        ids = []
        ids_lock = threading.Lock()

        def begin_transaction():
            transaction_id = manager.begin().transaction_id
            with ids_lock:
                ids.append(transaction_id)

        threads = [threading.Thread(target=begin_transaction) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(ids) == list(range(1, 9))
        manager.close(lambda record: None)


if __name__ == "__main__":
    test_begin_and_commit_are_durable()
    test_rollback_undoes_in_reverse_and_writes_clrs()
    test_rollback_requires_undo_handler_for_updates()
    test_reopen_recovers_state_and_next_id()
    test_concurrent_begin_uses_unique_ids()
    print("test_transaction_manager: OK")