import os
import random
import struct
import tempfile
from collections import Counter

from storage.file_manager import FileManager
from storage.buffer_manager import BufferManager
from indexes import extendible_hash as EHI
from indexes.extendible_hash import HashIndex


PAGE_SIZE = 8192
HEADER_SIZE = 16
BUFFER_FRAMES = 50

KEY_FORMAT = ">i"
KEY_VARIABLE = False

# Para los tests normales.
MAX_BUCKET_SIZE = 3



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_index(
    depth=1,
    max_bucket_size=MAX_BUCKET_SIZE,
    buffer_frames=BUFFER_FRAMES,
):
    """
    Crea un índice temporal.

    Se usa depth pequeño deliberadamente para poder provocar splits
    con pocos registros.
    """
    fd, filename = tempfile.mkstemp()
    os.close(fd)

    # FileManager espera una página 0 inicial.
    with open(filename, "wb") as f:
        f.write(b"\x00" * PAGE_SIZE)

    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, buffer_frames)

    index = HashIndex(
        table_name="test_table",
        column_name="id",
        key_format=KEY_FORMAT,
        key_variable=KEY_VARIABLE,
        buffer_manager=bm,
        max_bucket_size=max_bucket_size,
        depth=depth,
        seed=0,
    )

    return filename, fm, bm, index


def flush_and_close(filename, fm, bm, remove=True):
    """
    Cierra FileManager/BufferManager y opcionalmente elimina el archivo.

    En la arquitectura actual el buffer pool es global: cada página se
    identifica por (file_manager, phys_page_id), así que se persisten y
    cierran las páginas de ESTE archivo con bm.close(fm).
    """
    bm.flush_file(fm)
    bm.close(fm)

    if remove and os.path.exists(filename):
        os.remove(filename)


def reopen_index(filename):
    """
    Reabre el índice desde disco.

    Importante:
    El HashIndex mostrado por el usuario actualmente NO tiene un
    constructor de recuperación desde disco. Esta función existe como
    placeholder para que el test de persistencia sea fácil de adaptar
    si implementas esa recuperación.
    """
    fm = FileManager(filename, PAGE_SIZE, HEADER_SIZE)
    bm = BufferManager(fm, BUFFER_FRAMES)

    # Si tu implementación dispone de un constructor/load específico,
    # reemplazar esta parte.
    raise NotImplementedError(
        "HashIndex actualmente no tiene un constructor de recuperación "
        "desde disco."
    )


def index_keys(index):
    """
    Devuelve todos los KVs accesibles desde el directorio.

    Se deduplican por (page, slot) para detectar accidentalmente registros
    que aparezcan referenciados por más de una entrada del directorio.
    """
    result = []

    for directory_index in range(index.max_capacity):
        bucket_page = index._locate_bucket(directory_index)

        if bucket_page is None:
            continue

        seen_pages = set()

        while bucket_page != -1:
            if bucket_page in seen_pages:
                raise AssertionError(
                    f"Overflow cycle detected at page {bucket_page}"
                )

            seen_pages.add(bucket_page)

            bucket = index._load_bucket(bucket_page)

            try:
                for slot_id in range(bucket.size):
                    kv = bucket.get_kv_by_slot_id(slot_id)

                    if not kv.deleted:
                        result.append(
                            (
                                kv.key,
                                kv.rid,
                                bucket_page,
                                slot_id,
                            )
                        )

                bucket_page = bucket.next_bucket_page

            finally:
                index.buffer_manager.unpin_page(
                    bucket_page
                    if bucket_page in seen_pages
                    else next(iter(seen_pages)),
                    index.file_manager,
                )

                # El código anterior no permite conservar fácilmente
                # bucket_page después del unpin; por eso esta función se
                # implementa de forma más explícita abajo.
                break

    # La implementación anterior se evita en la práctica mediante
    # all_accessible_kvs(), que es la función usada por los tests.
    return result


def all_accessible_kvs(index):
    """
    Recorre cada cadena física de buckets una sola vez.

    Devuelve:
        [(key, rid, physical_page_id, slot_id), ...]
    """
    result = []
    seen_bucket_pages = set()

    for directory_index in range(index.max_capacity):
        first_page = index._locate_bucket(directory_index)

        if first_page is None:
            continue

        bucket_page = first_page

        while bucket_page != -1:
            if bucket_page in seen_bucket_pages:
                break

            seen_bucket_pages.add(bucket_page)

            current_page = bucket_page
            bucket = index._load_bucket(current_page)

            try:
                next_page = bucket.next_bucket_page

                for slot_id in range(bucket.size):
                    kv = bucket.get_kv_by_slot_id(slot_id)

                    if not kv.deleted:
                        result.append(
                            (
                                kv.key,
                                kv.rid,
                                current_page,
                                slot_id,
                            )
                        )

            finally:
                index.buffer_manager.unpin_page(current_page, index.file_manager)

            bucket_page = next_page
    return result




def accessible_key_counter(index):
    return Counter(
        key
        for key, rid, page_id, slot_id
        in all_accessible_kvs(index)
    )


def expected_counter(records):
    return Counter(key for key, rid in records)


def search_keys(index, key):
    return [kv.key for kv in index.search(key)]


def search_rids(index, key):
    return [kv.rid for kv in index.search(key)]


def directory_snapshot(index):
    """
    Devuelve:

        directory index ->
            (first physical page,
             last physical page,
             local depth)

    para inspeccionar el directorio.
    """
    result = {}

    for i in range(index.max_capacity):
        first = index._locate_bucket(i)
        last = index._locate_last_bucket(i)

        if first is None:
            result[i] = None
            continue

        bucket = index._load_bucket(first)

        try:
            local_depth = bucket.local_depth
        finally:
            index.buffer_manager.unpin_page(first, index.file_manager)

        result[i] = (first, last, local_depth)

    return result


def assert_directory_consistency(index):
    """
    Comprueba los invariantes fundamentales del extendible hashing.

    Cada entrada del directorio se valida individualmente, pero cada
    cadena física de buckets se recorre una sola vez.
    """

    global_depth = index.depth

    assert global_depth >= 0
    assert global_depth <= EHI.MAX_DEPTH

    # first_page ->
    #     (local_depth, expected_pattern, last_page)
    primary_info = {}

    for directory_index in range(index.max_capacity):
        first_page = index._locate_bucket(directory_index)
        last_page = index._locate_last_bucket(directory_index)

        assert first_page is not None, (
            f"Directory entry {directory_index} has no bucket"
        )

        assert last_page is not None, (
            f"Directory entry {directory_index} has no last bucket"
        )

        bucket = index._load_bucket(first_page)

        try:
            local_depth = bucket.local_depth

            assert 0 <= local_depth <= global_depth

            if local_depth == 0:
                mask = 0
            else:
                mask = (1 << local_depth) - 1

            pattern = directory_index & mask

        finally:
            index.buffer_manager.unpin_page(first_page, index.file_manager)

        # Si ya vimos este bucket primario, debe representar
        # exactamente el mismo patrón.
        previous = primary_info.get(first_page)

        if previous is None:
            primary_info[first_page] = (
                local_depth,
                pattern,
                last_page,
            )
        else:
            previous_depth, previous_pattern, previous_last = previous

            assert previous_depth == local_depth
            assert previous_pattern == pattern
            assert previous_last == last_page

    # ---------------------------------------------------------------
    # Ahora recorremos cada cadena física UNA sola vez.
    # ---------------------------------------------------------------

    for first_page, (
        local_depth,
        expected_pattern,
        expected_last_page,
    ) in primary_info.items():

        current_page = first_page
        seen = set()

        while current_page != -1:
            assert current_page not in seen, (
                f"Overflow cycle detected starting at page "
                f"{first_page}"
            )

            seen.add(current_page)

            bucket = index._load_bucket(current_page)

            try:
                next_page = bucket.next_bucket_page

                # Todos los buckets de la cadena deben mantener
                # la misma local depth.
                assert bucket.local_depth == local_depth

            finally:
                index.buffer_manager.unpin_page(current_page, index.file_manager)

            if next_page == -1:
                assert current_page == expected_last_page

            current_page = next_page


def assert_every_key_searchable(index):
    """
    Todo KV no borrado físicamente accesible mediante el directorio debe
    poder encontrarse mediante search().
    """
    kvs = all_accessible_kvs(index)

    expected = Counter(key for key, rid, page, slot in kvs)

    actual = Counter()

    for key in expected:
        result = index.search(key)

        for kv in result:
            assert not kv.deleted
            assert kv.key == key
            actual[key] += 1

    assert actual == expected


def find_keys_for_bucket(index, bucket_number, count):
    """
    Encuentra 'count' enteros cuyo hash apunta al bucket indicado.

    Útil para provocar splits controladamente.
    """
    keys = []
    candidate = 0

    while len(keys) < count:
        h = EHI.hash_key(candidate, KEY_FORMAT, 0)
        bucket = h % index.max_capacity

        if bucket == bucket_number:
            keys.append(candidate)

        candidate += 1

        # Protección ante un hash inesperadamente problemático.
        assert candidate < 10_000_000

    return keys


def find_colliding_keys(depth, count, seed=0):
    """
    Encuentra 'count' claves que colisionan en un directorio de profundidad
    dada.
    """
    mask = (1 << depth) - 1

    groups = {}
    candidate = 0

    while True:
        h = EHI.hash_key(candidate, KEY_FORMAT, seed)
        bucket = h & mask

        groups.setdefault(bucket, []).append(candidate)

        if len(groups[bucket]) >= count:
            return groups[bucket][:count]

        candidate += 1

        assert candidate < 10_000_000


def assert_bucket_records_match_hash(index):
    """
    Comprueba que cada registro de cada bucket físico pertenece al
    patrón de hash correspondiente.

    Cada página física se visita una sola vez, aunque múltiples
    entradas del directorio apunten al mismo bucket.
    """
    seen_bucket_pages = set()

    for directory_index in range(index.max_capacity):
        first_page = index._locate_bucket(directory_index)

        if first_page is None:
            continue

        # Primero determinamos el patrón que representa este bucket.
        bucket = index._load_bucket(first_page)

        try:
            local_depth = bucket.local_depth

            assert 0 <= local_depth <= index.depth

            if local_depth == 0:
                mask = 0
            else:
                mask = (1 << local_depth) - 1

            expected_pattern = directory_index & mask

        finally:
            index.buffer_manager.unpin_page(first_page, index.file_manager)

        # Si ya verificamos físicamente esta cadena, no la recorremos
        # otra vez aunque otra entrada del directorio apunte a ella.
        if first_page in seen_bucket_pages:
            continue

        bucket_page = first_page
        chain_seen = set()

        while bucket_page != -1:
            assert bucket_page not in chain_seen
            chain_seen.add(bucket_page)
            seen_bucket_pages.add(bucket_page)

            bucket = index._load_bucket(bucket_page)

            try:
                for slot_id in range(bucket.size):
                    kv = bucket.get_kv_by_slot_id(slot_id)

                    if kv.deleted:
                        continue

                    h = EHI.hash_key(
                        kv.key,
                        KEY_FORMAT,
                        index.seed,
                    )

                    assert (h & mask) == expected_pattern, (
                        f"Key {kv.key} is in wrong bucket. "
                        f"directory={directory_index}, "
                        f"page={bucket_page}, "
                        f"local_depth={local_depth}, "
                        f"hash={h}, "
                        f"expected_pattern={expected_pattern}"
                    )

                next_page = bucket.next_bucket_page

            finally:
                index.buffer_manager.unpin_page(bucket_page, index.file_manager)

            bucket_page = next_page





# ---------------------------------------------------------------------------
# Basic tests
# ---------------------------------------------------------------------------


def test_initialization():
    """
    El índice recién creado debe tener todos los buckets iniciales.

    Este test está diseñado para detectar directamente el problema:

        self.bucket_count = 0

    en el constructor.
    """
    filename, fm, bm, index = create_index(depth=2)

    try:
        assert index.depth == 2
        assert index.max_capacity == 4

        assert index.bucket_count == index.max_capacity, (
            "El índice debería crear un bucket primario por entrada "
            "inicial del directorio."
        )

        for i in range(index.max_capacity):
            page = index._locate_bucket(i)

            assert page is not None
            assert page != 0, (
                f"Directory[{i}] apunta a la página 0; "
                "probablemente no se crearon los buckets iniciales."
            )

        assert_directory_consistency(index)

    finally:
        flush_and_close(filename, fm, bm)


def test_initial_bucket_state():
    filename, fm, bm, index = create_index(depth=2)

    try:
        assert index.bucket_count == 4

        pages = set()

        for i in range(index.max_capacity):
            page = index._locate_bucket(i)

            assert page not in pages
            pages.add(page)

            bucket = index._load_bucket(page)

            try:
                assert bucket.size == 0
                assert bucket.local_depth == index.depth
                assert bucket.next_bucket_page == -1
                assert bucket.offset == PAGE_SIZE

            finally:
                index.buffer_manager.unpin_page(page, index.file_manager)

        assert len(pages) == 4

    finally:
        flush_and_close(filename, fm, bm)


def test_insert_single():
    filename, fm, bm, index = create_index(depth=1)

    try:
        rid = (123, 456)

        index.insert(10, rid)

        result = index.search(10)

        assert len(result) == 1
        assert result[0].key == 10
        assert result[0].rid == rid
        assert result[0].deleted is False

        assert index.search(999) == []

        assert accessible_key_counter(index) == Counter({10: 1})

    finally:
        flush_and_close(filename, fm, bm)


def test_insert_multiple():
    filename, fm, bm, index = create_index(depth=2)

    try:
        records = [
            (10, (1, 0)),
            (20, (1, 1)),
            (30, (1, 2)),
            (40, (1, 3)),
            (50, (1, 4)),
        ]

        for key, rid in records:
            index.insert(key, rid)

        assert accessible_key_counter(index) == expected_counter(records)

        for key, rid in records:
            result = index.search(key)

            assert len(result) == 1
            assert result[0].rid == rid

        assert_every_key_searchable(index)

    finally:
        flush_and_close(filename, fm, bm)


def test_duplicates():
    filename, fm, bm, index = create_index(depth=2)

    try:
        records = [
            (10, (1, 0)),
            (10, (1, 1)),
            (10, (1, 2)),
            (20, (1, 3)),
        ]

        for key, rid in records:
            index.insert(key, rid)

        result = index.search(10)

        assert len(result) == 3
        assert Counter(kv.rid for kv in result) == Counter(
            rid for key, rid in records if key == 10
        )

        assert accessible_key_counter(index) == expected_counter(records)

    finally:
        flush_and_close(filename, fm, bm)


def test_delete():
    filename, fm, bm, index = create_index(depth=2)

    try:
        records = [
            (10, (1, 0)),
            (20, (1, 1)),
            (30, (1, 2)),
            (40, (1, 3)),
        ]

        for key, rid in records:
            index.insert(key, rid)

        assert index.delete(20) == 1
        assert index.search(20) == []

        assert index.delete(999) == 0

        expected = Counter({
            10: 1,
            30: 1,
            40: 1,
        })

        assert accessible_key_counter(index) == expected

    finally:
        flush_and_close(filename, fm, bm)


def test_delete_duplicates():
    filename, fm, bm, index = create_index(depth=2)

    try:
        records = [
            (10, (1, 0)),
            (10, (1, 1)),
            (10, (1, 2)),
            (20, (1, 3)),
        ]

        for key, rid in records:
            index.insert(key, rid)

        assert index.delete(10) == 3
        assert index.search(10) == []

        assert accessible_key_counter(index) == Counter({20: 1})

    finally:
        flush_and_close(filename, fm, bm)


# ---------------------------------------------------------------------------
# Split tests
# ---------------------------------------------------------------------------


def test_split_without_directory_doubling():
    """
    depth=2 => hay 4 entradas iniciales.

    Los buckets iniciales tienen local_depth=2, por lo que para provocar
    un split normal primero necesitamos un caso donde global depth sea
    mayor que local depth.

    Este test verifica explícitamente que, después de un split, los KVs
    siguen siendo accesibles.
    """
    filename, fm, bm, index = create_index(
        depth=3,
        max_bucket_size=2,
    )

    try:
        # Buscar claves que colisionen en los primeros bits necesarios.
        keys = find_colliding_keys(2, 4)

        for i, key in enumerate(keys):
            index.insert(key, (10, i))

        assert accessible_key_counter(index) == Counter(keys)

        assert_directory_consistency(index)
        assert_bucket_records_match_hash(index)
        assert_every_key_searchable(index)

    finally:
        flush_and_close(filename, fm, bm)


def test_directory_doubling():
    """
    Fuerza un split de un bucket cuya local depth == global depth.

    Eso debe provocar:
        global depth += 1
    """
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=2,
    )

    try:
        assert index.depth == 1
        old_capacity = index.max_capacity

        # Todas las claves tienen que ir inicialmente al mismo bucket.
        keys = find_colliding_keys(1, 3)

        for i, key in enumerate(keys):
            index.insert(key, (1, i))

        assert index.depth == 2
        assert index.max_capacity == old_capacity * 2

        assert accessible_key_counter(index) == Counter(keys)

        assert_directory_consistency(index)
        assert_bucket_records_match_hash(index)
        assert_every_key_searchable(index)

    finally:
        flush_and_close(filename, fm, bm)


def test_multiple_directory_doublings():
    """
    Provoca varios incrementos de profundidad global.
    """
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=2,
    )

    try:
        inserted = []

        # Bastantes claves para obligar a realizar varios splits.
        for key in range(1000):
            rid = (5, key)
            index.insert(key, rid)
            inserted.append((key, rid))

            if key % 25 == 0:
                assert_directory_consistency(index)
                assert_bucket_records_match_hash(index)

        assert index.depth >= 3

        assert accessible_key_counter(index) == expected_counter(inserted)

        assert_directory_consistency(index)
        assert_bucket_records_match_hash(index)
        assert_every_key_searchable(index)

    finally:
        flush_and_close(filename, fm, bm)


def test_split_preserves_all_records():
    """
    Específicamente verifica que ningún registro desaparece durante
    una secuencia larga de splits.
    """
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=3,
    )

    try:
        expected = Counter()

        for key in range(500):
            rid = (100, key)
            index.insert(key, rid)
            expected[key] += 1

            actual = accessible_key_counter(index)

            assert actual == expected

        assert_directory_consistency(index)
        assert_bucket_records_match_hash(index)

    finally:
        flush_and_close(filename, fm, bm)


# ---------------------------------------------------------------------------
# Compactation tests
# ---------------------------------------------------------------------------


def test_compaction():
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=5,
    )

    try:
        records = [
            (0, (20, 0)),
            (1, (20, 1)),
            (2, (20, 2)),
            (3, (20, 3)),
            (4, (20, 4)),
        ]

        for key, rid in records:
            index.insert(key, rid)

        # Estado inicial: los 5 registros deben ser accesibles.
        expected_before = Counter({
            0: 1,
            1: 1,
            2: 1,
            3: 1,
            4: 1,
        })

        assert accessible_key_counter(index) == expected_before

        # Borramos dos registros.
        assert index.delete(1) == 1
        assert index.delete(3) == 1

        expected_after_delete = Counter({
            0: 1,
            2: 1,
            4: 1,
        })

        assert accessible_key_counter(index) == expected_after_delete

        # Localizamos todos los buckets físicos que contienen registros
        # y compactamos cada uno de ellos.
        pages = set()

        for directory_index in range(index.max_capacity):
            bucket_page = index._locate_bucket(directory_index)

            while bucket_page != -1:
                if bucket_page in pages:
                    break

                pages.add(bucket_page)

                bucket = index._load_bucket(bucket_page)

                try:
                    next_page = bucket.next_bucket_page

                    # Comprobar que antes de compactar puede haber huecos.
                    # No exigimos un tamaño concreto porque las claves
                    # pueden estar distribuidas entre varios buckets.
                    bucket.compact()
                    index.buffer_manager.mark_dirty(bucket_page, index.file_manager)

                finally:
                    index.buffer_manager.unpin_page(bucket_page, index.file_manager)

                bucket_page = next_page

        # La compactación no debe cambiar qué KVs son lógicamente accesibles.
        assert accessible_key_counter(index) == expected_after_delete

        # Y todos los registros restantes deben seguir pudiéndose buscar.
        assert_every_key_searchable(index)

    finally:
        flush_and_close(filename, fm, bm)



def test_insert_after_delete_and_compaction():
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=3,
    )

    try:
        for key in [1, 2, 3]:
            index.insert(key, (1, key))

        assert index.delete(2) == 1

        index.insert(4, (1, 4))

        expected = Counter({
            1: 1,
            3: 1,
            4: 1,
        })

        assert accessible_key_counter(index) == expected

        assert_every_key_searchable(index)

    finally:
        flush_and_close(filename, fm, bm)


# ---------------------------------------------------------------------------
# Overflow tests
# ---------------------------------------------------------------------------


def test_overflow_at_max_depth():
    """
    El índice original tiene MAX_DEPTH=20.

    Para que este test sea viable, temporalmente reducimos MAX_DEPTH.
    Esto no modifica la clase: simplemente cambia la constante global
    que insert() consulta.

    Con MAX_DEPTH=2:
        - comenzamos en depth=2;
        - cuando el bucket se llena, no se puede duplicar;
        - se crea overflow.
    """
    old_max_depth = EHI.MAX_DEPTH
    EHI.MAX_DEPTH = 2

    filename, fm, bm, index = create_index(
        depth=2,
        max_bucket_size=2,
    )

    try:
        assert index.depth == EHI.MAX_DEPTH

        keys = find_colliding_keys(index.depth, 8)

        for i, key in enumerate(keys):
            index.insert(key, (50, i))

        assert accessible_key_counter(index) == Counter(keys)

        # Debe existir al menos una cadena con overflow.
        found_overflow = False

        for directory_index in range(index.max_capacity):
            first = index._locate_bucket(directory_index)
            last = index._locate_last_bucket(directory_index)

            if first is None:
                continue

            bucket = index._load_bucket(first)

            try:
                if bucket.next_bucket_page != -1:
                    found_overflow = True
                    assert last != first
            finally:
                index.buffer_manager.unpin_page(first, index.file_manager)

        assert found_overflow, (
            "No se creó ningún bucket de overflow aunque se alcanzó "
            "MAX_DEPTH."
        )

        assert_directory_consistency(index)
        assert_bucket_records_match_hash(index)
        assert_every_key_searchable(index)

    finally:
        flush_and_close(filename, fm, bm)
        EHI.MAX_DEPTH = old_max_depth


def test_overflow_search():
    old_max_depth = EHI.MAX_DEPTH
    EHI.MAX_DEPTH = 2

    filename, fm, bm, index = create_index(
        depth=2,
        max_bucket_size=2,
    )

    try:
        keys = find_colliding_keys(2, 6)

        for i, key in enumerate(keys):
            index.insert(key, (60, i))

        for i, key in enumerate(keys):
            result = index.search(key)

            assert len(result) == 1
            assert result[0].key == key
            assert result[0].rid == (60, i)

    finally:
        flush_and_close(filename, fm, bm)
        EHI.MAX_DEPTH = old_max_depth


def test_overflow_delete():
    old_max_depth = EHI.MAX_DEPTH
    EHI.MAX_DEPTH = 2

    filename, fm, bm, index = create_index(
        depth=2,
        max_bucket_size=2,
    )

    try:
        keys = find_colliding_keys(2, 8)

        for i, key in enumerate(keys):
            index.insert(key, (70, i))

        target = keys[-1]

        assert index.search(target)
        assert index.delete(target) == 1
        assert index.search(target) == []

        remaining = Counter(keys)
        remaining[target] -= 1

        if remaining[target] == 0:
            del remaining[target]

        assert accessible_key_counter(index) == remaining

    finally:
        flush_and_close(filename, fm, bm)
        EHI.MAX_DEPTH = old_max_depth


# ---------------------------------------------------------------------------
# Variable/string key tests
# ---------------------------------------------------------------------------


def test_variable_string_serializer():
    """
    Prueba directamente el serializer variable-length.

    Esto no usa HashIndex porque el índice de test anterior está
    configurado con enteros.
    """
    from indexes.extendible_hash import KV, KVSerializer

    serializer = KVSerializer("s", True)

    values = [
        "",
        "a",
        "hello",
        "áéíóú",
        "hola mundo",
        "🙂",
        "a" * 100,
    ]

    for value in values:
        original = KV(
            value,
            (123, 456),
            False,
        )

        data = serializer.serialize(original)
        recovered = serializer.deserialize(data)

        assert recovered.key == value
        assert recovered.rid == (123, 456)
        assert recovered.deleted is False


def test_fixed_string_serializer():
    from indexes.extendible_hash import KV, KVSerializer

    serializer = KVSerializer("20s", False)

    values = [
        "",
        "a",
        "hello",
        "hello world",
    ]

    for value in values:
        original = KV(
            value,
            (1, 2),
            False,
        )

        data = serializer.serialize(original)
        recovered = serializer.deserialize(data)

        assert recovered.key == value
        assert recovered.rid == (1, 2)
        assert recovered.deleted is False


# ---------------------------------------------------------------------------
# Deleted flag / physical page tests
# ---------------------------------------------------------------------------


def test_deleted_flag_persists_in_page():
    filename, fm, bm, index = create_index(depth=2)

    try:
        key = 12345
        rid = (999, 888)

        index.insert(key, rid)

        bucket_number = (
            EHI.hash_key(key, KEY_FORMAT, index.seed)
            % index.max_capacity
        )

        page_id = index._locate_bucket(bucket_number)

        bucket = index._load_bucket(page_id)

        try:
            assert bucket.size >= 1

            found = False

            for slot_id in range(bucket.size):
                kv = bucket.get_kv_by_slot_id(slot_id)

                if kv.key == key:
                    assert kv.deleted is False

                    bucket.delete_slot(slot_id)
                    found = True
                    break

            assert found

        finally:
            index.buffer_manager.mark_dirty(page_id, index.file_manager)
            index.buffer_manager.unpin_page(page_id, index.file_manager)

        assert index.search(key) == []

    finally:
        flush_and_close(filename, fm, bm)


# ---------------------------------------------------------------------------
# Randomized/reference-model tests
# ---------------------------------------------------------------------------



def test_random_operations():
    """
    Test diferencial:

        HashIndex vs. modelo de referencia de Python.

    Ejercita conjuntamente:

        - insert
        - duplicates
        - delete
        - search
        - bucket splits
        - directory doubling
        - compactación
        - consistencia del directorio
        - distribución de registros
        - preservación de RIDs

    Se utilizan suficientes operaciones para encontrar errores que no
    aparecen en tests pequeños, pero se evita concentrar cientos de
    registros sobre un conjunto diminuto de hashes, lo que puede provocar
    un crecimiento artificialmente enorme del directorio.
    """
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=3,
    )

    try:
        rng = random.Random(123456)

        expected = []
        next_rid = 0

        for operation_number in range(1000):

            operation = rng.choices(
                [
                    "insert",
                    "delete",
                    "search",
                ],
                weights=[
                    3,
                    1,
                    1,
                ],
                k=1,
            )[0]

            # Suficientemente amplio para producir duplicados,
            # pero evitando concentrar cientos de registros en unas
            # pocas claves. Rango mas amplio que 0..1000: con un rango
            # chico, colisiones de bits bajos del hash hacen crecer el
            # directorio hasta MAX_DEPTH (miles de entradas) con pocos
            # buckets fisicos, y el test se vuelve inutilmente lento.
            key = rng.randint(0, 5000)

            # -----------------------------------------------------------
            # INSERT
            # -----------------------------------------------------------

            if operation == "insert":
                rid = (200, next_rid)
                next_rid += 1

                index.insert(key, rid)
                expected.append((key, rid))

            # -----------------------------------------------------------
            # DELETE
            # -----------------------------------------------------------

            elif operation == "delete":
                deleted_count = index.delete(key)

                before = len(expected)

                expected = [
                    (k, rid)
                    for k, rid in expected
                    if k != key
                ]

                expected_deleted = before - len(expected)

                assert deleted_count == expected_deleted

            # -----------------------------------------------------------
            # SEARCH
            # -----------------------------------------------------------

            elif operation == "search":
                actual = index.search(key)

                expected_rids = Counter(
                    rid
                    for k, rid in expected
                    if k == key
                )

                actual_rids = Counter(
                    kv.rid
                    for kv in actual
                )

                assert actual_rids == expected_rids

                for kv in actual:
                    assert not kv.deleted
                    assert kv.key == key

            # -----------------------------------------------------------
            # COMPROBACIONES ESTRUCTURALES
            # -----------------------------------------------------------

            if operation_number % 250 == 0:
                assert (
                    accessible_key_counter(index)
                    == expected_counter(expected)
                )

                assert_directory_consistency(index)
                assert_bucket_records_match_hash(index)

        # ---------------------------------------------------------------
        # COMPROBACIÓN FINAL COMPLETA
        # ---------------------------------------------------------------

        assert (
            accessible_key_counter(index)
            == expected_counter(expected)
        )

        assert_directory_consistency(index)
        assert_bucket_records_match_hash(index)
        assert_every_key_searchable(index)

        # Comprobar también los RIDs, no solamente las claves.
        expected_rids = Counter(
            rid
            for key, rid in expected
        )

        actual_rids = Counter(
            rid
            for key, rid, page, slot
            in all_accessible_kvs(index)
        )

        assert actual_rids == expected_rids

    finally:
        flush_and_close(filename, fm, bm)




# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------


def test_sequential_keys_stress():
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=4,
    )

    try:
        expected = Counter()

        for key in range(2000):
            index.insert(key, (300, key))
            expected[key] += 1

            if key % 100 == 0:
                assert accessible_key_counter(index) == expected
                assert_directory_consistency(index)

        assert accessible_key_counter(index) == expected
        assert_bucket_records_match_hash(index)

        for key in range(0, 2000, 7):
            result = index.search(key)

            assert len(result) == 1
            assert result[0].key == key
            assert result[0].rid == (300, key)

    finally:
        flush_and_close(filename, fm, bm)


def test_many_duplicates():
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=3,
    )

    try:
        expected = Counter()

        for i in range(500):
            key = i % 10

            index.insert(key, (400, i))
            expected[key] += 1

        assert accessible_key_counter(index) == expected

        for key in range(10):
            result = index.search(key)

            assert len(result) == expected[key]

        assert_directory_consistency(index)

    finally:
        flush_and_close(filename, fm, bm)


# ---------------------------------------------------------------------------
# Pin/unpin sanity tests
# ---------------------------------------------------------------------------


def test_no_pinned_pages_left_after_operations():
    """
    Este test depende de que BufferManager.page_table exponga los frames
    con pin count. No asumimos aquí el nombre del campo interno del frame,
    pero sí comprobamos que el page_table no crezca indefinidamente.

    Si tu BufferManager expone pin_count, conviene añadir una comprobación
    más fuerte.
    """
    filename, fm, bm, index = create_index(
        depth=1,
        max_bucket_size=3,
    )

    try:
        for key in range(100):
            index.insert(key, (500, key))

        for key in range(100):
            index.search(key)

        for key in range(0, 100, 2):
            index.delete(key)

        # El número de páginas en memoria no debería crecer sin límite
        # debido a pins olvidados.
        assert len(bm.page_table) <= BUFFER_FRAMES

    finally:
        flush_and_close(filename, fm, bm)


# ---------------------------------------------------------------------------
# Test global
# ---------------------------------------------------------------------------


tests = [
    test_initialization,
    test_initial_bucket_state,
    test_insert_single,
    test_insert_multiple,
    test_duplicates,
    test_delete,
    test_delete_duplicates,

    test_split_without_directory_doubling,
    test_directory_doubling,
    test_multiple_directory_doublings,
    test_split_preserves_all_records,

    test_compaction,
    test_insert_after_delete_and_compaction,

    test_overflow_at_max_depth,
    test_overflow_search,
    test_overflow_delete,

    test_variable_string_serializer,
    test_fixed_string_serializer,

    test_deleted_flag_persists_in_page,

    test_random_operations,

    test_sequential_keys_stress,
    test_many_duplicates,

    test_no_pinned_pages_left_after_operations,
]


def run_tests():
    passed = 0

    for test in tests:
        print(f"Running {test.__name__}...", end=" ")

        try:
            test()
            print("OK")
            passed += 1

        except Exception:
            print("FAILED")
            raise

    print(f"\n{passed}/{len(tests)} tests passed.")


if __name__ == "__main__":
    run_tests()
