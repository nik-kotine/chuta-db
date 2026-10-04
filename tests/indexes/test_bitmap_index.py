"""
Pruebas del indice de bitmap: algebra de mascaras, persistencia del
archivo .idx, desbordes a paginas de desborde y mantenimiento de bits con
ALTAS y BAJAS de filas.
PYTHONPATH=. python3 tests/indexes/test_bitmap_index.py  (o run_all_tests.py)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from indexes.bitmap_index import Bitmap, BitmapIndex
from storage.rid import RID

ARCHIVO = "test_bitmap.idx"


def abrir(nombre=ARCHIVO):
    return BitmapIndex(nombre)


def test_algebra_de_mascaras():
    a = Bitmap()
    a.add(RID(1, 0)).add(RID(1, 5)).add(RID(3, 2))
    b = Bitmap()
    b.add(RID(1, 5)).add(RID(2, 1)).add(RID(3, 2))

    assert a.count() == 3 and b.count() == 3
    assert a.page_count() == 2, "solo hay 2 paginas del heap tocadas"
    assert a.pages() == [1, 3]

    # AND: la interseccion fila a fila
    assert sorted(a.intersect(b).rids()) == [RID(1, 5), RID(3, 2)]
    # OR: la union
    assert sorted(a.union(b).rids()) == [RID(1, 0), RID(1, 5), RID(2, 1), RID(3, 2)]
    # AND NOT
    assert sorted(a.difference(b).rids()) == [RID(1, 0)]
    # interseccion vacia
    assert not a.intersect(b.difference(a))
    # el operando mas chico es el que se recorre
    assert a.intersect(Bitmap()) == Bitmap()
    assert Bitmap().union(a) == a
    assert Bitmap().difference(a) == Bitmap()

    # los RIDs salen en orden de pagina y de slot, que es el orden en que
    # conviene leer el heap
    rids = list(a.rids())
    assert rids == sorted(rids), rids

    print("test_algebra_de_mascaras: OK")


def test_mascara_es_densa_pero_sparse():
    m = Bitmap()
    for page in range(1, 400):
        m.add(RID(page, page % 8))
    assert m.count() == 399
    assert m.page_count() == 399

    # de una tabla de 400 paginas x 510 slots, el bitmap entero ocupa una
    # fraccion: solo se guardan los tramos con algun bit en uno
    bytes_completos = 400 * 64
    bytes_sparse = len(m.encode())
    assert bytes_sparse < bytes_completos / 10, (bytes_sparse, bytes_completos)

    # ida y vuelta por disco
    assert Bitmap.decode(m.encode()) == m
    assert Bitmap.decode(m.encode()).count() == m.count()

    print("test_mascara_es_densa_pero_sparse: OK")


def test_mascara_vacia_y_limites():
    vacia = Bitmap()
    assert not vacia and vacia.count() == 0 and list(vacia.rids()) == []
    assert Bitmap.decode(vacia.encode()) == vacia

    m = Bitmap()
    m.add(RID(7, 511))
    assert RID(7, 511) in m and RID(7, 510) not in m
    try:
        m.add(RID(7, 512))
    except ValueError:
        pass
    else:
        raise AssertionError("un slot fuera de la pagina debe rechazarse")

    # encender dos veces el mismo RID no cuenta dos filas
    m.add(RID(7, 511))
    assert m.count() == 1
    m.discard(RID(7, 511))
    assert not m, "el tramo vacio debe desaparecer de la mascara"

    print("test_mascara_vacia_y_limites: OK")


def test_insert_busqueda_y_baja():
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()

    # 200 filas de 20 paginas del heap, 7 valores distintos
    for i in range(200):
        idx._insert_ref(i % 7, RID(1 + i // 10, i % 10))
    assert idx.key_count() == 7

    for valor in range(7):
        esperado = [i for i in range(200) if i % 7 == valor]
        mascara = idx.search(valor)
        assert mascara.count() == len(esperado), (valor, mascara.count(), len(esperado))
        assert RID(1 + esperado[0] // 10, esperado[0] % 10) in mascara

    # una clave ausente se resuelve en RAM: ni una pagina del indice leida
    antes = idx.pages_read
    assert idx.search(12345).count() == 0
    assert idx.pages_read == antes, "una clave ausente no deberia leer paginas"

    # el rango es la union de los valores dentro de la ventana
    assert idx.search_range(2, 4).count() == sum(
        1 for i in range(200) if 2 <= i % 7 <= 4
    )
    assert idx.search_range(3, 3).count() == idx.search(3).count()
    assert idx.search_range(100, 200).count() == 0
    assert idx.search_range(50, 10).count() == 0

    # rangos abiertos
    assert idx.search_range(5, None).count() == sum(1 for i in range(200) if i % 7 >= 5)

    # baja de un RID
    total = idx.search(3).count()
    assert idx.delete_ref(3, RID(1, 3)) is True
    assert idx.search(3).count() == total - 1
    # bajar de nuevo el mismo RID no cambia nada
    assert idx.delete_ref(3, RID(1, 3)) is False
    assert idx.search(3).count() == total - 1

    # cuando el bitmap queda vacio, la clave desaparece del indice
    for i in range(200):
        if i % 7 == 5:
            idx.delete_ref(5, RID(1 + i // 10, i % 10))
    assert 5 not in idx.keys(), idx.keys()
    assert idx.search(5).count() == 0

    idx.close()
    os.remove(ARCHIVO)
    print("test_insert_busqueda_y_baja: OK")


def test_rango_con_demasiados_valores_se_rechaza():
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()
    for v in range(100):
        idx._insert_ref(v, RID(1, v % 10))
    assert idx.search_range(0, 99, max_keys=10) is None, "no conviene unir 100 mascaras"
    # en 0..5 caen las claves 0, 10, 20, 30, 40 y 50 (una fila cada una)
    assert idx.search_range(0, 5, max_keys=10).count() == 6
    # el limite no corta un rango chico
    assert idx.search_range(0, 2, max_keys=1000).count() == 3
    idx.close()
    os.remove(ARCHIVO)
    print("test_rango_con_demasiados_valores_se_rechaza: OK")


def test_persistencia_entre_sesiones():
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()
    for v in range(30):
        idx._insert_ref(v, RID(1 + v, v % 5))
    esperado = {v: idx.search(v).count() for v in range(30)}
    assert idx.stats()["keys"] == 30
    idx.close()

    # reabrir debe devolver exactamente los mismos bitmaps
    idx = abrir()
    assert idx.key_count() == 30
    assert sorted(idx.keys()) == list(range(30))
    for v in range(30):
        assert idx.search(v).count() == esperado[v], v
    assert idx.search_range(10, 20).count() == sum(
        esperado[v] for v in range(10, 21)
    )
    idx.close()
    os.remove(ARCHIVO)
    print("test_persistencia_entre_sesiones: OK")


def test_desborde_a_paginas_extra():
    """Una clave presente en muchas paginas del heap no entra en una sola
    pagina: su bitmap se parte y sigue en una cadena de paginas."""
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()
    for page in range(1, 301):
        idx._insert_ref("muchos", RID(page, 0))
    for page in range(1, 301):
        idx._insert_ref("pocos", RID(page, 3))

    stats = idx.stats()
    assert stats["keys"] == 2 and stats["data_pages"] == 1, stats
    # el archivo creció: hay paginas de desborde
    assert stats["bytes"] > 4108, stats

    mascara = idx.search("muchos")
    assert mascara.count() == 300 and mascara.page_count() == 300
    # los dos conjuntos son disjuntos sobre las mismas paginas
    assert not mascara.intersect(idx.search("pocos"))
    # el desborde sobrevive al cierre
    idx.close()

    idx = abrir()
    assert idx.search("muchos").count() == 300
    assert idx.search("pocos").count() == 300
    idx.close()
    os.remove(ARCHIVO)
    print("test_desborde_a_paginas_extra: OK")


def test_baja_libera_y_reutiliza_el_desborde():
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()
    for page in range(1, 301):
        idx._insert_ref("a", RID(page, 0))
        idx._insert_ref("b", RID(page, 1))
    idx.close()

    # se borra 'a' fila por fila (cada baja reescribe su bitmap y encoge el
    # desborde); al final la clave se va y su cadena queda libre
    for page in range(1, 301):
        t = abrir()
        t.delete_ref("a", RID(page, 0))
        t.close()

    idx = abrir()
    assert "a" not in idx.keys(), idx.keys()
    assert idx.search("b").count() == 300, "la otra clave no se toco"
    tamano = idx.stats()["bytes"]

    # una clave nueva del mismo tamano debe entrar en el hueco liberado
    for page in range(1, 301):
        idx._insert_ref("c", RID(page, 2))
    assert idx.search("c").count() == 300 and idx.search("b").count() == 300
    assert idx.stats()["bytes"] == tamano, "el indice debe reutilizar las paginas libres"
    idx.close()

    idx = abrir()
    assert sorted(idx.keys()) == ["b", "c"], idx.keys()
    assert idx.search("b").count() == 300 and idx.search("c").count() == 300
    idx.close()
    os.remove(ARCHIVO)
    print("test_baja_libera_y_reutiliza_el_desborde: OK")


def test_baja_de_una_clave_en_el_medio_de_la_pagina():
    """Al sacar una entrada, las de atras se corren de slot: el indice tiene
    que seguir apuntando a la clave correcta."""
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()
    for v in range(200):
        idx._insert_ref(v, RID(1 + v, v % 4))
    assert idx.stats()["data_pages"] > 1, "con 200 claves deberia usar varias paginas"

    assert idx.delete_ref(7, RID(8, 3)) is True
    assert 7 not in idx.keys()
    assert idx.search(8).count() == 1, "las claves de alrededor deben seguir en su slot"

    for v in (0, 6, 8, 150, 199):
        assert idx.search(v).count() == 1, v
    idx.close()

    idx = abrir()
    assert 7 not in idx.keys()
    for v in (0, 6, 8, 150, 199):
        assert idx.search(v).count() == 1, v
    assert idx.search_range(0, 199).count() == 199
    idx.close()
    os.remove(ARCHIVO)
    print("test_baja_de_una_clave_en_el_medio_de_la_pagina: OK")


def test_baja_de_un_rid_inexistente():
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()
    idx._insert_ref(1, RID(1, 0))
    assert idx.delete_ref(1, RID(9, 9)) is False
    assert idx.delete_ref(2, RID(1, 0)) is False, "una clave ausente no se baja"
    assert idx.search(1).count() == 1
    assert idx.delete_ref(1, RID(1, 0)) is True
    assert idx.key_count() == 0
    idx.close()
    os.remove(ARCHIVO)
    print("test_baja_de_un_rid_inexistente: OK")


def test_varios_tipos_de_clave():
    if os.path.exists(ARCHIVO):
        os.remove(ARCHIVO)
    idx = abrir()
    casos = {
        7: RID(1, 0),
        -3: RID(1, 1),
        2.5: RID(1, 2),
        True: RID(1, 3),
        "hola": RID(2, 0),
        "mundo": RID(2, 1),
    }
    for clave, rid in casos.items():
        idx._insert_ref(clave, rid)
    idx._insert_ref("hola", RID(3, 0))
    idx.close()

    idx = abrir()
    for clave, rid in casos.items():
        assert RID(1, 0) in idx.search(clave) or rid in idx.search(clave), clave
    assert idx.search("hola").count() == 2, "las claves string se acumulan"
    assert idx.search("adios").count() == 0

    # el rango sobre strings anda
    assert idx.search_range("a", "z").count() == 3
    # un rango numerico solo mira las claves numericas: las de texto no son
    # comparables y quedan afuera en vez de hacer fallar todo el rango
    assert idx.search_range(0, 10).count() == 3, "7, 2.5 y True"
    assert idx.search_range(3, 10).count() == 1, "solo 7"
    # extremos abiertos, tambien sobre strings
    assert idx.search_range(None, "hz").count() == 2, "las dos filas de hola"
    assert idx.search_range("hola", None).count() == 3, "hola y mundo"
    idx.close()
    os.remove(ARCHIVO)
    print("test_varios_tipos_de_clave: OK")


if __name__ == "__main__":
    print("=== PROBANDO EL INDICE DE BITMAP ===")
    test_algebra_de_mascaras()
    test_mascara_es_densa_pero_sparse()
    test_mascara_vacia_y_limites()
    test_insert_busqueda_y_baja()
    test_rango_con_demasiados_valores_se_rechaza()
    test_persistencia_entre_sesiones()
    test_desborde_a_paginas_extra()
    test_baja_libera_y_reutiliza_el_desborde()
    test_baja_de_una_clave_en_el_medio_de_la_pagina()
    test_baja_de_un_rid_inexistente()
    test_varios_tipos_de_clave()
    print("Todas las pruebas del indice de bitmap pasaron")
