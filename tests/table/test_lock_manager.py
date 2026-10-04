import threading

from storage.lock_manager import LockError, LockManager, LockMode, LockTimeoutError


RESOURCE = ("table", "ventas")


def test_readers_can_share_and_writer_waits():
    manager = LockManager()
    manager.acquire(RESOURCE, 1, LockMode.SHARED)
    manager.acquire(RESOURCE, 2, LockMode.SHARED)
    acquired = threading.Event()

    def writer():
        manager.acquire(RESOURCE, 3, LockMode.EXCLUSIVE, timeout=1)
        acquired.set()
        manager.release(RESOURCE, 3)

    thread = threading.Thread(target=writer)
    thread.start()
    assert not acquired.wait(0.05)
    manager.release(RESOURCE, 1)
    assert not acquired.wait(0.05)
    manager.release(RESOURCE, 2)
    assert acquired.wait(1)
    thread.join()


def test_exclusive_lock_is_reentrant():
    manager = LockManager()
    manager.acquire(RESOURCE, 1, LockMode.EXCLUSIVE)
    manager.acquire(RESOURCE, 1, LockMode.EXCLUSIVE)

    try:
        manager.acquire(RESOURCE, 2, LockMode.SHARED, timeout=0.01)
        assert False, "otro lector no debia entrar"
    except LockTimeoutError:
        pass

    manager.release(RESOURCE, 1)
    assert manager.held_resources(1)
    manager.release(RESOURCE, 1)
    assert not manager.held_resources(1)


def test_shared_to_exclusive_upgrade_waits_for_other_reader():
    manager = LockManager()
    manager.acquire(RESOURCE, 1, LockMode.SHARED)
    manager.acquire(RESOURCE, 2, LockMode.SHARED)

    try:
        manager.acquire(RESOURCE, 1, LockMode.EXCLUSIVE, timeout=0.01)
        assert False, "el upgrade debia esperar al lector 2"
    except LockTimeoutError:
        pass

    manager.release(RESOURCE, 2)
    manager.acquire(RESOURCE, 1, LockMode.EXCLUSIVE)
    manager.release(RESOURCE, 1)


def test_update_lock_is_compatible_with_shared_but_unique():
    manager = LockManager()
    manager.acquire(RESOURCE, 1, LockMode.SHARED)
    manager.acquire(RESOURCE, 2, LockMode.UREAD)

    try:
        manager.acquire(RESOURCE, 3, LockMode.UREAD, timeout=0.01)
        assert False, "solo una transaccion puede poseer UREAD"
    except LockTimeoutError:
        pass

    manager.release(RESOURCE, 1)
    manager.release(RESOURCE, 2)


def test_update_lock_can_be_promoted_to_exclusive():
    manager = LockManager()
    manager.acquire(RESOURCE, 1, LockMode.UREAD)
    manager.acquire(RESOURCE, 2, LockMode.SHARED)

    try:
        manager.acquire(RESOURCE, 1, LockMode.EXCLUSIVE, timeout=0.01)
        assert False, "la promocion debia esperar al lector"
    except LockTimeoutError:
        pass

    manager.release(RESOURCE, 2)
    manager.acquire(RESOURCE, 1, LockMode.EXCLUSIVE)
    manager.release(RESOURCE, 1)


def test_timeout_does_not_leave_waiting_or_owned_lock():
    manager = LockManager()
    manager.acquire(RESOURCE, 1, LockMode.EXCLUSIVE)

    try:
        manager.acquire(RESOURCE, 2, LockMode.EXCLUSIVE, timeout=0.01)
        assert False, "se esperaba timeout"
    except LockTimeoutError:
        pass

    assert not manager.held_resources(2)
    manager.release(RESOURCE, 1)
    manager.acquire(RESOURCE, 2, LockMode.EXCLUSIVE, timeout=0.1)
    manager.release(RESOURCE, 2)


def test_release_all_releases_every_resource():
    manager = LockManager()
    manager.acquire(("table", "ventas"), 7, LockMode.SHARED)
    manager.acquire(("table", "clientes"), 7, LockMode.EXCLUSIVE)

    manager.release_all(7)

    assert not manager.held_resources(7)
    manager.acquire(("table", "ventas"), 8, LockMode.EXCLUSIVE, timeout=0.1)
    manager.release(("table", "ventas"), 8)


def test_releasing_foreign_lock_fails():
    manager = LockManager()
    manager.acquire(RESOURCE, 1, LockMode.SHARED)

    try:
        manager.release(RESOURCE, 2)
        assert False, "la transaccion 2 no era propietaria"
    except LockError:
        pass

    manager.release(RESOURCE, 1)


if __name__ == "__main__":
    test_readers_can_share_and_writer_waits()
    test_exclusive_lock_is_reentrant()
    test_shared_to_exclusive_upgrade_waits_for_other_reader()
    test_update_lock_is_compatible_with_shared_but_unique()
    test_update_lock_can_be_promoted_to_exclusive()
    test_timeout_does_not_leave_waiting_or_owned_lock()
    test_release_all_releases_every_resource()
    test_releasing_foreign_lock_fails()
    print("test_lock_manager: OK")