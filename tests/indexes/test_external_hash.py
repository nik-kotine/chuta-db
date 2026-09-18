"""
Pruebas del hash externo (Grace Hash Join / hash aggregate) usado para
el JOIN y el GROUP BY.

    PYTHONPATH=. python3 tests/indexes/test_external_hash.py

Cubren: el particionado (reparto estable por hash de la clave), el
Grace Hash Join (incluidos los duplicados de ambos lados), el GROUP BY
(duplicados que acumulan), la limpieza de los archivos .part al
terminar (aun cortando la iteracion a la mitad) y que el hash FNV-1a
es estable entre procesos (no depende de PYTHONHASHSEED).
"""

import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile

from indexes.external_hash import ExternalHasher, Partitions, bucket_of

# Raiz del repo: los subprocesos la necesitan en PYTHONPATH
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def items_brutos(n: int, max_clave: int = 100):
    """Genera n pares (clave, value_bytes) con claves al azar (pueden
    repetirse). value_bytes codifica la clave con struct, asi los tests
    verifican que cada item llego intacto a donde corresponde."""
    claves = [random.randint(0, max_clave) for _ in range(n)]
    return [(k, struct.pack(">i", k)) for k in claves]


def datos(items):
    """Recupera los ints guardados en los value_bytes de los items."""
    return [struct.unpack(">i", v)[0] for _, v in items]


def _sumar(acc, value_bytes):
    """Acumulador de prueba para group_by: suma el valor al acumulador
    y devuelve el acumulador (el contrato que exige group_by)."""
    acc["s"] = acc.get("s", 0) + struct.unpack(">i", value_bytes)[0]
    return acc


def test_bucket_of_distribuye_en_rango():
    for nb in (1, 2, 8, 16):
        for key in (0, 1, -5, 3.14, "abc", True):
            b = bucket_of(key, nb)
            assert 0 <= b < nb, f"bucket {b} fuera de rango para {nb} buckets"
    # determinismo: la misma clave siempre cae en el mismo bucket
    assert bucket_of(42, 16) == bucket_of(42, 16)
    assert bucket_of("chuta", 16) == bucket_of("chuta", 16)
    print("OK: bucket_of devuelve buckets en rango y es determinista")


def test_bucket_of_bool_diferenciado_de_int():
    # Python trata True/False como ints; el hash debe distinguirlos de 0/1
    assert bucket_of(True, 256) != bucket_of(1, 256)
    assert bucket_of(False, 256) != bucket_of(0, 256)
    print("OK: bucket_of no confunde bool con int")


def test_bucket_of_rechaza_buckets_invalidos():
    for nb in (0, -3):
        try:
            bucket_of(1, nb)
            assert False, f"num_buckets={nb} debia rechazarse"
        except ValueError:
            pass
    print("OK: num_buckets < 1 se rechaza con ValueError")


def test_partition_reparte_cada_item_a_su_bucket():
    random.seed(21)
    items = items_brutos(200)
    hasher = ExternalHasher(num_buckets=8)
    particiones = hasher.partition(items)

    for b in range(8):
        for clave, value_bytes in particiones.bucket(b):
            # un item solo puede estar en el bucket que le toca por hash
            assert bucket_of(clave, 8) == b
            assert struct.unpack(">i", value_bytes)[0] == clave

    particiones.cleanup()
    hasher.cleanup()
    print("OK: particionar reparte cada (clave, value_bytes) a su bucket")


def test_partition_conserve_el_multiset():
    random.seed(22)
    items = items_brutos(300)
    hasher = ExternalHasher(num_buckets=16)
    particiones = hasher.partition(items)

    vueltos = []
    for b in range(particiones.num_buckets):
        vueltos.extend(datos(particiones.bucket(b)))

    particiones.cleanup()
    hasher.cleanup()

    assert sorted(vueltos) == sorted(datos(items))
    print("OK: ningun item se pierde al particionar (el multiset se conserva)")


def test_partition_rechaza_bucket_fuera_de_rango():
    random.seed(23)
    hasher = ExternalHasher(num_buckets=4)
    try:
        particiones = hasher.partition(items_brutos(10))
        try:
            list(particiones.bucket(4))
            assert False, "bucket (4) debia estar fuera de rango"
        except IndexError:
            pass
        particiones.cleanup()
    finally:
        hasher.cleanup()
    print("OK: pedir una particion fuera de rango se rechaza con IndexError")


def test_hash_join_equi_join_con_duplicados():
    # ambos lados con duplicados: 2 valores a la izquierda, 3 a la
    # derecha => 6 combinaciones para la clave compartida
    izquierda = [(1, struct.pack(">i", v)) for v in (10, 20)]
    derecha = [(1, struct.pack(">i", v)) for v in (100, 200, 300)]
    derecha.append((2, struct.pack(">i", 999)))  # sin match

    hasher = ExternalHasher(num_buckets=4)
    pares = list(hasher.hash_join(izquierda, derecha))
    hasher.cleanup()

    # todas las parejas (clave=1): izquierda x derecha
    assert len(pares) == 2 * 3
    for clave, izq, der in pares:
        assert clave == 1
        assert struct.unpack(">i", izq)[0] in (10, 20)
        assert struct.unpack(">i", der)[0] in (100, 200, 300)
    # las 6 combinaciones posibles estan presentes
    combinaciones = sorted(
        (struct.unpack(">i", izq)[0], struct.unpack(">i", der)[0])
        for _, izq, der in pares
    )
    assert len(set(combinaciones)) == 6
    print("OK: hash_join une todos los pares que matchean, con duplicados")


def test_hash_join_distintos_tipos_de_clave():
    izquierda = [("a", b"L1"), (1, b"L2"), (True, b"L3")]
    derecha = [("a", b"R1"), (1, b"R2"), (2.5, b"R3")]

    hasher = ExternalHasher(num_buckets=4)
    pares = [(c, li, dr) for c, li, dr in hasher.hash_join(izquierda, derecha)]
    hasher.cleanup()

    assert ("a", b"L1", b"R1") in pares
    assert (1, b"L2", b"R2") in pares
    # True != 1 y 2.5 no matchea con nada
    assert len(pares) == 2
    print("OK: hash_join une solo claves iguales, sin colisiones entre tipos")


def test_hash_join_lado_vacio():
    hasher = ExternalHasher(num_buckets=4)
    assert list(hasher.hash_join([], [])) == []
    assert list(hasher.hash_join([(1, b"a")], [])) == []
    assert list(hasher.hash_join([], [(1, b"a")])) == []
    hasher.cleanup()
    print("OK: un lado vacio produce un join vacio")


def test_group_by_acumula_grupos_en_uno():
    # 3 grupos (7, 3, 1); el acumulador suma los value_bytes
    random.seed(24)
    bruto = [(7, 2), (3, 5), (7, 1), (1, 9), (3, 3), (3, 4)]
    items = [(k, struct.pack(">i", v)) for k, v in bruto]

    hasher = ExternalHasher(num_buckets=8)
    resultado = {}
    for clave, acc in hasher.group_by(items, dict, _sumar):
        resultado[clave] = acc["s"]
    hasher.cleanup()

    assert resultado == {7: 3, 3: 12, 1: 9}
    print("OK: group_by agrupa por clave y acumula cada grupo en una particion")


def test_group_by_concentra_grupos_duplicados_aun_en_distintos_items():
    # las apariciones de un grupo deben caer en la misma particion y
    # sumarse todas; con 25 buckets y claves puras de 1 dígito, se
    # barajan las posiciones y cada grupo preserva su acumulado
    random.seed(25)
    bruto = []
    for k in range(10):
        bruto.extend((k, random.randint(1, 50)) for _ in range(40))
    random.shuffle(bruto)

    esperado = {}
    for k, v in bruto:
        esperado[k] = esperado.get(k, 0) + v

    items = [(k, struct.pack(">i", v)) for k, v in bruto]
    hasher = ExternalHasher(num_buckets=25)
    resultado = {}
    for clave, acc in hasher.group_by(items, dict, _sumar):
        resultado[clave] = acc["s"]
    hasher.cleanup()

    assert resultado == esperado
    print("OK: group_by suma todas las apariciones de cada grupo")


def test_group_by_sin_grupos():
    hasher = ExternalHasher(num_buckets=4)
    assert list(hasher.group_by([], dict, lambda acc, vb: acc)) == []
    hasher.cleanup()
    print("OK: entrada vacia produce cero grupos")


def test_no_quedan_archivos_en_disco():
    run_dir = tempfile.mkdtemp(prefix="test_external_hash_")
    try:
        random.seed(26)
        hasher = ExternalHasher(num_buckets=8, run_dir=run_dir)
        particiones = hasher.partition(items_brutos(100))
        particiones.cleanup()
        hasher.cleanup()

        sobrantes = [f for f in os.listdir(run_dir) if f.endswith(".part")]
        assert sobrantes == [], f"quedaron particiones sin borrar: {sobrantes}"

        # cleanup es idempotente
        particiones.cleanup()
    finally:
        shutil.rmtree(run_dir)
    print("OK: tras terminar, no quedan archivos .part en disco")


def test_corte_anticipado_no_deja_particiones():
    run_dir = tempfile.mkdtemp(prefix="test_external_hash_")
    try:
        random.seed(27)
        items = [(k, struct.pack(">i", k)) for k in range(5)] * 20

        # un consumidor (ej. un LIMIT) corta la iteracion a la mitad
        hasher = ExternalHasher(num_buckets=8, run_dir=run_dir)
        generador = hasher.hash_join(items, items)
        primeros = [next(generador) for _ in range(5)]
        assert len(primeros) == 5
        generador.close()
        hasher.cleanup()

        sobrantes = [f for f in os.listdir(run_dir) if f.endswith(".part")]
        assert sobrantes == [], f"el corte anticipado dejo particiones sueltas: {sobrantes}"
    finally:
        shutil.rmtree(run_dir)
    print("OK: cortar la iteracion a la mitad no deja particiones sueltas")


def test_hash_estable_entre_procesos():
    # FNV-1a no depende de PYTHONHASHSEED: un subproceso con seed bien
    # distinta debe repartir la clave al mismo bucket que el proceso padre
    codigo = (
        "import sys, ast; sys.path.insert(0, sys.argv[1]);"
        "from indexes.external_hash import bucket_of;"
        "print(bucket_of(ast.literal_eval(sys.argv[2]), int(sys.argv[3])))"
    )
    for clave, nb in ((7, 16), (12345, 16), (3.14, 16), ("abc", 16), (True, 16)):
        referencia = bucket_of(clave, nb)
        for seed in ("1", "random", "999999"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            salida = subprocess.run(
                [sys.executable, "-c", codigo, ROOT, repr(clave), str(nb)],
                capture_output=True, text=True, env=env, check=True,
            )
            assert int(salida.stdout.strip()) == referencia, (
                f"el bucket de {clave} cambio con PYTHONHASHSEED={seed}"
            )
    print("OK: el hash FNV-1a reparte igual entre procesos (seed estable)")


def test_num_buckets_invalido():
    try:
        ExternalHasher(num_buckets=0)
        assert False, "num_buckets=0 debia rechazarse"
    except ValueError:
        pass
    print("OK: num_buckets < 1 se rechaza con ValueError")


tests = [
    test_bucket_of_distribuye_en_rango,
    test_bucket_of_bool_diferenciado_de_int,
    test_bucket_of_rechaza_buckets_invalidos,
    test_partition_reparte_cada_item_a_su_bucket,
    test_partition_conserve_el_multiset,
    test_partition_rechaza_bucket_fuera_de_rango,
    test_hash_join_equi_join_con_duplicados,
    test_hash_join_distintos_tipos_de_clave,
    test_hash_join_lado_vacio,
    test_group_by_acumula_grupos_en_uno,
    test_group_by_concentra_grupos_duplicados_aun_en_distintos_items,
    test_group_by_sin_grupos,
    test_no_quedan_archivos_en_disco,
    test_corte_anticipado_no_deja_particiones,
    test_hash_estable_entre_procesos,
    test_num_buckets_invalido,
]

for test in tests:
    print(f"Corriendo {test.__name__}...", end=" ")
    test()
    print("OK")

print(f"\n{len(tests)} tests pasaron.")