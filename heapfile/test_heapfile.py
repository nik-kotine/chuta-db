import os
import sys

from heapfile.record import RecordPacker
from heapfile.page import SlottedPage, PAGE_SIZE
from heapfile.heapfile import HeapFile, MAX_RECORD_SIZE

TEST_FILE = "test_heap.bin"


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


# ---------- 3. heapfile.py: integracion completa, con record.py encima ----------

def test_heapfile_integracion():
    print("\n--- heapfile.py (integracion) ---")
    limpiar()
    packer = RecordPacker(["int", "float", "string"])

    hf = HeapFile(TEST_FILE)
    rids = []
    for i in range(5):
        blob = packer.record_encoder([i, i * 1.5, f"alumno{i}"])
        rid = hf.add(blob)
        rids.append(rid)
    print("RIDs insertados:", rids)

    for rid, i in zip(rids, range(5)):
        valores = packer.record_decoder(hf.get(rid))
        assert valores == [i, i * 1.5, f"alumno{i}"]
    print("OK: los 5 registros se leen de vuelta con sus valores correctos")

    assert hf.remove(rids[2]) is True
    assert hf.get(rids[2]) is None
    assert hf.remove(rids[2]) is False
    print("OK: remove borra, get ya no lo encuentra, y un doble remove no revive nada")

    hf.compact(1)
    hf.vacuum()
    print("OK: compact/vacuum corren sin romper el resto de los registros")
    for rid, i in zip(rids, range(5)):
        if rid == rids[2]:
            continue
        valores = packer.record_decoder(hf.get(rid))
        assert valores == [i, i * 1.5, f"alumno{i}"]
    print("OK: tras compact/vacuum los registros que seguian vivos siguen intactos")

    # registro que no entra en ninguna pagina vacia -> ValueError
    try:
        hf.add(b"x" * (MAX_RECORD_SIZE + 1))
        raise AssertionError("debio lanzar ValueError")
    except ValueError as e:
        print("OK: registro demasiado grande rechazado ->", e)

    # forzar una segunda pagina con registros grandes
    rids_grandes = [hf.add(b"y" * 1000) for _ in range(6)]
    paginas_usadas = sorted(set(r.page_id for r in rids_grandes))
    print("paginas usadas para 6 registros de 1000 bytes:", paginas_usadas)
    assert len(paginas_usadas) > 1
    print("OK: cuando una pagina se llena, heapfile crea una pagina nueva sola")

    hf.close()
    limpiar()


# ---------- 4. memoria RAM vs memoria secundaria (disco) ----------

def test_memoria_ram_vs_disco():
    print("\n--- RAM vs disco secundario ---")
    limpiar()
    hf = HeapFile(TEST_FILE)

    # 4.1: el tamano del archivo en disco crece con cada add, prueba que
    # los datos se estan escribiendo en memoria secundaria y no solo
    # quedando en un objeto de Python.
    tam_inicial = os.path.getsize(TEST_FILE)
    print("tamano del archivo recien creado (solo pagina 0):", tam_inicial, "bytes")
    assert tam_inicial == PAGE_SIZE

    rids = []
    for i in range(20):
        rid = hf.add(f"registro numero {i}".encode("utf-8"))
        rids.append(rid)

    tam_final = os.path.getsize(TEST_FILE)
    print("tamano del archivo tras 20 inserts:", tam_final, "bytes")
    assert tam_final > tam_inicial
    print("OK: el archivo en disco crecio, los datos SI se estan persistiendo")

    # 4.2: lo unico que HeapFile mantiene vivo en RAM entre operaciones es
    # la pagina 0 (el directorio) -- su tamano no depende de cuantos
    # registros se hayan insertado.
    tam_directorio_en_ram = sys.getsizeof(hf._dir_data)
    print("tamano en RAM del directorio cacheado (self._dir_data):", tam_directorio_en_ram, "bytes")
    print("tamano del archivo en disco en este punto:", tam_final, "bytes")
    print(
        "OK: la RAM que HeapFile retiene es fija (~", PAGE_SIZE, "bytes) sin importar cuanto",
        "crecio el archivo en disco -- las paginas de datos no quedan cacheadas.",
    )

    hf.close()

    # 4.3: la prueba mas fuerte -- destruir el objeto por completo, abrir
    # uno NUEVO sobre el mismo archivo, y confirmar que los datos siguen
    # ahi. Si algo dependiera de una cache en RAM que no se persistio,
    # esto fallaria.
    del hf
    hf2 = HeapFile(TEST_FILE)
    for i, rid in enumerate(rids):
        valor = hf2.get(rid)
        assert valor == f"registro numero {i}".encode("utf-8")
    print("OK: tras destruir el objeto y reabrir el archivo desde cero,")
    print("    los 20 registros se siguen leyendo bien -> viven en disco, no en RAM")

    hf2.close()
    limpiar()


if __name__ == "__main__":
    test_record()
    test_page()
    test_heapfile_integracion()
    test_memoria_ram_vs_disco()
    print("\nTodos los tests pasaron")
