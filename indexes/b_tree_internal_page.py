import struct
from indexes.b_tree_key_codec import encode_key, decode_key, MAX_KEY_SIZE

PAGE_SIZE = 4096
HEADER_FORMAT = ">IHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
KEY_DIR_FORMAT = ">HH"
KEY_DIR_SIZE = struct.calcsize(KEY_DIR_FORMAT)
CHILD_FORMAT = ">I"
CHILD_SIZE = struct.calcsize(CHILD_FORMAT)
MAX_ENTRY_SIZE = KEY_DIR_SIZE + CHILD_SIZE + MAX_KEY_SIZE
MAX_KEYS = (PAGE_SIZE - HEADER_SIZE - CHILD_SIZE) // MAX_ENTRY_SIZE
CAPACITY_BYTES = PAGE_SIZE - HEADER_SIZE - CHILD_SIZE
MIN_USED_BYTES = CAPACITY_BYTES // 4

class BTreeInternalPage:

    def __init__(self, page_id: int, data: bytearray = None):
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self.page_id = page_id
            self.n_keys = 0
            self.free_space_high = PAGE_SIZE
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.n_keys, self.free_space_high)

    def load_header(self):
        self.page_id, self.n_keys, self.free_space_high = struct.unpack_from(HEADER_FORMAT, self.data, 0)

    def _key_dir_offset(self, index: int) -> int:
        return HEADER_SIZE + index * KEY_DIR_SIZE

    def _children_base(self) -> int:
        return HEADER_SIZE + self.n_keys * KEY_DIR_SIZE

    def _child_offset(self, index: int) -> int:
        return self._children_base() + index * CHILD_SIZE

    def _read_key(self, index: int):
        key_offset, key_len = struct.unpack_from(KEY_DIR_FORMAT, self.data, self._key_dir_offset(index))
        return decode_key(self.data[key_offset:key_offset + key_len])

    def _read_child(self, index: int) -> int:
        return struct.unpack_from(CHILD_FORMAT, self.data, self._child_offset(index))[0]

    def _write_child(self, index: int, child_page_id: int):
        struct.pack_into(CHILD_FORMAT, self.data, self._child_offset(index), child_page_id)

    def _all_keys(self) -> list:
        return [self._read_key(i) for i in range(self.n_keys)]

    def _all_children(self) -> list:
        return [self._read_child(i) for i in range(self.n_keys + 1)]

    def used_bytes(self) -> int:
        return self.n_keys * KEY_DIR_SIZE + (self.n_keys + 1) * CHILD_SIZE + (PAGE_SIZE - self.free_space_high)

    def has_space(self, key) -> bool:
        needed = KEY_DIR_SIZE + CHILD_SIZE + len(encode_key(key))
        free = self.free_space_high - (self._children_base() + (self.n_keys + 1) * CHILD_SIZE)
        return free >= needed

    def _rewrite(self, keys: list, children: list):
        assert len(children) == len(keys) + 1

        cursor = PAGE_SIZE
        encoded_keys = []
        for key in keys:
            encoded = encode_key(key)
            cursor -= len(encoded)
            encoded_keys.append((cursor, encoded))

        for index, (offset, encoded) in enumerate(encoded_keys):
            self.data[offset:offset + len(encoded)] = encoded
            struct.pack_into(KEY_DIR_FORMAT, self.data, self._key_dir_offset(index), offset, len(encoded))

        self.n_keys = len(keys)

        children_base = self._children_base()
        for index, child in enumerate(children):
            struct.pack_into(CHILD_FORMAT, self.data, children_base + index * CHILD_SIZE, child)

        self.free_space_high = cursor
        self.save_header()

    def _write_key(self, index: int, key):
        keys = self._all_keys()
        children = self._all_children()
        keys[index] = key
        self._rewrite(keys, children)

    def _find_key_index(self, key) -> int:
        lo, hi = 0, self.n_keys
        while lo < hi:
            mid = (lo + hi) // 2
            if key < self._read_key(mid):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def find_child_index(self, key) -> int:
        lo, hi = 0, self.n_keys
        while lo < hi:
            mid = (lo + hi) // 2
            if key < self._read_key(mid):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def find_child(self, key) -> int:
        return self._read_child(self.find_child_index(key))

    def find_leftmost_child_index(self, key) -> int:
        lo, hi = 0, self.n_keys
        while lo < hi:
            mid = (lo + hi) // 2
            if self._read_key(mid) < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def find_leftmost_child(self, key) -> int:
        return self._read_child(self.find_leftmost_child_index(key))


    def insert_key(self, key, right_child_page_id: int) -> bool:
        if not self.has_space(key):
            return False

        keys = self._all_keys()
        children = self._all_children()
        index = self._find_key_index(key)

        keys.insert(index, key)
        children.insert(index + 1, right_child_page_id)
        self._rewrite(keys, children)
        return True

    def init_as_root(self, left_child_page_id: int, key, right_child_page_id: int):
        self._rewrite([key], [left_child_page_id, right_child_page_id])


    def split(self, new_page_id: int):
        keys = self._all_keys()
        children = self._all_children()
        mid = len(keys) // 2
        pushed_up_key = keys[mid]

        new_page = BTreeInternalPage(new_page_id)
        new_page._rewrite(keys[mid + 1:], children[mid + 1:])

        self._rewrite(keys[:mid], children[:mid + 1])

        return pushed_up_key, new_page

    def is_underflow(self) -> bool:
        return self.used_bytes() < MIN_USED_BYTES

    def can_lend(self) -> bool:
        if self.n_keys <= 1:
            return False
        return self.used_bytes() - MAX_ENTRY_SIZE >= MIN_USED_BYTES

    def delete_key_at(self, index: int):
        keys = self._all_keys()
        children = self._all_children()
        del keys[index]
        del children[index + 1]
        self._rewrite(keys, children)

    def borrow_from_left(self, left_sibling: "BTreeInternalPage", separator_key) :
        left_keys = left_sibling._all_keys()
        left_children = left_sibling._all_children()
        borrowed_key = left_keys[-1]
        borrowed_child = left_children[-1]
        left_sibling._rewrite(left_keys[:-1], left_children[:-1])

        keys = self._all_keys()
        children = self._all_children()
        keys.insert(0, separator_key)
        children.insert(0, borrowed_child)
        self._rewrite(keys, children)

        return borrowed_key

    def borrow_from_right(self, right_sibling: "BTreeInternalPage", separator_key):
        borrowed_key = right_sibling._read_key(0)
        borrowed_child = right_sibling._read_child(0)

        right_sibling.delete_key_at(0)

        keys = self._all_keys()
        children = self._all_children()
        keys.append(separator_key)
        children.append(borrowed_child)
        self._rewrite(keys, children)

        return borrowed_key

    def merge_with_right(self, right_sibling: "BTreeInternalPage", separator_key):
        keys = self._all_keys() + [separator_key] + right_sibling._all_keys()
        children = self._all_children() + right_sibling._all_children()
        self._rewrite(keys, children)
