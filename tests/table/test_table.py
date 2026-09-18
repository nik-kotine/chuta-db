import os
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.table import Table
from storage.files.sequential_file import FILE_HEADER_SIZE

PAGE_SIZE = 4096
HEADER_SIZE = 16
BUFFER_FRAMES = 10


def limpiar(filename):
    if os.path.exists(filename):
        os.remove(filename)


def test_table_heap():
    filename = "test_tabla_heap.dat"
    limpiar(filename)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    schema = ["integer", "varchar(20)", "boolean"]

    tabla = Table("test_tabla_heap", schema, bm, file_type="heap", key_index=0)

    # 1. Insertar
    rid1 = tabla.insert([1, "Alice", True])
    rid2 = tabla.insert([2, "Bob", False])
    assert rid1 is not None and rid2 is not None

    # 2. Fetch
    assert tabla.get(rid1) == [1, "Alice", True]

    # 3. Scan
    records = list(tabla.scan())
    assert len(records) == 2

    # 4. Search by Key
    res = tabla.search_by_key(2)
    assert len(res) == 1
    assert res[0][1] == "Bob"

    # 5. Delete
    assert tabla.delete(rid1) is True
    assert tabla.get(rid1) is None

    tabla.close()
    limpiar(filename)
    print("test_table_heap: OK")


def test_table_sequential():
    filename = "test_tabla_seq.dat"
    limpiar(filename)

    fm = FileManager(filename, PAGE_SIZE, FILE_HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    schema = ["integer", "integer"]

    tabla = Table("test_tabla_seq", schema, bm, file_type="sequential", key_index=0)

    # 1. Insertar desordenado
    tabla.insert([30, 300])
    tabla.insert([10, 100])
    tabla.insert([20, 200])

    # 2. Search by key (aprovecha la ordenación del SequentialFile)
    res = tabla.search_by_key(20)
    assert len(res) == 1
    assert res[0] == [20, 200]

    # 3. Scan ordenado
    scan_res = [val for _, val in tabla.scan()]
    assert scan_res == [[10, 100], [20, 200], [30, 300]]

    # 4. Delete by key
    assert tabla.delete_by_key(20) is True
    assert tabla.search_by_key(20) == []

    tabla.close()
    limpiar(filename)
    print("test_table_sequential: OK")


if __name__ == "__main__":
    print("=== PROBANDO CLASE TABLE ===")
    test_table_heap()
    test_table_sequential()
    print("¡Todas las pruebas de Table pasaron exitosamente!")