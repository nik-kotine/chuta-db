"""
Indice de bitmap (bitmap index).

Un indice de bitmap asocia a cada valor distinto de una columna el conjunto
de filas (RIDs) que lo tienen. Ese conjunto se guarda como una mascara de
bits y, como las mascaras se combinan entre si con un AND bit a bit, el
planificador puede resolver una conjuncion entera de predicados (y una
disyuncion con OR) sin tocar una sola pagina de datos: primero arma la
mascara resultado en memoria y recien ahi va a buscar las filas que de
verdad hacen falta.

Es el indice que mas rinde cuando la columna tiene pocos valores distintos
y el WHERE combina varios predicados, porque el trabajo pesado (la
interseccion) pasa en la CPU sobre bytes y las lecturas de disco se limitan
a las paginas del heap que la mascara final senala.

Layout en disco
---------------
El archivo tiene un header chico (`FILE_HEADER_FORMAT`) con la raiz, la
cantidad de paginas de datos y la cabeza de la lista de paginas libres. Las
paginas de datos van encadenadas por `next_page` y cada una guarda entradas
`(clave, bitmap)`. Como una mascara grande no entra en una pagina, cada
entrada escribe hasta `MAX_ON_PAGE` bytes en su pagina y el resto sigue en
una cadena de paginas de desborde, que se recicla con la lista de paginas
libres del header.

Por que el directorio clave -> (pagina, slot) vive en RAM: asi una consulta
toca una sola pagina del indice por clave involucrada en vez de recorrerlas
todas. Se rearma al abrir el archivo leyendo las claves de la cadena de
paginas, que es la unica lectura "barre el indice entero" del ciclo de vida
y ocurre una sola vez por apertura.
"""

import os
import struct

from indexes.b_tree_key_codec import encode_key, decode_key
from storage.buffer_manager import BufferManager
from storage.file_manager import FileManager
from storage.rid import RID


PAGE_SIZE = 4096

# Header de una pagina de datos: (offset libre, cantidad de entradas,
# siguiente pagina de la cadena, reservado). Mismo esquema que las demas
# paginas slotted del repo: el directorio crece desde el inicio y los datos
# desde el final.
PAGE_HEADER_FORMAT = ">iiii"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)

# Directorio de una entrada: (offset del primer trozo, cuanto ocupa de esa
# pagina, pagina de desborde o NO_PAGE). El largo total del payload se
# deduce recorriendo la cadena de desborde.
ENTRY_DIR_FORMAT = ">IHi"
ENTRY_DIR_SIZE = struct.calcsize(ENTRY_DIR_FORMAT)

# Header del archivo: (pagina raiz, cantidad de paginas de datos, pagina
# libre). El ultimo va con signo porque -1 es "no hay".
FILE_HEADER_FORMAT = ">IIi"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FORMAT)

NO_PAGE = -1

# Cuanto de cada entrada queda en su pagina; el resto va a desborde.
MAX_ON_PAGE = PAGE_SIZE // 4

SPILL_HEADER_FORMAT = ">i"
SPILL_HEADER_SIZE = struct.calcsize(SPILL_HEADER_FORMAT)
SPILL_CAP = PAGE_SIZE - SPILL_HEADER_SIZE

# El HeapFile usa paginas de 4096 bytes con cabecera de 12 y slots de 8, asi
# que una pagina de datos tiene a lo sumo 510 slots. Se redondea a 512 para
# que cada tramo del bitmap ocupe un numero entero de bytes: el bit global de
# un RID es page_id * SLOTS_PER_PAGE + slot_id.
SLOTS_PER_PAGE = 512
CHUNK_BYTES = SLOTS_PER_PAGE // 8

CHUNKS_HEADER_FORMAT = ">I"
CHUNKS_HEADER_SIZE = struct.calcsize(CHUNKS_HEADER_FORMAT)
CHUNK_FORMAT = ">IB"
CHUNK_HEADER_SIZE = struct.calcsize(CHUNK_FORMAT)

KEY_LEN_FORMAT = ">H"


def popcount(data) -> int:
    return sum(bin(byte).count("1") for byte in data)


class Bitmap:
    """
    Conjunto de RIDs del heap expresado como una mascara de bits por tramos.

    Cada tramo son los `CHUNK_BYTES` bytes que corresponden a los slots de
    una pagina del heap. Solo se guardan los tramos con algun bit en uno (los
    demas serian ceros y no aportarian nada), que es lo que hace que el
    indice siga siendo chico con tablas grandes.
    """

    __slots__ = ("_chunks", "_n")

    def __init__(self, chunks=None, n=0):
        self._chunks = chunks if chunks is not None else {}
        self._n = n

    # ---------------- construccion y consulta ----------------

    def add(self, rid) -> "Bitmap":
        return self.add_at(rid[0], rid[1])

    def add_at(self, page_id: int, slot_id: int) -> "Bitmap":
        if slot_id >= SLOTS_PER_PAGE:
            raise ValueError(
                f"slot_id {slot_id} fuera del rango de un bitmap ({SLOTS_PER_PAGE})"
            )
        chunk = self._chunks.get(page_id)
        if chunk is None:
            chunk = bytearray(CHUNK_BYTES)
            self._chunks[page_id] = chunk
        byte, bit = divmod(slot_id, 8)
        mask = 1 << bit
        if not chunk[byte] & mask:
            chunk[byte] |= mask
            self._n += 1
        return self

    def discard(self, rid) -> "Bitmap":
        return self.discard_at(rid[0], rid[1])

    def discard_at(self, page_id: int, slot_id: int) -> "Bitmap":
        chunk = self._chunks.get(page_id)
        if chunk is None:
            return self
        byte, bit = divmod(slot_id, 8)
        mask = 1 << bit
        if chunk[byte] & mask:
            chunk[byte] &= ~mask
            self._n -= 1
            if not any(chunk):
                del self._chunks[page_id]
        return self

    def copy(self) -> "Bitmap":
        return Bitmap({p: bytearray(c) for p, c in self._chunks.items()}, self._n)

    def __bool__(self) -> bool:
        return self._n > 0

    def __len__(self) -> int:
        return self._n

    def __contains__(self, rid) -> bool:
        chunk = self._chunks.get(rid[0])
        if chunk is None:
            return False
        byte, bit = divmod(rid[1], 8)
        return bool(chunk[byte] & (1 << bit))

    def __eq__(self, other) -> bool:
        if not isinstance(other, Bitmap):
            return NotImplemented
        return self._n == other._n and self._chunks == other._chunks

    def __repr__(self) -> str:
        return f"Bitmap({self._n} filas en {len(self._chunks)} pagina(s))"

    def count(self) -> int:
        """Cantidad de filas (RIDs) del conjunto."""
        return self._n

    def pages(self) -> list:
        """Paginas del heap que el conjunto toca: cuantas paginas de datos
        va a leer un scan por esta mascara."""
        return sorted(self._chunks)

    def page_count(self) -> int:
        return len(self._chunks)

    def rids(self):
        """Itera los RIDs del conjunto en orden de pagina y de slot, que es
        el orden en que conviene leer el heap para no paginar de mas."""
        for page_id in sorted(self._chunks):
            chunk = self._chunks[page_id]
            for byte_index, byte in enumerate(chunk):
                if not byte:
                    continue
                for bit in range(8):
                    if byte & (1 << bit):
                        yield RID(page_id, byte_index * 8 + bit)

    # ---------------- combinaciones ----------------

    def intersect(self, other: "Bitmap") -> "Bitmap":
        """AND bit a bit: quedan las filas que estan en los dos conjuntos."""
        small, large = (other, self) if self._n > other._n else (self, other)

        chunks = {}
        n = 0
        for page_id, chunk in small._chunks.items():
            other_chunk = large._chunks.get(page_id)
            if other_chunk is None:
                continue
            merged = bytes(a & b for a, b in zip(chunk, other_chunk))
            if any(merged):
                chunks[page_id] = bytearray(merged)
                n += popcount(merged)
        return Bitmap(chunks, n)

    def union(self, other: "Bitmap") -> "Bitmap":
        """OR bit a bit: quedan las filas que estan en alguno de los dos."""
        chunks = {p: bytearray(c) for p, c in self._chunks.items()}
        for page_id, chunk in other._chunks.items():
            current = chunks.get(page_id)
            if current is None:
                chunks[page_id] = bytearray(chunk)
            else:
                chunks[page_id] = bytearray(a | b for a, b in zip(current, chunk))
        n = sum(popcount(c) for c in chunks.values())
        return Bitmap(chunks, n)

    def difference(self, other: "Bitmap") -> "Bitmap":
        """AND NOT: quedan las filas de este conjunto que no estan en el otro."""
        chunks = {}
        n = 0
        for page_id, chunk in self._chunks.items():
            other_chunk = other._chunks.get(page_id)
            if other_chunk is None:
                chunks[page_id] = bytearray(chunk)
                n += popcount(chunk)
                continue
            merged = bytes(a & ~b for a, b in zip(chunk, other_chunk))
            if any(merged):
                chunks[page_id] = bytearray(merged)
                n += popcount(merged)
        return Bitmap(chunks, n)

    # ---------------- serializacion ----------------

    def encode(self) -> bytes:
        """Serializa los tramos: cuantos hay y, por cada uno, el page_id del
        heap y los bytes utiles de la mascara (la cola en ceros se omite)."""
        useful = []
        for page_id in sorted(self._chunks):
            chunk = bytes(self._chunks[page_id])
            length = len(chunk.rstrip(b"\x00"))
            if length:
                useful.append((page_id, chunk[:length]))

        out = bytearray(struct.pack(CHUNKS_HEADER_FORMAT, len(useful)))
        for page_id, chunk in useful:
            out += struct.pack(CHUNK_FORMAT, page_id, len(chunk))
            out += chunk
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> "Bitmap":
        n_chunks = struct.unpack_from(CHUNKS_HEADER_FORMAT, data, 0)[0]
        cursor = CHUNKS_HEADER_SIZE
        chunks = {}
        n = 0
        for _ in range(n_chunks):
            page_id, length = struct.unpack_from(CHUNK_FORMAT, data, cursor)
            cursor += CHUNK_HEADER_SIZE
            chunk = bytearray(CHUNK_BYTES)
            chunk[:length] = data[cursor:cursor + length]
            cursor += length
            chunks[page_id] = chunk
            n += popcount(chunk)
        return cls(chunks, n)


class BitmapPage:
    """
    Pagina slotted de entradas `(clave, bitmap)` de un indice de bitmap.

    Como el payload de una entrada tiene largo variable (y puede no caber
    entero), toda mutacion reescribe la pagina compacta desde el final, igual
    que `BTreeLeafPage._rewrite`. Asi la pagina no queda con huecos y la
    posicion de cada entrada se recalcula leyendo la pagina.
    """

    def __init__(self, page_id: int, data: bytearray, is_new: bool = False):
        self.page_id = page_id
        self.data = data
        if is_new:
            self.offset = PAGE_SIZE
            self.n_entries = 0
            self.next_page = NO_PAGE
            self.save_header()
        else:
            self.load_header()

    def save_header(self):
        struct.pack_into(
            PAGE_HEADER_FORMAT, self.data, 0,
            self.offset, self.n_entries, self.next_page, NO_PAGE,
        )

    def load_header(self):
        (self.offset, self.n_entries, self.next_page,
         _) = struct.unpack_from(PAGE_HEADER_FORMAT, self.data, 0)

    def _dir_offset(self, slot_id: int) -> int:
        return PAGE_HEADER_SIZE + slot_id * ENTRY_DIR_SIZE

    def free_space(self) -> int:
        return self.offset - self._dir_offset(self.n_entries)

    def fits(self, total_length: int) -> bool:
        """El payload se parte en MAX_ON_PAGE bytes dentro de la pagina y el
        resto en desborde, asi que alcanza con que entren esos primeros
        bytes mas la entrada del directorio."""
        return self.free_space() >= min(total_length, MAX_ON_PAGE) + ENTRY_DIR_SIZE

    def read_slot(self, slot_id: int):
        return struct.unpack_from(ENTRY_DIR_FORMAT, self.data, self._dir_offset(slot_id))

    def read_head(self, slot_id: int) -> bytes:
        """El trozo del payload que vive en esta pagina."""
        offset, length, _ = self.read_slot(slot_id)
        return bytes(self.data[offset:offset + length])

    def read_spill(self, slot_id: int) -> int:
        return self.read_slot(slot_id)[2]

    def _write_slot(self, slot_id: int, offset: int, length: int, spill: int):
        struct.pack_into(
            ENTRY_DIR_FORMAT, self.data, self._dir_offset(slot_id), offset, length, spill
        )

    def rewrite(self, payloads: list, spills: list):
        """Reescribe la pagina compacta. `payloads` son los payloads en el
        orden en que deben quedar y `spills[i]` la pagina de desborde de la
        entrada i."""
        cursor = PAGE_SIZE
        for slot_id, (payload, spill) in enumerate(zip(payloads, spills)):
            head = payload[:MAX_ON_PAGE]
            cursor -= len(head)
            self.data[cursor:cursor + len(head)] = head
            self._write_slot(slot_id, cursor, len(head), spill)
        self.n_entries = len(payloads)
        self.offset = cursor
        self.save_header()


class BitmapIndex:
    """
    Indice de bitmap sobre una columna de una tabla Heap: cada valor distinto
    mapea al bitmap de las filas que lo tienen.

    Se mantiene desde `Table.insert`/`Table.delete` con
    `_insert_ref`/`delete_ref`, igual que los demas indices no agrupados, y
    expone `search`/`search_rango` para que el planificador arme las mascaras
    a combinar.
    """

    def __init__(
        self,
        index_filename: str,
        table_name: str = "",
        column_name: str = "",
        buffer_frames: int = 50,
        file_manager: FileManager = None,
    ):
        self.index_filename = index_filename
        self.table_name = table_name
        self.column_name = column_name
        self.file_manager = file_manager or FileManager(
            index_filename, PAGE_SIZE, FILE_HEADER_SIZE
        )
        self.buffer_manager = BufferManager(self.file_manager, buffer_frames)

        # Lecturas de pagina del indice, para medir de cuanto ahorra un scan
        # con bitmap frente a barrer el heap.
        self.pages_read = 0

        # Directorio en RAM: clave -> (page_id, slot_id) dentro del indice.
        self._directory: dict = {}
        # Paginas de datos en el orden de la cadena.
        self._data_pages: list = []

        if len(self.file_manager.read_header()) < FILE_HEADER_SIZE:
            self._init_empty_index()
        else:
            self._load_header()
            self._load_directory()

    # ---------------- header y paginas ----------------

    def _init_empty_index(self):
        self.buffer_manager.invalidate_all(self.file_manager)
        self.file_manager.truncate(self.file_manager.file_header_size)

        root = self.file_manager.allocate_page()
        self.root_page_id = root
        self.n_data_pages = 1
        self.free_head = NO_PAGE
        self._save_header()

        page = self._load_page(root, is_new=True)
        self._save_page(page)

        self._directory = {}
        self._data_pages = [root]

    def _load_header(self):
        header = self.file_manager.read_header()
        (self.root_page_id, self.n_data_pages,
         self.free_head) = struct.unpack(FILE_HEADER_FORMAT, header)

    def _save_header(self):
        self.file_manager.write_header(
            struct.pack(
                FILE_HEADER_FORMAT, self.root_page_id, self.n_data_pages, self.free_head
            )
        )

    def _load_page(self, page_id: int, is_new: bool = False) -> BitmapPage:
        self.pages_read += 1
        buf = self.buffer_manager.fetch_page(page_id, self.file_manager)
        return BitmapPage(page_id, buf, is_new=is_new)

    def _save_page(self, page: BitmapPage):
        self.buffer_manager.mark_dirty(page.page_id, self.file_manager)
        self.buffer_manager.unpin_page(page.page_id, self.file_manager)

    def _unpin(self, page_id: int):
        self.buffer_manager.unpin_page(page_id, self.file_manager)

    def _alloc_page(self) -> int:
        if self.free_head != NO_PAGE:
            page_id = self.free_head
            self.pages_read += 1
            buf = self.buffer_manager.fetch_page(page_id, self.file_manager)
            self.free_head = struct.unpack_from(SPILL_HEADER_FORMAT, buf, 0)[0]
            self.buffer_manager.mark_dirty(page_id, self.file_manager)
            self._unpin(page_id)
            self._save_header()
            return page_id
        return self.file_manager.allocate_page()

    def _free_page(self, page_id: int):
        self.pages_read += 1
        buf = self.buffer_manager.fetch_page(page_id, self.file_manager)
        struct.pack_into(SPILL_HEADER_FORMAT, buf, 0, self.free_head)
        self.buffer_manager.mark_dirty(page_id, self.file_manager)
        self._unpin(page_id)
        self.free_head = page_id
        self._save_header()

    # ---------------- cadenas de desborde ----------------

    def _walk_spill(self, head: int):
        """Itera `(page_id, siguiente, datos)` de una cadena de desborde.
        Siempre rinde `SPILL_CAP` bytes por pagina: la cola en ceros se
        descarta al decodificar, asi que el payload no arrastra basura."""
        page_id = head
        while page_id != NO_PAGE:
            self.pages_read += 1
            buf = self.buffer_manager.fetch_page(page_id, self.file_manager)
            siguiente = struct.unpack_from(SPILL_HEADER_FORMAT, buf, 0)[0]
            data = bytes(buf[SPILL_HEADER_SIZE:SPILL_HEADER_SIZE + SPILL_CAP])
            self._unpin(page_id)
            yield page_id, siguiente, data
            page_id = siguiente

    def _write_spill(self, page_id: int, siguiente: int, data: bytes):
        self.pages_read += 1
        buf = self.buffer_manager.fetch_page(page_id, self.file_manager)
        struct.pack_into(SPILL_HEADER_FORMAT, buf, 0, siguiente)
        payload = data[:SPILL_CAP].ljust(SPILL_CAP, b"\x00")
        buf[SPILL_HEADER_SIZE:SPILL_HEADER_SIZE + SPILL_CAP] = payload
        self.buffer_manager.mark_dirty(page_id, self.file_manager)
        self._unpin(page_id)

    def _write_spill_chain(self, data: bytes, head: int) -> int:
        """
        Escribe `data` (la parte del payload que no entra en la pagina de la
        entrada) en la cadena de desborde `head`: reutiliza las paginas que
        ya estan, libera las que sobran y reserva las que faltan. Devuelve la
        pagina de inicio de la cadena, o NO_PAGE si no hace falta desborde.
        """
        if not data:
            for page_id, _siguiente, _datos in self._walk_spill(head):
                self._free_page(page_id)
            return NO_PAGE

        needed = (len(data) + SPILL_CAP - 1) // SPILL_CAP
        ids = [page_id for page_id, _siguiente, _datos in self._walk_spill(head)]

        for surplus in ids[needed:]:
            self._free_page(surplus)
        ids = ids[:needed]

        while len(ids) < needed:
            ids.append(self._alloc_page())

        for index, page_id in enumerate(ids):
            siguiente = ids[index + 1] if index + 1 < len(ids) else NO_PAGE
            self._write_spill(page_id, siguiente, data[index * SPILL_CAP:(index + 1) * SPILL_CAP])

        return ids[0]

    def _read_payload(self, page: BitmapPage, slot_id: int) -> bytes:
        """Payload completo de una entrada: su trozo en la pagina mas la
        cadena de desborde."""
        payload = bytearray(page.read_head(slot_id))
        for _page_id, _siguiente, data in self._walk_spill(page.read_spill(slot_id)):
            payload += data
        return bytes(payload)

    # ---------------- directorio ----------------

    @staticmethod
    def _encode_payload(key, bitmap: Bitmap) -> bytes:
        """`encode_key` no es auto-delimitante (una clave string no lleva
        largo), asi que el payload lo arranca con el largo de la clave."""
        encoded = encode_key(key)
        return struct.pack(KEY_LEN_FORMAT, len(encoded)) + encoded + bitmap.encode()

    @staticmethod
    def _key_of_payload(payload: bytes):
        length = struct.unpack_from(KEY_LEN_FORMAT, payload, 0)[0]
        return decode_key(payload[2:2 + length])

    @staticmethod
    def _decode_payload(payload: bytes):
        length = struct.unpack_from(KEY_LEN_FORMAT, payload, 0)[0]
        return decode_key(payload[2:2 + length]), Bitmap.decode(payload[2 + length:])

    def _load_directory(self):
        """Rearma el directorio (y la lista de paginas) leyendo las claves
        de la cadena de paginas de datos."""
        self._directory = {}
        self._data_pages = []
        page_id = self.root_page_id
        while page_id != NO_PAGE:
            self._data_pages.append(page_id)
            page = self._load_page(page_id)
            for slot_id in range(page.n_entries):
                key = self._key_of_payload(self._read_payload(page, slot_id))
                self._directory[key] = (page_id, slot_id)
            siguiente = page.next_page
            self._save_page(page)
            page_id = siguiente

    def _refresh_directory(self, page_id: int, payloads: list):
        """El rewrite de una pagina renumera los slots, asi que el directorio
        se rehace con las claves que quedaron."""
        for key in [k for k, loc in self._directory.items() if loc[0] == page_id]:
            del self._directory[key]
        for slot_id, payload in enumerate(payloads):
            self._directory[self._key_of_payload(payload)] = (page_id, slot_id)

    # ---------------- lectura ----------------

    def keys(self) -> list:
        """Valores distintos que tiene el indice."""
        return list(self._directory.keys())

    def key_count(self) -> int:
        return len(self._directory)

    def exists(self, key) -> bool:
        return key in self._directory

    def search(self, key) -> Bitmap:
        """Bitmap de las filas con ese valor; vacio si no hay ninguna. Una
        clave ausente se resuelve solo con el diccionario en RAM, asi que no
        toca ninguna pagina del indice."""
        location = self._directory.get(key)
        if location is None:
            return Bitmap()
        page_id, slot_id = location
        page = self._load_page(page_id)
        payload = self._read_payload(page, slot_id)
        self._save_page(page)
        return self._decode_payload(payload)[1]

    def search_range(self, low, high, max_keys: int = 4096) -> Bitmap | None:
        """
        Union de los bitmaps de los valores dentro de `[low, high]`, donde
        `None` deja el extremo abierto. Devuelve None si hay demasiados
        valores distintos para armarla en memoria (en ese caso el
        planificador prefiere otro indice).

        Una clave que no se puede comparar con el extremo (por ejemplo un
        int en un rango de strings) queda afuera: en un indice de una sola
        columna eso no deberia pasar, pero asi el rango nunca devuelve de
        mas.
        """
        def dentro(key) -> bool:
            if low is not None:
                try:
                    if key < low:
                        return False
                except TypeError:
                    return False
            if high is not None:
                try:
                    if key > high:
                        return False
                except TypeError:
                    return False
            return True

        candidates = []
        for key in self._directory:
            if dentro(key):
                candidates.append(key)
                if len(candidates) > max_keys:
                    return None

        result = Bitmap()
        for key in candidates:
            result = result.union(self.search(key))
        return result

    def stats(self) -> dict:
        """Metricas del indice, para medir el ahorro de un scan con bitmap."""
        return {
            "keys": self.key_count(),
            "data_pages": self.n_data_pages,
            "pages_read": self.pages_read,
            "bytes": os.path.getsize(self.index_filename)
            if os.path.exists(self.index_filename) else 0,
        }

    # ---------------- escritura ----------------

    def _read_entries(self, page: BitmapPage) -> list:
        return [self._read_payload(page, slot_id) for slot_id in range(page.n_entries)]

    def insert(self, key, rid):
        return self._insert_ref(key, rid)

    def _insert_ref(self, key, rid):
        """Mantenimiento desde `Table.insert`: enciende el bit del RID en el
        bitmap de esa clave."""
        self._put(key, self.search(key).add(rid))
        return rid

    def delete_ref(self, key, rid) -> bool:
        """Mantenimiento desde `Table.delete`: apaga el bit del RID. Si el
        bitmap queda vacio, la clave se borra del indice."""
        bitmap = self.search(key)
        if not bitmap:
            return False
        before = bitmap.count()
        bitmap = bitmap.discard(rid)
        if bitmap.count() == before:
            return False
        if not bitmap:
            return self._drop(key)
        self._put(key, bitmap)
        return True

    def delete(self, key) -> bool:
        return self._drop(key)

    def _put(self, key, bitmap: Bitmap):
        payload = self._encode_payload(key, bitmap)
        location = self._directory.get(key)
        if location is not None:
            page_id, slot_id = location
            entries, spills = self._entries_with(page_id, slot_id, payload)
            self._rewrite_page(page_id, entries, spills)
            return
        self._append(payload)

    def _drop(self, key) -> bool:
        location = self._directory.get(key)
        if location is None:
            return False
        page_id, slot_id = location
        entries, spills = self._entries_with(page_id, slot_id, None)
        self._rewrite_page(page_id, entries, spills)
        return True

    def _entries_with(self, page_id: int, slot_id: int, payload):
        """
        Entradas de `page_id` con `payload` en `slot_id`, o sin esa entrada si
        `payload` es None, junto con la cadena de desborde a reutilizar de
        cada una.

        La cadena de la entrada que sale (o que se reemplaza) se libera
        aca y no en el rewrite: si se liberara despues, esa pagina podria
        ser reasignada como cadena de otra entrada de la misma pagina en la
        misma operacion, y las dos apuntarian al mismo desborde.
        """
        page = self._load_page(page_id)
        entries = self._read_entries(page)
        previous = [page.read_spill(index) for index in range(page.n_entries)]
        self._unpin(page_id)

        self._write_spill_chain(b"", previous[slot_id])

        entries = [entry for index, entry in enumerate(entries) if index != slot_id]
        spills = [spill for index, spill in enumerate(previous) if index != slot_id]

        if payload is not None:
            entries.insert(slot_id, payload)
            spills.insert(slot_id, NO_PAGE)
        return entries, spills

    def _rewrite_page(self, page_id: int, entries: list, spills: list):
        page = self._load_page(page_id)
        new_spills = [
            self._write_spill_chain(payload[MAX_ON_PAGE:], previous)
            for payload, previous in zip(entries, spills)
        ]
        page.rewrite(entries, new_spills)
        self._refresh_directory(page_id, entries)
        self._save_page(page)

    def _append(self, payload: bytes):
        """Agrega una entrada nueva: en la ultima pagina de datos con lugar, o
        en una pagina nueva al final de la cadena."""
        for page_id in reversed(self._data_pages):
            page = self._load_page(page_id)
            entries = self._read_entries(page)
            spills = [page.read_spill(index) for index in range(page.n_entries)]
            self._unpin(page_id)
            if page.fits(len(payload)):
                entries.append(payload)
                spills.append(NO_PAGE)
                self._rewrite_page(page_id, entries, spills)
                return

        page_id = self._alloc_page()
        page = self._load_page(page_id, is_new=True)
        self._save_page(page)

        if self._data_pages:
            self._link(self._data_pages[-1], page_id)
        self._data_pages.append(page_id)
        self.n_data_pages += 1
        self._save_header()

        self._rewrite_page(page_id, [payload], [NO_PAGE])

    def _link(self, page_id: int, siguiente: int):
        self.pages_read += 1
        buf = self.buffer_manager.fetch_page(page_id, self.file_manager)
        struct.pack_into(">i", buf, 8, siguiente)
        self.buffer_manager.mark_dirty(page_id, self.file_manager)
        self._unpin(page_id)

    def close(self):
        self.buffer_manager.close(self.file_manager)