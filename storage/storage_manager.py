import os
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table
from storage.schema_catalog import SchemaCatalog
from storage.index_manager import IndexManager
from storage.files.sequential_file import FILE_HEADER_SIZE
from storage.log_manager import LogManager
from storage.transaction_manager import TransactionManager
from storage.lock_manager import LockManager
from storage.recovery_manager import RecoveryManager
from storage.rid import RID


class StorageManager:
    """
    Administrador central que coordina el catálogo del sistema en disco
    y el acceso/caché a las tablas físicas.
    """
    def __init__(
        self,
        page_size: int = 4096,
        buffer_frames: int = 10,
        header_size: int = FILE_HEADER_SIZE,
        wal_path: str = "chuta_wal.log"
    ):
        self.page_size = page_size
        self.buffer_frames = buffer_frames
        self.header_size = header_size
        self.log_manager = LogManager(wal_path)
        self.transaction_manager = TransactionManager(self.log_manager)
        self.lock_manager = LockManager()
        self._next_session_id = 1 << 62
        self.current_transaction_id = None

        # Inicializamos el catálogo en disco basado en HeapFiles
        self.catalog = SchemaCatalog(
            page_size=self.page_size,
            header_size=self.header_size,
            buffer_frames=self.buffer_frames
        )
        BufferManager.get_instance().configure_wal(
            self._log_physical_page,
            lambda: self.current_transaction_id,
        )
        FileManager.configure_wal(
            self._log_physical_file,
            lambda: self.current_transaction_id,
        )
        self.index_manager = IndexManager(self.catalog)
        self.tables: dict[str, Table] = {}  # Caché de tablas abiertas en memoria
        self.recovery_manager = RecoveryManager(
            self.transaction_manager,
            self._undo_log_record,
            self._redo_log_record,
            self._redo_physical_pages,
        )
        self.recovered_transactions = self.recovery_manager.recover()

    def _log_physical_page(
        self, transaction_id, file_manager, page_id, before, after
    ):
        self.transaction_manager.log_update(
            transaction_id,
            operation="physical_page",
            file_name=file_manager.filename,
            resource_type="page",
            page_id=page_id,
            before=before,
            after=after,
        )
        self.log_manager.force()

    def _log_physical_file(
        self, transaction_id, file_manager, resource_type,
        page_id, offset, before, after
    ):
        self.transaction_manager.log_update(
            transaction_id,
            operation="physical_file",
            file_name=file_manager.filename,
            resource_type=resource_type,
            page_id=page_id,
            offset=offset,
            before=before,
            after=after,
        )
        self.log_manager.force()

    def allocate_session_id(self) -> int:
        """Entrega un ID separado de los IDs persistidos de transacciones."""
        session_id = self._next_session_id
        self._next_session_id += 1
        return session_id

    def _undo_log_record(self, record):
        """Restaura una mutacion CRUD durante recovery, sin volver a loguearla."""
        import json

        if record.resource_type in ("page", "header", "allocation", "truncate"):
            self._undo_physical_record(record)
            return
        if record.resource_type == "ddl":
            import json
            payload = json.loads(record.after.decode("utf-8"))
            if record.operation == "ddl_create_table":
                self.drop_table(record.file_name)
            elif record.operation == "ddl_create_index":
                self.index_manager.drop_index(payload["index_name"])
            return

        payload_bytes = record.before or record.after
        payload = json.loads(payload_bytes.decode("utf-8"))
        table = self.open_table(record.file_name)
        if record.operation == "table_insert":
            table.delete_by_key(payload["key"])
        elif record.operation == "table_delete":
            table.insert(payload["values"])
        elif record.operation == "table_update":
            old_payload = json.loads(record.before.decode("utf-8"))
            new_payload = json.loads(record.after.decode("utf-8"))
            rid = new_payload.get("rid")
            if rid is not None and table.update(RID(*rid), old_payload["values"]):
                return
            table.delete_by_key(new_payload["key"])
            table.insert(old_payload["values"])
        else:
            raise RuntimeError(
                f"no existe recovery para la operacion '{record.operation}'"
            )

    def _undo_physical_record(self, record):
        """Restaura una imagen física registrada por una transacción abortada."""
        import struct

        if not os.path.exists(record.file_name):
            return
        header_size = (
            len(record.before)
            if record.resource_type == "header" and record.before
            else self.header_size
        )
        file_manager = FileManager(record.file_name, self.page_size, header_size)
        try:
            if record.resource_type == "page":
                BufferManager.get_instance().restore_page(
                    record.page_id, record.before, file_manager
                )
            elif record.resource_type == "header":
                file_manager.write_header(record.before)
                file_manager.force()
            elif record.resource_type == "truncate":
                old_size = struct.unpack(">Q", record.before)[0]
                file_manager.truncate(old_size)
                file_manager.force()
            elif record.resource_type == "allocation":
                size = file_manager.file_header_size + record.page_id * file_manager.page_size
                file_manager.truncate(size)
                file_manager.force()
        finally:
            file_manager.close()

    def _redo_log_record(self, record):
        """Reaplica una mutacion confirmada sin duplicar su efecto."""
        import json

        if record.resource_type == "page" and record.page_id >= 0:
            self._redo_physical_page(record)
            return
        if record.resource_type == "ddl":
            import json
            payload = json.loads(record.after.decode("utf-8"))
            if record.operation == "ddl_create_table":
                if self.catalog.get_table_info(record.file_name) is None:
                    self.create_table(record.file_name, **payload)
            elif record.operation == "ddl_create_index":
                if payload["index_name"] not in self.index_manager.open_indexes:
                    table = self.open_table(record.file_name)
                    if payload["clustered"]:
                        self.index_manager.create_clustered_index(
                            payload["index_name"], table, payload["column_name"]
                        )
                    elif payload["index_kind"] == "HASH_IDX":
                        self.index_manager.create_hash_index(
                            payload["index_name"], table,
                            payload["column_index"], payload["column_name"]
                        )
                    else:
                        self.index_manager.create_unclustered_index(
                            payload["index_name"], table,
                            payload["column_index"], payload["column_name"]
                        )
            return

        table = self.open_table(record.file_name)
        if record.operation == "table_insert":
            payload = json.loads(record.after.decode("utf-8"))
            if not table.search_by_key(payload["key"]):
                table.insert(payload["values"])
            return

        if record.operation == "table_delete":
            payload = json.loads(record.before.decode("utf-8"))
            table.delete_by_key(payload["key"])
            return

        if record.operation == "table_update":
            before = json.loads(record.before.decode("utf-8"))
            after = json.loads(record.after.decode("utf-8"))
            if table.search_by_key(after["key"]):
                return
            for rid, values in table.scan():
                if values[table.key_index] == before["key"]:
                    if table.update(rid, after["values"]):
                        return
            table.insert(after["values"])
            return

        raise RuntimeError(
            f"no existe redo para la operacion '{record.operation}'"
        )

    def _redo_physical_page(self, record):
        """Reaplica una imagen de pagina WAL sin escribirla dos veces."""
        if not record.after:
            raise RuntimeError("un redo de pagina necesita una imagen after")

        table = self.tables.get(record.file_name)
        if table is not None:
            file_manager = table.file_manager
            owns_file = False
        else:
            filename = record.file_name
            if not os.path.exists(filename):
                raise RuntimeError(f"no existe el archivo de pagina '{filename}'")
            file_manager = FileManager(filename, self.page_size, self.header_size)
            owns_file = True

        try:
            current = file_manager.read_page(record.page_id)
            offset = record.offset
            end = offset + len(record.after)
            if offset < 0 or end > self.page_size:
                raise RuntimeError("el rango del redo fisico excede la pagina")
            if current[offset:end] == record.after:
                return
            if record.before and current[offset:end] not in (record.before, record.after):
                raise RuntimeError(
                    "la pagina no coincide con before/after; "
                    f"archivo={record.file_name}, pagina={record.page_id}, "
                    f"offset={record.offset}"
                )
            updated = bytearray(current)
            updated[offset:end] = record.after
            file_manager.write_page(record.page_id, updated)
            file_manager.force()
        finally:
            if owns_file:
                file_manager.close()

    def _redo_physical_pages(self, records):
        """Reproduce una cadena de imágenes físicas de la misma página."""
        first = records[0]
        if first.resource_type in ("header", "allocation"):
            self._redo_file_metadata(records)
            return
        table = self.tables.get(first.file_name)
        owns_file = False
        if table is not None:
            file_manager = table.file_manager
        else:
            if not os.path.exists(first.file_name):
                raise RuntimeError(f"no existe el archivo de pagina '{first.file_name}'")
            file_manager = FileManager(first.file_name, self.page_size, self.header_size)
            owns_file = True

        try:
            current = file_manager.read_page(first.page_id)
            start = None
            for index, record in enumerate(records):
                if current[first.offset:first.offset + len(record.after)] == record.after:
                    start = index + 1
                elif current[first.offset:first.offset + len(record.before)] == record.before:
                    start = index
                    break
            if start is None:
                if current[first.offset:first.offset + len(records[-1].after)] == records[-1].after:
                    return
                # La pagina puede contener cambios posteriores al commit. No
                # se pisa: el redo logico se encargara de reconstruir la fila.
                return

            updated = bytearray(current)
            for record in records[start:]:
                offset = record.offset
                end = offset + len(record.after)
                updated[offset:end] = record.after
            if updated != current:
                file_manager.write_page(first.page_id, updated)
                file_manager.force()
        finally:
            if owns_file:
                file_manager.close()

    def _redo_file_metadata(self, records):
        """Reaplica headers o asignaciones registradas en el WAL."""
        import struct

        first = records[0]
        if not os.path.exists(first.file_name):
            return
        header_size = (
            len(first.before)
            if first.resource_type == "header" and first.before
            else self.header_size
        )
        file_manager = FileManager(first.file_name, self.page_size, header_size)
        try:
            if first.resource_type == "truncate":
                file_manager.file_ptr.seek(0, 2)
                current_size = file_manager.file_ptr.tell()
                for record in records:
                    before = struct.unpack(">Q", record.before)[0]
                    after = struct.unpack(">Q", record.after)[0]
                    if current_size == after:
                        continue
                    if current_size == before:
                        file_manager.truncate(after)
                        file_manager.force()
                        current_size = after
                return

            if first.resource_type == "header":
                current = file_manager.read_header()
                for record in records:
                    if current == record.after:
                        continue
                    if current == record.before:
                        file_manager.write_header(record.after)
                        file_manager.force()
                        current = record.after
                return

            for record in records:
                current = file_manager.read_page(record.page_id)
                if current == record.after:
                    continue
                if not current or current == record.before:
                    file_manager.write_page(record.page_id, record.after)
                    file_manager.force()
        finally:
            file_manager.close()

    def create_table(
        self, 
        name: str, 
        schema: list[str], 
        file_type: str = "heap", 
        key_index: int = 0,
        column_names: list[str] = None
    ) -> Table:
        """
        Registra una tabla en las tablas del sistema y la abre.
        """
        if self.catalog.get_table_info(name) is not None:
            raise ValueError(f"La tabla '{name}' ya existe en el catálogo.")

        self.catalog.register_table(name, schema, file_type, key_index, column_names)
        return self.open_table(name)

    def open_table(self, name: str) -> Table:
        """
        Abre una tabla cargando su metadata desde el catálogo del sistema.
        """
        if name in self.tables:
            return self.tables[name]

        meta = self.catalog.get_table_info(name)
        if meta is None:
            raise KeyError(f"La tabla '{name}' no existe en el catálogo.")

        filename = f"{name}.dat"
        fm = FileManager(filename, self.page_size, self.header_size)
        bm = BufferManager(fm, self.buffer_frames)

        table = Table(
            name=name,
            schema=meta["schema"],
            buffer_manager=bm,
            file_type=meta["file_type"],
            key_index=meta["key_index"],
            column_names=meta["column_names"],
            file_manager=fm
        )

        # Reacopla los indices B+ que el catalogo diga que esta tabla tiene
        # (creados en una sesion anterior o en esta misma), para que las
        # consultas los puedan usar y el CRUD los mantenga al dia.
        self.index_manager.load_indexes_for_table(table)

        self.tables[name] = table
        return table

    def drop_table(self, name: str):
        """
        Elimina la metadata de la tabla del catálogo y remueve el archivo .dat de disco.
        """
        meta = self.catalog.get_table_info(name)
        if meta is None:
            raise KeyError(f"La tabla '{name}' no existe en el catálogo.")

        if name in self.tables:
            self.tables[name].close()
            del self.tables[name]

        self.catalog.drop_table_info(name)

        filename = f"{name}.dat"
        if os.path.exists(filename):
            os.remove(filename)

    def close(self):
        """
        Guarda los cambios y cierra todas las tablas y el catálogo.
        """
        # El undo necesita que el catalogo y las tablas sigan disponibles.
        self.current_transaction_id = None
        for transaction in self.transaction_manager.active_transactions():
            self.transaction_manager.rollback(
                transaction.transaction_id, self._undo_log_record
            )

        for name, table in list(self.tables.items()):
            table.close()
        self.tables.clear()

        self.catalog.close()

        self.index_manager.close()
        self.recovery_manager.checkpoint()
        self.transaction_manager.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()