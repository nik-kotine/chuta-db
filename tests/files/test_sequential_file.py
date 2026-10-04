"""
Tests del archivo secuencial unificado (registros de longitud fija y
variable con una sola clase SequentialFile).

Nota: los tests fueron generados por IA.
"""

import os
import struct
import tempfile

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager

from storage.files import sequential_file
from storage.files.sequential_file import (
    SequentialFile, FILE_HEADER_FORMAT, PAGE_SIZE
)
from indexes.external_sort import ExternalSorter
from storage.pages.seq_page import Page
from storage.pages.fixed_page import FixedPage
from storage.pages.variable_page import VariablePage
from storage.seq_record import Record
from storage.rid import RID, RID_SIZE, DELETED_SIZE
from storage.formats.data_types import return_format
from storage.formats.serializers.record_serializer import RecordSerializer
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer


# PAGE_SIZE viene del modulo: SequentialFile trabaja siempre con paginas de 4096
FIXED_PAGE_SIZE = PAGE_SIZE
HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)
BUFFER_FRAMES = 10

FIXED_FORMAT = ["integer"]          # todo fijo -> FixedPage
VARIABLE_FORMAT = ["integer", "text"]  # text -> VariablePage


def create_sequential(record_format=VARIABLE_FORMAT):
    fd, filename = tempfile.mkstemp()
    os.close(fd)

    with open(filename, "wb") as f:
        f.write(struct.pack(FILE_HEADER_FORMAT, 0, -1, -1, 0, 0, 1, 0, 0))
        # página 0 = overflow, entregada como bloque de ceros (la lazy
        # initialization de VariablePage se ejercita así)
        f.write(b"\x00" * PAGE_SIZE)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, record_format)

    return filename, fm, bm, seq


def close_file(fm, bm):
    # el buffer pool es global: cerrar el archivo implica persistir y
    # descartar SOLO las paginas de ese FileManager
    bm.close(fm)


def close_sequential(filename, fm, bm):
    close_file(fm, bm)
    os.remove(filename)


def logical_records(seq):
    result = []
    current_rid = seq.first_rid

    while current_rid is not None:
        record = seq._get_record(current_rid)

        if record is None:
            break

        if not record.deleted:
            result.append(record)

        current_rid = record.next_rid

    return result


def logical_keys(seq):
    return [record.params[0] for record in logical_records(seq)]


def primary_page_keys(seq):
    result = []

    for phys_page_id in range(1, seq.n_pages + 1):
        page = seq._load_page(phys_page_id)

        try:
            page_keys = []

            for slot_id in range(page.n_records):
                record = page.get_record(slot_id)

                if not record.deleted:
                    page_keys.append(record.params[0])

            result.append(page_keys)
        finally:
            seq.buffer_manager.unpin_page(phys_page_id)

    return result


def every_main_page_has_live(seq):
    for phys_page_id in range(1, seq.n_pages + 1):
        if seq._first_live_in_page(phys_page_id) is None:
            return False

    return True


def por_pagina(seq, params):
    """Cuántos registros de ese tamaño entran en una página de datos."""
    slot = seq.serializer.get_size_of(params) + RID_SIZE + DELETED_SIZE

    return (PAGE_SIZE - seq.page_class.PAGE_HEADER_SIZE) // slot


# ---------------------------------------------------------------------------
# SERIALIZERS
# ---------------------------------------------------------------------------

def test_serializer_class_hierarchy():
    assert issubclass(FixedLengthRecordSerializer, RecordSerializer)
    assert issubclass(VariableLengthRecordSerializer, RecordSerializer)
    assert issubclass(FixedPage, Page)
    assert issubclass(VariablePage, Page)


def test_fixed_length_record_serializer():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        assert isinstance(seq.serializer, FixedLengthRecordSerializer)
        assert fixed_record_size(seq) > 0

        data = seq.serializer.serialize([12345])
        assert len(data) == 4
        assert seq.serializer.deserialize(data) == (12345,)

    finally:
        close_sequential(filename, fm, bm)


def fixed_record_size(seq):
    return seq.serializer.record_size


def test_fixed_length_record_serializer_multiple_fields():
    filename, fm, bm, seq = create_sequential(["integer", "smallint", "bigint"])

    try:
        params = (10, 5, 1234567890123)

        data = seq.serializer.serialize(params)
        
        assert len(data) == 4 + 2 + 8
        assert seq.serializer.deserialize(data) == params
        assert seq.serializer.get_size_of(params) == 14

    finally:
        close_sequential(filename, fm, bm)


def test_fixed_length_serializer_with_strings():
    filename, fm, bm, seq = create_sequential(["char(4)", "integer"])

    try:
        assert isinstance(seq.serializer, FixedLengthRecordSerializer)

        data = seq.serializer.serialize(("ab", 7))

        assert len(data) == 4 + 4
        assert seq.serializer.deserialize(data) == ("ab", 7)
        assert seq.serializer.record_size == 8
        assert seq.serializer.slot_size == 8 + 8 + 1

    finally:
        close_sequential(filename, fm, bm)


def test_variable_length_record_serializer():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        assert isinstance(seq.serializer, VariableLengthRecordSerializer)

        params = [42, "hola mundo"]

        data = seq.serializer.serialize(params)
        assert seq.serializer.deserialize(data) == params

    finally:
        close_sequential(filename, fm, bm)


def test_variable_length_record_serializer_unicode():
    filename, fm, bm, seq = create_sequential(["text"])

    try:
        params = ["áéíóú ñ 中文 😀"]
        data = seq.serializer.serialize(params)

        assert seq.serializer.deserialize(data) == params

    finally:
        close_sequential(filename, fm, bm)


def test_return_format():
    assert return_format("integer") == [">i", 4]
    assert return_format("text") == ["s", -1]
    assert return_format("varchar(20)") == ["20s", 20]


# ---------------------------------------------------------------------------
# PÁGINA / SERIALIZADOR ELEGIDOS SEGÚN EL FORMATO
# ---------------------------------------------------------------------------

def test_fixed_format_uses_fixed_page():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        assert isinstance(seq._load_page(0), FixedPage)
        assert isinstance(seq.serializer, FixedLengthRecordSerializer)

    finally:
        close_sequential(filename, fm, bm)


def test_variable_format_uses_variable_page():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        assert isinstance(seq._load_page(0), VariablePage)
        assert isinstance(seq.serializer, VariableLengthRecordSerializer)

    finally:
        close_sequential(filename, fm, bm)


def test_bounded_string_format_uses_fixed_page():
    filename, fm, bm, seq = create_sequential(["char(4)", "integer"])

    try:
        assert isinstance(seq.serializer, FixedLengthRecordSerializer)
        assert isinstance(seq._load_page(0), FixedPage)

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------

def test_insert_first_record_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        rid = seq.insert((10,))

        assert rid == seq.first_rid
        assert seq.first_rid is not None

        record = seq._get_record(rid)

        assert isinstance(record, Record)
        assert record.params == (10,)
        assert record.next_rid is None
        assert record.deleted is False

        assert seq.n_records == 1
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_insert_first_record_variable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        rid = seq.insert((10, "diez"))

        assert rid == seq.first_rid
        assert seq.first_rid is not None

        record = seq._get_record(rid)

        assert record is not None
        assert record.params == [10, "diez"]
        assert record.next_rid is None
        assert record.deleted is False

        assert seq.n_records == 1
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_insert_sorted_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        for key in [50, 20, 80, 10, 40, 30, 60]:
            seq.insert((key,))

        assert logical_keys(seq) == [10, 20, 30, 40, 50, 60, 80]
        assert seq.n_records == 7

    finally:
        close_sequential(filename, fm, bm)


def test_insert_sorted_variable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        for key in [50, 20, 80, 10, 40, 30, 60]:
            seq.insert((key, f"value-{key}"))

        assert logical_keys(seq) == [10, 20, 30, 40, 50, 60, 80]

    finally:
        close_sequential(filename, fm, bm)


def test_insert_before_first():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((20, "twenty"))
        seq.insert((30, "thirty"))
        seq.insert((40, "forty"))

        rid = seq.insert((10, "ten"))

        assert logical_keys(seq) == [10, 20, 30, 40]
        assert seq.first_rid == rid

    finally:
        close_sequential(filename, fm, bm)


def test_insert_after_last():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((10, "ten"))
        seq.insert((20, "twenty"))

        rid = seq.insert((30, "thirty"))

        assert logical_keys(seq) == [10, 20, 30]
        assert seq._get_record(rid).next_rid is None

    finally:
        close_sequential(filename, fm, bm)


def test_insert_between_records():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((10, "ten"))
        seq.insert((30, "thirty"))

        seq.insert((20, "twenty"))

        assert logical_keys(seq) == [10, 20, 30]

    finally:
        close_sequential(filename, fm, bm)


def test_duplicate_keys_keep_insertion_order():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((20, "a"))
        seq.insert((20, "b"))
        seq.insert((20, "c"))
        seq.insert((10, "ten"))
        seq.insert((30, "thirty"))

        assert logical_keys(seq) == [10, 20, 20, 20, 30]

        results = seq.search(20)
        assert [r.params[1] for r in results] == ["a", "b", "c"]

    finally:
        close_sequential(filename, fm, bm)


def test_duplicates_interleaved_keep_insertion_order():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((20, "a"))
        seq.insert((10, "ten"))
        seq.insert((20, "b"))
        seq.insert((30, "thirty"))
        seq.insert((20, "c"))

        # los duplicados deben quedar contiguos y en orden de llegada
        assert logical_keys(seq) == [10, 20, 20, 20, 30]

        results = seq.search(20)
        assert [r.params[1] for r in results] == ["a", "b", "c"]

    finally:
        close_sequential(filename, fm, bm)


def test_duplicates_only_in_overflow_found_by_search():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        # un registro gigante llena la única página de datos: lo que viene
        # después cae en overflow y, como son menos de MIN_RECORDS, ninguna
        # reorganize toca los discos
        seq.insert((1, "x" * 3000))
        seq.insert((5, "x"))
        seq.insert((6, "six"))
        seq.insert((5, "z"))

        # ambos 5 estan en la pagina de overflow (nunca hubo reorganize)
        assert seq.reorganize_count == 0
        assert [r.params[1] for r in seq.search(5)] == ["x", "z"]
        assert seq.search(1)[0].params[1] == "x" * 3000
        assert seq.search(6)[0].params[1] == "six"

    finally:
        close_sequential(filename, fm, bm)


def test_duplicates_only_in_overflow_deleted():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((1, "x" * 3000))
        seq.insert((5, "x"))
        seq.insert((6, "six"))
        seq.insert((5, "z"))

        assert seq.delete(5) is True
        assert seq.search(5) == []
        assert logical_keys(seq) == [1, 6]
        assert seq.n_records == 2

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------

def test_search_existing():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        for key in [10, 20, 30, 40]:
            seq.insert((key, f"value-{key}"))

        result = seq.search(20)

        assert len(result) == 1
        assert result[0].params == [20, "value-20"]

    finally:
        close_sequential(filename, fm, bm)


def test_search_missing():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        for key in [10, 20, 30, 40]:
            seq.insert((key,))

        assert seq.search(25) == []
        assert seq.search(100) == []
        assert seq.search(1) == []

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

def test_delete_existing():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        # 10 registros para que borrar 1 solo no cruce OVERFLOW_RATIO/
        # WASTED_RATIO (0.3) y dispare un reorganize a mitad del test --
        # con solo 3 registros, 1/3 ya lo cruza (ver INDEX_BENCHMARK /
        # historial: WASTED_RATIO se bajo de 0.5 a 0.3 a proposito)
        claves = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        for key in claves:
            seq.insert((key,))

        assert seq.delete(50) is True
        assert seq.search(50) == []
        assert logical_keys(seq) == [k for k in claves if k != 50]

        assert seq.n_records == 9
        assert seq.n_deleted == 1

    finally:
        close_sequential(filename, fm, bm)


def test_delete_missing():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        seq.insert((10,))

        assert seq.delete(99) is False
        assert seq.n_records == 1
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_delete_duplicates():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((20, "a"))
        seq.insert((20, "b"))
        seq.insert((20, "c"))
        seq.insert((30, "d"))

        assert seq.delete(20) is True
        assert seq.search(20) == []
        assert logical_keys(seq) == [30]

    finally:
        close_sequential(filename, fm, bm)


def test_delete_same_key_twice():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        # suficientes registros para que el delete no cruce WASTED_RATIO
        # (0.3) y dispare un reorganize antes del segundo delete
        for i in range(10):
            seq.insert((i * 10, f"val{i}"))

        assert seq.delete(90) is True
        assert seq.delete(90) is False

        assert seq.n_records == 9
        assert seq.n_deleted == 1

    finally:
        close_sequential(filename, fm, bm)


def test_insert_after_delete():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        seq.insert((10, "ten"))
        seq.insert((20, "twenty"))
        seq.insert((30, "thirty"))

        assert seq.delete(20)

        seq.insert((25, "twenty-five"))

        assert logical_keys(seq) == [10, 25, 30]
        assert seq.search(20) == []
        assert seq.search(25)[0].params == [25, "twenty-five"]

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# OVERFLOW / VARIABLE-LENGTH
# ---------------------------------------------------------------------------

def test_overflow_is_used():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        # más registros de los que entran en una página: los que no salen a
        # overflow terminados en la página 0
        total = por_pagina(seq, (0, "x" * 20)) + 5

        for key in range(1, total + 1):
            seq.insert((key, "x" * 20))

        assert seq.n_overflow_records > 0
        assert seq.overflow_tail >= 0

        overflow = seq._load_page(0)

        try:
            assert overflow.n_records > 0
        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == list(range(1, total + 1))

    finally:
        close_sequential(filename, fm, bm)


def test_overflow_records_are_reachable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        total = por_pagina(seq, (0, "x" * 30)) * 3

        for key in range(1, total + 1):
            seq.insert((key, "x" * 30))

        records = logical_records(seq)

        assert len(records) == total
        assert logical_keys(seq) == list(range(1, total + 1))

    finally:
        close_sequential(filename, fm, bm)


def test_records_have_different_sizes():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        values = [
            (10, "a"),
            (20, "texto mucho más largo"),
            (30, ""),
            (40, "x" * 50),
        ]

        for record in values:
            seq.insert(record)

        records = logical_records(seq)

        assert [r.params for r in records] == [list(v) for v in values]

    finally:
        close_sequential(filename, fm, bm)


def test_variable_string_sizes():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        values = [
            (1, ""),
            (2, "a"),
            (3, "short"),
            (4, "x" * 20),
            (5, "x" * 50),
        ]

        for value in values:
            seq.insert(value)

        assert logical_keys(seq) == [1, 2, 3, 4, 5]

        for expected in values:
            result = seq.search(expected[0])
            assert len(result) == 1
            assert result[0].params == list(expected)

    finally:
        close_sequential(filename, fm, bm)


def test_utf8_strings():
    filename, fm, bm, seq = create_sequential(["integer", "text"])

    try:
        values = [
            (1, "áéíóú"),
            (2, "你好"),
            (3, "🙂"),
            (4, "año"),
            (5, "こんにちは"),
        ]

        for key, value in values:
            seq.insert((key, value))

        for key, value in values:
            result = seq.search(key)
            assert len(result) == 1
            assert result[0].params[1] == value

    finally:
        close_sequential(filename, fm, bm)


def test_record_too_large():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        huge_text = "x" * (PAGE_SIZE + 1)

        try:
            seq.insert((10, huge_text))
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass

        assert seq.n_records == 0
        assert seq.first_rid is None

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# REORGANIZATION
# ---------------------------------------------------------------------------

def test_reorganize_packs_and_relinks():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        # tres páginas de datos para que reorganize tenga que repartir
        total = por_pagina(seq, (0, "x")) * 3

        for key in range(1, total + 1):
            seq.insert((key, "x" * (key % 20 + 1)))

        seq.reorganize()

        assert logical_keys(seq) == list(range(1, total + 1))
        assert seq.n_records == total
        assert seq.n_deleted == 0
        assert seq.n_pages >= 2

        every_main_page_has_live(seq)

        # la cadena debe estar relinkeada de punta a punta
        expected_key = 1
        current_rid = seq.first_rid

        while current_rid is not None:
            record = seq._get_record(current_rid)

            assert record is not None
            assert record.params[0] == expected_key

            current_rid = record.next_rid
            expected_key += 1

        assert expected_key == total + 1

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_clears_overflow():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        total = por_pagina(seq, (0, "x" * 20)) * 2

        for key in range(1, total + 1):
            seq.insert((key, "x" * 20))

        assert seq.n_overflow_records > 0

        seq.reorganize()

        assert seq.n_deleted == 0
        assert seq.n_overflow_records == 0
        assert seq.overflow_tail == 0

        page = seq._load_page(0)

        try:
            assert page.n_records == 0
        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == list(range(1, total + 1))

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_removes_deleted_records():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        for key in range(1, 11):
            seq.insert((key, f"value-{key}"))

        seq.delete(2)
        seq.delete(4)
        seq.delete(6)
        seq.delete(8)

        seq.reorganize()

        assert logical_keys(seq) == [1, 3, 5, 7, 9, 10]
        assert seq.n_records == 6
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_duplicate_order_survives():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        values = [
            (20, "a"),
            (20, "b"),
            (20, "c"),
            (10, "ten"),
            (30, "thirty"),
        ] + [(i, f"v{i}") for i in range(40, 70)]

        for value in values:
            seq.insert(value)

        seq.reorganize()

        assert [r.params[1] for r in seq.search(20)] == ["a", "b", "c"]

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_truncates_file():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        total = por_pagina(seq, (0, "x" * 10)) * 4

        for key in range(1, total + 1):
            seq.insert((key, "x" * 10))

        close_file(fm, bm)

        fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
        bm = BufferManager(fm, BUFFER_FRAMES)
        seq = SequentialFile(bm, VARIABLE_FORMAT)

        size_before = os.path.getsize(filename)
        expected_before = HEADER_SIZE + PAGE_SIZE * (seq.n_pages + 1)
        assert size_before == expected_before
        assert seq.n_pages >= 4

        for key in range(1, total + 1, 2):
            seq.delete(key)

        size_after = os.path.getsize(filename)
        expected_after = HEADER_SIZE + PAGE_SIZE * (seq.n_pages + 1)

        assert size_after < size_before
        assert size_after == expected_after
        assert logical_keys(seq) == list(range(2, total + 1, 2))
        assert every_main_page_has_live(seq)

    finally:
        close_sequential(filename, fm, bm)


def test_multiple_reorganizations():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        for i in range(30):
            seq.insert((i, f"value-{i}"))

        for i in range(0, 30, 2):
            seq.delete(i)

        seq.reorganize()

        expected = list(range(1, 30, 2))
        assert logical_keys(seq) == expected

        for i in range(31, 60, 2):
            seq.insert((i, f"value-{i}"))

        assert logical_keys(seq) == list(range(1, 60, 2))

        seq.reorganize()

        assert logical_keys(seq) == list(range(1, 60, 2))
        assert seq.n_records == len(list(range(1, 60, 2)))
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_empty_file_keeps_usable():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        for key in range(1, 9):
            seq.insert((key,))

        for key in range(1, 9):
            assert seq.delete(key) is True

        assert seq.first_rid is None
        assert seq.n_records == 0
        assert seq.n_deleted == 0
        assert seq._find_neighbors(5, duplicates_after=False) == (None, None)

        seq.insert((7,))
        assert logical_keys(seq) == [7]

    finally:
        close_sequential(filename, fm, bm)


def test_every_main_page_has_live_after_delete():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        # 4 páginas llenas + un registro suelto en la quinta
        capacidad = por_pagina(seq, (0,))
        total = capacidad * 4 + 1

        for key in range(1, total + 1):
            seq.insert((key,))

        seq.reorganize()
        assert seq.n_pages == 5
        assert every_main_page_has_live(seq)

        # borrar el único registro vivo de la última página dispara reorganize
        assert seq.delete(total) is True
        assert seq.n_pages == 4
        assert every_main_page_has_live(seq)
        assert logical_keys(seq) == list(range(1, total))

        # vaciar páginas del medio también (proporción tachados fina)
        for key in range(5, 9):
            assert seq.delete(key) is True

        assert every_main_page_has_live(seq)
        assert logical_keys(seq) == [
            key for key in range(1, total) if key not in (5, 6, 7, 8)
        ]

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# NEIGHBORS / RID
# ---------------------------------------------------------------------------

def test_neighbors_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        capacidad = por_pagina(seq, (0,))
        keys = [(i + 1) * 10 for i in range(capacidad * 2)]

        for key in keys:
            seq.insert((key,))

        seq.reorganize()

        assert seq.n_pages == 2
        assert logical_keys(seq) == keys

        # frontera exacta entre las dos páginas
        assert seq._find_neighbors(keys[capacidad - 1] + 5, duplicates_after=False) == (
            (1, capacidad - 1),
            (2, 0),
        )
        # con duplicates_after=False el "next" es el primer registro con clave >= la buscada
        assert seq._find_neighbors(keys[capacidad - 1], duplicates_after=False) == (
            (1, capacidad - 2),
            (1, capacidad - 1),
        )
        # frontera en medio de la primera página
        assert seq._find_neighbors(keys[1] + 5, duplicates_after=False) == (
            (1, 1),
            (1, 2),
        )
        # clave más pequeña que todo
        assert seq._find_neighbors(5, duplicates_after=False) == (None, (1, 0))
        # clave más grande que todo
        assert seq._find_neighbors(keys[-1] + 50, duplicates_after=False) == (
            (2, capacidad - 1),
            None,
        )

    finally:
        close_sequential(filename, fm, bm)


def test_first_rid_se_guarda_como_dos_enteros():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        total = por_pagina(seq, (0, "x" * 10)) + 3

        for key in range(1, total + 1):
            seq.insert((key, "x" * 10))

        seq.reorganize()

        # el header guarda page_id y slot_id por separado, sin empaquetar
        crudo = fm.read_header()
        campos = struct.unpack(FILE_HEADER_FORMAT, crudo)

        assert len(campos) == 8
        assert (campos[1], campos[2]) == (seq.first_rid.page_id, seq.first_rid.slot_id)
        assert campos[1] != -1

        n_records, n_deleted, ovf_pages, ovf_records, tail = campos[3:]
        assert campos[0] == seq.n_pages
        assert n_records == total
        assert n_deleted == 0
        assert ovf_pages == 1
        assert ovf_records == 0
        assert tail == 0

        # cerrar y reabrir debe devolver el mismo first_rid
        close_file(fm, bm)
        fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
        bm = BufferManager(fm, BUFFER_FRAMES)
        seq = SequentialFile(bm, VARIABLE_FORMAT)

        assert seq.first_rid == RID(campos[1], campos[2])
        assert logical_keys(seq) == list(range(1, total + 1))

    finally:
        close_sequential(filename, fm, bm)


def test_first_rid_nulo_se_guarda_como_menos_uno():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        assert seq.first_rid is None
        assert struct.unpack(FILE_HEADER_FORMAT, fm.read_header())[1:3] == (-1, -1)

        seq.insert((7,))
        assert seq.first_rid == RID(1, 0)

        seq.delete(7)
        seq.reorganize()

        assert seq.first_rid is None
        assert struct.unpack(FILE_HEADER_FORMAT, fm.read_header())[1:3] == (-1, -1)

    finally:
        close_sequential(filename, fm, bm)


def test_sequential_file_rechaza_file_manager_con_otra_page_size():
    fd, filename = tempfile.mkstemp()
    os.close(fd)

    fm = FileManager(filename, 128, HEADER_SIZE)

    try:
        try:
            SequentialFile(BufferManager(fm, BUFFER_FRAMES), FIXED_FORMAT)
            assert False, "Expected ValueError"
        except ValueError:
            pass
    finally:
        fm.close()
        os.remove(filename)


def test_overflow_pages_no_son_contiguas():
    """El puntero de overflow manda, no la cuenta de páginas.

    Cada insert va a la página de overflow, así que el archivo termina con la
    página 0 más un montón de páginas de overflow al final, muy por encima de
    las principales.
    """
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        total = 19

        for key in range(1, total + 1):
            seq.insert((key, "x" * 400))

        assert seq.n_pages == 1
        assert seq.n_overflow_pages >= 2
        # el puntero apunta a una página real, más allá del área de datos
        assert seq.overflow_tail > seq.n_pages
        assert seq.file_manager.grow_to_page(seq.overflow_tail) == seq.overflow_tail + 1

        assert logical_keys(seq) == list(range(1, total + 1))

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_crece_paginas_principales_con_overflow():
    """Reorganize tiene que poder reusar páginas que hoy son de overflow.

    Con el área de datos llena de páginas de overflow, ordenar puede necesitar
    más páginas principales de las que hay: las que están más allá se
    reconvierten y el archivo queda compacto.
    """
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        total = 19

        for key in range(1, total + 1):
            seq.insert((key, "x" * 400))

        # 19 registros de 413 bytes no entran en una sola página
        assert seq.n_pages == 1
        assert seq.n_overflow_pages >= 2

        seq.reorganize()

        assert logical_keys(seq) == list(range(1, total + 1))
        assert seq.n_records == total
        assert seq.n_overflow_records == 0
        assert seq.overflow_tail == 0
        assert seq.n_pages >= 2
        assert every_main_page_has_live(seq)

        bm.flush_file(fm)
        assert os.path.getsize(filename) == HEADER_SIZE + PAGE_SIZE * (seq.n_pages + 1)

    finally:
        close_sequential(filename, fm, bm)


def test_overflow_tail_sobrevive_al_reopen():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        for key in range(1, 19):
            seq.insert((key, "x" * 400))

        tail = seq.overflow_tail
        page_ids = {
            rid.page_id for rid, _ in seq._iter_records() if rid.page_id == tail
        }
        assert page_ids, "el puntero de overflow tiene que tener registros"

        close_file(fm, bm)
        fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
        bm = BufferManager(fm, BUFFER_FRAMES)
        seq = SequentialFile(bm, VARIABLE_FORMAT)

        assert seq.overflow_tail == tail
        assert logical_keys(seq) == list(range(1, 19))

        # y sigue pudiendo crecer por donde iba
        seq.insert((100, "x" * 400))
        assert seq.overflow_tail >= tail

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_ordena_con_external_sort():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    uso = []

    class Espia(ExternalSorter):
        def spill(self, items):
            uso.append(self.budget)
            return super().spill(items)

    original = sequential_file.ExternalSorter
    sequential_file.ExternalSorter = Espia

    try:
        for key in range(1, 40):
            seq.insert((key, f"v{key}"))

        seq.reorganize()

        assert uso, "reorganize no uso ExternalSorter"
        assert uso[0] == sequential_file.SORT_BUDGET
        assert logical_keys(seq) == list(range(1, 40))

    finally:
        sequential_file.ExternalSorter = original
        close_sequential(filename, fm, bm)


def test_reorganize_estable_con_varios_runs():
    """Con budget chico el sort se mezcla en varios runs y aun así es estable."""
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    original = sequential_file.SORT_BUDGET
    # cada clave repetida llega a un run distinto
    sequential_file.SORT_BUDGET = 2

    try:
        claves = [20, 20, 20, 10, 30, 20, 10, 30, 20, 20] * 8

        for i, clave in enumerate(claves):
            seq.insert((clave, f"v{i}"))

        seq.reorganize()

        assert [r.params[0] for r in logical_records(seq)] == sorted(claves)

        for clave in (10, 20, 30):
            esperados = [f"v{i}" for i, k in enumerate(claves) if k == clave]

            assert [r.params[1] for r in seq.search(clave)] == esperados

    finally:
        sequential_file.SORT_BUDGET = original
        close_sequential(filename, fm, bm)


def test_reorganize_rechaza_clave_no_ordenable():
    filename, fm, bm, seq = create_sequential(["point"])

    try:
        puntos = [(float(x), float(x) * 2) for x in range(1, 5)]

        for punto in puntos:
            seq.insert([punto])

        try:
            seq.reorganize()
            assert False, "Expected RuntimeError"
        except RuntimeError as exc:
            assert "tuple" in str(exc)

        # el archivo quedó intacto y se puede seguir usando
        assert logical_keys(seq) == puntos
        assert seq.n_records == 4

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------

def test_persistence_variable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    for key in [50, 20, 80, 10, 40]:
        seq.insert((key, f"value-{key}"))

    expected = [record.params for record in logical_records(seq)]
    assert seq.n_records == 5

    close_file(fm, bm)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, VARIABLE_FORMAT)

    try:
        assert seq.n_records == 5
        assert [r.params for r in logical_records(seq)] == expected

        assert [r.params for r in seq.search(20)] == [[20, "value-20"]]

    finally:
        close_sequential(filename, fm, bm)


def test_persistence_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    for key in [10, 20, 30, 40, 50]:
        seq.insert((key,))

    expected = logical_keys(seq)

    close_file(fm, bm)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, FIXED_FORMAT)

    try:
        assert seq.n_records == 5
        assert logical_keys(seq) == expected

        assert [r.params[0] for r in seq.search(10)] == [10]
        assert [r.params[0] for r in seq.search(30)] == [30]
        assert [r.params[0] for r in seq.search(50)] == [50]

    finally:
        close_sequential(filename, fm, bm)


def test_persistence_after_delete():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    for key in range(1, 8):
        seq.insert((key, f"value-{key}"))

    seq.delete(3)
    seq.delete(5)

    expected = [1, 2, 4, 6, 7]

    close_file(fm, bm)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, VARIABLE_FORMAT)

    try:
        assert logical_keys(seq) == expected
        assert seq.n_records == 5
        assert seq.n_deleted == 2

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# EVERYTHING TOGETHER
# ---------------------------------------------------------------------------

def test_everything_together_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT)

    try:
        for key in [
            50, 20, 80, 10, 30,
            20, 70, 90, 40, 60,
            20, 100, 5,
        ]:
            seq.insert((key,))

        assert logical_keys(seq) == [
            5, 10, 20, 20, 20,
            30, 40, 50, 60, 70,
            80, 90, 100,
        ]

        assert len(seq.search(20)) == 3

        assert seq.delete(20) is True
        assert seq.delete(70) is True
        assert seq.delete(5) is True

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100,
        ]

        seq.reorganize()

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100,
        ]
        assert seq.n_deleted == 0

        pages = primary_page_keys(seq)
        flattened = [key for page in pages for key in page]

        assert flattened == sorted(flattened)

    finally:
        close_sequential(filename, fm, bm)


def test_everything_together_variable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT)

    try:
        values = [
            (50, "fifty"),
            (20, "twenty"),
            (80, "eighty"),
            (10, "ten"),
            (30, "thirty"),
            (20, "twenty-a"),
            (70, "seventy"),
            (90, "ninety"),
            (40, "forty"),
            (60, "sixty"),
            (20, "twenty-b"),
            (100, "x" * 40),
            (5, "five"),
        ]

        for record in values:
            seq.insert(record)

        assert logical_keys(seq) == [
            5, 10, 20, 20, 20,
            30, 40, 50, 60, 70,
            80, 90, 100,
        ]

        assert [r.params[1] for r in seq.search(20)] == [
            "twenty",
            "twenty-a",
            "twenty-b",
        ]

        assert seq.delete(20) is True
        assert seq.delete(70) is True
        assert seq.delete(5) is True

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100,
        ]

        seq.reorganize()

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100,
        ]
        assert seq.n_deleted == 0

        pages = primary_page_keys(seq)
        flattened = [key for page in pages for key in page]

        assert flattened == sorted(flattened)

    finally:
        close_sequential(filename, fm, bm)


tests = [
    test_serializer_class_hierarchy,
    test_fixed_length_record_serializer,
    test_fixed_length_record_serializer_multiple_fields,
    test_fixed_length_serializer_with_strings,
    test_variable_length_record_serializer,
    test_variable_length_record_serializer_unicode,
    test_return_format,

    test_fixed_format_uses_fixed_page,
    test_variable_format_uses_variable_page,
    test_bounded_string_format_uses_fixed_page,

    test_insert_first_record_fixed,
    test_insert_first_record_variable,
    test_insert_sorted_fixed,
    test_insert_sorted_variable,
    test_insert_before_first,
    test_insert_after_last,
    test_insert_between_records,
    test_duplicate_keys_keep_insertion_order,
    test_duplicates_interleaved_keep_insertion_order,
    test_duplicates_only_in_overflow_found_by_search,
    test_duplicates_only_in_overflow_deleted,

    test_search_existing,
    test_search_missing,

    test_delete_existing,
    test_delete_missing,
    test_delete_duplicates,
    test_delete_same_key_twice,
    test_insert_after_delete,

    test_overflow_is_used,
    test_overflow_records_are_reachable,
    test_records_have_different_sizes,
    test_variable_string_sizes,
    test_utf8_strings,
    test_record_too_large,

    test_reorganize_packs_and_relinks,
    test_reorganize_clears_overflow,
    test_reorganize_removes_deleted_records,
    test_reorganize_duplicate_order_survives,
    test_reorganize_truncates_file,
    test_multiple_reorganizations,
    test_reorganize_empty_file_keeps_usable,
    test_every_main_page_has_live_after_delete,

    test_neighbors_fixed,
    test_first_rid_se_guarda_como_dos_enteros,
    test_first_rid_nulo_se_guarda_como_menos_uno,
    test_sequential_file_rechaza_file_manager_con_otra_page_size,
    test_overflow_pages_no_son_contiguas,
    test_reorganize_crece_paginas_principales_con_overflow,
    test_overflow_tail_sobrevive_al_reopen,
    test_reorganize_ordena_con_external_sort,
    test_reorganize_estable_con_varios_runs,
    test_reorganize_rechaza_clave_no_ordenable,

    test_persistence_variable,
    test_persistence_fixed,
    test_persistence_after_delete,

    test_everything_together_fixed,
    test_everything_together_variable,
]


for test in tests:
    print(f"Running {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests passed.")