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

from storage.files.sequential_file import SequentialFile
from storage.pages.seq_page import Page
from storage.pages.fixed_page import FixedPage
from storage.pages.variable_page import VariablePage
from storage.seq_record import Record
from storage.formats.data_types import return_format
from storage.formats.serializers.record_serializer import RecordSerializer
from storage.formats.serializers.fixed_length_serializer import FixedLengthRecordSerializer
from storage.formats.serializers.variable_length_serializer import VariableLengthRecordSerializer


PAGE_SIZE = 128
FIXED_PAGE_SIZE = 64
HEADER_SIZE = 16
BUFFER_FRAMES = 10

FIXED_FORMAT = ["integer"]          # todo fijo -> FixedPage
VARIABLE_FORMAT = ["integer", "text"]  # text -> VariablePage


def create_sequential(record_format=VARIABLE_FORMAT, page_size=PAGE_SIZE):
    fd, filename = tempfile.mkstemp()
    os.close(fd)

    with open(filename, "wb") as f:
        f.write(struct.pack(">iiii", 0, -1, 0, 0))
        # página 0 = overflow, entregada como bloque de ceros (la lazy
        # initialization de VariablePage se ejercita así)
        f.write(b"\x00" * page_size)

    fm = FileManager(filename, page_size, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, page_size, record_format)

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


# ---------------------------------------------------------------------------
# SERIALIZERS
# ---------------------------------------------------------------------------

def test_serializer_class_hierarchy():
    assert issubclass(FixedLengthRecordSerializer, RecordSerializer)
    assert issubclass(VariableLengthRecordSerializer, RecordSerializer)
    assert issubclass(FixedPage, Page)
    assert issubclass(VariablePage, Page)


def test_fixed_length_record_serializer():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(
        ["integer", "smallint", "bigint"], FIXED_PAGE_SIZE
    )

    try:
        params = (10, 5, 1234567890123)

        data = seq.serializer.serialize(params)
        
        assert len(data) == 4 + 2 + 8
        assert seq.serializer.deserialize(data) == params
        assert seq.serializer.get_size_of(params) == 14

    finally:
        close_sequential(filename, fm, bm)


def test_fixed_length_serializer_with_strings():
    filename, fm, bm, seq = create_sequential(["char(4)", "integer"], FIXED_PAGE_SIZE)

    try:
        assert isinstance(seq.serializer, FixedLengthRecordSerializer)

        data = seq.serializer.serialize(("ab", 7))

        assert len(data) == 4 + 4
        assert seq.serializer.deserialize(data) == ("ab\x00\x00", 7)
        assert seq.serializer.record_size == 8
        assert seq.serializer.slot_size == 8 + 8 + 1

    finally:
        close_sequential(filename, fm, bm)


def test_variable_length_record_serializer():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        assert isinstance(seq.serializer, VariableLengthRecordSerializer)

        params = [42, "hola mundo"]

        data = seq.serializer.serialize(params)
        assert seq.serializer.deserialize(data) == params

    finally:
        close_sequential(filename, fm, bm)


def test_variable_length_record_serializer_unicode():
    filename, fm, bm, seq = create_sequential(["text"], PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

    try:
        assert isinstance(seq._load_page(0), FixedPage)
        assert isinstance(seq.serializer, FixedLengthRecordSerializer)

    finally:
        close_sequential(filename, fm, bm)


def test_variable_format_uses_variable_page():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        assert isinstance(seq._load_page(0), VariablePage)
        assert isinstance(seq.serializer, VariableLengthRecordSerializer)

    finally:
        close_sequential(filename, fm, bm)


def test_bounded_string_format_uses_fixed_page():
    filename, fm, bm, seq = create_sequential(["char(4)", "integer"], FIXED_PAGE_SIZE)

    try:
        assert isinstance(seq.serializer, FixedLengthRecordSerializer)
        assert isinstance(seq._load_page(0), FixedPage)

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------

def test_insert_first_record_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

    try:
        for key in [50, 20, 80, 10, 40, 30, 60]:
            seq.insert((key,))

        assert logical_keys(seq) == [10, 20, 30, 40, 50, 60, 80]
        assert seq.n_records == 7

    finally:
        close_sequential(filename, fm, bm)


def test_insert_sorted_variable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        for key in [50, 20, 80, 10, 40, 30, 60]:
            seq.insert((key, f"value-{key}"))

        assert logical_keys(seq) == [10, 20, 30, 40, 50, 60, 80]

    finally:
        close_sequential(filename, fm, bm)


def test_insert_before_first():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        seq.insert((10, "ten"))
        seq.insert((20, "twenty"))

        rid = seq.insert((30, "thirty"))

        assert logical_keys(seq) == [10, 20, 30]
        assert seq._get_record(rid).next_rid is None

    finally:
        close_sequential(filename, fm, bm)


def test_insert_between_records():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        seq.insert((10, "ten"))
        seq.insert((30, "thirty"))

        seq.insert((20, "twenty"))

        assert logical_keys(seq) == [10, 20, 30]

    finally:
        close_sequential(filename, fm, bm)


def test_duplicate_keys_keep_insertion_order():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        seq.insert((1, "one"))
        seq.insert((5, "x"))
        seq.insert((6, "six"))
        seq.insert((5, "z"))

        # ambos 5 estan en la pagina de overflow (nunca hubo reorganize)
        assert logical_keys(seq) == [1, 5, 5, 6]

        results = seq.search(5)
        assert [r.params[1] for r in results] == ["x", "z"]

        assert seq.search(1)[0].params[1] == "one"
        assert seq.search(6)[0].params[1] == "six"

    finally:
        close_sequential(filename, fm, bm)


def test_duplicates_only_in_overflow_deleted():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        seq.insert((1, "one"))
        seq.insert((5, "x"))
        seq.insert((6, "six"))
        seq.insert((5, "z"))

        assert seq.delete(5) is True
        assert logical_keys(seq) == [1, 6]
        assert seq.search(5) == []
        assert seq.n_records == 2

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------

def test_search_existing():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        for key in [10, 20, 30, 40]:
            seq.insert((key, f"value-{key}"))

        result = seq.search(20)

        assert len(result) == 1
        assert result[0].params == [20, "value-20"]

    finally:
        close_sequential(filename, fm, bm)


def test_search_missing():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

    try:
        for key in [10, 20, 30]:
            seq.insert((key,))

        assert seq.delete(20) is True
        assert seq.search(20) == []
        assert logical_keys(seq) == [10, 30]

        assert seq.n_records == 2
        assert seq.n_deleted == 1

    finally:
        close_sequential(filename, fm, bm)


def test_delete_missing():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

    try:
        seq.insert((10,))

        assert seq.delete(99) is False
        assert seq.n_records == 1
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_delete_duplicates():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        seq.insert((10, "ten"))
        seq.insert((20, "twenty"))
        seq.insert((30, "thirty"))

        assert seq.delete(30) is True
        assert seq.delete(30) is False

        assert seq.n_records == 2
        assert seq.n_deleted == 1

    finally:
        close_sequential(filename, fm, bm)


def test_insert_after_delete():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        for key in range(1, 10):
            seq.insert((key, "x" * 20))

        overflow = seq._load_page(0)

        try:
            assert overflow.n_records > 0
        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == list(range(1, 10))

    finally:
        close_sequential(filename, fm, bm)


def test_overflow_records_are_reachable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        for key in range(1, 20):
            seq.insert((key, "x" * 30))

        records = logical_records(seq)

        assert len(records) == 19
        assert logical_keys(seq) == list(range(1, 20))

    finally:
        close_sequential(filename, fm, bm)


def test_records_have_different_sizes():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(["integer", "text"], PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        huge_text = "x" * 500

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        for key in range(1, 30):
            seq.insert((key, "x" * (key % 20 + 1)))

        seq.reorganize()

        assert logical_keys(seq) == list(range(1, 30))
        assert seq.n_records == 29
        assert seq.n_deleted == 0

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

        assert expected_key == 30

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_clears_overflow():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        for key in range(1, 20):
            seq.insert((key, "x" * 20))

        seq.reorganize()

        assert seq.n_deleted == 0

        page = seq._load_page(0)

        try:
            assert page.n_records == 0
        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == list(range(1, 20))

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_removes_deleted_records():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        for key in range(1, 21):
            seq.insert((key, "x" * 10))

        close_file(fm, bm)

        fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
        bm = BufferManager(fm, BUFFER_FRAMES)
        seq = SequentialFile(bm, PAGE_SIZE, VARIABLE_FORMAT)

        size_before = os.path.getsize(filename)
        expected_before = HEADER_SIZE + PAGE_SIZE * (seq.n_pages + 1)
        assert size_before == expected_before

        for key in range(1, 21, 2):
            seq.delete(key)

        size_after = os.path.getsize(filename)
        expected_after = HEADER_SIZE + PAGE_SIZE * (seq.n_pages + 1)

        assert size_after < size_before
        assert size_after == expected_after
        assert logical_keys(seq) == list(range(2, 21, 2))
        assert every_main_page_has_live(seq)

    finally:
        close_sequential(filename, fm, bm)


def test_multiple_reorganizations():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

    try:
        # 21 registros -> 6 páginas (4 por página)
        for key in range(1, 22):
            seq.insert((key,))

        seq.reorganize()
        assert seq.n_pages == 6
        assert every_main_page_has_live(seq)

        # borrar el único registro vivo de la última página dispara reorganize
        assert seq.delete(21) is True
        assert seq.n_pages == 5
        assert every_main_page_has_live(seq)
        assert logical_keys(seq) == list(range(1, 21))

        # vaciar una página del medio también (proporción tachados fina)
        for key in range(5, 9):
            assert seq.delete(key) is True

        assert every_main_page_has_live(seq)
        assert logical_keys(seq) == [
            1, 2, 3, 4, 9, 10, 11, 12,
            13, 14, 15, 16, 17, 18, 19, 20,
        ]

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# NEIGHBORS / RID
# ---------------------------------------------------------------------------

def test_neighbors_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

    try:
        for key in [10, 20, 30, 40, 50, 60]:
            seq.insert((key,))

        seq.reorganize()

        assert seq.n_pages == 2
        assert logical_keys(seq) == [10, 20, 30, 40, 50, 60]

        # frontera a mitad de la última página
        assert seq._find_neighbors(55, duplicates_after=False) == ((2, 0), (2, 1))
        # frontera en medio de la primera página
        assert seq._find_neighbors(35, duplicates_after=False) == ((1, 2), (1, 3))
        # clave más pequeña que todo
        assert seq._find_neighbors(5, duplicates_after=False) == (None, (1, 0))
        # clave más grande que todo
        assert seq._find_neighbors(100, duplicates_after=False) == ((2, 1), None)
        # clave de la frontera exacta entre páginas
        assert seq._find_neighbors(50, duplicates_after=False) == ((1, 3), (2, 0))

    finally:
        close_sequential(filename, fm, bm)


def test_rid_conversion():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    try:
        rids = [
            (0, 0),
            (0, 1),
            (1, 0),
            (1, 20),
            (100, 65535),
        ]

        for rid in rids:
            value = seq._rid_to_int(rid)
            result = seq._int_to_rid(value)

            assert result == rid

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------

def test_persistence_variable():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    for key in [50, 20, 80, 10, 40]:
        seq.insert((key, f"value-{key}"))

    expected = [record.params for record in logical_records(seq)]
    assert seq.n_records == 5

    close_file(fm, bm)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, PAGE_SIZE, VARIABLE_FORMAT)

    try:
        assert seq.n_records == 5
        assert [r.params for r in logical_records(seq)] == expected

        assert [r.params for r in seq.search(20)] == [[20, "value-20"]]

    finally:
        close_sequential(filename, fm, bm)


def test_persistence_fixed():
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

    for key in [10, 20, 30, 40, 50]:
        seq.insert((key,))

    expected = logical_keys(seq)

    close_file(fm, bm)

    fm = FileManager(filename, FIXED_PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, FIXED_PAGE_SIZE, FIXED_FORMAT)

    try:
        assert seq.n_records == 5
        assert logical_keys(seq) == expected

        assert [r.params[0] for r in seq.search(10)] == [10]
        assert [r.params[0] for r in seq.search(30)] == [30]
        assert [r.params[0] for r in seq.search(50)] == [50]

    finally:
        close_sequential(filename, fm, bm)


def test_persistence_after_delete():
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

    for key in range(1, 8):
        seq.insert((key, f"value-{key}"))

    seq.delete(3)
    seq.delete(5)

    expected = [1, 2, 4, 6, 7]

    close_file(fm, bm)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, PAGE_SIZE, VARIABLE_FORMAT)

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
    filename, fm, bm, seq = create_sequential(FIXED_FORMAT, FIXED_PAGE_SIZE)

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
    filename, fm, bm, seq = create_sequential(VARIABLE_FORMAT, PAGE_SIZE)

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
    test_rid_conversion,

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