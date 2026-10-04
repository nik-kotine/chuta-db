"""Coordinador del ciclo de vida de las transacciones.

La integracion con Table y los locks pertenece a fases posteriores. En esta
fase el manager coordina estados y eventos del WAL, y expone un callback para
que la futura capa de almacenamiento aplique undo sobre cambios fisicos.
"""

from dataclasses import dataclass
from enum import Enum, auto
import threading
from typing import Callable

from storage.log_manager import LogManager, LogRecord, LogRecordType


class TransactionError(RuntimeError):
	"""Error de uso o de estado del TransactionManager."""


class TransactionStatus(Enum):
	"""Estados posibles de una transaccion."""

	ACTIVE = auto()
	COMMITTED = auto()
	ABORTED = auto()


@dataclass
class Transaction:
	"""Estado en memoria asociado a una transaccion del WAL."""

	transaction_id: int
	status: TransactionStatus
	last_lsn: int


UndoHandler = Callable[[LogRecord], None]


class TransactionManager:
	"""Administra transacciones y su relacion con el Write-Ahead Log.

	El lock interno protege el diccionario de estados y la cadena de CLRs. El
	callback de undo se ejecuta dentro de la seccion protegida para impedir que
	otra operacion use la transaccion mientras sus cambios se estan revirtiendo.
	"""

	def __init__(self, log_manager: LogManager):
		"""Carga el siguiente ID y reconstruye estados conocidos del WAL."""
		if not isinstance(log_manager, LogManager):
			raise TypeError("log_manager debe ser una instancia de LogManager")

		self.log_manager = log_manager
		self._lock = threading.RLock()
		self._transactions: dict[int, Transaction] = {}
		self._next_transaction_id = 1
		self._load_log_state()

	def _load_log_state(self) -> None:
		"""Reconstruye estados a partir de BEGIN/COMMIT/ABORT existentes."""
		for record in self.log_manager.iter_records():
			if record.record_type in (LogRecordType.CHECKPOINT, LogRecordType.CLR):
				continue
			self._next_transaction_id = max(
				self._next_transaction_id, record.transaction_id + 1
			)
			transaction = self._transactions.get(record.transaction_id)
			if transaction is None:
				transaction = Transaction(
					record.transaction_id,
					TransactionStatus.ACTIVE,
					record.lsn,
				)
				self._transactions[record.transaction_id] = transaction
			transaction.last_lsn = record.lsn
			if record.record_type == LogRecordType.COMMIT:
				transaction.status = TransactionStatus.COMMITTED
			elif record.record_type == LogRecordType.ABORT:
				transaction.status = TransactionStatus.ABORTED

	def begin(self) -> Transaction:
		"""Inicia una transaccion y fuerza su registro BEGIN al WAL."""
		with self._lock:
			transaction_id = self._next_transaction_id
			self._next_transaction_id += 1
			lsn = self.log_manager.append(LogRecordType.BEGIN, transaction_id)
			self.log_manager.force()
			transaction = Transaction(
				transaction_id, TransactionStatus.ACTIVE, lsn
			)
			self._transactions[transaction_id] = transaction
			return transaction

	def get(self, transaction_id: int) -> Transaction:
		"""Devuelve una transaccion conocida o lanza TransactionError."""
		with self._lock:
			try:
				return self._transactions[transaction_id]
			except KeyError as error:
				raise TransactionError(
					f"transaccion desconocida: {transaction_id}"
				) from error

	def get_active(self, transaction_id: int) -> Transaction:
		"""Valida que una transaccion exista y siga activa."""
		transaction = self.get(transaction_id)
		if transaction.status != TransactionStatus.ACTIVE:
			raise TransactionError(
				f"la transaccion {transaction_id} no esta activa"
			)
		return transaction

	def log_update(self, transaction_id: int, **change) -> int:
		"""Registra una mutacion fisica y actualiza el last_lsn.

		``change`` contiene argumentos de LogManager.append, como before,
		after, page_id y operation. El caller debe hacer force antes de
		escribir la pagina para respetar WAL.
		"""
		with self._lock:
			transaction = self.get_active(transaction_id)
			lsn = self.log_manager.append(
				LogRecordType.UPDATE, transaction_id, **change
			)
			transaction.last_lsn = lsn
			return lsn

	def commit(self, transaction_id: int) -> Transaction:
		"""Escribe un COMMIT durable y marca la transaccion como confirmada."""
		with self._lock:
			transaction = self.get_active(transaction_id)
			lsn = self.log_manager.append(
				LogRecordType.COMMIT,
				transaction_id,
				prev_lsn=transaction.last_lsn,
			)
			# La transaccion solo se considera confirmada despues del force.
			self.log_manager.force()
			transaction.last_lsn = lsn
			transaction.status = TransactionStatus.COMMITTED
			return transaction

	def rollback(
		self,
		transaction_id: int,
		undo_handler: UndoHandler | None = None,
	) -> Transaction:
		"""Deshace UPDATE en orden inverso y registra un CLR por cada undo.

		``undo_handler`` recibe cada registro original y debe restaurar su
		imagen before. El manager no conoce paginas ni formatos fisicos.
		"""
		with self._lock:
			transaction = self.get_active(transaction_id)
			records = [
				record
				for record in self.log_manager.iter_records()
				if record.transaction_id == transaction_id
				and record.record_type == LogRecordType.UPDATE
			]

		if records and undo_handler is None:
			raise TransactionError(
				"rollback necesita undo_handler para restaurar cambios"
			)

		with self._lock:
			transaction = self.get_active(transaction_id)
			abort_lsn = self.log_manager.append(
				LogRecordType.ABORT,
				transaction_id,
				prev_lsn=transaction.last_lsn,
			)
			transaction.last_lsn = abort_lsn
			self.log_manager.force()

			for record in reversed(records):
				undo_handler(record)
				clr_lsn = self.log_manager.append(
					LogRecordType.CLR,
					transaction_id,
					prev_lsn=transaction.last_lsn,
					operation="undo" if record.operation is None else record.operation,
					file_name=record.file_name,
					resource_type=record.resource_type,
					page_id=record.page_id,
					offset=record.offset,
					before=record.after,
					after=record.before,
					page_lsn=record.page_lsn,
				)
				transaction.last_lsn = clr_lsn

			self.log_manager.force()
			transaction.status = TransactionStatus.ABORTED
			return transaction

	def abort(
		self,
		transaction_id: int,
		undo_handler: UndoHandler | None = None,
	) -> Transaction:
		"""Alias de rollback para abortos provocados por errores."""
		return self.rollback(transaction_id, undo_handler)

	def active_transactions(self) -> list[Transaction]:
		"""Devuelve una instantanea de las transacciones activas."""
		with self._lock:
			return [
				transaction
				for transaction in self._transactions.values()
				if transaction.status == TransactionStatus.ACTIVE
			]

	def close(self, undo_handler: UndoHandler | None = None) -> None:
		"""Aborta transacciones activas y cierra el WAL de forma durable."""
		for transaction in self.active_transactions():
			if undo_handler is None:
				# El cierre del motor no puede dejar una transaccion abierta en el
				# estado logico, aunque el undo fisico lo haga RecoveryManager.
				self.log_manager.append(
					LogRecordType.ABORT,
					transaction.transaction_id,
					prev_lsn=transaction.last_lsn,
				)
				transaction.status = TransactionStatus.ABORTED
			else:
				self.rollback(transaction.transaction_id, undo_handler)
		self.log_manager.force()
		self.log_manager.close()
