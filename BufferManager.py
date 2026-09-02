from FileManager import FileManager

"""
Cada frame alberga exactamente una pagina.
La idea es que cada pagina que traigamos de disco
se almacene en un buffer pool, y podamos mantener
acceso rapido hacia ellas.

Cada frame contiene:
    phys_page_id: el indice fisico de la pagina
    page_bin: los contenidos de la pagina, en binario
    pin_count: cantidad de recursos de la DBMS que actualmente estan usando este frame
    dirty: booleano que señala si existe alguna modificacion en RAM que no haya sido propagada a disco todavia
    reference: indica que la pagina ha sido usada recientemente, importante para el algoritmo clock-sweep
"""
class Frame:

    def __init__(self):
        self.phys_page_id: int          = -1
        self.page_bin: bytearray | None = None
        self.pin_count: int             = 0
        self.dirty: bool                = False
        self.reference: bool            = False


"""
    file_manager: objeto tipo FileManager para input/output
    frames: lista de todos los frames
    max_frames: cantidad maxima de frames
    page_table: diccionario que corresponde el indice de una pagina con su frame 
    clock_hand: indice de la pagina con la que inicia el algoritmo clock-sweep
"""
class BufferManager:

    def __init__(self, file_manager: FileManager, max_frames: int):
        self.file_manager = file_manager
        self.frames = [Frame() for _ in range(max_frames)]
        self.max_frames = max_frames
        self.page_table = {}
        self.clock_hand = 0

    def _update_clock_hand(self):
        self.clock_hand = (self.clock_hand + 1) % self.max_frames

    def _find_victim(self) -> int:
        """
        Busca algun frame en el pool que pueda ser reemplazado
        usando el algoritmo clock-sweep. Retorna -1 si no es
        posible reemplazar ningun frame.
        """
        for _ in range(2 * self.max_frames):
            frame = self.frames[self.clock_hand]
            
            # si el frame esta vacio, usarlo
            if frame.phys_page_id == -1:
                victim = self.clock_hand
                self._update_clock_hand()
                return victim

            # si hay algun recurso que esta usando el frame, no usarlo
            if frame.pin_count > 0:
                self._update_clock_hand()
                continue;

            # si el frame fue usado recientemente, setear reference = False,
            # y esperar a la segunda iteracion
            if frame.reference == True:
                frame.reference = False
                self._update_clock_hand()
                continue

            # si hay diferencias entre la pagina de disco y RAM, persistir los cambios
            if frame.dirty == True and frame.page_bin is not None:
                self.file_manager.write_page(frame.phys_page_id, frame.page_bin)
                frame.dirty = False
            
            # si no hay nada utilizando el frame actual y reference = false, usar
            # la pagina
            victim = self.clock_hand
            self._update_clock_hand()
            return victim

        return -1

    def fetch_page(self, phys_page_id: int) -> bytearray:
        """
        Retorna una pagina del buffer pool. Si la pagina no esta en
        memoria, la carga desde el FileManager.
        """
        if phys_page_id in self.page_table:
            frame_id = self.page_table[phys_page_id]
            frame = self.frames[frame_id]
            frame.pin_count += 1
            frame.reference = True
            return frame.page_bin

        frame_id = self._find_victim()

        if frame_id == -1:
            raise RuntimeError("no available frames for fetching")

        frame = self.frames[frame_id]
        if frame.phys_page_id != -1:
            del self.page_table[frame.phys_page_id]

        frame.phys_page_id = phys_page_id
        frame.page_bin = bytearray(self.file_manager.read_page(phys_page_id))
        frame.pin_count = 1
        frame.dirty = False
        frame.reference = True

        return frame.page_bin

    def unpin_page(self, phys_page_id: int) -> bool:
        """
        Reduce el contador de referencias de una pagina.
        """
        if phys_page_id not in self.page_table:
            return False

        frame_id = self.page_table[phys_page_id]
        frame = self.frames[frame_id]

        if frame.pin_count == 0:
            return False

        frame.pin_count -= 1
        return True

    def mark_dirty(self, phys_page_id: int) -> bool:
        """
        Marca una pagina como modificada en RAM pero no actualizada en disco.
        """
        if phys_page_id not in self.page_table:
            return False

        frame_id = self.page_table[phys_page_id]
        self.frames[frame_id].dirty = True
        return True

    def flush_page(self, phys_page_id: int) -> bool:
        """
        Persiste los cambios hechos a una pagina en RAM a disco.
        """ 
        if phys_page_id not in self.page_table:
            return False

        frame_id = self.page_table[phys_page_id]
        frame = self.frames[frame_id]

        if frame.dirty == True:
            self.file_manager.write_page(phys_page_id, frame.page_bin)
            frame.dirty = False

        return True

