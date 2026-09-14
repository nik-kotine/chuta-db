import os
import struct
import tempfile

from FileManager import FileManager
from BufferManager import BufferManager
from VariableSeqFile_aftercommit import VariableSequentialFile


PAGE_SIZE = 128
HEADER_SIZE = 16
BUFFER_FRAMES = 10

# Un entero + strings variables
RECORD_FORMAT = ["integer", "text"]


def create_sequential(record_format=RECORD_FORMAT, page_size=PAGE_SIZE):
    fd, filename = tempfile.mkstemp()
    os.close(fd)

    with open(filename, "wb") as f:
        f.write(struct.pack(">iiii", 0, -1, 0, 0))

        # Página 0 = overflow
        page = bytearray(page_size)
        struct.pack_into(">i", page, 0, page_size)  # offset
        struct.pack_into(">i", page, 4, 0)          # size

        f.write(page)

    fm = FileManager(filename, page_size, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = VariableSequentialFile(bm, page_size, record_format)

    return filename, fm, bm, seq



def close_file(fm, bm):
    for phys_page_id in list(bm.page_table.keys()):
        bm.flush_page(phys_page_id)

    fm.flush()
    fm.close()


def close_sequential(filename, fm, bm):
    for phys_page_id in list(bm.page_table.keys()):
        bm.flush_page(phys_page_id)

    fm.flush()
    fm.close()
    os.remove(filename)


def logical_records(seq):
    """
    Devuelve los registros vivos siguiendo la cadena lógica.
    """
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
    return [
        record.params[0]
        for record in logical_records(seq)
    ]


def all_physical_records(seq):
    """
    Devuelve todos los registros físicos, incluyendo deleted,
    agrupados por página.
    """
    result = {}

    for phys_page_id in range(0, seq.n_pages + 1):
        page = seq._load_page(phys_page_id)

        try:
            result[phys_page_id] = []

            for slot_id in range(page.size):
                record = page.get_by_slot_id(slot_id)

                result[phys_page_id].append(
                    (slot_id, record)
                )
        finally:
            seq.buffer_manager.unpin_page(phys_page_id)

    return result


def primary_page_keys(seq):
    result = []

    for phys_page_id in range(1, seq.n_pages + 1):
        page = seq._load_page(phys_page_id)

        try:
            page_keys = []

            for slot_id in range(page.size):
                record = page.get_record_by_slot_id(slot_id)

                if not record.deleted:
                    page_keys.append(record.params[0])

            result.append(page_keys)

        finally:
            seq.buffer_manager.unpin_page(phys_page_id)

    return result


# ---------------------------------------------------------------------------
# SERIALIZER
# ---------------------------------------------------------------------------

def test_serializer_integer():
    filename, fm, bm, seq = create_sequential(
        ["integer"]
    )

    try:
        data = seq.serializer.serialize([12345])

        assert len(data) == 4

        result = seq.serializer.deserialize(data)

        assert result == [12345]

    finally:
        close_sequential(filename, fm, bm)


def test_serializer_bigint():
    filename, fm, bm, seq = create_sequential(
        ["bigint"]
    )

    try:
        value = 1234567890123

        data = seq.serializer.serialize([value])
        result = seq.serializer.deserialize(data)

        assert result == [value]

    finally:
        close_sequential(filename, fm, bm)


def test_serializer_variable_text():
    filename, fm, bm, seq = create_sequential(
        ["text"]
    )

    try:
        value = "hola mundo"

        data = seq.serializer.serialize([value])
        result = seq.serializer.deserialize(data)

        assert result == [value]

    finally:
        close_sequential(filename, fm, bm)


def test_serializer_multiple_variable_fields():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text", "text"]
    )

    try:
        params = [
            42,
            "hola",
            "esto es un texto bastante más largo"
        ]

        data = seq.serializer.serialize(params)
        result = seq.serializer.deserialize(data)

        assert result == params

    finally:
        close_sequential(filename, fm, bm)


def test_serializer_empty_text():
    filename, fm, bm, seq = create_sequential(
        ["text"]
    )

    try:
        params = [""]

        data = seq.serializer.serialize(params)
        result = seq.serializer.deserialize(data)

        assert result == params

    finally:
        close_sequential(filename, fm, bm)


def test_serializer_unicode():
    filename, fm, bm, seq = create_sequential(
        ["text"]
    )

    try:
        params = ["áéíóú ñ 中文 😀"]

        data = seq.serializer.serialize(params)
        result = seq.serializer.deserialize(data)

        assert result == params

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------

def test_insert_first_record():
    filename, fm, bm, seq = create_sequential()

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


def test_insert_records_sorted():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [50, 20, 80, 10, 40, 30, 60]:
            seq.insert((key, f"value-{key}"))

        assert logical_keys(seq) == [
            10, 20, 30, 40, 50, 60, 80
        ]

    finally:
        close_sequential(filename, fm, bm)


def test_insert_before_first():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((20, "twenty"))
        seq.insert((30, "thirty"))
        seq.insert((40, "forty"))

        rid = seq.insert((10, "ten"))

        assert logical_keys(seq) == [10, 20, 30, 40]
        assert seq.first_rid == rid

        first = seq._get_record(seq.first_rid)

        assert first.params == [10, "ten"]

    finally:
        close_sequential(filename, fm, bm)


def test_insert_after_last():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((10, "ten"))
        seq.insert((20, "twenty"))
        rid = seq.insert((30, "thirty"))

        assert logical_keys(seq) == [10, 20, 30]

        record = seq._get_record(rid)

        assert record.next_rid is None

    finally:
        close_sequential(filename, fm, bm)


def test_insert_between_records():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((10, "ten"))
        seq.insert((30, "thirty"))

        seq.insert((20, "twenty"))

        assert logical_keys(seq) == [10, 20, 30]

    finally:
        close_sequential(filename, fm, bm)
"""
def test_duplicate_keys():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((20, "a"))
        seq.insert((20, "b"))
        seq.insert((20, "c"))

        records = logical_records(seq)

        print("logical records:")
        for r in records:
            print(r.params, "next =", r.next_rid)

        print("logical keys:", logical_keys(seq))

        assert logical_keys(seq) == [20, 20, 20]
        assert [r.params[1] for r in records] == ["a", "b", "c"]
    finally:
        pass
"""

def test_duplicate_keys():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((20, "a"))
        seq.insert((20, "b"))
        seq.insert((20, "c"))

        assert logical_keys(seq) == [20, 20, 20]

        results = seq.search(20)
        assert len(results) == 3
        assert [r.params[1] for r in results] == [
            "a", "b", "c"
        ]

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------

def test_search_existing_key():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30, 40]:
            seq.insert((key, f"value-{key}"))

        result = seq.search(20)

        assert len(result) == 1
        assert result[0].params == [20, "value-20"]

    finally:
        close_sequential(filename, fm, bm)


def test_search_non_existing_key():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30, 40]:
            seq.insert((key, f"value-{key}"))

        assert seq.search(25) == []
        assert seq.search(100) == []
        assert seq.search(1) == []

    finally:
        close_sequential(filename, fm, bm)


def test_search_duplicate_key():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((20, "a"))
        seq.insert((20, "b"))
        seq.insert((20, "c"))
        seq.insert((30, "d"))

        result = seq.search(20)

        assert len(result) == 3
        assert [r.params[1] for r in result] == [
            "a", "b", "c"
        ]

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

def test_delete_existing_key():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30]:
            seq.insert((key, f"value-{key}"))

        assert seq.delete(20) is True

        assert seq.search(20) == []

        assert logical_keys(seq) == [10, 30]

        assert seq.n_records == 2
        assert seq.n_deleted == 1

    finally:
        close_sequential(filename, fm, bm)


def test_delete_non_existing_key():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((10, "ten"))

        assert seq.delete(99) is False

        assert seq.n_records == 1
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_delete_duplicate_keys():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        seq.insert((20, "a"))
        seq.insert((20, "b"))
        seq.insert((20, "c"))
        seq.insert((30, "d"))
        seq.insert((40, "e"))
        seq.insert((50, "f"))

        assert seq.delete(50) is True
        assert seq.search(50) == []
        assert seq.n_deleted == 1

        assert seq.delete(20) is True

        assert seq.search(20) == []
        assert logical_keys(seq) == [30, 40]

        assert seq.n_records == 2

        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)

def test_delete_same_key_twice():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((10, "ten"))
        seq.insert((20, "twenty"))
        seq.insert((30, "thirty"))
        seq.insert((40, "forty"))

        assert seq.delete(10) is True
        assert seq.delete(10) is False
        assert seq.delete(30) is True
        assert seq.delete(30) is False
        assert seq.n_records == 2

        assert seq.n_deleted == 1 #debido al reorganize

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# VARIABLE-LENGTH RECORDS
# ---------------------------------------------------------------------------

def test_records_have_different_sizes():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

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

        assert [r.params for r in records] == [
            list(record)
            for record in values
        ]

    finally:
        close_sequential(filename, fm, bm)


def test_long_record():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        text = "x" * 80

        seq.insert((10, text))

        result = seq.search(10)

        assert len(result) == 1
        assert result[0].params == [10, text]

    finally:
        close_sequential(filename, fm, bm)


def test_many_different_record_sizes():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        expected = []

        for key in range(1, 30):
            text = "x" * (key * 3)

            seq.insert((key, text))
            expected.append([key, text])

        actual = [
            record.params
            for record in logical_records(seq)
        ]

        assert actual == expected

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# OVERFLOW
# ---------------------------------------------------------------------------

def test_overflow_is_used():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        for key in range(1, 10):
            seq.insert((key, "x" * 20))

        overflow = seq._load_page(0)

        try:
            assert overflow.size > 0
        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == list(range(1, 10))

    finally:
        close_sequential(filename, fm, bm)


def test_overflow_records_are_reachable():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        for key in range(1, 20):
            seq.insert((key, "x" * 30))

        records = logical_records(seq)

        assert len(records) == 19
        assert logical_keys(seq) == list(range(1, 20))

    finally:
        close_sequential(filename, fm, bm)


def test_insert_after_overflow_reorganization():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        for key in range(1, 15):
            seq.insert((key, "x" * 20))

        before = logical_keys(seq)

        # Fuerza explícitamente una reorganización.
        seq.reorganize()

        after = logical_keys(seq)

        assert after == before
        assert seq.n_records == len(before)
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# PAGE / SLOT
# ---------------------------------------------------------------------------

def test_slot_points_to_correct_variable_record():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        rid = seq.insert((10, "hello"))

        page_id, slot_id = rid

        page = seq._load_page(page_id)

        try:
            record = page.get_record_by_slot_id(slot_id)

            assert record.params == [10, "hello"]
            assert record.deleted is False
            assert record.next_rid is None

        finally:
            seq.buffer_manager.unpin_page(page_id)

    finally:
        close_sequential(filename, fm, bm)


def test_next_rid_chain():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        for key in [10, 20, 30, 40]:
            seq.insert((key, f"value-{key}"))

        current_rid = seq.first_rid
        keys = []

        while current_rid is not None:
            record = seq._get_record(current_rid)

            keys.append(record.params[0])

            current_rid = record.next_rid

        assert keys == [10, 20, 30, 40]

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# REORGANIZATION
# ---------------------------------------------------------------------------

def test_reorganize_removes_deleted_records():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        for key in range(1, 11):
            seq.insert((key, f"value-{key}"))

        seq.delete(2)
        seq.delete(4)
        seq.delete(6)
        seq.delete(8)

        expected = [1, 3, 5, 7, 9, 10]

        seq.reorganize()

        assert logical_keys(seq) == expected
        assert seq.n_records == 6
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_clears_overflow():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        for key in range(1, 20):
            seq.insert((key, "x" * 20))

        seq.reorganize()

        overflow = seq._load_page(0)

        try:
            assert overflow.size == 0
        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == list(range(1, 20))

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_resets_first_rid():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        for key in [30, 10, 20]:
            seq.insert((key, f"value-{key}"))

        seq.reorganize()

        assert seq.first_rid == (1, 0)
        assert logical_keys(seq) == [10, 20, 30]

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize_primary_pages_sorted():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        for key in range(1, 40):
            seq.insert((key, "x" * (key % 10)))

        seq.reorganize()

        pages = primary_page_keys(seq)

        flattened = [
            key
            for page in pages
            for key in page
        ]

        assert flattened == sorted(flattened)

        for i in range(len(pages) - 1):
            if pages[i] and pages[i + 1]:
                assert pages[i][-1] <= pages[i + 1][0]

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------

def test_persistence():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        for key in [50, 20, 80, 10, 40]:
            seq.insert((key, f"value-{key}"))

        expected = [
            record.params
            for record in logical_records(seq)
        ]

        close_file(fm, bm)

        fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
        bm = BufferManager(fm, BUFFER_FRAMES)
        seq = VariableSequentialFile(
            bm,
            PAGE_SIZE,
            ["integer", "text"]
        )

        assert seq.n_records == 5

        actual = [
            record.params
            for record in logical_records(seq)
        ]

        assert actual == expected

        assert [
            r.params
            for r in seq.search(20)
        ] == [[20, "value-20"]]

    finally:
        close_sequential(filename, fm, bm)


def test_persistence_after_delete():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"]
    )

    try:
        for key in range(1, 8):
            seq.insert((key, f"value-{key}"))

        seq.delete(3)
        seq.delete(5)

        expected = [1, 2, 4, 6, 7]

        close_file(fm, bm)

        fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
        bm = BufferManager(fm, BUFFER_FRAMES)
        seq = VariableSequentialFile(
            bm,
            PAGE_SIZE,
            ["integer", "text"]
        )

        assert logical_keys(seq) == expected
        assert seq.n_records == 5
        assert seq.n_deleted == 2

    finally:
        close_sequential(filename, fm, bm)


# ---------------------------------------------------------------------------
# RID
# ---------------------------------------------------------------------------

def test_rid_conversion():
    filename, fm, bm, seq = create_sequential()

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

def test_reorganize_next_rids():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        for key in range(1, 30):
            seq.insert((key, "x" * (key % 20 + 1)))

        seq.reorganize()

        current_rid = seq.first_rid
        expected_key = 1

        while current_rid is not None:
            record = seq._get_record(current_rid)

            assert record is not None
            assert record.params[0] == expected_key

            current_rid = record.next_rid
            expected_key += 1

        assert expected_key == 30

    finally:
        close_sequential(filename, fm, bm)


def test_record_too_large():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        huge_text = "x" * 500

        try:
            seq.insert((10, huge_text))
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass

    finally:
        close_sequential(filename, fm, bm)


def test_variable_string_sizes():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

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
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        values = [
            (1, "áéíóú"),
            (2, "你好"),
            (3, "🙂"),
            (4, "año"),
            (5, "こんにちは"),
        ]

        for value in values:
            seq.insert(value)

        for key, value in values:
            result = seq.search(key)
            assert len(result) == 1

            assert result[0].params[1] == value

    finally:
        close_sequential(filename, fm, bm)



def test_record_near_page_limit():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        # Ir aumentando hasta encontrar un registro que ya no quepa.
        length = 0

        while True:
            value = (1, "x" * length)

            try:
                seq.insert(value)
                length += 1
            except RuntimeError:
                break

        assert length > 0

    finally:
        close_sequential(filename, fm, bm)



def test_record_too_large_2():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        huge_value = (1, "x" * 1000)

        try:
            seq.insert(huge_value)
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass

        assert seq.n_records == 0
        assert seq.first_rid is None

    finally:
        close_sequential(filename, fm, bm)


def test_insert_after_delete():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

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



def test_duplicate_order_survives_reorganization():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        values = [
            (20, "a"),
            (20, "b"),
            (20, "c"),
            (10, "ten"),
            (30, "thirty"),
            (40, "forty"),
            (50, "fifty"),
            (60, "sixty"),
            (70, "seventy"),
            (80, "eighty"),
        ]

        for value in values:
            seq.insert(value)

        seq.reorganize()

        assert [
            r.params[1]
            for r in seq.search(20)
        ] == ["a", "b", "c"]

    finally:
        close_sequential(filename, fm, bm)




def test_multiple_reorganizations():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

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

    finally:
        close_sequential(filename, fm, bm)



def test_record_counters():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

    try:
        for i in range(10):
            seq.insert((i, f"value-{i}"))

        assert seq.n_records == 10
        assert seq.n_deleted == 0

        assert seq.delete(2)
        assert seq.delete(5)
        assert seq.delete(8)

        assert seq.n_records == 7
        assert seq.n_deleted == 3

        seq.reorganize()

        assert seq.n_records == 7
        assert seq.n_deleted == 0

    finally:
        close_sequential(filename, fm, bm)



# ---------------------------------------------------------------------------
# EVERYTHING TOGETHER
# ---------------------------------------------------------------------------


def test_everything_together():
    filename, fm, bm, seq = create_sequential(
        ["integer", "text"],
        page_size=128
    )

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
            80, 90, 100
        ]

        #print(r.params[1] for r in seq.search(20))
        assert [
            r.params[1]
            for r in seq.search(20)
        ] == [
            "twenty",
            "twenty-a",
            "twenty-b"
        ]

        assert seq.delete(20) is True
        assert seq.delete(70) is True
        assert seq.delete(5) is True

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100
        ]

        seq.reorganize()

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100
        ]

        assert seq.n_deleted == 0

        pages = primary_page_keys(seq)

        flattened = [
            key
            for page in pages
            for key in page
        ]

        assert flattened == sorted(flattened)

    finally:
        close_sequential(filename, fm, bm)

# ---------------------------------------------------------------------------
# RUNNER
# ---------------------------------------------------------------------------

tests = [
    test_serializer_integer,
    test_serializer_bigint,
    test_serializer_variable_text,
    test_serializer_multiple_variable_fields,
    test_serializer_empty_text,
    test_serializer_unicode,

    test_insert_first_record,
    test_insert_records_sorted,
    test_insert_before_first,
    test_insert_after_last,
    test_insert_between_records,
    test_duplicate_keys,

    test_search_existing_key,
    test_search_non_existing_key,
    test_search_duplicate_key,

    test_delete_existing_key,
    test_delete_non_existing_key,
    test_delete_duplicate_keys,
    test_delete_same_key_twice,

    test_records_have_different_sizes,
    test_long_record,
    test_many_different_record_sizes,

    test_overflow_is_used,
    test_overflow_records_are_reachable,
    test_insert_after_overflow_reorganization,

    test_slot_points_to_correct_variable_record,
    test_next_rid_chain,

    test_reorganize_removes_deleted_records,
    test_reorganize_clears_overflow,
    test_reorganize_resets_first_rid,
    test_reorganize_primary_pages_sorted,

    test_persistence,
    test_persistence_after_delete,

    test_rid_conversion,
    test_reorganize_next_rids,
    test_record_too_large,

    test_variable_string_sizes,
    test_utf8_strings,
    test_record_near_page_limit,
    test_record_too_large_2,
    test_insert_after_delete,
    test_duplicate_order_survives_reorganization,
    test_multiple_reorganizations,
    test_record_counters,

    test_everything_together,
]


for test in tests:
    print(f"Running {test.__name__}...", end=" ")

    test()

    print("OK")


print(f"\n{len(tests)} tests passed.")