import os
import tempfile
import threading

from storage.file_manager import FileManager
from storage.log_manager import (
    LogCorruptionError,
    LogManager,
    LogRecordType,
)


def test_append_read_and_payloads():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "wal.log")
        log = LogManager(path)
        begin_lsn = log.append(LogRecordType.BEGIN, 7)
        update_lsn = log.append(
            LogRecordType.UPDATE,
            7,
            operation="page_write",
            file_name="ventas.dat",
            resource_type="page",
            page_id=3,
            offset=12,
            before=b"\x00\xffantes",
            after="despues".encode("utf-8"),
        )
        log.force()
        log.close()

        reopened = LogManager(path)
        result = reopened.read_all()
        assert not result.truncated_tail
        assert [record.record_type for record in result.records] == [
            LogRecordType.BEGIN,
            LogRecordType.UPDATE,
        ]
        assert result.records[0].lsn == begin_lsn
        assert result.records[1].lsn == update_lsn
        assert result.records[1].prev_lsn == begin_lsn
        assert result.records[1].before == b"\x00\xffantes"
        assert result.records[1].after == "despues".encode("utf-8")
        reopened.close()


def test_reopen_does_not_reuse_lsn():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "wal.log")
        log = LogManager(path)
        first_lsn = log.append(LogRecordType.BEGIN, 1)
        log.close()

        reopened = LogManager(path)
        second_lsn = reopened.append(LogRecordType.COMMIT, 1)
        assert second_lsn > first_lsn
        assert reopened.last_lsn() == second_lsn
        reopened.close()


def test_crc_corruption_is_detected():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "wal.log")
        log = LogManager(path)
        log.append(LogRecordType.BEGIN, 1)
        log.force()
        log.close()

        with open(path, "r+b") as file:
            file.seek(LogManager._LENGTH_SIZE + LogManager._HEADER_SIZE)
            value = file.read(1)
            file.seek(-1, os.SEEK_CUR)
            file.write(bytes([value[0] ^ 0xFF]))

        try:
            LogManager(path)
            assert False, "se esperaba detectar corrupcion del CRC"
        except LogCorruptionError:
            pass


def test_truncated_tail_is_discarded():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "wal.log")
        log = LogManager(path)
        log.append(LogRecordType.BEGIN, 1)
        log.append(LogRecordType.COMMIT, 1)
        log.force()
        log.close()

        original_size = os.path.getsize(path)
        with open(path, "r+b") as file:
            file.truncate(original_size - 2)

        reopened = LogManager(path)
        result = reopened.read_all()
        assert result.truncated_tail
        assert len(result.records) == 1
        assert os.path.getsize(path) > result.valid_end_offset
        reopened.close()


def test_concurrent_append_keeps_records_intact():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "wal.log")
        log = LogManager(path)
        errors = []

        def append_records(transaction_id):
            try:
                for _ in range(20):
                    log.append(LogRecordType.UPDATE, transaction_id, before=b"old", after=b"new")
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=append_records, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        log.force()
        result = log.read_all()
        assert not errors
        assert len(result.records) == 80
        assert [record.lsn for record in result.records] == sorted(record.lsn for record in result.records)
        log.close()


def test_file_manager_force():
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "data.dat")
        file_manager = FileManager(path, page_size=64, file_header_size=0)
        file_manager.write_page(0, b"data")
        file_manager.force()
        file_manager.close()
        with open(path, "rb") as file:
            assert file.read(4) == b"data"


if __name__ == "__main__":
    test_append_read_and_payloads()
    test_reopen_does_not_reuse_lsn()
    test_crc_corruption_is_detected()
    test_truncated_tail_is_discarded()
    test_concurrent_append_keeps_records_intact()
    test_file_manager_force()
    print("test_log_manager: OK")
