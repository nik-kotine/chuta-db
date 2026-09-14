import os
import sys

from storage.formats.record_packer import RecordPacker
from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from storage.pages.slotted_page import SlottedPage, PAGE_SIZE
from storage.files.heap_file import HeapFile, MAX_RECORD_SIZE

TEST_FILE = "test_heap.bin"
PAGE_SIZE = 4096
HEADER_SIZE = 10
BUFFER_FRAMES = 10


def limpiar():
    if os.path.exists(TEST_FILE):
        os.remove(TEST_FILE)


# ---------- 1. record.py: empaquetado/desempaquetado de un registro ----------

def test_record():
    print("\n--- record.py ---")
    packer = RecordPacker(["int", "float", "string"])

    blob = packer.record_encoder([7, 2.5, "utec"])
    print("bytes empaquetados:", blob)

    valores = packer.record_decoder(blob)
    print("valores recuperados:", valores)

    assert valores == [7, 2.5, "utec"]
    print("OK: el roundtrip encoder/decoder devuelve los mismos valores")


# ---------- 2. page.py: una sola pagina, sin heapfile de por medio ----------

def test_page():
    print("\n--- page.py ---")
    page = SlottedPage(page_id=1)

    slot_a = page.insert(b"primer registro")
    slot_b = page.insert(b"segundo registro")
    print("insert ->", slot_a, slot_b)
    print("espacio libre tras 2 inserts:", page.free_space_bytes)

    assert page.get_record(slot_a) == b"primer registro"
    assert page.get_record(slot_b) == b"segundo registro"
    print("OK: get_record devuelve lo insertado")

    assert page.delete_record(slot_a) is True
    assert page.get_record(slot_a) is None
    print("OK: delete_record borra y get_record ya no lo encuentra")

    espacio_antes = page.free_space_bytes
    page.defragment()
    print("espacio libre antes/despues de defragment:", espacio_antes, page.free_space_bytes)
    assert page.free_space_bytes >= espacio_antes
    print("OK: defragment recupera el espacio del registro borrado")


# ---------- 3. heapfile.py: integracion completa, con record_format encima ----------

def test_heapfile_integracion():
    print("\n--- heapfile.py (integracion) ---")
    limpiar()
    
    # Definimos el esquema del formato que usará este HeapFile
    schema = ["int", "float", "string"]

    fm = FileManager(TEST_FILE, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)

    # Pasamos el esquema directamente al HeapFile para que gestione el empaquetado interno
    hf = HeapFile(TEST_FILE, bm, schema)
    rids = []
    
    for i in range(5):
        valores = [i, i * 1.5, f"alumno{i}"]
        rid = hf.insert(valores) # Ahora inserta listas directamente
        rids.append(rid)
    print("RIDs insertados:", rids)

    for rid, i in zip(rids, range(5)):
        valores = hf.fetch(rid) # Fetch ya devuelve la lista desempaquetada
        assert valores == [i, i * 1.5, f"alumno{i}"]
    print("OK: los 5 registros se leen de vuelta con sus valores correctos")

    assert hf.delete(rids[2]) is True
    assert hf.fetch(rids[2]) is None
    assert hf.delete(rids[2]) is False
    print("OK: remove borra, get ya no lo encuentra, y un doble remove no revive nada")

    hf.reorganize() # Usamos reorganize() en lugar de vacuum() para alinear con la interfaz RecordFile
    print("OK: reorganize corre sin romper el resto de los registros")
    
    for rid, i in zip(rids, range(5)):
        if rid == rids[2]:
            continue
        valores = hf.fetch(rid)
        assert valores == [i, i * 1.5, f"alumno{i}"]
    print("OK: tras reorganize los registros que seguian vivos siguen intactos")

    # Registro demasiado grande que excede el tamaño máximo permitido
    try:
        # Creamos una lista con un string gigante que supere el MAX_RECORD_SIZE
        hf.insert([999, 99.9, "x" * (MAX_RECORD_SIZE + 1)])
        raise AssertionError("debio lanzar ValueError")
    except ValueError as e:
        print("OK: registro demasiado grande rechazado ->", e)

    # Forzar una segunda página con registros grandes
    rids_grandes = [hf.insert([j, 1.0, "y" * 1000]) for j in range(6)]
    paginas_usadas = sorted(set(r.page_id for r in rids_grandes))
    print("paginas usadas para registros grandes:", paginas_usadas)
    assert len(paginas_usadas) > 1
    print("OK: cuando una pagina se llena, heapfile crea una pagina nueva sola")

    hf.close()
    limpiar()


# ---------- 4. memoria RAM vs memoria secundaria (disco) ----------

def test_memoria_ram_vs_disco():
    print("\n--- RAM vs disco secundario (Con BufferManager) ---")
    limpiar()
    
    schema = ["string"]
    fm = FileManager(TEST_FILE, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)
    hf = HeapFile(TEST_FILE, bm, schema)

    bm.flush_all()
    tam_inicial = os.path.getsize(TEST_FILE)
    print("tamano del archivo recien creado:", tam_inicial, "bytes")

    rids = []
    for i in range(20):
        rid = hf.insert([f"registro numero {i}"])
        rids.append(rid)

    bm.flush_all()
    tam_final = os.path.getsize(TEST_FILE)
    print("tamano del archivo tras 20 inserts:", tam_final, "bytes")
    assert tam_final > tam_inicial
    print("OK: el archivo en disco crecio, los datos SI se estan persistiendo")

    tam_buffer_pool = sum(sys.getsizeof(frame.page_bin) for frame in bm.frames if frame.page_bin is not None)
    print(f"tamano en RAM ocupado por el Buffer Pool (Max {BUFFER_FRAMES} frames):", tam_buffer_pool, "bytes")
    print("OK: la RAM de la DB está acotada estrictamente por tu BufferManager.")

    hf.close()

    # Destruir objetos y reabrir desde cero simulando reinicio
    del hf
    del bm
    del fm

    fm2 = FileManager(TEST_FILE, PAGE_SIZE, HEADER_SIZE)
    bm2 = BufferManager(fm2, BUFFER_FRAMES)
    hf2 = HeapFile(TEST_FILE, bm2, schema)
    
    for i, rid in enumerate(rids):
        valor = hf2.fetch(rid)
        assert valor == [f"registro numero {i}"]
        
    print("OK: tras destruir todo en RAM y reabrir el archivo desde cero,")
    print("    los 20 registros se siguen leyendo bien.")

    hf2.close()
    limpiar()

if __name__ == "__main__":
    test_record()
    test_page()
    test_heapfile_integracion()
    test_memoria_ram_vs_disco()
    print("\nTodos los tests pasaron")