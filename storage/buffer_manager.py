from storage.file_manager import FileManager

class Frame:

    def __init__(self):
        self.file_manager: FileManager | None = None
        self.phys_page_id: int          = -1
        self.page_bin: bytearray | None = None
        self.original_page: bytes | None = None
        self.pin_count: int             = 0
        self.dirty: bool                = False
        self.reference: bool            = False

class BufferManager:

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(BufferManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, file_manager: FileManager = None, max_frames: int = 10):
        if getattr(self, "_initialized", False):
            self._register_file(file_manager)
            return

        self._initialized = True
        self.active_file: FileManager | None = file_manager
        self.frames = [Frame() for _ in range(max_frames)]
        self.max_frames = max_frames
        self.page_table = {}
        self.clock_hand = 0
        self._known_files = []
        self._wal_logger = None
        self._transaction_id_provider = None

        self._register_file(file_manager)

    @classmethod
    def get_instance(cls) -> "BufferManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _register_file(self, file_manager):
        if file_manager is not None:
            self.active_file = file_manager
            if not any(fm is file_manager for fm in self._known_files):
                self._known_files.append(file_manager)

    def configure_wal(self, wal_logger, transaction_id_provider):
        self._wal_logger = wal_logger
        self._transaction_id_provider = transaction_id_provider

    def _resolve_file(self, file_manager) -> FileManager:
        return file_manager if file_manager is not None else self.active_file

    @staticmethod
    def _is_closed(file_manager) -> bool:
        file_ptr = getattr(file_manager, "file_ptr", None)
        return file_ptr is None or getattr(file_ptr, "closed", False)

    def _update_clock_hand(self):
        self.clock_hand = (self.clock_hand + 1) % self.max_frames

    def _find_victim(self) -> int:
        for _ in range(2 * self.max_frames):
            frame = self.frames[self.clock_hand]
            
            if frame.phys_page_id == -1:
                victim = self.clock_hand
                self._update_clock_hand()
                return victim

            if frame.pin_count > 0:
                self._update_clock_hand()
                continue;

            if frame.reference == True:
                frame.reference = False
                self._update_clock_hand()
                continue

            if frame.dirty == True and frame.page_bin is not None:
                frame.file_manager.write_page(frame.phys_page_id, frame.page_bin)
                frame.dirty = False
                frame.original_page = bytes(frame.page_bin)
            
            victim = self.clock_hand
            self._update_clock_hand()
            return victim

        return -1

    def fetch_page(self, phys_page_id: int, file_manager: FileManager = None) -> bytearray:
        file_manager = self._resolve_file(file_manager)
        if file_manager is None:
            raise RuntimeError("no hay FileManager para resolver la pagina")

        key = (file_manager, phys_page_id)
        if key in self.page_table:
            frame_id = self.page_table[key]
            frame = self.frames[frame_id]
            frame.pin_count += 1
            frame.reference = True
            return frame.page_bin

        frame_id = self._find_victim()

        if frame_id == -1:
            raise RuntimeError("no available frames for fetching")

        frame = self.frames[frame_id]
        if frame.phys_page_id != -1:
            del self.page_table[(frame.file_manager, frame.phys_page_id)]

        frame.file_manager = file_manager
        frame.phys_page_id = phys_page_id
        frame.page_bin = bytearray(file_manager.read_page(phys_page_id))
        frame.original_page = bytes(frame.page_bin)
        frame.pin_count = 1
        frame.dirty = False
        frame.reference = True
        
        self.page_table[key] = frame_id

        return frame.page_bin

    def unpin_page(self, phys_page_id: int, file_manager: FileManager = None) -> bool:
        file_manager = self._resolve_file(file_manager)
        key = (file_manager, phys_page_id)
        if key not in self.page_table:
            return False

        frame = self.frames[self.page_table[key]]

        if frame.pin_count == 0:
            return False

        frame.pin_count -= 1
        return True

    def mark_dirty(self, phys_page_id: int, file_manager: FileManager = None) -> bool:
        file_manager = self._resolve_file(file_manager)
        key = (file_manager, phys_page_id)
        if key not in self.page_table:
            return False

        frame = self.frames[self.page_table[key]]
        frame.dirty = True
        transaction_id = (
            self._transaction_id_provider()
            if self._transaction_id_provider is not None
            else None
        )
        if (
            transaction_id is not None
            and self._wal_logger is not None
            and frame.original_page is not None
            and bytes(frame.page_bin) != frame.original_page
        ):
            self._wal_logger(
                transaction_id,
                frame.file_manager,
                frame.phys_page_id,
                frame.original_page,
                bytes(frame.page_bin),
            )
            frame.original_page = bytes(frame.page_bin)
        elif transaction_id is None:
            frame.original_page = bytes(frame.page_bin)
        return True

    def restore_page(self, phys_page_id: int, page_bin, file_manager=None):
        """Restaura una página en disco y sincroniza su frame si está cargado."""
        file_manager = self._resolve_file(file_manager)
        key = (file_manager, phys_page_id)
        if key in self.page_table:
            frame = self.frames[self.page_table[key]]
            frame.page_bin[:] = page_bin
            frame.original_page = bytes(page_bin)
            frame.dirty = False
        else:
            file_manager.write_page(phys_page_id, page_bin)
        file_manager.force()

    def flush_page(self, phys_page_id: int, file_manager: FileManager = None) -> bool:
        file_manager = self._resolve_file(file_manager)
        key = (file_manager, phys_page_id)
        if key not in self.page_table:
            return False

        frame = self.frames[self.page_table[key]]

        if frame.dirty == True:
            file_manager.write_page(phys_page_id, frame.page_bin)
            frame.dirty = False
            frame.original_page = bytes(frame.page_bin)

        return True
    
    def flush_file(self, file_manager: FileManager):
        if self._is_closed(file_manager):
            return

        for frame in self.frames:
            if (
                frame.file_manager is file_manager
                and frame.phys_page_id != -1
                and frame.dirty
                and frame.page_bin is not None
            ):
                file_manager.write_page(frame.phys_page_id, frame.page_bin)
                frame.dirty = False
                frame.original_page = bytes(frame.page_bin)

        file_manager.flush()
    
    def flush_all(self):
        for frame in self.frames:
            if (
                frame.phys_page_id != -1
                and frame.dirty
                and frame.page_bin is not None
                and not self._is_closed(frame.file_manager)
            ):
                frame.file_manager.write_page(
                    frame.phys_page_id,
                    frame.page_bin
                )
                frame.dirty = False
                frame.original_page = bytes(frame.page_bin)

        for fm in self._known_files:
            if not self._is_closed(fm):
                fm.flush()

    def close(self, file_manager: FileManager = None):
        if file_manager is None:
            self.flush_all()
            for fm in self._known_files:
                if not self._is_closed(fm):
                    fm.close()
            self.page_table = {}
            for frame in self.frames:
                frame.file_manager = None
                frame.phys_page_id = -1
                frame.page_bin = None
                frame.original_page = None
                frame.pin_count = 0
                frame.dirty = False
                frame.reference = False
            return

        self.flush_file(file_manager)

        for page_key in list(self.page_table.keys()):
            if page_key[0] is file_manager:
                del self.page_table[page_key]

        for frame in self.frames:
            if frame.file_manager is file_manager:
                frame.file_manager = None
                frame.phys_page_id = -1
                frame.page_bin = None
                frame.original_page = None
                frame.pin_count = 0
                frame.dirty = False
                frame.reference = False

        if not self._is_closed(file_manager):
            file_manager.close()

    def invalidate_all(self, file_manager: FileManager = None):
        def _drop(frame):
            frame.phys_page_id = -1
            frame.page_bin = None
            frame.original_page = None
            frame.pin_count = 0
            frame.dirty = False
            frame.reference = False

        for page_key in list(self.page_table.keys()):
            if file_manager is None or page_key[0] is file_manager:
                del self.page_table[page_key]

        for frame in self.frames:
            if file_manager is None or frame.file_manager is file_manager:
                if file_manager is not None:
                    frame.file_manager = None
                _drop(frame)