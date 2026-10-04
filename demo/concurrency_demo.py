"""Demo reproducible de transacciones y concurrencia.
"""

import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from storage.lock_manager import LockManager, LockMode
from storage.log_manager import LogManager
from storage.transaction_manager import TransactionManager


RESOURCE = ("table", "cuentas")


def main():
    """Ejecuta dos transacciones que compiten por el mismo recurso."""
    with tempfile.TemporaryDirectory() as directory:
        log = LogManager(os.path.join(directory, "demo.wal"))
        transactions = TransactionManager(log)
        locks = LockManager()
        barrier = threading.Barrier(2)
        first_lock_held = threading.Event()
        second_waiting = threading.Event()
        release_first = threading.Event()
        results = []
        results_lock = threading.Lock()

        def print_event(message):
            with results_lock:
                print(message, flush=True)

        def first_transaction():
            transaction = transactions.begin()
            print_event(f"[T{transaction.transaction_id}] BEGIN")
            barrier.wait()
            locks.acquire(RESOURCE, transaction.transaction_id, LockMode.EXCLUSIVE)
            print_event(f"[T{transaction.transaction_id}] obtuvo EXCLUSIVE sobre {RESOURCE}")
            first_lock_held.set()
            second_waiting.wait(1)
            print_event(f"[T{transaction.transaction_id}] ejecuta UPDATE logico: saldo = 1")
            transactions.log_update(
                transaction.transaction_id,
                operation="demo_update",
                file_name="cuentas",
                resource_type="logical_value",
                before=b"0",
                after=b"1",
            )
            transactions.commit(transaction.transaction_id)
            locks.release_all(transaction.transaction_id)
            with results_lock:
                results.append((transaction.transaction_id, "COMMITTED", 1))
            print_event(f"[T{transaction.transaction_id}] COMMIT y libera el lock")
            release_first.set()

        def second_transaction():
            transaction = transactions.begin()
            print_event(f"[T{transaction.transaction_id}] BEGIN")
            barrier.wait()
            first_lock_held.wait(1)
            second_waiting.set()
            print_event(f"[T{transaction.transaction_id}] intenta EXCLUSIVE y queda esperando")
            locks.acquire(RESOURCE, transaction.transaction_id, LockMode.EXCLUSIVE, timeout=2)
            print_event(f"[T{transaction.transaction_id}] obtiene EXCLUSIVE despues de T1")
            print_event(f"[T{transaction.transaction_id}] ejecuta UPDATE logico: saldo = 2")
            transactions.log_update(
                transaction.transaction_id,
                operation="demo_update",
                file_name="cuentas",
                resource_type="logical_value",
                before=b"1",
                after=b"2",
            )
            transactions.commit(transaction.transaction_id)
            locks.release_all(transaction.transaction_id)
            with results_lock:
                results.append((transaction.transaction_id, "COMMITTED", 2))
            print_event(f"[T{transaction.transaction_id}] COMMIT y libera el lock")

        first = threading.Thread(target=first_transaction, name="transaction-1")
        second = threading.Thread(target=second_transaction, name="transaction-2")
        first.start()
        second.start()
        first.join()
        second.join()

        assert not first.is_alive() and not second.is_alive()
        assert not locks.held_resources(1)
        assert not locks.held_resources(2)
        print("\nResultado final: saldo = 2")
        print("La segunda transaccion no interleavo su UPDATE: espero el lock y luego continuo.")
        print(f"Transacciones confirmadas: {len(results)}")
        transactions.close()


if __name__ == "__main__":
    main()
