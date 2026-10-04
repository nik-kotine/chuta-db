from storage.log_manager import LogManager, LogRecordType
from storage.transaction_manager import TransactionManager


class RecoveryManager:
    """Reaplica commits y revierte transacciones sin COMMIT."""

    def __init__(self, transaction_manager: TransactionManager, undo_handler,
                 redo_handler=None, physical_redo_handler=None):
        """Recibe callbacks para aplicar redo y restaurar undo lógico."""
        self.transaction_manager = transaction_manager
        self.log_manager: LogManager = transaction_manager.log_manager
        self.undo_handler = undo_handler
        self.redo_handler = redo_handler
        self.physical_redo_handler = physical_redo_handler

    def recover(self) -> list[int]:
        """Reaplica commits y deshace transacciones activas encontradas en el WAL.

        Devuelve los IDs recuperados. El redo lógico es idempotente, por lo que
        puede ejecutarse aunque parte del estado confirmado ya esté en disco.
        """
        if self.redo_handler is not None:
            committed_ids = {
                transaction.transaction_id
                for transaction in self.transaction_manager.committed_transactions()
            }
            committed_updates = [
                record
                for record in self.log_manager.iter_records()
                if (
                    record.record_type == LogRecordType.UPDATE
                    and record.transaction_id in committed_ids
                )
            ]
            physical_updates = [
                record for record in committed_updates
                if record.resource_type in ("page", "header", "allocation", "truncate")
            ]
            if self.physical_redo_handler is not None:
                groups = {}
                for record in physical_updates:
                    groups.setdefault(
                        (record.file_name, record.resource_type,
                         record.page_id, record.offset), []
                    ).append(record)
                for records in groups.values():
                    self.physical_redo_handler(records)
            else:
                for record in physical_updates:
                    self.redo_handler(record)
            for record in committed_updates:
                if record.resource_type not in ("page", "header", "allocation", "truncate"):
                    self.redo_handler(record)

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