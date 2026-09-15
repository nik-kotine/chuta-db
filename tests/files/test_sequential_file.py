import os
import struct
import tempfile

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.files.sequential_file import SequentialFile


PAGE_SIZE = 64
HEADER_SIZE = 16
RECORD_FORMAT = "i"
BUFFER_FRAMES = 10


def create_sequential():
    fd, filename = tempfile.mkstemp()
    os.close(fd)

    with open(filename, "wb") as f:
        f.write(struct.pack("iiii", 0, -1, 0, 0))
        f.write(b"\x00" * PAGE_SIZE)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, PAGE_SIZE, RECORD_FORMAT)

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

def keys(seq):
    return [record.params[0] for record in seq.search_all()]


def logical_keys(seq):
    result = []
    current_rid = seq.first_rid

    while current_rid is not None:
        record = seq._get_record(current_rid)

        if record is None:
            break

        if not record.deleted:
            result.append(record.params[0])

        current_rid = record.next_rid

    return result


def primary_page_keys(seq):
    result = []

    for phys_page_id in range(1, seq.n_pages + 1):
        page = seq._load_page(phys_page_id)

        try:
            page_keys = []

            for slot_id in range(page.n_records):
                record = page.get_record_by_slot_id(slot_id)

                if not record.deleted:
                    page_keys.append(record.params[0])

            result.append(page_keys)

        finally:
            seq.buffer_manager.unpin_page(phys_page_id)

    return result


def test_insert():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30, 40, 50]:
            seq.insert((key,))

        assert logical_keys(seq) == [10, 20, 30, 40, 50]
        assert seq.n_records == 5

    finally:
        close_sequential(filename, fm, bm)


def test_search():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30, 40, 50]:
            seq.insert((key,))

        assert [r.params[0] for r in seq.search(10)] == [10]
        assert [r.params[0] for r in seq.search(30)] == [30]
        assert [r.params[0] for r in seq.search(50)] == [50]
        assert seq.search(99) == []

    finally:
        close_sequential(filename, fm, bm)


def test_insert_before_first():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [20, 30, 40]:
            seq.insert((key,))

        seq.insert((10,))

        assert logical_keys(seq) == [10, 20, 30, 40]
        assert seq.first_rid is not None
        assert seq._get_record(seq.first_rid).params[0] == 10

    finally:
        close_sequential(filename, fm, bm)


def test_insert_after_last():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30]:
            seq.insert((key,))

        seq.insert((40,))

        assert logical_keys(seq) == [10, 20, 30, 40]

        last_rid = None
        current_rid = seq.first_rid

        while current_rid is not None:
            last_rid = current_rid
            current_rid = seq._get_record(current_rid).next_rid

        assert seq._get_record(last_rid).next_rid is None

    finally:
        close_sequential(filename, fm, bm)


def test_duplicate_records():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 20, 20, 30]:
            seq.insert((key,))

        assert logical_keys(seq) == [10, 20, 20, 20, 30]

        result = seq.search(20)

        assert len(result) == 3
        assert [r.params[0] for r in result] == [20, 20, 20]

    finally:
        close_sequential(filename, fm, bm)


def test_delete_by_key():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30, 40, 50]:
            seq.insert((key,))

        assert seq.delete_by_key(30) is True
        assert seq.search(30) == []

        assert seq.delete_by_key(99) is False

        assert logical_keys(seq) == [10, 20, 40, 50]

    finally:
        close_sequential(filename, fm, bm)


def test_delete_by_key_duplicates():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 20, 20, 30]:
            seq.insert((key,))

        assert seq.delete_by_key(20) is True
        assert seq.search(20) == []
        assert logical_keys(seq) == [10, 30]

    finally:
        close_sequential(filename, fm, bm)


def test_chain_sorted_after_delete_by_key():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30, 40, 50, 60]:
            seq.insert((key,))

        assert seq.delete_by_key(20) is True
        assert seq.delete_by_key(40) is True

        assert logical_keys(seq) == [10, 30, 50, 60]

    finally:
        close_sequential(filename, fm, bm)


def test_reorganize():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [10, 20, 30, 40, 50, 60, 70, 80]:
            seq.insert((key,))

        assert seq.delete_by_key(20) is True
        assert seq.delete_by_key(40) is True
        assert seq.delete_by_key(60) is True
        assert seq.delete_by_key(80) is True

        seq.reorganize()

        assert logical_keys(seq) == [10, 30, 50, 70]
        assert seq.n_records == 4
        assert seq.n_pages == 1
        assert seq.first_rid == (1, 0)

        page = seq._load_page(1)

        try:
            assert page.n_records == 4
            assert [
                page.get_record_by_slot_id(i).params[0]
                for i in range(page.n_records)
            ] == [10, 30, 50, 70]

        finally:
            seq.buffer_manager.unpin_page(1)

        overflow = seq._load_page(0)

        try:
            assert overflow.n_records == 0

        finally:
            seq.buffer_manager.unpin_page(0)

    finally:
        close_sequential(filename, fm, bm)


def test_primary_pages_sorted():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in range(1, 21):
            seq.insert((key,))

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


def test_overflow_full():
    filename, fm, bm, seq = create_sequential()

    try:
        seq.insert((10,))
        seq.insert((20,))
        seq.insert((30,))
        seq.insert((40,))
        seq.insert((50,))

        overflow = seq._load_page(0)

        try:
            assert overflow.n_records == 4

        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == [10, 20, 30, 40, 50]

        seq.insert((60,))

        overflow = seq._load_page(0)

        try:
            assert overflow.n_records == 1

        finally:
            seq.buffer_manager.unpin_page(0)

        assert logical_keys(seq) == [10, 20, 30, 40, 50, 60]

    finally:
        close_sequential(filename, fm, bm)


def test_persistence():
    filename, fm, bm, seq = create_sequential()

    for key in [10, 20, 30, 40, 50]:
        seq.insert((key,))

    expected = logical_keys(seq)

    close_file(fm, bm)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    seq = SequentialFile(bm, PAGE_SIZE, RECORD_FORMAT)

    try:
        assert seq.n_records == 5
        assert logical_keys(seq) == expected

        assert [r.params[0] for r in seq.search(10)] == [10]
        assert [r.params[0] for r in seq.search(30)] == [30]
        assert [r.params[0] for r in seq.search(50)] == [50]

    finally:
        close_sequential(filename, fm, bm)

def test_everything_together():
    filename, fm, bm, seq = create_sequential()

    try:
        for key in [
            50, 20, 80, 10, 30,
            20, 70, 90, 40, 60,
            20, 100, 5
        ]:
            seq.insert((key,))

        assert logical_keys(seq) == [
            5, 10, 20, 20, 20,
            30, 40, 50, 60, 70,
            80, 90, 100
        ]

        assert [r.params[0] for r in seq.search(20)] == [20, 20, 20]

        assert seq.delete_by_key(20) is True
        assert seq.delete_by_key(70) is True
        assert seq.delete_by_key(5) is True

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100
        ]

        seq.reorganize()

        assert logical_keys(seq) == [
            10, 30, 40, 50, 60,
            80, 90, 100
        ]

        pages = primary_page_keys(seq)
        flattened = [key for page in pages for key in page]

        assert flattened == sorted(flattened)

    finally:
        close_sequential(filename, fm, bm)


tests = [
    test_insert,
    test_search,
    test_insert_before_first,
    test_insert_after_last,
    test_duplicate_records,
    test_delete_by_key,
    test_delete_by_key_duplicates,
    test_chain_sorted_after_delete_by_key,
    test_reorganize,
    test_primary_pages_sorted,
    test_overflow_full,
    test_persistence,
    test_everything_together,
]


for test in tests:
    print(f"Running {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests passed.")