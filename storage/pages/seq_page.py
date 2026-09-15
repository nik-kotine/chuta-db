from storage.seq_record import Record

class Page:
    """
    Página de almacenamiento. Define la interfaz común que comparten
    FixedPage y VariablePage.
    """
    def __init__(self, page_ba: bytearray, page_size: int, serializer):
        self.page_ba = page_ba
        self.page_size = page_size
        self.serializer = serializer

    @property
    def n_records(self) -> int:
        raise NotImplementedError

    def has_space(self, size=None) -> bool:
        raise NotImplementedError

    def get_record(self, slot_id: int) -> Record | None:
        raise NotImplementedError

    def set_record(self, slot_id: int, record: Record):
        raise NotImplementedError

    def insert(self, record: Record) -> int:
        raise NotImplementedError

    def delete_slot(self, slot_id: int) -> bool:
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError

    def ensure_initialized(self) -> bool:
        return False