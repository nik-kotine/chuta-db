"""Recovery logico de transacciones incompletas.

La Fase 5 registra operaciones CRUD con suficiente informacion para deshacer
un insert o delete. Este manager usa esos registros durante el arranque; el
redo fisico de paginas quedara para cuando el almacenamiento tenga page_lsn.
"""

from storage.log_manager import LogManager, LogRecordType
from storage.transaction_manager import TransactionManager


class RecoveryManager:
    """Revisa el WAL y revierte transacciones sin COMMIT."""

    def __init__(self, transaction_manager: TransactionManager, undo_handler):
        """Recibe el manager y una funcion que restaura un ``LogRecord``."""
        self.transaction_manager = transaction_manager
        self.log_manager: LogManager = transaction_manager.log_manager
        self.undo_handler = undo_handler

    def recover(self) -> list[int]:
        """Aborta y deshace transacciones activas encontradas en el WAL.

        Devuelve los IDs recuperados. Las transacciones ya confirmadas no se
        tocan: sus cambios son el estado durable que debe conservarse.
        """
        recovered = []
        for transaction in self.transaction_manager.active_transactions():
            self.transaction_manager.rollback(
                transaction.transaction_id, self.undo_handler
            )
            recovered.append(transaction.transaction_id)
        return recovered

    def checkpoint(self) -> int:
        """Escribe un checkpoint durable del estado actual del WAL."""
        lsn = self.log_manager.append(
            LogRecordType.CHECKPOINT,
            0,
            operation="transaction_checkpoint",
        )
        self.log_manager.force()
        return lsn