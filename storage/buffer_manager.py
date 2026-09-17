from storage.file_manager import FileManager

"""
Cada frame alberga exactamente una pagina.
La idea es que cada pagina que traigamos de disco
se almacene en un buffer pool ÚNICO y GLOBAL, y podamos mantener
acceso rapido hacia ellas.

Como ahora existe UN solo buffer pool compartido por TODOS los
archivos de la base (tablas, catalogo e indices), una pagina se
identifica por el par (file_manager, phys_page_id): el mismo numero
de pagina en dos archivos distintos son DOS paginas distintas del
pool. Cada frame recuerda a que archivo pertenece su pagina, para
saber donde escribirla cuando hay que perseguirla a disco.

Cada frame contiene:
    file_manager: el FileManager del archivo al que pertenece la pagina
    phys_page_id: el indice fisico de la pagina DENTRO de ese archivo
    page_bin: los contenidos de la pagina, en binario
    pin_count: cantidad de recursos de la DBMS que actualmente estan usando este frame
    dirty: booleano que señala si existe alguna modificacion en RAM que no haya sido propagada a disco todavia
    reference: indica que la pagina ha sido usada recientemente, importante para el algoritmo clock-sweep
"""
class Frame:

    def __init__(self):
        self.file_manager: FileManager | None = None
        self.phys_page_id: int          = -1
        self.page_bin: bytearray | None = None
        self.pin_count: int             = 0
        self.dirty: bool                = False
        self.reference: bool            = False


"""
BufferManager GLOBAL (singleton).

Hay exactamente una instancia de BufferManager por proceso, y por lo
tanto un solo buffer pool (un unico arreglo de frames, un unico
clock-sweep) compartido por TODO el motor: tablas heap, tablas
sequenciales, catalogo del sistema e indices B+.

    frames: lista de todos los frames
    max_frames: cantidad maxima de frames
    page_table: diccionario que corresponde el par
        (FileManager, phys_page_id) de una pagina con su frame
    clock_hand: indice de la pagina con la que inicia el algoritmo clock-sweep

Como el pool es global, los metodos reciben la pagina Y el archivo:
    fetch_page(phys_page_id, file_manager)
    unpin_page(phys_page_id, file_manager)
    mark_dirty(phys_page_id, file_manager)
...
El parametro file_manager es opcional: si se omite se usa el ultimo
archivo registrado (via BufferManager(file_manager, ...)), para no
romper los usos de una sola pagina a la vez.

Cualquier componente puede acceder al pool con
BufferManager.get_instance().
"""
class BufferManager:

    _instance = None

    def __new__(cls, *args, **kwargs):
        # patrón singleton: todas las llamadas a BufferManager(...)
        # devuelven la MISMA instancia (un único buffer pool global)
        if cls._instance is None:
            cls._instance = super(BufferManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, file_manager: FileManager = None, max_frames: int = 10):
        # la primera construcción fija el tamaño del pool; las
        # siguientes solo (re)registran el file_manager activo
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

        self._register_file(file_manager)

    @classmethod
    def get_instance(cls) -> "BufferManager":
        """
        Devuelve el buffer pool global. Si todavia nadie lo construyo,
        lo crea con los valores por defecto.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _register_file(self, file_manager):
        # actualiza cual es el archivo "activo" (ultimo registrado, usado
        # como default cuando un llamador no indica el FileManager) y lleva
        # la lista de FileManager que alguna vez pasaron por el pool, para
        # poder cerrarlos todos en close() sin argumentos
        if file_manager is not None:
            self.active_file = file_manager
            if not any(fm is file_manager for fm in self._known_files):
                self._known_files.append(file_manager)

    def _resolve_file(self, file_manager) -> FileManager:
        # file_manager ausente -> el último archivo registrado (semántica
        # compatible con el viejo "un buffer manager por archivo")
        return file_manager if file_manager is not None else self.active_file

    @staticmethod
    def _is_closed(file_manager) -> bool:
        # True si el archivo subyacente ya no acepta escrituras
        file_ptr = getattr(file_manager, "file_ptr", None)
        return file_ptr is None or getattr(file_ptr, "closed", False)

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
                frame.file_manager.write_page(frame.phys_page_id, frame.page_bin)
                frame.dirty = False
            
            # si no hay nada utilizando el frame actual y reference = false, usar
            # la pagina
            victim = self.clock_hand
            self._update_clock_hand()
            return victim

        return -1

    def fetch_page(self, phys_page_id: int, file_manager: FileManager = None) -> bytearray:
        """
        Retorna una pagina del buffer pool. Si la pagina no esta en
        memoria, la carga desde su FileManager. La pagina queda pinneada.
        """
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
        frame.pin_count = 1
        frame.dirty = False
        frame.reference = True
        
        self.page_table[key] = frame_id

        return frame.page_bin

    def unpin_page(self, phys_page_id: int, file_manager: FileManager = None) -> bool:
        """
        Reduce el contador de cantidad de recursos ajenos que usan
        una pagina.
        """
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
        """
        Marca una pagina como modificada en RAM pero no actualizada en disco.
        """
        file_manager = self._resolve_file(file_manager)
        key = (file_manager, phys_page_id)
        if key not in self.page_table:
            return False

        self.frames[self.page_table[key]].dirty = True
        return True

    def flush_page(self, phys_page_id: int, file_manager: FileManager = None) -> bool:
        """
        Persiste los cambios hechos a una pagina en RAM a disco.
        """ 
        file_manager = self._resolve_file(file_manager)
        key = (file_manager, phys_page_id)
        if key not in self.page_table:
            return False

        frame = self.frames[self.page_table[key]]

        if frame.dirty == True:
            file_manager.write_page(phys_page_id, frame.page_bin)
            frame.dirty = False

        return True
    
    def flush_file(self, file_manager: FileManager):
        """
        Escribe en disco todas las paginas modificadas de UN archivo.
        """
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

        file_manager.flush()
    
    def flush_all(self):
        """
        Escribe en disco todas las paginas modificadas que se encuentran
        actualmente en el buffer.
        """
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

        # llama a flush() de todos los archivos conocidos que sigan abiertos
        for fm in self._known_files:
            if not self._is_closed(fm):
                fm.flush()

    def close(self, file_manager: FileManager = None):
        """
        Cierra UN archivo: persiste sus paginas modificadas, las descarta
        del pool y cierra su FileManager. Sin argumentos, cierra todos los
        archivos conocidos (comportamiento historico de close() del buffer
        manager por-archivo).
        """
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
                frame.pin_count = 0
                frame.dirty = False
                frame.reference = False
            return

        self.flush_file(file_manager)

        # descartar las paginas de ese archivo del pool
        for page_key in list(self.page_table.keys()):
            if page_key[0] is file_manager:
                del self.page_table[page_key]

        for frame in self.frames:
            if frame.file_manager is file_manager:
                frame.file_manager = None
                frame.phys_page_id = -1
                frame.page_bin = None
                frame.pin_count = 0
                frame.dirty = False
                frame.reference = False

        if not self._is_closed(file_manager):
            file_manager.close()

    def invalidate_all(self, file_manager: FileManager = None):
        """
        Descarta las paginas cacheadas de UN archivo sin persistirlas.
        Se usa cuando el archivo subyacente fue reescrito por fuera del
        buffer pool (por ejemplo, al reconstruir un indice desde cero),
        asi que cualquier pagina en cache quedaria apuntando a contenido
        que ya no corresponde a lo que hay en disco. Sin argumentos,
        descarta todas las paginas del pool.
        """
        def _drop(frame):
            frame.phys_page_id = -1
            frame.page_bin = None
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