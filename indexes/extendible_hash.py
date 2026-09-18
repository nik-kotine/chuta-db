import struct
PAGE_HEADER_FORMAT = ">iiii"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)
LOCAL_DEPTH_FORMAT = ">i"
LOCAL_DEPTH_SIZE = struct.calcsize(LOCAL_DEPTH_FORMAT)


SLOT_FORMAT = ">ii"
SLOT_SIZE = struct.calcsize(SLOT_FORMAT)
DELETED_FORMAT = ">?"
DELETED_SIZE = struct.calcsize(DELETED_FORMAT)

PAGE_SIZE = 8192
BUCKETS_PER_PAGE = 8

MAX_KV_SIZE = 800

MAX_DEPTH = 20


#Función hash (proveniente de IA por facilidad)
def murmurhash3_32(data: bytes, seed: int = 0) -> int:
    length = len(data)

    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    h = seed & 0xFFFFFFFF

    # Procesar bloques de 4 bytes
    for i in range(0, length - length % 4, 4):
        k = int.from_bytes(data[i:i + 4], byteorder="little")

        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF

        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF

    # Procesar los 1-3 bytes restantes
    tail = data[length - length % 4:]
    k = 0

    if len(tail) == 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k

    # Finalización
    h ^= length

    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def hash_key(key, key_format: str, seed: int = 0) -> int:
    if isinstance(key, str):
        data = key.encode("utf-8")
    else:
        data = struct.pack(key_format, key)
    return murmurhash3_32(data, seed)


DIRECTORY_ENTRY_FORMAT = ">ii"
DIRECTORY_ENTRY_SIZE = struct.calcsize(DIRECTORY_ENTRY_FORMAT)

class DirectoryPage:
    page_ba: bytearray
    def __init__(self, page_ba: bytearray):
        self.page_ba = page_ba
    def get_first(self, index):
        if index < 0 or index >= PAGE_SIZE // DIRECTORY_ENTRY_SIZE:
            raise IndexError("Index for accessing directory page is out of page limit range")
        offset = index * DIRECTORY_ENTRY_SIZE
        return struct.unpack_from(">i", self.page_ba, offset)[0]
    def set_first(self, index, page_id):
        if index < 0 or index >= PAGE_SIZE // DIRECTORY_ENTRY_SIZE:
            raise IndexError("Index for accessing directory page is out of page limit range")
        offset = index * DIRECTORY_ENTRY_SIZE
        struct.pack_into(">i", self.page_ba, offset, page_id)
    def get_second(self, index):
        if index < 0 or index >= PAGE_SIZE // DIRECTORY_ENTRY_SIZE:
            raise IndexError("Index for accessing directory page is out of page limit range")
        offset = index * DIRECTORY_ENTRY_SIZE + 4
        return struct.unpack_from(">i", self.page_ba, offset)[0]
    def set_second(self, index, page_id):
        if index < 0 or index >= PAGE_SIZE // DIRECTORY_ENTRY_SIZE:
            raise IndexError("Index for accessing directory page is out of page limit range")
        offset = index * DIRECTORY_ENTRY_SIZE + 4
        struct.pack_into(">i", self.page_ba, offset, page_id)

class MetadataPage:
    page_ba: bytearray
    def __init__(self, page_ba: bytearray):
        self.page_ba = page_ba
    def get(self, index):
        offset = index * 4
        return struct.unpack_from(">i", self.page_ba, offset)[0]
    def set(self, index, page_id):
        offset = index * 4
        struct.pack_into(">i", self.page_ba, offset, page_id)


class KV:
    key = None
    rid = None
    deleted: bool
    def __init__(self, key, rid, deleted):
        self.key = key
        self.rid = rid
        self.deleted = deleted


class KVSerializer:
    key_format: str
    key_variable: bool

    def __init__(self, key_format, key_variable):
        self.key_format = key_format
        self.key_variable = key_variable

    def serialize(self, kv: KV):
        output = bytearray()
        if self.key_variable:
            key_encoded = kv.key.encode("utf-8")
            output += struct.pack(">i", len(key_encoded))
            output += key_encoded
        elif self.key_format[-1] == "s" and self.key_format[:-1].isnumeric():
            length = int(self.key_format[:-1])
            attencoded = kv.key.encode("utf-8")
            if len(attencoded) > length:
                raise ValueError("Key length is longer than key format")
                #attencoded2 = attencoded[0:length]
            else:
                attencoded2 = bytearray(length)
                attencoded2[0:len(attencoded)] = attencoded
            output += attencoded2
        else:
            output += struct.pack(self.key_format, kv.key)
        output += struct.pack(">ii", kv.rid[0], kv.rid[1])
        output += struct.pack(DELETED_FORMAT, kv.deleted)
        return output

    def deserialize(self, data: bytes):
        unpacking_index = 0
        if self.key_variable:
            field_length = struct.unpack(">i", data[unpacking_index:unpacking_index + 4])[0]
            unpacking_index += 4
            if self.key_format == "s":
                current_key = (data[unpacking_index:unpacking_index + field_length].decode("utf-8"))
            else:
                raise TypeError("Format not supported yet")  # mientras no haya numeric
            unpacking_index += field_length
            rid_page_id, rid_slot_id = struct.unpack(">ii", data[unpacking_index:unpacking_index + 8])
            unpacking_index += 8
            deleted = struct.unpack(DELETED_FORMAT, data[unpacking_index:unpacking_index+DELETED_SIZE])[0]
            return KV(current_key, self._make_rid(rid_page_id, rid_slot_id), deleted)
        elif self.key_format[-1] == "s" and self.key_format[:-1].isnumeric():
            length = int(self.key_format[:-1])
            current_key = data[unpacking_index:unpacking_index + length].rstrip(b"\x00").decode("utf-8")
            unpacking_index += length
            rid_page_id, rid_slot_id = struct.unpack(">ii", data[unpacking_index:unpacking_index + 8])
            unpacking_index += 8
            deleted = struct.unpack(DELETED_FORMAT, data[unpacking_index:unpacking_index + DELETED_SIZE])[0]
            return KV(current_key, self._make_rid(rid_page_id, rid_slot_id), deleted)
        else:
            key_size = struct.calcsize(self.key_format)
            current_key = struct.unpack(self.key_format, data[unpacking_index:unpacking_index + key_size])[0]
            unpacking_index += key_size
            rid_page_id, rid_slot_id = struct.unpack(">ii", data[unpacking_index:unpacking_index + 8])
            unpacking_index += 8
            deleted = struct.unpack(DELETED_FORMAT, data[unpacking_index:unpacking_index + DELETED_SIZE])[0]
            return KV(current_key, self._make_rid(rid_page_id, rid_slot_id), deleted)

    def get_size_of(self, kv):
        record_bytes = self.serialize(kv)
        return len(record_bytes)


    def _make_rid(self, page_id: int, slot_id: int):
        return page_id, slot_id



class VariableBucketPage:
    offset: int
    size: int
    local_depth: int
    next_bucket_page: int
    slots: list[tuple[int, int]]
    page_ba: bytearray
    page_size: int

    def __init__(self, page_ba: bytearray, page_size: int, serializer: KVSerializer, is_new: bool=False) -> None:
        self.page_size = page_size
        self.page_ba = page_ba
        self.serializer = serializer
        self.slots = []
        if is_new:
            self.size = 0
            self.offset = self.page_size
        else:
            base = PAGE_HEADER_SIZE
            for i in range(self.size):
                data0, data1 = struct.unpack_from(">ii", self.page_ba, base)
                self.slots.append(tuple([data0, data1]))
                base += 8


    @property
    def next_bucket_page(self):
        return struct.unpack_from(">i", self.page_ba, 12)[0]

    @next_bucket_page.setter
    def next_bucket_page(self, next_bucket_page: int):
        struct.pack_into(">i", self.page_ba, 12, next_bucket_page)

    @property
    def local_depth(self) -> int:
        return struct.unpack_from(LOCAL_DEPTH_FORMAT, self.page_ba, 8)[0]

    @local_depth.setter
    def local_depth(self, local_depth: int):
        struct.pack_into(LOCAL_DEPTH_FORMAT, self.page_ba, 8, local_depth)

    @property
    def size(self) -> int:
        return struct.unpack_from(">i", self.page_ba, 4)[0]

    @size.setter
    def size(self, size: int):
        struct.pack_into(">i", self.page_ba, 4, size)

    @property
    def offset(self) -> int:
        return struct.unpack_from(">i", self.page_ba, 0)[0]

    @offset.setter
    def offset(self, offset: int):
        struct.pack_into(">i", self.page_ba, 0, offset)



    def has_space_int(self, size: int) -> bool:
        return self.offset - (PAGE_HEADER_SIZE + self.size * SLOT_SIZE) >= size + SLOT_SIZE

    def has_space_two_int(self, size1: int, size2: int) -> bool:
        return self.offset - (PAGE_HEADER_SIZE + self.size * SLOT_SIZE) >= size1 + size2 + SLOT_SIZE * 2

    def _slot_offset(self, slot_id: int) -> int:
        if slot_id < 0 or slot_id >= self.size:
            raise RuntimeError(
                "index out of range")  # else if deleted TODO
        return PAGE_HEADER_SIZE + slot_id * SLOT_SIZE

    def get_kv_by_slot_id(self, slot_id: int):  #
        if slot_id < 0 or slot_id >= self.size:
            raise IndexError("slot_id is out of range")
        slot_offset = self._slot_offset(slot_id)
        offset, size = struct.unpack_from(SLOT_FORMAT, self.page_ba, slot_offset)
        if offset < PAGE_HEADER_SIZE or size < 0 or offset + size > self.page_size:
            raise ValueError("Page offset and/or size are out of range")
        record_data = bytes(self.page_ba[offset: offset + size])
        return self.serializer.deserialize(record_data)

    def set_by_slot_id_same_size(self, slot_id: int,
                                 kv: KV):  #solo para uso interno
        if slot_id < 0 or slot_id >= self.size:  # else if deleted maybe
            raise RuntimeError("slot_id is out of range")
        slot_offset = self._slot_offset(slot_id)
        offset, size = struct.unpack_from(SLOT_FORMAT, self.page_ba, slot_offset)
        kv_data = self.serializer.serialize(kv)
        if len(kv_data) != size:
            raise RuntimeError("KV calculated size and record size stored on slot do not match")
        self.page_ba[offset: (offset + size)] = kv_data #ahora size sí incluye el bool de deleted

    def insert(self,
               kv: KV):  # notar que siempre se inserta al final
        """
        Inserta un registro en el primer slot libre al final de la
        pagina y retorna su slot_id.
        """
        kv_bytes = self.serializer.serialize(
            kv)  # No uso get_size_of para no tener que recalcular record_bytes
        kv_size = len(kv_bytes)
        if not self.has_space_int(kv_size):
            return -1

        slot_id = self.size
        self.size += 1

        self.offset -= kv_size

        struct.pack_into(SLOT_FORMAT, self.page_ba, PAGE_HEADER_SIZE+(self.size-1)*SLOT_SIZE, self.offset, kv_size)
        self.slots.append((self.offset, kv_size))

        self.page_ba[self.offset: self.offset + len(kv_bytes)] = kv_bytes
        return slot_id

    def delete_all_permanently(self, clear_header: bool = False):
        previous_local_depth = self.local_depth
        previous_next_bucket_page = self.next_bucket_page
        self.page_ba[:] = b"\x00" * self.page_size
        self.slots = []
        self.size = 0
        if not clear_header:
            self.offset = self.page_size
            self.local_depth = previous_local_depth
            self.next_bucket_page = previous_next_bucket_page



    def delete_slot(self,
                    slot_id: int) -> bool:
        kv = self.get_kv_by_slot_id(slot_id)
        if kv is None or kv.deleted:
            return False
        kv.deleted = True
        self.set_by_slot_id_same_size(slot_id, kv)
        return True

    def compact(self):
        kvlist = []
        for i in range(self.size):
            kv = self.get_kv_by_slot_id(i)
            if kv is None:
                raise ValueError("Occupied slot id does not return a KV or slot numer and size do not match")
            if not kv.deleted:
                kvlist.append(kv)
        self.delete_all_permanently(clear_header=False)
        for item in kvlist:
            if self.insert(item) == -1:
                raise ValueError("Error while trying to reinsert elements into page")





class HashIndex:
    bucket_count: int
    column_name: str
    table_name: str
    key_format: str
    key_variable: bool
    max_bucket_size: int
    seed: int
    depth: int
    n_pages: int
    n_directory_pages: int

    @property
    def max_capacity(self):
        return 1 << self.depth

    @property
    def max_directory_page_capacity(self):
        return (PAGE_SIZE//DIRECTORY_ENTRY_SIZE) * self.n_directory_pages # 1024 * self.n_directory_pages

    def __init__(self, table_name, column_name, key_format, key_variable, buffer_manager, max_bucket_size, depth, seed, file_manager=None): #TODO QUE DEJEN DE SER CLASES BUCKET
        self.table_name = table_name
        self.column_name = column_name
        self.max_bucket_size = max_bucket_size
        self.depth = depth
        self.seed = seed
        self.bucket_count = 0
        self.buffer_manager = buffer_manager
        # En la arquitectura actual el buffer pool es global (singleton), asi
        # que el indice guarda aparte el FileManager de SU archivo para
        # identificar sus paginas: misma idea que Table/B+.
        if file_manager is None:
            file_manager = getattr(buffer_manager, "active_file", None)
        if file_manager is None:
            raise ValueError(
                "HashIndex necesita un FileManager: pasalo o registra uno "
                "en el buffer pool"
            )
        self.file_manager = file_manager
        self.key_format = key_format
        self.key_variable = key_variable
        self.serializer = KVSerializer(self.key_format, self.key_variable)
        self.n_pages = 0
        self.n_directory_pages = 0
        self.file_manager.allocate_page() #para la página 0
        metadata_page = self._load_metadata_page()
        for i in range(0, (self.max_capacity + 1023) // 1024):
            directory_page_id = self._append_directory_page()
            metadata_page.set(i, directory_page_id)
        for i in range(self.max_capacity):
            self._append_bucket(i, self.depth, is_overflow=False) #ya actualiza directory
        self.buffer_manager.mark_dirty(0, self.file_manager)
        self.buffer_manager.unpin_page(0, self.file_manager)



    def _locate_bucket(self, index: int) -> int | None:
        if self.max_directory_page_capacity <= index:
            raise IndexError("Bucket index out of range")
        metadata_page = self._load_metadata_page()
        directory_page_id = metadata_page.get(index // 1024)
        directory_page = self._load_directory_page(directory_page_id)
        output = directory_page.get_first(index % 1024)
        self.buffer_manager.unpin_page(0, self.file_manager)
        self.buffer_manager.unpin_page(directory_page_id, self.file_manager)
        return output

    def _locate_last_bucket(self, index: int) -> int | None:
        if self.max_directory_page_capacity <= index:
            raise IndexError("Bucket index out of range")
        metadata_page = self._load_metadata_page()
        directory_page_id = metadata_page.get(index // 1024)
        directory_page = self._load_directory_page(directory_page_id)
        output = directory_page.get_second(index % 1024)
        self.buffer_manager.unpin_page(0, self.file_manager)
        self.buffer_manager.unpin_page(directory_page_id, self.file_manager)
        return output

    def _set_bucket_page(self, index: int, page_id: int):
        if self.max_directory_page_capacity <= index:
            raise IndexError("Bucket index out of range")
        metadata_page = self._load_metadata_page()
        directory_page_id = metadata_page.get(index // 1024)
        directory_page = self._load_directory_page(directory_page_id)
        try:
            directory_page.set_first(index % 1024, page_id)
            self.buffer_manager.mark_dirty(directory_page_id, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(0, self.file_manager)
            self.buffer_manager.unpin_page(directory_page_id, self.file_manager)

    def _set_last_bucket_page(self, index: int, page_id: int):
        if self.max_directory_page_capacity <= index:
            raise IndexError("Bucket index out of range")
        metadata_page = self._load_metadata_page()
        directory_page_id = metadata_page.get(index // 1024)
        directory_page = self._load_directory_page(directory_page_id)
        try:
            directory_page.set_second(index % 1024, page_id)
            self.buffer_manager.mark_dirty(directory_page_id, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(0, self.file_manager)
            self.buffer_manager.unpin_page(directory_page_id, self.file_manager)

    def _split_bucket(self, bucket_to_split: int): #Cuando se llame a esta función, el bucket no puede ser de máximo depth, sino bota error
        old_page_id = self._locate_bucket(bucket_to_split)
        if old_page_id is None:
            raise IndexError("Bucket not found")
        bucket = self._load_bucket(old_page_id, is_new=False)
        bucket_depth = bucket.local_depth
        if bucket_depth == self.depth or bucket.next_bucket_page != -1: #se podría usar >= por seguridad
            raise IndexError("Bucket has already been split to its maximum value before rehash")
        bucket_index = bucket_to_split & ((1 << bucket_depth) - 1)
        new_location_index = bucket_index + (1 << bucket_depth)
        list1 = []
        list2 = []
        for i in range(bucket.size):
            item = bucket.get_kv_by_slot_id(i)
            hashed_key = hash_key(item.key, self.key_format, self.seed)
            if (hashed_key & ((1 << bucket_depth) - 1)) != bucket_index:
                raise RuntimeError("Hash function does not match the bucket being split")
            if (hashed_key & (1 << bucket_depth)) == 0:
                list1.append(item)
            else:
                list2.append(item)

        new_page_id = self._append_bucket(new_location_index, bucket.local_depth+1, is_overflow=False) #esto ya hace ++bucket_count
        new_bucket = self._load_bucket(new_page_id, is_new=False)

        try:
            bucket.delete_all_permanently(clear_header=False)
            for item in list1:
                if bucket.insert(item) == -1:
                    raise RuntimeError("New version of old bucket does not have enough space to fit values from itself after clearance")
            for item in list2:
                if new_bucket.insert(item) == -1:
                    raise RuntimeError("New bucket does not have enough space to fit values from new bucket")
            bucket.local_depth = bucket.local_depth + 1
            step = 1 << new_bucket.local_depth
            start = new_location_index & (step - 1)  # bitwise and sugerido por ia, aunque viene bien
            for i in range(start, self.max_capacity, step):
                #self.bucket_locations[i] = new_page_id
                self._set_bucket_page(i, new_page_id)
                self._set_last_bucket_page(i, new_page_id)

            if bucket.local_depth != new_bucket.local_depth:
                raise ValueError("Local depths of newly split buckets do not match")

            self.buffer_manager.mark_dirty(new_page_id, self.file_manager)
            self.buffer_manager.mark_dirty(old_page_id, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(new_page_id, self.file_manager)
            self.buffer_manager.unpin_page(old_page_id, self.file_manager)


    def _add_overflow_bucket(self, bucket_number: int):
        old_page_id = self._locate_last_bucket(bucket_number)
        old_bucket = self._load_bucket(old_page_id, is_new=False)
        new_page_id = self._append_bucket(bucket_number, old_bucket.local_depth, is_overflow=True)
        old_bucket.next_bucket_page = new_page_id
        self._set_last_bucket_page(bucket_number, new_page_id)
        self.buffer_manager.mark_dirty(old_page_id, self.file_manager)
        self.buffer_manager.unpin_page(old_page_id, self.file_manager)


    def _double_in_size(self):
        oldmax = self.max_capacity
        self.depth += 1
        for i in range(oldmax):
            if i+oldmax >= self.max_directory_page_capacity:
                self._append_directory_page()
            bucket_location = self._locate_bucket(i)
            last_bucket_location = self._locate_last_bucket(i)
            self._set_bucket_page(i+oldmax, bucket_location)
            self._set_last_bucket_page(i+oldmax, last_bucket_location) #ineficiente


    def _load_bucket(self, page_id: int, is_new: bool=False) -> VariableBucketPage:
        """
        Obtiene una pagina del BufferManager y crea una interfaz
        VariablePage sobre el bytearray almacenado en el frame.
        """
        if page_id is None:
            raise IndexError("Bucket not found")
        page_ba = self.buffer_manager.fetch_page(page_id, self.file_manager)
        return VariableBucketPage(page_ba, PAGE_SIZE, self.serializer, is_new)


    def _load_directory_page(self, page_id: int) -> DirectoryPage:
        if page_id is None:
            raise IndexError("Directory page not found")
        page_ba = self.buffer_manager.fetch_page(page_id, self.file_manager)
        return DirectoryPage(page_ba)

    def _load_metadata_page(self):
        page_ba = self.buffer_manager.fetch_page(0, self.file_manager)
        return MetadataPage(page_ba)


    def _append_bucket(self, new_bucket_number: int, local_depth: int, is_overflow: bool = False) -> int:

        page_id = self.file_manager.allocate_page()
        self.n_pages += 1
        new_bucket_size = PAGE_SIZE # cambiar por self.page_size eventualmente
        if not is_overflow:
            self._set_bucket_page(new_bucket_number, page_id)
            self._set_last_bucket_page(new_bucket_number, page_id)
            self.bucket_count += 1
        bucket = self._load_bucket(page_id, is_new=True)
        try:
            bucket.size = 0
            bucket.offset = new_bucket_size
            bucket.local_depth = local_depth
            bucket.next_bucket_page = -1
            self.buffer_manager.mark_dirty(page_id, self.file_manager)  # página física del nuevo bucket
        finally:
            self.buffer_manager.unpin_page(page_id, self.file_manager)
        return page_id


    def _append_directory_page(self):
        phys_page_id = self.file_manager.allocate_page()
        metadata_page = self._load_metadata_page()
        metadata_page.set(self.n_directory_pages, phys_page_id)
        self.n_directory_pages += 1
        try:
            self.buffer_manager.mark_dirty(0, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(0, self.file_manager)
        return phys_page_id


    def _get_kv(self, bucket_page: int, slot_id: int):
        if bucket_page is None:
            return None
        bucket = self._load_bucket(bucket_page, is_new=False)
        try:
            return bucket.get_kv_by_slot_id(slot_id)
        finally:
            self.buffer_manager.unpin_page(bucket_page, self.file_manager)


    def _iter_kvs_in_bucket(self, bucket_number: int):
        bucket_page = self._locate_bucket(bucket_number)
        if bucket_page is None:
            raise IndexError("Bucket redirection from locator dictionary is wrong or capacity is too big")

        while bucket_page != -1:
            bucket = self._load_bucket(bucket_page, is_new=False)
            bucket_size = bucket.size
            next_bucket_page = bucket.next_bucket_page
            try:
                for i in range(bucket_size):
                    yield bucket.get_kv_by_slot_id(i)
            finally:
                self.buffer_manager.unpin_page(bucket_page, self.file_manager)
                bucket_page = next_bucket_page



    def insert(self, key, rid):
        hashed_key = hash_key(key, self.key_format, self.seed)  # hacer que se realice con la key y dado el tamaño actual de la tabla
        bucket_to_insert = hashed_key % self.max_capacity
        bucket_page = self._locate_last_bucket(bucket_to_insert)
        if bucket_page is None:
            raise IndexError("Bucket redirection from locator dictionary is wrong or capacity is too big")
        original_bucket_page = bucket_page
        bucket = self._load_bucket(original_bucket_page, is_new=False)
        kv = KV(key, rid, False)
        kv_size = self.serializer.get_size_of(kv)
        if kv_size > MAX_KV_SIZE:
            raise IndexError("KV size is too big")

        if bucket.size >= self.max_bucket_size or (not bucket.has_space_int(kv_size)): #considerar cambiar
            bucket.compact()
            self.buffer_manager.mark_dirty(original_bucket_page, self.file_manager)

        while bucket.size >= self.max_bucket_size or (not bucket.has_space_int(kv_size)): #considerar cambiar
            if bucket.local_depth == self.depth:
                if self.depth >= MAX_DEPTH:
                    self._add_overflow_bucket(bucket_to_insert)
                    self.buffer_manager.mark_dirty(bucket_page, self.file_manager)
                    if bucket_page != original_bucket_page: #solo si procede después de un split, porque sino ya se hace unpin al final (será == original_bucket_page)
                        self.buffer_manager.unpin_page(bucket_page, self.file_manager)
                    bucket_page = bucket.next_bucket_page
                    bucket = self._load_bucket(bucket_page, is_new=False)
                    break
                else:
                    self._double_in_size()
            self._split_bucket(bucket_to_insert)
            bucket_to_insert = hashed_key % self.max_capacity
            bucket_page_next = self._locate_last_bucket(bucket_to_insert)
            if bucket_page_next is None:
                raise RuntimeError("Somehow new bucket was either deleted or not appended by duplication of size")
            if bucket_page != bucket_page_next:
                self.buffer_manager.unpin_page(bucket_page, self.file_manager)
                bucket_page = bucket_page_next
                bucket = self._load_bucket(bucket_page, is_new=False)
        try:
            bucket.insert(kv)
            self.buffer_manager.mark_dirty(bucket_page, self.file_manager)
        finally:
            self.buffer_manager.unpin_page(bucket_page, self.file_manager)
            if original_bucket_page != bucket_page:
                self.buffer_manager.unpin_page(original_bucket_page, self.file_manager)


    def search(self, key):
        hashed_key = hash_key(key, self.key_format, self.seed)
        bucket_to_insert = hashed_key % self.max_capacity
        output = []
        bucket_page = self._locate_bucket(bucket_to_insert)
        if bucket_page is None:
            raise IndexError("Bucket redirection from locator dictionary is wrong or capacity is too big")
        for kv in self._iter_kvs_in_bucket(bucket_to_insert):
            if kv is not None and kv.key == key and (not kv.deleted):
                output.append(kv)
        return output


    def delete(self, key):
        hashed_key = hash_key(key, self.key_format, self.seed)
        bucket_to_delete = hashed_key % self.max_capacity
        bucket_page = self._locate_bucket(bucket_to_delete)
        if bucket_page is None:
            raise IndexError("Bucket redirection from locator dictionary is wrong or capacity is too big")
        deleted_count = 0
        while bucket_page != -1:
            bucket = self._load_bucket(bucket_page, is_new=False)
            try:
                for i in range(bucket.size):
                    kv = bucket.get_kv_by_slot_id(i)
                    if kv is not None and kv.key == key and (not kv.deleted):
                        bucket.delete_slot(i)
                        deleted_count += 1
                self.buffer_manager.mark_dirty(bucket_page, self.file_manager)
            finally:
                previous_bucket_page = bucket_page
                bucket_page = bucket.next_bucket_page
                self.buffer_manager.unpin_page(previous_bucket_page, self.file_manager)
        return deleted_count


    def _insert_ref(self, key, ref):
        """
        Mantenimiento desde Table.insert: inserta un par (clave, RID).
        El indice hash permite claves duplicadas (otra fila distinta puede
        tener el mismo valor), asi que cada RID es su propia entrada.
        """
        return self.insert(key, ref)


    def delete_ref(self, key, rid):
        """
        Mantenimiento desde Table.delete: borra SOLO la entrada de esta
        fila (clave, RID), en vez de todas las filas con esa clave como
        hace delete().
        """
        hashed_key = hash_key(key, self.key_format, self.seed)
        bucket_number = hashed_key % self.max_capacity
        bucket_page = self._locate_bucket(bucket_number)
        if bucket_page is None:
            raise IndexError("Bucket redirection from locator dictionary is wrong or capacity is too big")
        was_deleted = False
        while bucket_page != -1:
            bucket = self._load_bucket(bucket_page, is_new=False)
            try:
                for i in range(bucket.size):
                    kv = bucket.get_kv_by_slot_id(i)
                    if kv is not None and kv.key == key and kv.rid == rid and (not kv.deleted):
                        if bucket.delete_slot(i):
                            was_deleted = True
                self.buffer_manager.mark_dirty(bucket_page, self.file_manager)
            finally:
                previous_bucket_page = bucket_page
                bucket_page = bucket.next_bucket_page
                self.buffer_manager.unpin_page(previous_bucket_page, self.file_manager)
        return was_deleted


    def close(self):
        """
        Cierra el archivo del indice: persiste sus paginas modificadas y
        las descarta del buffer pool global.
        """
        self.buffer_manager.close(self.file_manager)
