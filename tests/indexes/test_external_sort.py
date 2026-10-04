"""
Pruebas del sorter externo (k-way merge) usado para el ORDER BY.

    PYTHONPATH=. python3 tests/indexes/test_external_sort.py

Cubren: el camino en memoria (input chico), el camino externo (varios
runs a disco + merge), claves de distintos tipos, el encode/decode
orden-preservante, y que nunca quedan runs sueltos en disco aun si el
consumidor corta a la mitad.
"""

import os
import random
import shutil
import struct
import tempfile

from indexes.external_sort import ExternalSorter, decode_key, encode_key

def items_desordenados(n: int, rango: int = 1_000_000):
    """Genera n pares (clave, value_bytes) con claves unicas al azar.

    El value_bytes codifica la misma clave con struct, asi los tests
    pueden verificar que cada clave llego a su lugar correcto.
    """
    claves = random.sample(range(rango), n)
    return [(k, struct.pack(">i", k)) for k in claves]


def datos(items):
    """Recupera los ints guardados en los value_bytes de los items."""
    return [struct.unpack(">i", v)[0] for _, v in items]


def test_ordena_en_memoria():
    random.seed(7)
    items = items_desordenados(50)

    sorter = ExternalSorter(budget=1000)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    # todo cabe en el budget: nunca se toco disco
    assert sorter.run_count == 0
    assert datos(resultado) == sorted(datos(items))
    print("OK: input chico se ordena en memoria, sin crear runs")


def test_descendente_en_memoria():
    random.seed(8)
    items = items_desordenados(30)

    sorter = ExternalSorter(budget=1000, reverse=True)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    assert datos(resultado) == sorted(datos(items), reverse=True)
    print("OK: reverse=True retorna el orden descendente")


def test_k_way_merge_fuerza_varios_runs():
    random.seed(9)
    n = 200
    items = items_desordenados(n)

    # budget=16: 200 items => 13 runs a disco, el plan k-way merge se usa
    # y sin embargo los runs se limpian al terminar.
    sorter = ExternalSorter(budget=16)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    assert sorter.run_count >= 1, "con budget chico el sorter debe escribir runs"
    assert datos(resultado) == sorted(datos(items))

    # El multiset no se toco: cada clave llego exactamente una vez
    assert sorted(resultado) == sorted(items)
    print(f"OK: {n} items con budget=16 => {sorter.run_count} runs, merge correcto")


def test_orden_descendente_con_runs():
    random.seed(10)
    items = items_desordenados(150)

    sorter = ExternalSorter(budget=8, reverse=True)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    assert sorter.run_count >= 1
    assert datos(resultado) == sorted(datos(items), reverse=True)
    print(f"OK: DESC con {sorter.run_count} runs a disco")


def test_merge_grande():
    random.seed(11)
    n = 5000
    items = items_desordenados(n, rango=1_000_000_000)

    sorter = ExternalSorter(budget=64)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    assert sorter.run_count >= 1
    assert datos(resultado) == sorted(datos(items))
    assert sorted(resultado) == sorted(items)
    print(f"OK: {n} items, {sorter.run_count} runs, todo ordenado sin errores")


def test_claves_string():
    random.seed(12)
    palabras = [
        "pera", "manzana", "uva", "banana", "kiwi", "frutilla", "naranja",
        "durazno", "ciruela", "sandia", "anana", "membrillo"
    ]
    random.shuffle(palabras)
    items = [(p, struct.pack(">i", i)) for i, p in enumerate(palabras)]

    sorter = ExternalSorter(budget=3)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    assert [k for k, _ in resultado] == sorted(palabras)
    print("OK: claves de tipo str se ordenan bien con runs")


def test_claves_float():
    random.seed(13)
    values = [-100.5, -3.14, -0.5, 0.0, 0.25, 1.0, 42.42, 1e10]
    random.shuffle(values)
    items = [(v, struct.pack(">d", v)) for v in values]

    sorter = ExternalSorter(budget=2)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    assert [k for k, _ in resultado] == sorted(values)
    print("OK: claves de tipo float (incluidos negativos) se ordenan bien")


def test_claves_duplicadas():
    random.seed(14)
    values = [7, 3, 3, 7, 1, 3, 1, 3, 9, 9]
    items = [(v, struct.pack(">i", i)) for i, v in enumerate(values)]

    sorter = ExternalSorter(budget=2)
    resultado = list(sorter.sort(items))
    sorter.cleanup()

    claves_ordenadas = [k for k, _ in resultado]
    assert claves_ordenadas == sorted(values)
    # ninguna clave se perdio ni se duplico de mas
    assert len(claves_ordenadas) == len(values)
    print("OK: claves duplicadas conservan la cantidad de apariciones")


def test_vacio():
    sorter = ExternalSorter(budget=4)
    assert list(sorter.sort([])) == []
    sorter.cleanup()
    print("OK: entrada vacia produce salida vacia")


def test_encode_key_preserva_orden():
    flotantes = [-100.5, -1.0, -0.0, 0.0, 0.5, 3.14159, 1e10, float("inf")]
    codificados = [encode_key(f) for f in flotantes]
    assert codificados == sorted(codificados)
    for f in flotantes:
        assert decode_key(encode_key(f)) == f

    enteros = [-2 ** 60, -10, -1, 0, 1, 10, 2 ** 60]
    codificados = [encode_key(i) for i in enteros]
    assert codificados == sorted(codificados)
    for i in enteros:
        assert decode_key(encode_key(i)) == i

    cadenas = ["", "a", "ab", "b", "barba", "manzana", "ño"]
    codificados = [encode_key(s) for s in cadenas]
    assert codificados == sorted(codificados)
    assert [decode_key(c) for c in codificados] == cadenas

    assert encode_key(False) < encode_key(True)
    assert decode_key(encode_key(True)) is True
    assert decode_key(encode_key(False)) is False

    print("OK: encode_key es orden-preservante para int, float, str y bool")


def test_encode_key_tipo_no_soportado():
    try:
        encode_key(None)
        assert False, "None debia ser rechazado como clave"
    except TypeError:
        pass
    print("OK: una clave de tipo no soportado se rechaza con TypeError")


def test_no_quedan_runs_en_disco():
    run_dir = tempfile.mkdtemp(prefix="test_external_sort_")
    try:
        sorter = ExternalSorter(budget=3, run_dir=run_dir)
        resultado = list(sorter.sort(items_desordenados(50)))
        sorter.cleanup()

        assert sorter.run_count >= 1, "el camino externo debio usarse"
        assert datos(resultado) == sorted(datos(resultado))  # ordenado

        sobrantes = [f for f in os.listdir(run_dir) if f.endswith(".run")]
        assert sobrantes == [], f"quedaron runs sin borrar: {sobrantes}"
    finally:
        shutil.rmtree(run_dir)
    print("OK: tras terminar, no quedan archivos .run en disco")


def test_corte_anticipado_limpia_runs():
    run_dir = tempfile.mkdtemp(prefix="test_external_sort_")
    try:
        sorter = ExternalSorter(budget=3, run_dir=run_dir)
        generador = sorter.sort(items_desordenados(100))

        # el consumidor (ej. un LIMIT) corta apenas si le alcanzo
        primeros = [next(generador) for _ in range(5)]
        assert len(primeros) == 5
        generador.close()
        sorter.cleanup()

        sobrantes = [f for f in os.listdir(run_dir) if f.endswith(".run")]
        assert sobrantes == [], f"el corte anticipado dejo runs sueltos: {sobrantes}"
    finally:
        shutil.rmtree(run_dir)
    print("OK: cortar la iteracion a la mitad no deja runs sueltos")


def test_budget_invalido():
    try:
        ExternalSorter(budget=0)
        assert False, "budget=0 debia rechazarse"
    except ValueError:
        pass
    print("OK: budget < 1 se rechaza con ValueError")


def test_error_a_mitad_no_deja_runs():
    """Si la entrada se interrumpe con una clave invalida despues de
    escribir varios runs, los runs ya creados no quedan sueltos."""
    run_dir = tempfile.mkdtemp(prefix="test_external_sort_")
    try:
        sorter = ExternalSorter(budget=2, run_dir=run_dir)
        items = [(k, struct.pack(">i", k)) for k in range(10)] + [(None, b"xx")]

        try:
            list(sorter.sort(items))
            assert False, "la clave None debia reventar encode_key"
        except TypeError:
            pass

        sorter.cleanup()
        sobrantes = [f for f in os.listdir(run_dir) if f.endswith(".run")]
        assert sobrantes == [], f"un error a mitad dejo runs sueltos: {sobrantes}"
    finally:
        shutil.rmtree(run_dir)
    print("OK: una clave invalida a mitad de la entrada no deja runs sueltos")


tests = [
    test_ordena_en_memoria,
    test_descendente_en_memoria,
    test_k_way_merge_fuerza_varios_runs,
    test_orden_descendente_con_runs,
    test_merge_grande,
    test_claves_string,
    test_claves_float,
    test_claves_duplicadas,
    test_vacio,
    test_encode_key_preserva_orden,
    test_encode_key_tipo_no_soportado,
    test_no_quedan_runs_en_disco,
    test_corte_anticipado_limpia_runs,
    test_budget_invalido,
    test_error_a_mitad_no_deja_runs,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")