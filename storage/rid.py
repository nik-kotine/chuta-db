import struct
from collections import namedtuple

"""
rid: (page_id, slot_id)
page_id representa en que pagina se encuentra (0+, 0 siendo overflow) y
slot_id el numero de registro dentro de esa pagina. (-1, -1) es un registro nulo.
"""
RID = namedtuple("RID", ["page_id", "slot_id"])
NULL_RID = RID(-1, -1)

RID_FORMAT = "ii"
RID_SIZE = struct.calcsize(RID_FORMAT)

"""
deleted: bool
Representa si el registro fue marcado como eliminado
"""
DELETED_FORMAT = "?"
DELETED_SIZE = struct.calcsize(DELETED_FORMAT)