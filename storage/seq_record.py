from storage.rid import RID

class Record:
    """
    Representa un registro lógico en memoria para el SequentialFile.
    Contiene los datos (params) y la metadata de la lista enlazada (next_rid y deleted).
    """
    def __init__(self, params: list | tuple, next_rid: RID | None = None, deleted: bool = False):
        self.params = params
        self.next_rid = next_rid
        self.deleted = deleted