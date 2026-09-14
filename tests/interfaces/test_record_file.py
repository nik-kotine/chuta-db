import os
from storage.record_file import RecordFile
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.files.heap_file import HeapFile
from storage.files.sequential_file import SequentialFile

PAGE_SIZE = 4096
HEADER_SIZE = 16
BUFFER_FRAMES = 10

def limpiar_archivos(*filenames):
    for f in filenames:
        if os.path.exists(f):
            os.remove(f)

def probar_contrato_record_file(rf: RecordFile):
    """
    Test: valida que cualquier implementación de RecordFile
    cumpla con las promesas de la interfaz.
    """
    # 1. Test de Insert
    rec1 = [1, 100]
    rec2 = [2, 200]
    rec3 = [3, 300]

    rid1 = rf.insert(rec1)
    rid2 = rf.insert(rec2)
    rid3 = rf.insert(rec3)

    assert rid1 is not None
    assert rid2 is not None
    assert rid3 is not None

    # 2. Test de Fetch
    assert rf.fetch(rid1) == rec1
    assert rf.fetch(rid2) == rec2
    assert rf.fetch(rid3) == rec3

    # 3. Test de Scan (debe encontrar los 3 registros vivos)
    registros_escaneados = dict(rf.scan())
    assert len(registros_escaneados) == 3
    assert registros_escaneados[rid1] == rec1
    assert registros_escaneados[rid2] == rec2

    # 4. Test de Delete
    assert rf.delete(rid2) is True
    assert rf.fetch(rid2) is None  # Ya no existe

    # Scan tras borrado debe tener 2 elementos
    registros_tras_delete = dict(rf.scan())
    assert len(registros_tras_delete) == 2
    assert rid2 not in registros_tras_delete

    # 5. Test de Reorganize
    rf.reorganize()
    # Tras reorganizar, los sobrevivientes deben seguir siendo legibles o haberse compactado
    registros_tras_reorganize = list(rf.scan())
    assert len(registros_tras_reorganize) == 2

    # 6. Test de Close
    rf.close()

def test_heap_file_cumple_record_file():
    filename = "test_contract_heap.bin"
    limpiar_archivos(filename, filename + ".fmt")

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    # HeapFile configurado
    rf = HeapFile(filename, bm, record_format=["int", "int"])

    print("Probando HeapFile bajo interfaz RecordFile...", end=" ")
    probar_contrato_record_file(rf)
    print("OK")

    limpiar_archivos(filename, filename + ".fmt")

def test_sequential_file_cumple_record_file():
    filename = "test_contract_seq.bin"
    limpiar_archivos(filename)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    # SequentialFile configurado (formato "ii" -> 2 enteros)
    rf = SequentialFile(bm, PAGE_SIZE, record_format="ii")

    print("Probando SequentialFile bajo interfaz RecordFile...", end=" ")
    probar_contrato_record_file(rf)
    print("OK")

    limpiar_archivos(filename)

if __name__ == "__main__":
    print("=== INICIANDO SUITE DE PRUEBAS RECORDFILE ===")
    test_heap_file_cumple_record_file()
    test_sequential_file_cumple_record_file()
    print("\n¡Ambas clases cumplen al 100% con la interfaz RecordFile!")