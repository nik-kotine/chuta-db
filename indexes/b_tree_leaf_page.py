import struct
from collections import namedtuple
from indexes.b_tree_key_codec import encode_key, decode_key, MAX_KEY_SIZE

PAGE_SIZE = 4096
HEADER_FORMAT = ">IHIH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
NULL_LEAF = 0
DIR_FORMAT = ">HHii"
DIR_SIZE = struct.calcsize(DIR_FORMAT)
MAX_ENTRY_SIZE = DIR_SIZE + MAX_KEY_SIZE
MAX_ENTRIES = (PAGE_SIZE - HEADER_SIZE) // MAX_ENTRY_SIZE
CAPACITY_BYTES = PAGE_SIZE - HEADER_SIZE
MIN_USED_BYTES = CAPACITY_BYTES // 4

RID = namedtuple("RID", ["page_id", "slot_id"])

class BTreeLeafPage:

    def __init__(self, page_id: int, data: bytearray = None):
        if data is None:
            self.data = bytearray(PAGE_SIZE)
            self.page_id = page_id
            self.n_entries = 0
            self.next_leaf_id = NULL_LEAF
            self.free_space_high = PAGE_SIZE
            self.save_header()
        else:
            self.data = data
            self.load_header()

    def save_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, self.n_entries, self.next_leaf_id, self.free_space_high)

    def load_header(self):
        self.page_id, self.n_entries, self.next_leaf_id, self.free_space_high = struct.unpack_from(HEADER_FORMAT, self.data, 0)

    def _dir_offset(self, index: int) -> int:
        return HEADER_SIZE + index * DIR_SIZE

    def _read_dir(self, index: int):
        return struct.unpack_from(DIR_FORMAT, self.data, self._dir_offset(index))

    def _read_entry(self, index: int):
        key_offset, key_len, ref_page_id, ref_slot_id = self._read_dir(index)
        key = decode_key(self.data[key_offset:key_offset + key_len])
        return key, RID(ref_page_id, ref_slot_id)

    def _find_index(self, key) -> int:
        lo, hi = 0, self.n_entries
        while lo < hi:
            mid = (lo + hi) // 2
            mid_key, _ = self._read_entry(mid)
            if mid_key < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def find(self, key) -> RID | None:
        index = self._find_index(key)
        if index < self.n_entries:
            found_key, ref = self._read_entry(index)
            if found_key == key:
                return ref
        return None

    def _all_entries(self) -> list:
        return [self._read_entry(i) for i in range(self.n_entries)]

    def used_bytes(self) -> int:
        return self.n_entries * DIR_SIZE + (PAGE_SIZE - self.free_space_high)

    def has_space(self, key) -> bool:
        needed = DIR_SIZE + len(encode_key(key))
        free = self.free_space_high - self._dir_offset(self.n_entries)
        return free >= needed

    def _rewrite(self, entries: list):
        cursor = PAGE_SIZE
        for index, (key, ref) in enumerate(entries):
            encoded = encode_key(key)
            cursor -= len(encoded)
            self.data[cursor:cursor + len(encoded)] = encoded
            struct.pack_into(DIR_FORMAT, self.data, self._dir_offset(index), cursor, len(encoded), ref.page_id, ref.slot_id)

        self.n_entries = len(entries)
        self.free_space_high = cursor
        self.save_header()

    def insert(self, key, ref: RID) -> bool:
        if not self.has_space(key):
            return False

        entries = self._all_entries()
        entries.insert(self._find_index(key), (key, ref))
        self._rewrite(entries)
        return True

    def delete(self, key) -> bool:
        index = self._find_index(key)
        if index >= self.n_entries:
            return False

        found_key, _ = self._read_entry(index)
        if found_key != key:
            return False

        entries = self._all_entries()
        del entries[index]
        self._rewrite(entries)
        return True

    def split(self, new_page_id: int):
        entries = self._all_entries()
        mid = len(entries) // 2

        new_page = BTreeLeafPage(new_page_id)
        new_page.next_leaf_id = self.next_leaf_id
        new_page._rewrite(entries[mid:])

        self.next_leaf_id = new_page_id
        self._rewrite(entries[:mid])

        split_key, _ = new_page._read_entry(0)
        return split_key, new_page

    def is_underflow(self) -> bool:
        return self.used_bytes() < MIN_USED_BYTES

    def can_lend(self) -> bool:
        if self.n_entries <= 1:
            return False
        return self.used_bytes() - MAX_ENTRY_SIZE >= MIN_USED_BYTES

    def borrow_from_left(self, left_sibling: "BTreeLeafPage"):
        left_entries = left_sibling._all_entries()
        borrowed = left_entries.pop()
        left_sibling._rewrite(left_entries)

        entries = self._all_entries()
        entries.insert(0, borrowed)
        self._rewrite(entries)

        return borrowed[0]

    def borrow_from_right(self, right_sibling: "BTreeLeafPage"):
        right_entries = right_sibling._all_entries()
        borrowed = right_entries.pop(0)
        right_sibling._rewrite(right_entries)

        entries = self._all_entries()
        entries.append(borrowed)
        self._rewrite(entries)

        return right_entries[0][0]

    def merge_with_right(self, right_sibling: "BTreeLeafPage"):
        entries = self._all_entries() + right_sibling._all_entries()
        self.next_leaf_id = right_sibling.next_leaf_id
        self._rewrite(entries)
