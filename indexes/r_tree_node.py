import struct
from collections import namedtuple

from indexes.b_tree_leaf_page import RID
from spatial.geometry import Point, Rectangle

PAGE_SIZE = 4096

HEADER_FORMAT = ">IH?"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

MBR_FORMAT = ">dddd"
MBR_SIZE = struct.calcsize(MBR_FORMAT)

# una hoja indexa puntos, no geometrias con area -- guardar un MBR completo
# (min==max en ambos ejes) por cada punto desperdicia la mitad del espacio
# de la entrada. Alcanza con (x, y).
LEAF_POINT_FORMAT = ">dd"
LEAF_POINT_SIZE = struct.calcsize(LEAF_POINT_FORMAT)

LEAF_REF_FORMAT = ">ii"
LEAF_REF_SIZE = struct.calcsize(LEAF_REF_FORMAT)
LEAF_ENTRY_SIZE = LEAF_POINT_SIZE + LEAF_REF_SIZE
LEAF_M = (PAGE_SIZE - HEADER_SIZE) // LEAF_ENTRY_SIZE
LEAF_m = max(2, LEAF_M // 2)

INTERNAL_REF_FORMAT = ">I"
INTERNAL_REF_SIZE = struct.calcsize(INTERNAL_REF_FORMAT)
INTERNAL_ENTRY_SIZE = MBR_SIZE + INTERNAL_REF_SIZE
INTERNAL_M = (PAGE_SIZE - HEADER_SIZE) // INTERNAL_ENTRY_SIZE
INTERNAL_m = max(2, INTERNAL_M // 2)

# entry de hoja: el dato indexado es un punto (x, y), no un MBR -- ref es un
# RID hacia el registro real (HeapFile).
LeafEntry = namedtuple("LeafEntry", ["point", "ref"])

# entry de nodo interno: ref es el child_page_id -- el MBR del hijo ya va
# inline aca, bajar al hijo para saber su propio MBR no hace falta nunca.
InternalEntry = namedtuple("InternalEntry", ["mbr", "ref"])


class RTreeNode:

    def __init__(self, page_id, is_leaf=True, data=None):
        self.page_id = page_id
        if data is None:
            self.is_leaf = is_leaf
            self.entries = []
            self.data = bytearray(PAGE_SIZE)
            self._write_header()
        else:
            self.data = bytearray(data)
            _, n_entries, self.is_leaf = struct.unpack_from(HEADER_FORMAT, self.data, 0)
            self.entries = self._read_entries(n_entries)

    @property
    def max_entries(self):
        return LEAF_M if self.is_leaf else INTERNAL_M

    @property
    def min_entries(self):
        return LEAF_m if self.is_leaf else INTERNAL_m

    def _entry_size(self):
        return LEAF_ENTRY_SIZE if self.is_leaf else INTERNAL_ENTRY_SIZE

    def _read_entries(self, n_entries):
        entry_size = self._entry_size()
        entries = []
        offset = HEADER_SIZE
        if self.is_leaf:
            for _ in range(n_entries):
                x, y = struct.unpack_from(LEAF_POINT_FORMAT, self.data, offset)
                ref = RID(*struct.unpack_from(LEAF_REF_FORMAT, self.data, offset + LEAF_POINT_SIZE))
                entries.append(LeafEntry(Point(x, y), ref))
                offset += entry_size
        else:
            for _ in range(n_entries):
                min_x, min_y, max_x, max_y = struct.unpack_from(MBR_FORMAT, self.data, offset)
                (child_page_id,) = struct.unpack_from(INTERNAL_REF_FORMAT, self.data, offset + MBR_SIZE)
                entries.append(InternalEntry(Rectangle(min_x, min_y, max_x, max_y), child_page_id))
                offset += entry_size
        return entries

    def _write_header(self):
        struct.pack_into(HEADER_FORMAT, self.data, 0, self.page_id, len(self.entries), self.is_leaf)

    def flush(self):
        # Serializa self.entries a self.data. Solo es seguro llamarlo cuando
        # len(self.entries) <= max_entries -- mientras se decide un split,
        # self.entries puede tener transitoriamente una entrada de mas, que
        # no entraria en el buffer de PAGE_SIZE bytes. Por eso insert_entry/
        # delete_at/replace_entry no llaman a esto solas: el caller
        # (RTreeBase._save_node) lo hace recien antes de persistir, momento
        # en el que un split ya redujo las entradas al tope real.
        entry_size = self._entry_size()
        offset = HEADER_SIZE
        if self.is_leaf:
            for entry in self.entries:
                struct.pack_into(LEAF_POINT_FORMAT, self.data, offset, entry.point.x, entry.point.y)
                struct.pack_into(LEAF_REF_FORMAT, self.data, offset + LEAF_POINT_SIZE,
                                  entry.ref.page_id, entry.ref.slot_id)
                offset += entry_size
        else:
            for entry in self.entries:
                mbr = entry.mbr
                struct.pack_into(MBR_FORMAT, self.data, offset, mbr.min_x, mbr.min_y, mbr.max_x, mbr.max_y)
                struct.pack_into(INTERNAL_REF_FORMAT, self.data, offset + MBR_SIZE, entry.ref)
                offset += entry_size
        self._write_header()

    def is_full(self):
        return len(self.entries) > self.max_entries

    def is_underflow(self):
        return len(self.entries) < self.min_entries

    def mbr(self):
        if not self.entries:
            return None
        if self.is_leaf:
            return Rectangle.from_points([entry.point for entry in self.entries])
        result = self.entries[0].mbr
        for entry in self.entries[1:]:
            result = result.union(entry.mbr)
        return result

    def insert_entry(self, entry):
        # No rechaza por capacidad ni serializa -- el caller (RTreeBase)
        # decide si hace falta split despues de insertar (mientras tanto
        # self.entries puede tener una entrada de mas que no entra en
        # PAGE_SIZE bytes), y flush() recien se llama al persistir.
        self.entries.append(entry)

    def delete_at(self, index):
        del self.entries[index]

    def replace_entry(self, index, entry):
        self.entries[index] = entry

    def _region_of(self, entry):
        # Region (Rectangle) usada solo durante el split para comparar area/
        # enlargement -- para una hoja, un punto es un rectangulo de area 0.
        # No se guarda materializada en la entry (eso es justo lo que se
        # evita: un leaf solo persiste el punto, 16 bytes en vez de 32).
        return entry.mbr if not self.is_leaf else Rectangle.from_point(entry.point)

    def quadratic_split(self, new_page_id):
        # Algoritmo de Guttman (1984): elegir los dos "seeds" que
        # desperdiciarian mas area si se agruparan juntos, y repartir el
        # resto por minima expansion de area, forzando el minimo m si a
        # algun grupo le faltan entradas para completarlo.
        paired = [(entry, self._region_of(entry)) for entry in self.entries]
        seed1, seed2 = _pick_seeds([region for _, region in paired])
        group1 = [paired[seed1]]
        group2 = [paired[seed2]]
        remaining = [p for i, p in enumerate(paired) if i not in (seed1, seed2)]
        mbr1 = group1[0][1]
        mbr2 = group2[0][1]
        m = self.min_entries

        while remaining:
            if len(group1) + len(remaining) <= m:
                group1.extend(remaining)
                remaining = []
                break
            if len(group2) + len(remaining) <= m:
                group2.extend(remaining)
                remaining = []
                break
            idx, target = _pick_next([region for _, region in remaining], mbr1, mbr2)
            pair = remaining.pop(idx)
            if target == 1:
                group1.append(pair)
                mbr1 = mbr1.union(pair[1])
            else:
                group2.append(pair)
                mbr2 = mbr2.union(pair[1])

        self.entries = [entry for entry, _ in group1]
        new_node = RTreeNode(new_page_id, is_leaf=self.is_leaf)
        new_node.entries = [entry for entry, _ in group2]
        return new_node


def _pick_seeds(regions):
    best_pair = (0, 1)
    best_waste = -1
    for i in range(len(regions)):
        for j in range(i + 1, len(regions)):
            union_area = regions[i].union(regions[j]).area()
            waste = union_area - regions[i].area() - regions[j].area()
            if waste > best_waste:
                best_waste = waste
                best_pair = (i, j)
    return best_pair


def _pick_next(remaining_regions, mbr1, mbr2):
    best_idx = 0
    best_pref = -1
    best_target = 1
    for idx, region in enumerate(remaining_regions):
        d1 = mbr1.enlargement(region)
        d2 = mbr2.enlargement(region)
        pref = abs(d1 - d2)
        if pref > best_pref:
            best_pref = pref
            best_idx = idx
            if d1 < d2:
                best_target = 1
            elif d2 < d1:
                best_target = 2
            else:
                best_target = 1 if mbr1.area() <= mbr2.area() else 2
    return best_idx, best_target
