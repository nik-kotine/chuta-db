"""Administrador de locks para la fase de concurrencia.

Esta fase trabaja con locks logicos sobre recursos identificables, por ejemplo
``("table", "ventas")``. La integracion con Table y el executor se hara en
una fase posterior; aqui solo se garantiza la coordinacion entre hilos.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
import threading
import time


class LockError(RuntimeError):
    """Error de ownership, modo o estado de un lock."""


class LockTimeoutError(LockError):
    """La transaccion no pudo adquirir el lock antes del timeout."""


class LockMode(Enum):
    """Modos soportados por el lock manager."""

    SHARED = auto()
    EXCLUSIVE = auto()


@dataclass
class _LockEntry:
    """Estado interno de un recurso protegido."""

    shared: dict[int, int] = field(default_factory=dict)
    exclusive_owner: int | None = None
    exclusive_count: int = 0
    waiting_writers: int = 0


class LockManager:
    """Coordina locks shared/exclusive con espera y timeout.

    Un mismo transaction ID puede adquirir repetidamente el mismo lock. Cada
    adquisicion incrementa un contador y cada ``release`` decrementa uno, lo
    que evita liberar un lock que aun tiene usos pendientes.
    """

    def __init__(self):
        """Inicializa la tabla de recursos y la condicion de espera global."""
        self._condition = threading.Condition(threading.RLock())
        self._locks: dict[object, _LockEntry] = {}
        self._held_by_transaction: dict[int, dict[object, LockMode]] = {}

    @staticmethod
    def _validate_mode(mode: LockMode) -> None:
        if not isinstance(mode, LockMode):
            raise TypeError("mode debe ser LockMode.SHARED o LockMode.EXCLUSIVE")

    @staticmethod
    def _validate_transaction(transaction_id: int) -> None:
        if not isinstance(transaction_id, int) or transaction_id < 0:
            raise ValueError("transaction_id debe ser un entero no negativo")

    def _entry(self, resource: object) -> _LockEntry:
        return self._locks.setdefault(resource, _LockEntry())

    @staticmethod
    def _can_share(entry: _LockEntry, transaction_id: int) -> bool:
        return entry.exclusive_owner in (None, transaction_id)

    @staticmethod
    def _can_exclude(entry: _LockEntry, transaction_id: int) -> bool:
        other_shared = any(owner != transaction_id for owner in entry.shared)
        return (
            entry.exclusive_owner in (None, transaction_id)
            and not other_shared
        )

    def acquire(
        self,
        resource: object,
        transaction_id: int,
        mode: LockMode,
        timeout: float | None = None,
    ) -> None:
        """Adquiere un lock o espera hasta ``timeout`` segundos.

        Los lectores son compatibles entre si. Un escritor espera a cualquier
        lector o escritor distinto. Si la transaccion ya posee SHARED y pide
        EXCLUSIVE, se intenta un upgrade; dos upgrades simultaneos pueden
        terminar por timeout, evitando un deadlock infinito.
        """
        self._validate_transaction(transaction_id)
        self._validate_mode(mode)
        if timeout is not None and timeout < 0:
            raise ValueError("timeout no puede ser negativo")

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            entry = self._entry(resource)
            if mode == LockMode.EXCLUSIVE:
                entry.waiting_writers += 1
            try:
                while True:
                    can_acquire = (
                        self._can_share(entry, transaction_id)
                        and (
                            mode == LockMode.SHARED
                            and (
                                entry.waiting_writers == 0
                                or transaction_id in entry.shared
                                or entry.exclusive_owner == transaction_id
                            )
                            or mode == LockMode.EXCLUSIVE
                            and self._can_exclude(entry, transaction_id)
                        )
                    )
                    if can_acquire:
                        self._grant(entry, resource, transaction_id, mode)
                        return

                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        raise LockTimeoutError(
                            f"timeout adquiriendo {mode.name} sobre {resource!r}"
                        )
                    self._condition.wait(remaining)
            finally:
                if mode == LockMode.EXCLUSIVE:
                    entry.waiting_writers -= 1

    def _grant(
        self,
        entry: _LockEntry,
        resource: object,
        transaction_id: int,
        mode: LockMode,
    ) -> None:
        """Registra ownership despues de que ``acquire`` valida compatibilidad."""
        if mode == LockMode.SHARED:
            if entry.exclusive_owner != transaction_id:
                entry.shared[transaction_id] = entry.shared.get(transaction_id, 0) + 1
        else:
            # Un upgrade elimina el contador shared propio antes de promover.
            entry.shared.pop(transaction_id, None)
            if entry.exclusive_owner == transaction_id:
                entry.exclusive_count += 1
            else:
                entry.exclusive_owner = transaction_id
                entry.exclusive_count = 1
        held = self._held_by_transaction.setdefault(transaction_id, {})
        held[resource] = mode

    def release(self, resource: object, transaction_id: int) -> None:
        """Libera una adquisicion del recurso para la transaccion indicada."""
        self._validate_transaction(transaction_id)
        with self._condition:
            entry = self._locks.get(resource)
            if entry is None:
                raise LockError(f"recurso sin locks: {resource!r}")

            if entry.exclusive_owner == transaction_id:
                entry.exclusive_count -= 1
                if entry.exclusive_count == 0:
                    entry.exclusive_owner = None
            elif transaction_id in entry.shared:
                entry.shared[transaction_id] -= 1
                if entry.shared[transaction_id] == 0:
                    del entry.shared[transaction_id]
            else:
                raise LockError(
                    f"la transaccion {transaction_id} no posee {resource!r}"
                )

            self._remove_held_reference(resource, transaction_id)
            self._cleanup(resource, entry)
            self._condition.notify_all()

    def release_all(self, transaction_id: int) -> None:
        """Libera todos los recursos retenidos por una transaccion."""
        self._validate_transaction(transaction_id)
        while True:
            with self._condition:
                resources = list(self._held_by_transaction.get(transaction_id, {}))
            if not resources:
                return
            for resource in resources:
                while True:
                    try:
                        self.release(resource, transaction_id)
                    except LockError:
                        break
                    with self._condition:
                        entry = self._locks.get(resource)
                        owned = (
                            entry is not None
                            and (
                                entry.exclusive_owner == transaction_id
                                or transaction_id in entry.shared
                            )
                        )
                    if not owned:
                        break

    def _remove_held_reference(self, resource: object, transaction_id: int) -> None:
        held = self._held_by_transaction.get(transaction_id)
        if held is None:
            return
        entry = self._locks.get(resource)
        if entry is None or (
            entry.exclusive_owner != transaction_id
            and transaction_id not in entry.shared
        ):
            held.pop(resource, None)
        if not held:
            self._held_by_transaction.pop(transaction_id, None)

    def _cleanup(self, resource: object, entry: _LockEntry) -> None:
        if (
            not entry.shared
            and entry.exclusive_owner is None
            and entry.waiting_writers == 0
        ):
            self._locks.pop(resource, None)

    def held_resources(self, transaction_id: int) -> dict[object, LockMode]:
        """Devuelve una copia de los recursos retenidos por una transaccion."""
        self._validate_transaction(transaction_id)
        with self._condition:
            return dict(self._held_by_transaction.get(transaction_id, {}))